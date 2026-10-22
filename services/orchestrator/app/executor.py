"""Single-agent run executor.

Loads the agent's current version, runs the Anthropic tool-use loop against
the mapped model tier (model_router.py), dispatching any tool_use blocks
through the Tool Gateway (tool_gateway.py) and persisting one `run_steps` row
per model/tool call as it goes — so a run is inspectable mid-flight, not just
at completion (architecture doc §5).

Multi-agent *flow* execution (sequential handoff, parallel fan-out/fan-in via
LangGraph, §9) still isn't implemented — this only covers a standalone agent
run and the agent-as-tool case, both of which share `_execute_agent_loop`.
"""

import time
from uuid import UUID

import asyncpg
from anthropic import AsyncAnthropic

from app.config import settings
from app.model_router import resolve_model_id
from app.tool_gateway import ToolDispatchError, dispatch_tool

client = AsyncAnthropic(api_key=settings.anthropic_api_key)


async def run_single_agent(conn: asyncpg.Connection, run_id: UUID) -> None:
    run = await conn.fetchrow("select * from runs where id = $1", run_id)
    agent_version = await _load_current_version(conn, run["agent_id"])
    limits = await conn.fetchrow(
        """
        select l.* from plan_tier_limits l
        join workspaces w on w.plan_tier = l.plan_tier
        where w.id = $1
        """,
        run["workspace_id"],
    )
    max_steps = agent_version["max_steps"] or limits["max_steps_per_run"]

    await conn.execute(
        "update runs set status = 'running', started_at = now() where id = $1", run_id
    )

    status, output, total_tokens = await _execute_agent_loop(
        conn,
        run_id=run_id,
        workspace_id=run["workspace_id"],
        agent_version=agent_version,
        messages=[{"role": "user", "content": run["input"].get("message", "")}],
        max_steps=max_steps,
        depth=0,
    )

    await conn.execute(
        """
        update runs
        set status = $2, output = $3, total_tokens = $4, error = $5, finished_at = now()
        where id = $1
        """,
        run_id,
        status,
        output,
        total_tokens,
        output.get("error") if status == "failed" else None,
    )


async def run_agent_as_tool(
    conn: asyncpg.Connection,
    *,
    parent_run_id: UUID,
    sub_agent_id: UUID,
    tool_input: dict,
    depth: int,
) -> dict:
    """Entered from tool_gateway.dispatch_tool when a tool of kind='agent' is
    called. Reuses the parent run's row (architecture §5: agent-as-tool is a
    synchronous call, not a separate Run) — steps are logged under the same
    run_id so the whole delegation chain shows up in one inspector view."""
    run = await conn.fetchrow("select workspace_id from runs where id = $1", parent_run_id)
    agent_version = await _load_current_version(conn, sub_agent_id)
    limits = await conn.fetchrow(
        """
        select l.* from plan_tier_limits l
        join workspaces w on w.plan_tier = l.plan_tier
        where w.id = $1
        """,
        run["workspace_id"],
    )
    max_steps = agent_version["max_steps"] or limits["max_steps_per_run"]

    message = tool_input.get("message") or tool_input.get("input") or str(tool_input)
    status, output, _tokens = await _execute_agent_loop(
        conn,
        run_id=parent_run_id,
        workspace_id=run["workspace_id"],
        agent_version=agent_version,
        messages=[{"role": "user", "content": message}],
        max_steps=max_steps,
        depth=depth,
    )
    if status != "succeeded":
        raise ToolDispatchError(f"sub-agent call did not succeed: {output}")
    return output


async def _load_current_version(conn: asyncpg.Connection, agent_id: UUID) -> asyncpg.Record:
    return await conn.fetchrow(
        "select * from agent_versions where agent_id = $1 order by version desc limit 1",
        agent_id,
    )


async def _load_tools(conn: asyncpg.Connection, agent_version_id: UUID) -> list[dict]:
    rows = await conn.fetch(
        """
        select t.id, t.key, t.name, t.kind, t.config_schema, t.requires_credential, avt.config
        from agent_version_tools avt
        join tools t on t.id = avt.tool_id
        where avt.agent_version_id = $1
        """,
        agent_version_id,
    )
    return [dict(r) for r in rows]


async def _next_step_index(conn: asyncpg.Connection, run_id: UUID) -> int:
    row = await conn.fetchrow(
        "select coalesce(max(step_index), -1) + 1 as next from run_steps where run_id = $1",
        run_id,
    )
    return row["next"]


