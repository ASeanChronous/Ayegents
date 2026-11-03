"""Multi-agent flow execution — sequential handoff, parallel fan-out/fan-in,
and simple conditional branching over a Flow's node/edge spec (architecture
doc §4/§5), compiled into a LangGraph StateGraph per run.

Node types: 'trigger' (seeds the flow's input into state), 'agent' (reuses
the same _execute_agent_loop a standalone single-agent run uses), 'tool' (a
bare tool call, not wrapped in a model loop), 'conditional'/'merge'
(passthrough nodes — the real branching/joining is expressed on *edges*, via
_eval_condition and LangGraph's default "wait for every predecessor" join,
not on these nodes themselves).

A flow run is checkpointed to Postgres (via a `BaseCheckpointSaver` the caller supplies —
`main.py` owns the actual `AsyncPostgresSaver` instance and its connection pool, this module
just compiles every graph with whichever one it's handed) keyed on `run_id` as the LangGraph
`thread_id`. `execute_flow` always checks for an existing checkpoint before invoking: none
(the normal case, every run's first attempt) means start from the trigger with the flow's
input; one found means a prior attempt got partway through before the orchestrator died, so
`ainvoke(None, ...)` resumes from the last completed super-step instead of re-running
already-finished nodes. `main.py`'s startup recovery pass is what actually re-invokes a
stuck `status = 'running'` flow run after a restart — this module doesn't poll or recover
anything on its own, same separation `webhooks.py`/`scheduler.py` have from their own firing
logic. See `main.py`'s own docstring for the recovery pass and its known limits.

The condition language ("<node_id>.output.<key...>", optionally prefixed "not ", optionally
followed by a comparison) can't reference anything but one predecessor's output and has no
arithmetic -- see `_eval_condition`'s own docstring for the full grammar and reasoning.

A flow started by a telegram or slack trigger gets its result sent back to the chat/channel
that triggered it once it finishes, success or failure -- see
`telegram_reply.reply_if_telegram_triggered`/`slack_reply.reply_if_slack_triggered`, called
right alongside `fire_agent_event_triggers` below, same best-effort shape. Both are cheap
no-ops for any run whose trigger isn't that platform (a single `select ... where kind =
'...'` that comes back empty), so calling both unconditionally on every run is simpler than
branching on which chat platform (if any) actually triggered it.
"""

import json
import operator
import re
import time
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict
from uuid import UUID

import asyncpg
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from orchestrator_app.agent_event import fire_agent_event_triggers
from orchestrator_app.executor import (
    _execute_agent_loop,
    _load_current_version,
    _log_tool_step,
)
from orchestrator_app.notify import notify_run
from orchestrator_app.slack_reply import reply_if_slack_triggered
from orchestrator_app.telegram_reply import reply_if_telegram_triggered
from orchestrator_app.tool_gateway import ToolDispatchError, dispatch_tool


class FlowExecutionError(Exception):
    """Raised when a flow node fails; caught by run_flow and turned into the
    run's failed status/error, same contract _execute_agent_loop follows."""


class FlowState(TypedDict):
    # Annotated with operator.or_ so concurrent (fan-out) branches merge their
    # writes instead of one clobbering another — verified against the real
    # langgraph package, not assumed (see commit history for the check).
    node_outputs: Annotated[dict[str, Any], operator.or_]


