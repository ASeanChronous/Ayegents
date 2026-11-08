"""Regression suite for §19 (docs/01-architecture.md): fire_webhook and
fire_telegram_webhook must compare the caller-supplied secret against the
trigger's stored one with hmac.compare_digest, not Python's `!=` -- a plain
`!=` on strings short-circuits at the first mismatched character, leaking a
timing side-channel on the *entire* access control for these two
deliberately public, unauthenticated endpoints.

A black-box functional test (wrong secret -> 404, missing -> 404, correct ->
success) can't actually catch a regression back to `!=`, since both
comparisons produce the identical pass/fail outcome -- only their timing
differs, and asserting on wall-clock timing in a test suite is exactly the
kind of flaky, environment-sensitive test to avoid. So alongside the
functional checks (still worth having, for the handlers' basic correctness),
this spies on hmac.compare_digest itself to confirm each handler's secret
check actually goes through it rather than a plain `==`/`!=`.
"""

import hmac

import pytest

from app.routers import webhooks


@pytest.mark.asyncio
async def test_generic_webhook_outcomes(async_client, factory):
    ws = await factory.workspace()
    flow_id = await factory.flow(ws["id"])
    trigger_id = await factory.trigger(flow_id, kind="webhook", config={"secret": "correct-horse-battery-staple"})

    wrong = await async_client.post(f"/webhooks/{trigger_id}", headers={"x-webhook-secret": "wrong"})
    assert wrong.status_code == 404

    missing = await async_client.post(f"/webhooks/{trigger_id}")
    assert missing.status_code == 404

    correct = await async_client.post(
        f"/webhooks/{trigger_id}", headers={"x-webhook-secret": "correct-horse-battery-staple"}
    )
    assert correct.status_code == 202
    assert "run_id" in correct.json()


@pytest.mark.asyncio
async def test_telegram_webhook_outcomes(async_client, factory):
    ws = await factory.workspace()
    flow_id = await factory.flow(ws["id"])
    trigger_id = await factory.trigger(flow_id, kind="telegram", config={"webhook_secret": "tg-secret-abc"})
    payload = {"message": {"text": "hi", "chat": {"id": 1}}}

    wrong = await async_client.post(
        f"/webhooks/telegram/{trigger_id}", headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"}, json=payload
    )
    assert wrong.status_code == 404

    missing = await async_client.post(f"/webhooks/telegram/{trigger_id}", json=payload)
    assert missing.status_code == 404

    correct = await async_client.post(
        f"/webhooks/telegram/{trigger_id}",
        headers={"X-Telegram-Bot-Api-Secret-Token": "tg-secret-abc"},
        json=payload,
    )
    assert correct.status_code == 200
    assert correct.json() == {"ok": True}


@pytest.mark.asyncio
async def test_generic_webhook_uses_compare_digest(async_client, factory, monkeypatch):
    ws = await factory.workspace()
    flow_id = await factory.flow(ws["id"])
    trigger_id = await factory.trigger(flow_id, kind="webhook", config={"secret": "correct-horse-battery-staple"})

    calls = []
    real_compare_digest = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real_compare_digest(a, b)

    monkeypatch.setattr(webhooks.hmac, "compare_digest", spy)

    await async_client.post(f"/webhooks/{trigger_id}", headers={"x-webhook-secret": "wrong"})
    assert calls, "fire_webhook's secret check never called hmac.compare_digest"


@pytest.mark.asyncio
async def test_telegram_webhook_uses_compare_digest(async_client, factory, monkeypatch):
    ws = await factory.workspace()
    flow_id = await factory.flow(ws["id"])
    trigger_id = await factory.trigger(flow_id, kind="telegram", config={"webhook_secret": "tg-secret-abc"})

    calls = []
    real_compare_digest = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real_compare_digest(a, b)

    monkeypatch.setattr(webhooks.hmac, "compare_digest", spy)

    await async_client.post(
        f"/webhooks/telegram/{trigger_id}",
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        json={"message": {"text": "hi", "chat": {"id": 1}}},
    )
    assert calls, "fire_telegram_webhook's secret check never called hmac.compare_digest"
