"""Schedule-trigger polling loop, run alongside main.py's run-queue poll loop (same
process, same pool -- one more asyncio task, not a second service to deploy and monitor).

A schedule trigger is "due" when croniter's next fire time after its last firing (or its
`created_at`, if it has never fired -- see 0005_trigger_last_fired.sql) has passed. Due
triggers get the same treatment a webhook POST or a manual Runs-page start gets: a `runs`
row plus a `run_queue` row, picked up by the existing claim_next_run loop in main.py --
this module only ever decides *whether* to start a run, never how one executes.

`for update skip locked` on the triggers row (not just on run_queue, which only governs
runs already created) is what keeps two orchestrator replicas from both deciding the same
due trigger needs firing and creating two runs for one scheduled tick.
"""

import asyncio
import logging
from datetime import datetime, timezone

import asyncpg
from croniter import croniter

log = logging.getLogger("orchestrator.scheduler")


def _is_due(cron: str, base: datetime, now: datetime) -> bool:
    return croniter(cron, base).get_next(datetime) <= now


async def fire_due_triggers(pool: asyncpg.Pool) -> None:
    now = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        async with conn.transaction():
            triggers = await conn.fetch(
                """
                select t.id, t.config, t.last_fired_at, t.created_at, f.id as flow_id, f.workspace_id
                from triggers t
                join flows f on f.id = t.flow_id
                where t.kind = 'schedule' and t.enabled
                for update of t skip locked
                """
            )
            for trigger in triggers:
                cron = trigger["config"].get("cron")
                if not cron:
                    continue
                base = trigger["last_fired_at"] or trigger["created_at"]
                if not _is_due(cron, base, now):
                    continue

                run = await conn.fetchrow(
                    """
                    insert into runs (workspace_id, flow_id, trigger_id, input, status)
                    values ($1, $2, $3, '{}'::jsonb, 'queued')
                    returning id
                    """,
                    trigger["workspace_id"],
                    trigger["flow_id"],
                    trigger["id"],
                )
                await conn.execute("insert into run_queue (run_id) values ($1)", run["id"])
                await conn.execute("update triggers set last_fired_at = $2 where id = $1", trigger["id"], now)
                log.info("schedule trigger %s fired run %s", trigger["id"], run["id"])


async def scheduler_loop(pool: asyncpg.Pool, poll_interval_seconds: float) -> None:
    while True:
        try:
            await fire_due_triggers(pool)
        except Exception:  # noqa: BLE001 -- log and keep polling, same contract main.py's own loop follows
            log.exception("schedule trigger poll failed")
        await asyncio.sleep(poll_interval_seconds)
