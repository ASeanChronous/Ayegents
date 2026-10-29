"""Multi-agent flow execution — sequential handoff, parallel fan-out/fan-in,
and simple conditional branching over a Flow's node/edge spec (architecture
doc §4/§5), compiled into a LangGraph StateGraph per run.

Node types: 'trigger' (seeds the flow's input into state), 'agent' (reuses
the same _execute_agent_loop a standalone single-agent run uses), 'tool' (a
bare tool call, not wrapped in a model loop), 'conditional'/'merge'
(passthrough nodes — the real branching/joining is expressed on *edges*, via
_eval_condition and LangGraph's default "wait for every predecessor" join,
not on these nodes themselves).

Two deliberate scope cuts, both called out in docs/01-architecture.md's
roadmap as later work, not oversights:
- Condition language is tiny: "<node_id>.output.<key...>" (optionally
  prefixed "not "), resolved by dict lookup + truthiness. No comparisons,
  no arithmetic, no referencing anything but a predecessor's output.
- No checkpointing: a flow run compiles and executes as one in-memory
  `ainvoke` call. If the orchestrator process dies mid-flow, the run is
  lost — same as every other run in this scaffold, not a flow-specific gap.
"""

import json
import operator
import time
from datetime import datetime, timezone
from typing import Annotated, Any, TypedDict
from uuid import UUID

import asyncpg
from langgraph.graph import END, START, StateGraph

from orchestrator_app.agent_event import fire_agent_event_triggers
from orchestrator_app.executor import (
    _execute_agent_loop,
    _load_current_version,
    _log_tool_step,
)
from orchestrator_app.notify import notify_run
from orchestrator_app.tool_gateway import ToolDispatchError, dispatch_tool


class FlowExecutionError(Exception):
    """Raised when a flow node fails; caught by run_flow and turned into the
    run's failed status/error, same contract _execute_agent_loop follows."""


class FlowState(TypedDict):
    # Annotated with operator.or_ so concurrent (fan-out) branches merge their
    # writes instead of one clobbering another — verified against the real
    # langgraph package, not assumed (see commit history for the check).
    node_outputs: Annotated[dict[str, Any], operator.or_]


async def run_flow(pool: asyncpg.Pool, run_id: UUID) -> None:
    async with pool.acquire() as conn:
        run = await conn.fetchrow("select * from runs where id = $1", run_id)
        if run["draft_spec"] is not None:
            # A true draft dry-run (docs/04-frontend-builder.md §10): the
            # canvas's own nodes/edges, handed straight to execute_flow below
            # exactly as the API received them -- never written to
            # flow_versions, so there's nothing to look up here at all.
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

        await conn.execute(
            "update runs set status = 'running', started_at = now() where id = $1", run_id
        )
        await notify_run(conn, run_id)

    try:
        # execute_flow acquires its own connection per node below — a flow with
        # concurrent (fan-out) branches cannot share one connection, since a
        # single asyncpg connection can't run two queries at once. The conn
        # released above is free again by the time this starts.
        final_state, total_tokens = await execute_flow(
            pool,
            run_id=run_id,
            workspace_id=run["workspace_id"],
            spec=spec,
            initial_input=run["input"],
            max_steps_per_agent_node=limits["max_steps_per_run"],
        )
        async with pool.acquire() as conn:
            await conn.execute(
                """
                update runs set status = 'succeeded', output = $2, total_tokens = $3, finished_at = now(),
                    total_cost_usd = coalesce((select sum(cost_usd) from run_steps where run_id = $1), 0)
                where id = $1
                """,
                run_id,
                {"node_outputs": final_state["node_outputs"]},
                total_tokens,
            )
            await notify_run(conn, run_id)
            await fire_agent_event_triggers(
                conn,
                source_flow_id=run["flow_id"],
                status="succeeded",
                output={"node_outputs": final_state["node_outputs"]},
            )
    except Exception as exc:  # noqa: BLE001 — surfaced into the run row, not swallowed
        async with pool.acquire() as conn:
            await conn.execute(
                """
                update runs set status = 'failed', error = $2, finished_at = now(),
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


async def execute_flow(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    workspace_id: UUID,
    spec: dict,
    initial_input: dict,
    max_steps_per_agent_node: int,
) -> tuple[FlowState, int]:
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

    tokens_counter = {"total": 0}
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
                tokens_counter=tokens_counter,
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

    compiled = graph.compile()
    final_state = await compiled.ainvoke({"node_outputs": {}})
    return final_state, tokens_counter["total"]


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


def _eval_condition(condition: str, state: FlowState) -> bool:
    text = condition.strip()
    negate = text.startswith("not ")
    if negate:
        text = text[len("not "):]
    parts = text.split(".")
    if len(parts) < 3 or parts[1] != "output":
        raise FlowExecutionError(
            f"unsupported condition syntax: {condition!r} (expected '<node_id>.output.<key...>')"
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
    result = bool(value)
    return not result if negate else result


def _make_node_fn(node, *, pool, workspace_id, run_id, initial_input, max_steps, tokens_counter):
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
                agent_version = await _load_current_version(conn, agent_id)
                if agent_version is None:
                    raise FlowExecutionError(f"node {node['id']!r}: agent {agent_id} has no version")
                message = _resolve_input_text(node, state, initial_input)
                status, output, tokens = await _execute_agent_loop(
                    conn,
                    run_id=run_id,
                    workspace_id=workspace_id,
                    agent_version=agent_version,
                    messages=[{"role": "user", "content": message}],
                    max_steps=agent_version["max_steps"] or max_steps,
                    depth=0,
                    node_id=node["id"],
                )
            tokens_counter["total"] += tokens
            if status != "succeeded":
                raise FlowExecutionError(f"node {node['id']!r} (agent) did not succeed: {output}")
            return {"node_outputs": {node["id"]: {"output": output}}}

        return node_fn

    if node_type == "tool":

        async def node_fn(state):
            async with pool.acquire() as conn:
                tool_id = UUID(node["tool_id"])
                tool = await conn.fetchrow("select * from tools where id = $1", tool_id)
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