async def run_flow(
    pool: asyncpg.Pool, run_id: UUID, checkpointer: BaseCheckpointSaver, *, resuming: bool = False
) -> None:
    async with pool.acquire() as conn:
        run = await conn.fetchrow("select * from runs where id = $1", run_id)
        if run["draft_spec"] is not None:
            # A true draft dry-run (docs/04-frontend-builder.md §10): the
            # canvas's own nodes/edges, handed straight to execute_flow below
            # exactly as the API received them -- never written to
            # flow_versions, so there's nothing to look up here at all. Also
            # never needs the "pin the resolved version" treatment below --
            # the spec itself is already pinned, on the run row.
            spec = run["draft_spec"]
        elif run["flow_version_id"]:
            flow_version = await conn.fetchrow(
                "select * from flow_versions where id = $1", run["flow_version_id"]
            )
            spec = flow_version["spec"] if flow_version else None
        else:
            flow_version = await conn.fetchrow(
                "select * from flow_versions where flow_id = $1 order by version desc limit 1",
                run["flow_id"],
            )
            spec = flow_version["spec"] if flow_version else None
            if flow_version is not None:
                # Pin the version this run actually resolved to "latest" as
                # onto the run itself -- "latest" has to mean "latest at the
                # moment this run started," not "latest whenever we happen to
                # look." Without this, a flow run interrupted mid-flight
                # (orchestrator restart) and resumed after someone published
                # a newer version in between would rebuild a *different*
                # graph than the one the checkpoint was written against,
                # which LangGraph has no way to detect or reconcile.
                await conn.execute(
                    "update runs set flow_version_id = $2 where id = $1", run_id, flow_version["id"]
                )

        if spec is None:
            await conn.execute(
                """
                update runs set status = 'failed', error = 'flow has no published version', finished_at = now()
                where id = $1
                """,
                run_id,
            )
            await notify_run(conn, run_id)
            return

        limits = await conn.fetchrow(
            """
            select l.* from plan_tier_limits l
            join workspaces w on w.plan_tier = l.plan_tier
            where w.id = $1
            """,
            run["workspace_id"],
        )

        if not resuming:
            await conn.execute(
                "update runs set status = 'running', started_at = now() where id = $1", run_id
            )
            await notify_run(conn, run_id)

    try:
        # execute_flow acquires its own connection per node below — a flow with
        # concurrent (fan-out) branches cannot share one connection, since a
        # single asyncpg connection can't run two queries at once. The conn
        # released above is free again by the time this starts.
        final_state = await execute_flow(
            pool,
            checkpointer=checkpointer,
            run_id=run_id,
            workspace_id=run["workspace_id"],
            spec=spec,
            initial_input=run["input"],
            max_steps_per_agent_node=limits["max_steps_per_run"],
            max_tokens_per_agent_node=limits["max_tokens_per_run"],
            max_wall_clock_seconds=limits["max_wall_clock_seconds"],
        )
        async with pool.acquire() as conn:
            await conn.execute(
                """
                update runs set status = 'succeeded', output = $2, finished_at = now(),
                    total_tokens = coalesce((select sum(tokens_input + tokens_output) from run_steps where run_id = $1), 0),
                    total_cost_usd = coalesce((select sum(cost_usd) from run_steps where run_id = $1), 0)
                where id = $1
                """,
                run_id,
                {"node_outputs": final_state["node_outputs"]},
            )
            await notify_run(conn, run_id)
            await fire_agent_event_triggers(
                conn,
                source_flow_id=run["flow_id"],
                status="succeeded",
                output={"node_outputs": final_state["node_outputs"]},
            )
            await reply_if_telegram_triggered(
                conn,
                trigger_id=run["trigger_id"],
                run_input=run["input"],
                status="succeeded",
                output={"node_outputs": final_state["node_outputs"]},
                error=None,
            )
            await reply_if_slack_triggered(
                conn,
                trigger_id=run["trigger_id"],
                run_input=run["input"],
                status="succeeded",
                output={"node_outputs": final_state["node_outputs"]},
                error=None,
            )
    except Exception as exc:  # noqa: BLE001 — surfaced into the run row, not swallowed
        async with pool.acquire() as conn:
            await conn.execute(
                """
                update runs set status = 'failed', error = $2, finished_at = now(),
                    total_tokens = coalesce((select sum(tokens_input + tokens_output) from run_steps where run_id = $1), 0),
                    total_cost_usd = coalesce((select sum(cost_usd) from run_steps where run_id = $1), 0)
                where id = $1
                """,
                run_id,
                str(exc),
            )
            await notify_run(conn, run_id)
            await fire_agent_event_triggers(
                conn, source_flow_id=run["flow_id"], status="failed", output=None
            )
            await reply_if_telegram_triggered(
                conn, trigger_id=run["trigger_id"], run_input=run["input"], status="failed", output=None, error=str(exc)
            )
            await reply_if_slack_triggered(
                conn, trigger_id=run["trigger_id"], run_input=run["input"], status="failed", output=None, error=str(exc)
            )


