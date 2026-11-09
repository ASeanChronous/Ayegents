"""Regression suite for routers/payments.py (architecture doc §6/§15's docstring,
docs/05-frontend-marketplace.md §10/§11) -- the one router in this API that moves
real money, and (like marketplace.py before §26) had no automated coverage at all.

Stripe itself is mocked here the same way e2b_code_interpreter's AsyncSandbox is
mocked in services/orchestrator/tests/test_code_exec_network_default.py: a
genuinely external, uncontrollable boundary this project has no live test account
for, not the core logic under test. `stripe.checkout.Session.create`,
`stripe.Account.create`/`.retrieve`, `stripe.AccountLink.create`, and
`stripe.Webhook.construct_event` are monkeypatched; everything downstream of
them -- the gating logic, the DB writes, the fee math -- runs for real against the
Docker Postgres, no mocking of this project's own code.
"""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.config import settings
from app.routers import payments


@pytest.fixture(autouse=True)
def _stripe_configured(monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_fake")
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_test_fake")


@pytest.mark.asyncio
async def test_create_checkout_session_rejects_unpublished_listing(factory):
    publisher = await factory.workspace()
    buyer = await factory.workspace(members={"member": "member"})
    agent_id = await factory.agent(publisher["id"])
    listing_id = await factory.listing(publisher["id"], source_agent_id=agent_id, price_cents=500, status="draft")

    with pytest.raises(HTTPException) as exc_info:
        await payments.create_checkout_session(workspace_id=buyer["id"], listing_id=listing_id, user_id=buyer["member"])
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_create_checkout_session_rejects_free_listing(factory):
    publisher = await factory.workspace()
    buyer = await factory.workspace(members={"member": "member"})
    agent_id = await factory.agent(publisher["id"])
    listing_id = await factory.listing(publisher["id"], source_agent_id=agent_id, price_cents=0, status="published")

    with pytest.raises(HTTPException) as exc_info:
        await payments.create_checkout_session(workspace_id=buyer["id"], listing_id=listing_id, user_id=buyer["member"])
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_create_checkout_session_rejects_when_payouts_not_enabled(factory):
    publisher = await factory.workspace()
    buyer = await factory.workspace(members={"member": "member"})
    agent_id = await factory.agent(publisher["id"])
    listing_id = await factory.listing(publisher["id"], source_agent_id=agent_id, price_cents=500, status="published")
    # Payouts deliberately left disabled -- defense in depth even though
    # create_listing/update_listing already refuse to price a listing without it.

    with pytest.raises(HTTPException) as exc_info:
        await payments.create_checkout_session(workspace_id=buyer["id"], listing_id=listing_id, user_id=buyer["member"])
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_create_checkout_session_rejects_already_installed(factory):
    publisher = await factory.workspace()
    buyer = await factory.workspace(members={"member": "member"})
    agent_id = await factory.agent(publisher["id"])
    listing_id = await factory.listing(publisher["id"], source_agent_id=agent_id, price_cents=500, status="published")
    await factory.set_stripe_connect(publisher["id"], account_id="acct_test_123", payouts_enabled=True)
    version_id = await factory.listing_version(
        listing_id, {"type": "agent", "name": "x", "system_prompt": "hi", "model_tier": "sonnet", "tools": []}
    )
    await factory.install(listing_id, version_id, buyer["id"], buyer["member"])

    with pytest.raises(HTTPException) as exc_info:
        await payments.create_checkout_session(workspace_id=buyer["id"], listing_id=listing_id, user_id=buyer["member"])
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_create_checkout_session_creates_pending_purchase_with_correct_fee_and_destination(factory, monkeypatch, pool):
    publisher = await factory.workspace()
    buyer = await factory.workspace(members={"member": "member"})
    agent_id = await factory.agent(publisher["id"])
    listing_id = await factory.listing(publisher["id"], source_agent_id=agent_id, price_cents=1000, status="published")
    await factory.set_stripe_connect(publisher["id"], account_id="acct_test_destination", payouts_enabled=True)

    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="cs_test_abc", url="https://checkout.stripe.com/abc")

    monkeypatch.setattr(payments.stripe.checkout.Session, "create", fake_create)

    result = await payments.create_checkout_session(workspace_id=buyer["id"], listing_id=listing_id, user_id=buyer["member"])

    assert result.checkout_url == "https://checkout.stripe.com/abc"
    # settings.platform_fee_bps defaults to 1500 (15%) -- 1000 * 1500 / 10_000 = 150.
    assert captured["payment_intent_data"]["application_fee_amount"] == 150
    assert captured["payment_intent_data"]["transfer_data"]["destination"] == "acct_test_destination"

    purchase = await pool.fetchrow(
        "select status, amount_cents, stripe_checkout_session_id from listing_purchases where listing_id = $1",
        listing_id,
    )
    assert purchase["status"] == "pending"
    assert purchase["amount_cents"] == 1000
    assert purchase["stripe_checkout_session_id"] == "cs_test_abc"


