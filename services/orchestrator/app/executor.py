"""Single-agent run executor.

Loads the agent's current version, runs the Anthropic tool-use loop against
the mapped model tier (model_router.py), and persists one `run_steps` row per
model/tool call as it goes — so a run is inspectable mid-flight, not just at
completion (architecture doc §5).

Multi-agent flow execution (sequential handoff, parallel fan-out/fan-in,
agent-as-tool delegation) builds on top of this as the per-node executor,
orchestrated via LangGraph (§9) — not yet implemented in this scaffold.
"""

import time
from uuid import UUID

import asyncpg
from anthropic import AsyncAnthropic

from app.config import settings
from app.model_router import resolve_model_id

client = AsyncAnthropic(api_key=settings.anthropic_api_key)


async def run_single_agent(conn: asyncpg.Connection, run_id: UUID) -> None:
    run = await conn.fetchrow("select * from runs where id = $1", run_id)
    agent_version = await conn.fetchrow(
        """
        select av.* from agent_versions av
        where av.agent_id = $1
        order by av.version desc
        limit 1
        """,
        run["agent_id"],
    )
    limits = await conn.fetchrow(
        """
        select l.* from plan_tier_limits l
        join workspaces w on w.plan_tier = l.plan_tier
        where w.id = $1
        """,
        run["workspace_id"],
    )

    model_id = resolve_model_id(agent_version["model_tier"])
    max_steps = agent_version["max_steps"] or limits["max_steps_per_run"]

    await conn.execute(
        "update runs set status = 'running', started_at = now() where id = $1", run_id
    )

    messages = [{"role": "user", "content": run["input"].get("message", "")}]
    total_tokens = 0
    step_index = 0

    try:
        for step_index in range(max_steps):
            started = time.monotonic()
            response = await client.messages.create(
                model=model_id,
                max_tokens=4096,
                system=agent_version["system_prompt"],
                messages=messages,
            )
            latency_ms = int((time.monotonic() - started) * 1000)
            total_tokens += response.usage.input_tokens + response.usage.output_tokens

            await conn.execute(
                """
                insert into run_steps
                    (run_id, step_index, kind, agent_id, input, output,
                     tokens_input, tokens_output, latency_ms, status)
                values ($1, $2, 'agent_call', $3, $4, $5, $6, $7, $8, 'succeeded')
                """,
                run_id,
                step_index,
                run["agent_id"],
                {"messages": messages},
                {"content": [block.model_dump() for block in response.content]},
                response.usage.input_tokens,
                response.usage.output_tokens,
                latency_ms,
            )

            if response.stop_reason != "tool_use":
                final_text = "".join(
                    block.text for block in response.content if block.type == "text"
                )
                await conn.execute(
                    """
                    update runs
                    set status = 'succeeded', output = $2, total_tokens = $3, finished_at = now()
                    where id = $1
                    """,
                    run_id,
                    {"text": final_text},
                    total_tokens,
                )
                return

            # TODO: dispatch tool_use blocks through the Tool Gateway, append
            # tool_result messages, and continue the loop. Not yet implemented.
            break

        await conn.execute(
            """
            update runs set status = 'budget_exceeded', total_tokens = $2, finished_at = now()
            where id = $1
            """,
            run_id,
            total_tokens,
        )
    except Exception as exc:  # noqa: BLE001 — surfaced into the run row, not swallowed
        await conn.execute(
            """
            update runs set status = 'failed', error = $2, total_tokens = $3, finished_at = now()
            where id = $1
            """,
            run_id,
            str(exc),
            total_tokens,
        )