async def _execute_agent_loop(
    conn: asyncpg.Connection,
    *,
    run_id: UUID,
    workspace_id: UUID,
    agent_version: asyncpg.Record,
    messages: list[dict],
    max_steps: int,
    depth: int,
) -> tuple[str, dict, int]:
    model_id = resolve_model_id(agent_version["model_tier"])
    tool_rows = await _load_tools(conn, agent_version["id"])
    tool_defs = [
        {
            "name": t["key"],
            "description": t["name"],
            "input_schema": t["config_schema"] or {"type": "object", "properties": {}},
        }
        for t in tool_rows
    ]
    tools_by_key = {t["key"]: t for t in tool_rows}
    total_tokens = 0

    try:
        for _ in range(max_steps):
            started = time.monotonic()
            response = await client.messages.create(
                model=model_id,
                max_tokens=4096,
                system=agent_version["system_prompt"],
                messages=messages,
                tools=tool_defs or None,
            )
            latency_ms = int((time.monotonic() - started) * 1000)
            total_tokens += response.usage.input_tokens + response.usage.output_tokens
            assistant_content = [block.model_dump() for block in response.content]
            messages.append({"role": "assistant", "content": assistant_content})

            step_index = await _next_step_index(conn, run_id)
            await conn.execute(
                """
                insert into run_steps
                    (run_id, step_index, kind, agent_id, node_id, input, output,
                     tokens_input, tokens_output, latency_ms, status)
                values ($1, $2, 'agent_call', $3, $4, $5, $6, $7, $8, $9, 'succeeded')
                """,
                run_id,
                step_index,
                agent_version["agent_id"],
                f"depth:{depth}",
                {"messages": messages[:-1]},
                {"content": assistant_content},
                response.usage.input_tokens,
                response.usage.output_tokens,
                latency_ms,
            )

            if response.stop_reason != "tool_use":
                final_text = "".join(
                    block.text for block in response.content if block.type == "text"
                )
                return "succeeded", {"text": final_text}, total_tokens

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            tool_results = []
            for block in tool_use_blocks:
                tool_results.append(
                    await _dispatch_and_log(
                        conn,
                        run_id=run_id,
                        workspace_id=workspace_id,
                        depth=depth,
                        tool_use_id=block.id,
                        tool=tools_by_key.get(block.name),
                        tool_name=block.name,
                        tool_input=block.input,
                    )
                )
            messages.append({"role": "user", "content": tool_results})

        return "budget_exceeded", {"text": None}, total_tokens
    except Exception as exc:  # noqa: BLE001 — surfaced into the run row, not swallowed
        return "failed", {"error": str(exc)}, total_tokens


async def _dispatch_and_log(
    conn: asyncpg.Connection,
    *,
    run_id: UUID,
    workspace_id: UUID,
    depth: int,
    tool_use_id: str,
    tool: dict | None,
    tool_name: str,
    tool_input: dict,
) -> dict:
    started = time.monotonic()
    step_index = await _next_step_index(conn, run_id)

    if tool is None:
        error = f"agent has no tool attached for {tool_name!r}"
        await _log_tool_step(conn, run_id, step_index, None, tool_input, {"error": error}, started, "failed", error)
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": error, "is_error": True}

    try:
        result = await dispatch_tool(
            conn,
            tool=tool,
            config=tool["config"],
            tool_input=tool_input,
            workspace_id=workspace_id,
            run_id=run_id,
            depth=depth + 1,
        )
        await _log_tool_step(conn, run_id, step_index, tool["id"], tool_input, result, started, "succeeded", None)
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": str(result)}
    except ToolDispatchError as exc:
        await _log_tool_step(conn, run_id, step_index, tool["id"], tool_input, {"error": str(exc)}, started, "failed", str(exc))
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": str(exc), "is_error": True}


async def _log_tool_step(conn, run_id, step_index, tool_id, tool_input, output, started, status, error) -> None:
    latency_ms = int((time.monotonic() - started) * 1000)
    await conn.execute(
        """
        insert into run_steps (run_id, step_index, kind, tool_id, input, output, latency_ms, status, error)
        values ($1, $2, 'tool_call', $3, $4, $5, $6, $7, $8)
        """,
        run_id,
        step_index,
        tool_id,
        {"input": tool_input},
        output,
        latency_ms,
        status,
        error,
    )