async def execute_flow(
    pool: asyncpg.Pool,
    *,
    checkpointer: BaseCheckpointSaver,
    run_id: UUID,
    workspace_id: UUID,
    spec: dict,
    initial_input: dict,
    max_steps_per_agent_node: int,
    max_tokens_per_agent_node: int,
    max_wall_clock_seconds: int,
) -> FlowState:
    nodes = {n["id"]: n for n in spec.get("nodes", [])}
    edges = spec.get("edges", [])
    if not nodes:
        raise FlowExecutionError("flow spec has no nodes")

    trigger_ids = [node_id for node_id, n in nodes.items() if n["type"] == "trigger"]
    if len(trigger_ids) != 1:
        raise FlowExecutionError(
            f"flow spec must have exactly one trigger node, found {len(trigger_ids)}"
        )
    trigger_id = trigger_ids[0]

    graph = StateGraph(FlowState)
    for node_id, node in nodes.items():
        graph.add_node(
            node_id,
            _make_node_fn(
                node,
                pool=pool,
                workspace_id=workspace_id,
                run_id=run_id,
                initial_input=initial_input,
                max_steps=max_steps_per_agent_node,
                max_tokens_budget=max_tokens_per_agent_node,
                max_wall_clock_seconds=max_wall_clock_seconds,
            ),
        )

    outgoing: dict[str, list[dict]] = {}
    for edge in edges:
        outgoing.setdefault(edge["from"], []).append(edge)

    graph.add_edge(START, trigger_id)
    for source_id, source_edges in outgoing.items():
        if any(e.get("condition") for e in source_edges):
            graph.add_conditional_edges(source_id, _make_router(source_edges))
        else:
            for edge in source_edges:
                graph.add_edge(source_id, edge["to"])

    # A node with no outgoing edges is a leaf — wire it to END so the graph
    # actually terminates there instead of LangGraph treating it as dangling.
    for node_id in nodes:
        if node_id not in outgoing:
            graph.add_edge(node_id, END)

    compiled = graph.compile(checkpointer=checkpointer)
    # thread_id = run_id is what makes a crash-and-restart's resume (main.py's
    # recovery pass) and this run's own checkpoint the same conversation, as
    # far as LangGraph is concerned — every invocation of this run, first or
    # resumed, must use this exact config for the checkpointer to agree.
    config = {"configurable": {"thread_id": str(run_id)}}
    # A thread with no prior checkpoint (every run's first attempt; the
    # overwhelming majority of the time) has an empty state snapshot — start
    # normally. One *with* a checkpoint means a previous attempt got partway
    # through before the orchestrator died (main.py's recovery pass re-enters
    # here with the same run_id after a restart): ainvoke(None, ...) resumes
    # from the last completed super-step instead of re-running finished nodes
    # (whose run_steps rows, and hence tokens/cost, already exist from before
    # the crash -- re-running them would double both).
    existing_state = await compiled.aget_state(config)
    if existing_state.values:
        final_state = await compiled.ainvoke(None, config)
    else:
        final_state = await compiled.ainvoke({"node_outputs": {}}, config)
    return final_state


def _make_router(source_edges: list[dict]):
    def route(state: FlowState) -> list[str]:
        targets = [
            edge["to"]
            for edge in source_edges
            if edge.get("condition") is None or _eval_condition(edge["condition"], state)
        ]
        if not targets:
            raise FlowExecutionError(f"no edge condition matched out of {source_edges!r}")
        return targets

    return route


_COMPARISON_PATTERN = re.compile(r"^(?P<lhs>\S+)\s+(?P<op>==|!=|>=|<=|>|<)\s+(?P<rhs>.+)$")

_COMPARISON_OPS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">": lambda a, b: _as_number(a) > _as_number(b),
    "<": lambda a, b: _as_number(a) < _as_number(b),
    ">=": lambda a, b: _as_number(a) >= _as_number(b),
    "<=": lambda a, b: _as_number(a) <= _as_number(b),
}


def _as_number(value: Any) -> float:
    # bool excluded even though it's technically an int subclass -- "true > 0"
    # being valid Python is a footgun here, not a feature, for a condition
    # language users write by hand.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FlowExecutionError(f"can't compare non-numeric value {value!r} with >, <, >=, or <=")
    return float(value)


