"""Orchestrator poll loop.

Postgres-native job queue using `SELECT ... FOR UPDATE SKIP LOCKED` on
run_queue — deliberately not pg-boss (its client is Node-only) and not Redis
(one less paid/managed service, architecture doc §9). Multiple orchestrator
replicas can run this same loop concurrently; SKIP LOCKED means they never
race on the same run.

Runs scheduler.py's schedule-trigger poll as a second task in this same
process/pool (main() below) rather than a separate deployable -- it only
ever *creates* run_queue rows, never consumes them, so there's no reason for
it to be anything other than one more thing this process does.

Flow-run recovery (startup, before either poll loop starts): a flow run
checkpoints its progress to Postgres via `AsyncPostgresSaver` (flow_executor.py's
own docstring has the mechanics), keyed on `run_id`. If this process dies
mid-flow, the run is left at `status = 'running'` with its `run_queue` row
permanently locked (the claim that started it set `locked_at`, and nothing
ever clears a lock except the normal completion path, which never ran) — so
it would otherwise sit stuck forever, invisible to `claim_next_run`'s own
`where locked_at is null`. `recover_interrupted_flows` finds exactly these
rows at startup and re-enters `run_flow` directly (bypassing the queue
entirely, since there's nothing left to claim) with `resuming=True`, which
skips re-stamping `started_at` and lets `execute_flow`'s own checkpoint
check decide whether to resume mid-graph or start over.

Deliberately scoped to **flow** runs only, matching the gap this closes
(flow_executor.py's module docstring used to call out "no checkpointing" as
a flow-specific limitation). A single-agent run interrupted mid-flight is
*not* recovered here — `_execute_agent_loop` is a plain Python loop with no
checkpointing mechanism of its own, so there's nothing to resume into; it's
simply left `running` forever, exactly the pre-existing behavior. Also not
handled: a run still `queued` whose `run_queue` row got locked (claimed)
right before a crash, before its status flipped to `running` — a narrower,
separate race that predates this change and isn't opened by it; flagged
here rather than silently left undiscoverable.
"""

import asyncio
import json
import logging

import asyncpg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from orchestrator_app.config import settings
from orchestrator_app.executor import run_single_agent
from orchestrator_app.flow_executor import run_flow
from orchestrator_app.scheduler import scheduler_loop

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("orchestrator")


async def _init_connection(conn: asyncpg.Connection) -> None:
    # See services/api/app/db.py — same asyncpg jsonb-codec requirement.
    for pg_type in ("jsonb", "json"):
        await conn.set_type_codec(
            pg_type, encoder=json.dumps, decoder=json.loads, schema="pg_catalog", format="text"
        )


async def claim_next_run(pool: asyncpg.Pool) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                select run_id from run_queue
                where locked_at is null
                order by enqueued_at
                for update skip locked
                limit 1
                """
            )
            if row is None:
                return None
            await conn.execute(
                "update run_queue set locked_at = now(), locked_by = $2 where run_id = $1",
                row["run_id"],
                settings.worker_id,
            )
            return row


async def process_run(pool: asyncpg.Pool, run_id, checkpointer) -> None:
    async with pool.acquire() as conn:
        run = await conn.fetchrow("select flow_id from runs where id = $1", run_id)
        is_flow = run["flow_id"] is not None
        if not is_flow:
            # runs.run_is_flow_xor_agent (0001_init_schema.sql) guarantees
            # exactly one of flow_id/agent_id is set.
            await run_single_agent(conn, run_id)

    if is_flow:
        # run_flow manages its own connections (acquiring one per concurrent
        # flow branch, see flow_executor.py) rather than using the one above —
        # a single connection can't serve two queries from parallel branches
        # at once.
        await run_flow(pool, run_id, checkpointer)

    async with pool.acquire() as conn:
        await conn.execute("delete from run_queue where run_id = $1", run_id)


async def run_queue_loop(pool: asyncpg.Pool, checkpointer) -> None:
    while True:
        claimed = await claim_next_run(pool)
        if claimed is None:
            await asyncio.sleep(settings.poll_interval_seconds)
            continue
        log.info("processing run %s", claimed["run_id"])
        try:
            await process_run(pool, claimed["run_id"], checkpointer)
        except Exception:  # noqa: BLE001 — log and keep polling, don't crash the worker
            log.exception("run %s failed", claimed["run_id"])


async def recover_interrupted_flows(pool: asyncpg.Pool, checkpointer) -> None:
    """Startup-only: see this module's own docstring for why `status = 'running'`
    flow runs need a direct re-entry into run_flow rather than going back through
    run_queue (there's nothing left there to claim — see the docstring)."""
    async with pool.acquire() as conn:
        stuck = await conn.fetch(
            "select id from runs where status = 'running' and flow_id is not null and agent_id is null"
        )
    for row in stuck:
        log.info("resuming interrupted flow run %s", row["id"])
        try:
            await run_flow(pool, row["id"], checkpointer, resuming=True)
        except Exception:  # noqa: BLE001 — log and keep starting up, don't block the worker
            log.exception("failed to resume flow run %s", row["id"])
        async with pool.acquire() as conn:
            # Mirrors process_run's own cleanup -- this run never went through
            # claim_next_run this time (recovery bypasses run_queue entirely),
            # but its *original* run_queue row, locked by whichever process
            # crashed, is still sitting there and would otherwise never clear.
            await conn.execute("delete from run_queue where run_id = $1", row["id"])


async def main() -> None:
    pool = await asyncpg.create_pool(settings.database_url, init=_init_connection)
    log.info(
        "orchestrator %s started, polling runs every %.1fs and schedule triggers every %.1fs",
        settings.worker_id,
        settings.poll_interval_seconds,
        settings.schedule_poll_interval_seconds,
    )
    try:
        async with AsyncPostgresSaver.from_conn_string(settings.database_url) as checkpointer:
            # Idempotent -- creates its own checkpoint tables on first run,
            # no-ops on every run after. Owned entirely by this library, no
            # hand-written SQL migration the way every other table gets one.
            await checkpointer.setup()
            await recover_interrupted_flows(pool, checkpointer)
            await asyncio.gather(
                run_queue_loop(pool, checkpointer),
                scheduler_loop(pool, settings.schedule_poll_interval_seconds),
            )
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