@pytest.mark.asyncio
async def test_get_purchase_status_reflects_paid_state(factory):
    publisher = await factory.workspace()
    buyer = await factory.workspace(members={"member": "member"})
    agent_id = await factory.agent(publisher["id"])
    listing_id = await factory.listing(publisher["id"], source_agent_id=agent_id, price_cents=500, status="published")

    unpaid = await payments.get_purchase_status(workspace_id=buyer["id"], listing_id=listing_id, user_id=buyer["member"])
    assert unpaid.paid is False

    await factory.purchase(listing_id, buyer["id"], buyer["member"], status="paid")

    paid = await payments.get_purchase_status(workspace_id=buyer["id"], listing_id=listing_id, user_id=buyer["member"])
    assert paid.paid is True


@pytest.mark.asyncio
async def test_create_connect_onboarding_creates_account_once_and_reuses_it(factory, monkeypatch, pool):
    ws = await factory.workspace()
    create_calls = []

    def fake_account_create(**kwargs):
        create_calls.append(kwargs)
        return SimpleNamespace(id="acct_test_new")

    def fake_link_create(**kwargs):
        return SimpleNamespace(url=f"https://connect.stripe.com/setup/{kwargs['account']}")

    monkeypatch.setattr(payments.stripe.Account, "create", fake_account_create)
    monkeypatch.setattr(payments.stripe.AccountLink, "create", fake_link_create)

    first = await payments.create_connect_onboarding(workspace_id=ws["id"], user_id=ws["id"])
    assert first.onboarding_url == "https://connect.stripe.com/setup/acct_test_new"
    assert len(create_calls) == 1
    assert await pool.fetchval("select stripe_connect_account_id from workspaces where id = $1", ws["id"]) == "acct_test_new"

    # A second call for a workspace that already has an account must not create
    # a second one -- just a fresh Account Link for the same underlying account.
    second = await payments.create_connect_onboarding(workspace_id=ws["id"], user_id=ws["id"])
    assert second.onboarding_url == "https://connect.stripe.com/setup/acct_test_new"
    assert len(create_calls) == 1


@pytest.mark.asyncio
async def test_get_payouts_status_not_connected(factory):
    ws = await factory.workspace()
    status = await payments.get_payouts_status(workspace_id=ws["id"], user_id=ws["id"])
    assert (status.connected, status.payouts_enabled) == (False, False)


@pytest.mark.asyncio
async def test_get_payouts_status_self_heals_from_live_lookup_then_trusts_cache(factory, monkeypatch, pool):
    ws = await factory.workspace()
    await factory.set_stripe_connect(ws["id"], account_id="acct_test_heal", payouts_enabled=False)

    retrieve_calls = []

    def fake_retrieve(account_id):
        retrieve_calls.append(account_id)
        return SimpleNamespace(payouts_enabled=True)

    monkeypatch.setattr(payments.stripe.Account, "retrieve", fake_retrieve)

    first = await payments.get_payouts_status(workspace_id=ws["id"], user_id=ws["id"])
    assert (first.connected, first.payouts_enabled) == (True, True)
    assert retrieve_calls == ["acct_test_heal"]
    assert await pool.fetchval("select stripe_connect_payouts_enabled from workspaces where id = $1", ws["id"]) is True

    # Now that the cache says enabled, a second call must trust it rather than
    # hitting Stripe again -- break fake_retrieve to prove it's never called.
    def fail_if_called(account_id):
        raise AssertionError("should not re-check Stripe once the cache says enabled")

    monkeypatch.setattr(payments.stripe.Account, "retrieve", fail_if_called)
    second = await payments.get_payouts_status(workspace_id=ws["id"], user_id=ws["id"])
    assert (second.connected, second.payouts_enabled) == (True, True)


