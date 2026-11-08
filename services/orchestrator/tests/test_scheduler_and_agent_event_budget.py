"""Regression suite for §17 (docs/01-architecture.md): scheduler.py's
fire_due_triggers and agent_event.py's fire_agent_event_triggers each check
orchestrator_app/limits.py::daily_run_budget_exceeded before inserting a
runs row -- neither has an external caller to turn away (unlike a webhook),
so without this a tight cron or an event-trigger cycle would blow through a
workspace's plan_tier_limits.max_runs_per_day for free, forever.

The two skip slightly differently, matching what "skip" means for each:
scheduler.py deliberately does NOT update last_fired_at when it skips, so
the same tick stays "due" and gets retried next poll rather than silently
dropping a scheduled fire; agent_event.py has no "next due time" to defer to
in the first place, so a skip there is just a dropped firing.
"""

from datetime import datetime, timedelta, timezone

import pytest

from orchestrator_app.agent_event import fire_agent_event_triggers
from orchestrator_app.scheduler import fire_due_triggers


async def _run_count(pool, workspace_id):
    return await pool.fetchval("select count(*) from runs where workspace_id = $1", workspace_id)


@pytest.mark.asyncio
async def test_scheduler_skips_at_budget_without_updating_last_fired_at(pool, factory):
    ws = await factory.workspace()
    flow_id = await factory.flow(ws["id"])
    await factory.fill_runs(ws["id"], flow_id=flow_id, count=50)
    # Due a long time ago, so _is_due is true regardless of exactly when this runs.
    old_fire_time = datetime.now(timezone.utc) - timedelta(days=1)
    trigger_id = await factory.trigger(
        flow_id, kind="schedule", config={"cron": "* * * * *"}, last_fired_at=old_fire_time
    )

    await fire_due_triggers(pool)

    assert await _run_count(pool, ws["id"]) == 50
    last_fired_at = await pool.fetchval("select last_fired_at from triggers where id = $1", trigger_id)
    assert last_fired_at == old_fire_time


@pytest.mark.asyncio
async def test_scheduler_fires_under_budget(pool, factory):
    ws = await factory.workspace()
    flow_id = await factory.flow(ws["id"])
    await factory.fill_runs(ws["id"], flow_id=flow_id, count=49)
    old_fire_time = datetime.now(timezone.utc) - timedelta(days=1)
    trigger_id = await factory.trigger(
        flow_id, kind="schedule", config={"cron": "* * * * *"}, last_fired_at=old_fire_time
    )

    await fire_due_triggers(pool)

    assert await _run_count(pool, ws["id"]) == 50
    last_fired_at = await pool.fetchval("select last_fired_at from triggers where id = $1", trigger_id)
    assert last_fired_at != old_fire_time


@pytest.mark.asyncio
async def test_agent_event_skips_at_budget(pool, factory):
    ws = await factory.workspace()
    flow_id = await factory.flow(ws["id"])
    await factory.fill_runs(ws["id"], flow_id=flow_id, count=50)
    source_agent_id = await factory.agent(ws["id"])
    await factory.trigger(
        flow_id,
        kind="agent_event",
        config={"source_agent_id": str(source_agent_id), "on_status": "succeeded"},
    )

    async with pool.acquire() as conn:
        await fire_agent_event_triggers(
            conn, source_agent_id=source_agent_id, status="succeeded", output={"ok": True}
        )

    assert await _run_count(pool, ws["id"]) == 50


@pytest.mark.asyncio
async def test_agent_event_fires_under_budget(pool, factory):
    ws = await factory.workspace()
    flow_id = await factory.flow(ws["id"])
    await factory.fill_runs(ws["id"], flow_id=flow_id, count=49)
    source_agent_id = await factory.agent(ws["id"])
    await factory.trigger(
        flow_id,
        kind="agent_event",
        config={"source_agent_id": str(source_agent_id), "on_status": "succeeded"},
    )

    async with pool.acquire() as conn:
        await fire_agent_event_triggers(
            conn, source_agent_id=source_agent_id, status="succeeded", output={"ok": True}
        )

    assert await _run_count(pool, ws["id"]) == 50
