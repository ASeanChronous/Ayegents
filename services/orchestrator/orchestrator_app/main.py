"""Orchestrator poll loop.

Postgres-native job queue using `SELECT ... FOR UPDATE SKIP LOCKED` on
run_queue — deliberately not pg-boss (its client is Node-only) and not Redis
(one less paid/managed service, architecture doc §9). Multiple orchestrator
replicas can run this same loop concurrently; SKIP LOCKED means they never
race on the same run.
"""

import asyncio
import json
import logging

import asyncpg

from orchestrator_app.config import settings
from orchestrator_app.executor import run_single_agent
from orchestrator_app.flow_executor import run_flow

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


async def process_run(pool: asyncpg.Pool, run_id) -> None:
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
        await run_flow(pool, run_id)

    async with pool.acquire() as conn:
        await conn.execute("delete from run_queue where run_id = $1", run_id)


async def main() -> None:
    pool = await asyncpg.create_pool(settings.database_url, init=_init_connection)
    log.info("orchestrator %s started, polling every %.1fs", settings.worker_id, settings.poll_interval_seconds)
    try:
        while True:
            claimed = await claim_next_run(pool)
            if claimed is None:
                await asyncio.sleep(settings.poll_interval_seconds)
                continue
            log.info("processing run %s", claimed["run_id"])
            try:
                await process_run(pool, claimed["run_id"])
            except Exception:  # noqa: BLE001 — log and keep polling, don't crash the worker
                log.exception("run %s failed", claimed["run_id"])
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
