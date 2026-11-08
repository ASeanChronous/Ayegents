"""Regression suite for §16 (docs/01-architecture.md): app/limits.py's
enforce_daily_run_budget is shared by every place a runs row gets inserted on
the API side -- create_run, and the three public trigger-fired paths in
routers/webhooks.py -- so a workspace can't dodge its plan's
max_runs_per_day cap just by firing a trigger instead of using the Runs UI.
Each of the three webhook handlers reacts to going over budget differently
(create_run/the generic webhook still raise; telegram/slack ack with 200 and
silently drop, matching the "don't make Telegram/Slack retry forever" posture
they already use for a disabled trigger), so each needs its own assertion of
*which* outcome, not just "no run got created."

'free' tier's max_runs_per_day is 50 (0001_init_schema.sql's plan_tier_limits
seed) -- every test here fills a throwaway workspace to exactly that many
runs in the last 24h first, the same way this session's manual verification
for §16 did.
"""

import hashlib
import hmac
import time

import pytest
from fastapi import HTTPException

from app.routers.runs import RunCreate, create_run


async def _run_count(pool, workspace_id):
    return await pool.fetchval("select count(*) from runs where workspace_id = $1", workspace_id)


@pytest.mark.asyncio
async def test_create_run_blocks_at_budget(pool, factory):
    ws = await factory.workspace()
    agent_id = await factory.agent(ws["id"])
    await factory.fill_runs(ws["id"], flow_id=await factory.flow(ws["id"]), count=50)

    with pytest.raises(HTTPException) as exc_info:
        await create_run(workspace_id=ws["id"], body=RunCreate(agent_id=agent_id), user_id=ws["id"])
    assert exc_info.value.status_code == 429
    assert await _run_count(pool, ws["id"]) == 50


@pytest.mark.asyncio
async def test_generic_webhook_blocks_at_budget(async_client, pool, factory):
    ws = await factory.workspace()
    flow_id = await factory.flow(ws["id"])
    await factory.fill_runs(ws["id"], flow_id=flow_id, count=50)
    trigger_id = await factory.trigger(flow_id, kind="webhook", config={"secret": "correct-horse-battery-staple"})

    resp = await async_client.post(
        f"/webhooks/{trigger_id}", headers={"x-webhook-secret": "correct-horse-battery-staple"}
    )
    assert resp.status_code == 429
    assert await _run_count(pool, ws["id"]) == 50


@pytest.mark.asyncio
async def test_generic_webhook_under_budget_still_fires(async_client, pool, factory):
    ws = await factory.workspace()
    flow_id = await factory.flow(ws["id"])
    await factory.fill_runs(ws["id"], flow_id=flow_id, count=49)
    trigger_id = await factory.trigger(flow_id, kind="webhook", config={"secret": "correct-horse-battery-staple"})

    resp = await async_client.post(
        f"/webhooks/{trigger_id}", headers={"x-webhook-secret": "correct-horse-battery-staple"}
    )
    assert resp.status_code == 202
    assert "run_id" in resp.json()
    assert await _run_count(pool, ws["id"]) == 50


@pytest.mark.asyncio
async def test_telegram_webhook_silently_drops_at_budget(async_client, pool, factory):
    ws = await factory.workspace()
    flow_id = await factory.flow(ws["id"])
    await factory.fill_runs(ws["id"], flow_id=flow_id, count=50)
    trigger_id = await factory.trigger(flow_id, kind="telegram", config={"webhook_secret": "tg-secret-abc"})

    resp = await async_client.post(
        f"/webhooks/telegram/{trigger_id}",
        headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret-abc"},
        json={"message": {"text": "hi", "chat": {"id": 1}}},
    )
    # Telegram retries anything that doesn't get a prompt 200 -- over budget still
    # acks, it just doesn't create a run, same as a disabled trigger would.
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert await _run_count(pool, ws["id"]) == 50


@pytest.mark.asyncio
async def test_slack_webhook_silently_drops_at_budget(async_client, pool, factory):
    ws = await factory.workspace()
    flow_id = await factory.flow(ws["id"])
    await factory.fill_runs(ws["id"], flow_id=flow_id, count=50)
    signing_secret = "slack-signing-secret"
    trigger_id = await factory.trigger(flow_id, kind="slack", config={"signing_secret": signing_secret})

    body = b'{"type": "event_callback", "event": {"type": "message", "text": "hi"}}'
    timestamp = str(int(time.time()))
    basestring = b"v0:" + timestamp.encode() + b":" + body
    signature = "v0=" + hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()

    resp = await async_client.post(
        f"/webhooks/slack/{trigger_id}",
        headers={"X-Slack-Signature": signature, "X-Slack-Request-Timestamp": timestamp},
        content=body,
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert await _run_count(pool, ws["id"]) == 50
