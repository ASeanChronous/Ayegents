"""agent_event triggers -- the fourth trigger_kind the schema enum allows (0001_init_schema.sql),
closing the gap docs/04-frontend-builder.md's webhook/schedule increment explicitly left open:
one flow starting automatically when another agent's or flow's *top-level* run reaches a given
terminal status (services/api/app/routers/triggers.py builds/validates the trigger row itself;
this module is the firing side, same split webhooks.py/scheduler.py already have for the other
two kinds).

"Top-level" matters: this is only ever called from run_single_agent (a standalone agent run)
and run_flow (a flow run), never from run_agent_as_tool -- a sub-agent delegation doesn't touch
`runs.status` at all (it reuses the parent run's row, executor.py's own docstring), so it was
already naturally excluded without needing a special case here.

Known, deliberate scope cut: only an immediate self-reference (a flow's own agent_event trigger
naming that same flow as its source) is rejected, at trigger-creation time (triggers.py). A
longer cycle -- flow A's trigger fires B, B's fires A -- is not detected. Same category of gap
flow_executor.py's own docstring already flags for condition language and checkpointing: real,
left for later, not an oversight. A cycle would show up immediately as runs piling up in the
Runs list, not silently -- the operator's own "disable this trigger" lever already handles it.
"""

from datetime import datetime, timezone
from uuid import UUID

import asyncpg


async def fire_agent_event_triggers(
    conn: asyncpg.Connection,
    *,
    source_agent_id: UUID | None = None,
    source_flow_id: UUID | None = None,
    status: str,
    output: dict | None,
) -> None:
    rows = await conn.fetch(
        """
        select t.id, t.flow_id, f.workspace_id
        from triggers t
        join flows f on f.id = t.flow_id
        where t.kind = 'agent_event' and t.enabled
          and t.config->>'on_status' = $3
          and (
            ($1::uuid is not null and t.config->>'source_agent_id' = $1::text)
            or ($2::uuid is not null and t.config->>'source_flow_id' = $2::text)
          )
        """,
        source_agent_id,
        source_flow_id,
        status,
    )
    if not rows:
        return

    # The new run's input is the source run's output taken wholesale -- same
    # "predecessor output becomes the next thing's input" convention a flow's
    # own tool-node input_map already uses (flow_executor.py's
    # _resolve_input_dict), not a new one invented here. A non-dict output
    # (there isn't one today, but nothing stops a future run kind producing
    # one) falls back to {}, same as a webhook's non-object body.
    run_input = output if isinstance(output, dict) else {}
    now = datetime.now(timezone.utc)

    for trigger in rows:
        run = await conn.fetchrow(
            """
            insert into runs (workspace_id, flow_id, trigger_id, input, status)
            values ($1, $2, $3, $4, 'queued')
            returning id
            """,
            trigger["workspace_id"],
            trigger["flow_id"],
            trigger["id"],
            run_input,
        )
        await conn.execute("insert into run_queue (run_id) values ($1)", run["id"])
        await conn.execute("update triggers set last_fired_at = $2 where id = $1", trigger["id"], now)