@pytest.mark.asyncio
async def test_stripe_webhook_rejects_invalid_signature(async_client, monkeypatch):
    def fake_construct_event(payload, sig_header, secret):
        raise ValueError("signature mismatch")

    monkeypatch.setattr(payments.stripe.Webhook, "construct_event", fake_construct_event)

    response = await async_client.post(
        "/webhooks/stripe", content=b'{"type": "checkout.session.completed"}', headers={"Stripe-Signature": "bad-sig"}
    )
    assert response.status_code == 400


def _fake_construct_event_factory():
    def fake_construct_event(payload, sig_header, secret):
        if sig_header != "valid-sig":
            raise ValueError("signature mismatch")
        return json.loads(payload)

    return fake_construct_event


@pytest.mark.asyncio
async def test_stripe_webhook_checkout_completed_marks_purchase_paid_idempotently(factory, async_client, monkeypatch, pool):
    publisher = await factory.workspace()
    buyer = await factory.workspace(members={"member": "member"})
    agent_id = await factory.agent(publisher["id"])
    listing_id = await factory.listing(publisher["id"], source_agent_id=agent_id, price_cents=500, status="published")
    purchase_id = await factory.purchase(listing_id, buyer["id"], buyer["member"], status="pending")
    session_id = await pool.fetchval("select stripe_checkout_session_id from listing_purchases where id = $1", purchase_id)

    monkeypatch.setattr(payments.stripe.Webhook, "construct_event", _fake_construct_event_factory())

    event_payload = json.dumps(
        {
            "type": "checkout.session.completed",
            "data": {"object": {"id": session_id, "payment_intent": "pi_test_123"}},
        }
    ).encode()

    response = await async_client.post(
        "/webhooks/stripe", content=event_payload, headers={"Stripe-Signature": "valid-sig"}
    )
    assert response.status_code == 200

    row = await pool.fetchrow(
        "select status, stripe_payment_intent_id, paid_at from listing_purchases where id = $1", purchase_id
    )
    assert row["status"] == "paid"
    assert row["stripe_payment_intent_id"] == "pi_test_123"
    first_paid_at = row["paid_at"]
    assert first_paid_at is not None

    # A retried delivery (Stripe's own at-least-once guarantee) must be a no-op,
    # not re-stamp paid_at -- the `where status = 'pending'` guard is what this proves.
    response = await async_client.post(
        "/webhooks/stripe", content=event_payload, headers={"Stripe-Signature": "valid-sig"}
    )
    assert response.status_code == 200
    row = await pool.fetchrow("select paid_at from listing_purchases where id = $1", purchase_id)
    assert row["paid_at"] == first_paid_at


@pytest.mark.asyncio
async def test_stripe_webhook_account_updated_flips_payouts_enabled(factory, async_client, monkeypatch, pool):
    ws = await factory.workspace()
    await factory.set_stripe_connect(ws["id"], account_id="acct_test_webhook", payouts_enabled=False)

    monkeypatch.setattr(payments.stripe.Webhook, "construct_event", _fake_construct_event_factory())

    event_payload = json.dumps(
        {"type": "account.updated", "data": {"object": {"id": "acct_test_webhook", "payouts_enabled": True}}}
    ).encode()

    response = await async_client.post(
        "/webhooks/stripe", content=event_payload, headers={"Stripe-Signature": "valid-sig"}
    )
    assert response.status_code == 200
    assert await pool.fetchval(
        "select stripe_connect_payouts_enabled from workspaces where id = $1", ws["id"]
    ) is True