def _parse_condition_literal(raw: str) -> Any:
    """The right-hand side of a comparison. JSON syntax covers numbers,
    true/false/null, and double-quoted strings; single quotes are also
    accepted (JSON doesn't allow them) since hand-typing \\" in a form field
    is awkward. Anything else that isn't valid JSON is taken as a bare,
    unquoted string -- 'status == approved' works without quoting."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _eval_condition(condition: str, state: FlowState) -> bool:
    """Grammar: "[not ]<node_id>.output.<key...>[ <op> <value>]".

    With no `<op> <value>`, the condition is the referenced value's truthiness
    (the original, still-default behavior). With one, `<op>` is one of
    ==/!=/>/</>=/<=  and `<value>` is a JSON literal (or a bare unquoted
    string) compared against the referenced value. `>`/`<`/`>=`/`<=` require
    both sides to be numbers, or the condition raises rather than silently
    coercing strings. A leading "not " negates the final result either way.

    Deliberately still can't reference anything but one predecessor's output
    (no cross-node expressions) and has no arithmetic -- a condition this
    small is meant to pick a branch, not compute one; an agent or tool node
    upstream is where real logic belongs.
    """
    text = condition.strip()
    negate = text.startswith("not ")
    if negate:
        text = text[len("not "):]

    match = _COMPARISON_PATTERN.match(text)
    lhs_text = match.group("lhs") if match else text

    parts = lhs_text.split(".")
    if len(parts) < 3 or parts[1] != "output":
        raise FlowExecutionError(
            f"unsupported condition syntax: {condition!r} "
            "(expected '<node_id>.output.<key...>', optionally followed by '<op> <value>')"
        )
    node_id, _output_literal, *key_path = parts
    value: Any = state["node_outputs"].get(node_id, {}).get("output", {})
    # An agent node's output is always {"text": "<model's final reply>"} —
    # _execute_agent_loop's contract is deliberately that simple and stable
    # (it's shared with standalone single-agent runs and agent-as-tool, so it
    # can't grow a special case just for flows). A condition that wants to
    # branch on an agent's decision (e.g. a classifier prompted to answer in
    # JSON) needs *something* to key into beyond raw text, so: if the output
    # is exactly {"text": ...} and that text parses as a JSON object, branch
    # against the parsed object instead. Tool node outputs are already
    # structured dicts and pass through this unchanged.
    if isinstance(value, dict) and value.keys() == {"text"} and isinstance(value["text"], str):
        try:
            parsed = json.loads(value["text"])
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            value = parsed
    for key in key_path:
        value = value.get(key) if isinstance(value, dict) else None

    if match is None:
        result = bool(value)
    else:
        rhs = _parse_condition_literal(match.group("rhs"))
        result = _COMPARISON_OPS[match.group("op")](value, rhs)
    return not result if negate else result


def _make_node_fn(node, *, pool, workspace_id, run_id, initial_input, max_steps, max_tokens_budget, max_wall_clock_seconds):
    node_type = node["type"]

    if node_type == "trigger":

        async def node_fn(state):
            return {"node_outputs": {node["id"]: {"output": initial_input}}}

        return node_fn

    if node_type == "agent":

        async def node_fn(state):
            # Own connection per node call: fan-out branches run concurrently
            # (different asyncio tasks), and a single asyncpg connection can't
            # serve two queries at once — sharing one here raised
            # "cannot perform operation: another operation is in progress"
            # under real concurrency, caught by testing this against true
            # parallel branches rather than only sequential ones.
            async with pool.acquire() as conn:
                agent_id = UUID(node["agent_id"])
                # §20's cross-workspace agent-delegation gap (executor.py's
                # run_agent_as_tool) had a twin here: this node's agent_id comes
                # straight from the flow spec (routers/flows.py's create_flow_version
                # deliberately leaves per-node-type field validation to run time, see
                # its own comment), and nothing here ever checked it names an agent in
                # *this* workspace before running it -- a flow referencing another
                # workspace's agent_id would run that agent's system prompt and tools
                # under this run, same exposure §20 closed for the agent-as-tool path,
                # just reachable without even needing to craft a tool row first.
                owning_workspace = await conn.fetchval("select workspace_id from agents where id = $1", agent_id)
                if owning_workspace != workspace_id:
                    raise FlowExecutionError(f"node {node['id']!r}: agent {agent_id} not found in this workspace")
                agent_version = await _load_current_version(conn, agent_id)
                if agent_version is None:
                    raise FlowExecutionError(f"node {node['id']!r}: agent {agent_id} has no version")
                message = _resolve_input_text(node, state, initial_input)
                status, output, _tokens = await _execute_agent_loop(
                    conn,
                    run_id=run_id,
                    workspace_id=workspace_id,
                    agent_version=agent_version,
                    messages=[{"role": "user", "content": message}],
                    max_steps=agent_version["max_steps"] or max_steps,
                    max_tokens_budget=agent_version["max_tokens"] or max_tokens_budget,
                    max_wall_clock_seconds=max_wall_clock_seconds,
                    depth=0,
                    node_id=node["id"],
                )
            # Not accumulated here -- run_flow computes the run's total_tokens
            # via a SQL sum over run_steps instead (same reason total_cost_usd
            # already does), since a resumed node_fn call for an *already
            # completed* node never re-executes (LangGraph skips it, reading
            # its output from the checkpoint), so a Python-side running total
            # would silently undercount tokens from before a restart.
            if status != "succeeded":
                raise FlowExecutionError(f"node {node['id']!r} (agent) did not succeed: {output}")
            return {"node_outputs": {node["id"]: {"output": output}}}

        return node_fn

    if node_type == "tool":

        async def node_fn(state):
            async with pool.acquire() as conn:
                tool_id = UUID(node["tool_id"])
                # Same gap as the agent node above, same reason: tool_id comes straight
                # from the flow spec with no ownership check. Scoped the same way
                # tools.py::list_tools already does (a global builtin, workspace_id is
                # null, or a tool this workspace itself created) -- this doesn't leak a
                # credential either way (_load_credential_secret below is already scoped
                # by (tool_id, workspace_id), so a tool from an unrelated workspace just
                # 404s on its own credential lookup rather than finding this workspace's),
                # but it did let a flow use another workspace's tool definition --
                # borrowing its allowed_hosts egress scope, for instance -- without owning
                # it.
                tool = await conn.fetchrow(
                    "select * from tools where id = $1 and (workspace_id is null or workspace_id = $2)",
                    tool_id,
                    workspace_id,
                )
                if tool is None:
                    raise FlowExecutionError(f"node {node['id']!r}: tool {tool_id} not found")
                tool_input = _resolve_input_dict(node, state, initial_input)
                started = time.monotonic()
                started_at = datetime.now(timezone.utc)
                try:
                    result = await dispatch_tool(
                        conn,
                        tool=tool,
                        config=node.get("config", {}),
                        tool_input=tool_input,
                        workspace_id=workspace_id,
                        run_id=run_id,
                        depth=0,
                    )
                    await _log_tool_step(
                        conn, run_id, tool["id"], tool_input, result, started, started_at, "succeeded", None
                    )
                except ToolDispatchError as exc:
                    await _log_tool_step(
                        conn,
                        run_id,
                        tool["id"],
                        tool_input,
                        {"error": str(exc)},
                        started,
                        started_at,
                        "failed",
                        str(exc),
                    )
                    raise FlowExecutionError(f"node {node['id']!r} (tool): {exc}") from exc
            return {"node_outputs": {node["id"]: {"output": result}}}

        return node_fn

    if node_type in ("conditional", "merge"):

        async def node_fn(state):
            return {"node_outputs": {node["id"]: {"output": {}}}}

        return node_fn

    raise FlowExecutionError(f"unknown flow node type: {node_type!r}")


def _resolve_input_text(node: dict, state: FlowState, initial_input: dict) -> str:
    data = _resolve_input_dict(node, state, initial_input)
    return data.get("message") or data.get("text") or str(data)


def _resolve_input_dict(node: dict, state: FlowState, initial_input: dict) -> dict:
    """config.input_map = {"from": "<node_id>"} takes that predecessor's
    output wholesale; omitted (or its source has no output yet) falls back
    to the flow's original input."""
    input_map = node.get("config", {}).get("input_map")
    source_node = input_map.get("from") if input_map else None
    if not source_node:
        return initial_input
    return state["node_outputs"].get(source_node, {}).get("output") or initial_input
