"""Marketplace monetization: paid listings via Stripe Checkout, and creator payouts via
Stripe Connect (architecture doc §6's "Monetization ... deferred" note, and
docs/05-frontend-marketplace.md §10/§11).

Two passes, same file: §10 (paid listings, buyer-side only — a Checkout Session the buyer
completes, confirmed via webhook, gating install) landed first with funds settling entirely
into the platform's own Stripe account. This pass closes the half that was deliberately left
open then: a listing's *publisher* actually receiving a revenue share.

Design: Stripe Connect **Express** accounts, one per workspace (the publishing entity, same
unit every other marketplace interaction is already scoped to) — chosen over Standard/Custom
because Stripe hosts almost the entire onboarding UI (KYC, bank details, tax info), which is
the whole point: this platform owns none of that compliance surface itself, just an
`Account` object id and a cached `payouts_enabled` flag. **Destination charges**, not a
hand-rolled payout job: a priced listing's Checkout Session carries
`payment_intent_data.transfer_data.destination` (the connected account) and
`application_fee_amount` (the platform's cut, `settings.platform_fee_bps` of the price) —
Stripe splits the charge at capture time and pays the connected account out on its own
payout schedule. There is no code in this project that ever moves money between accounts;
Stripe does that part entirely.

A listing can't carry a nonzero price unless its workspace has finished Connect onboarding
(`create_listing`/`update_listing`, marketplace.py) — avoids ever publishing a priced
listing with nowhere for its share of a sale to go. `workspaces.stripe_connect_payouts_enabled`
is the fast local check that gates this; it's kept current by the `account.updated` webhook
below, and self-healed by a live Stripe lookup on every `GET .../payouts/status` call (covers
the same "redirect-back races the webhook" gap the purchase-confirmation flow already has to
handle, this time for "did Stripe finish verifying this account" instead of "did Stripe
finish charging this card").

Three routers: `checkout_router` (buyer-side, §10, unchanged) and `payouts_router`
(publisher-side, this pass) are both ordinary authenticated workspace API;
`stripe_webhook_router` is public, outside `/workspaces/...` and `get_current_user_id` —
Stripe itself is the caller, verified by its own request signature
(`stripe.Webhook.construct_event`) rather than a Supabase session, the same reason the
trigger webhook verifies an `X-Webhook-Secret` instead.
"""

from uuid import UUID

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app.config import settings
from app.db import get_pool
from app.deps import get_current_user_id

checkout_router = APIRouter(prefix="/workspaces/{workspace_id}/marketplace/listings", tags=["marketplace"])
payouts_router = APIRouter(prefix="/workspaces/{workspace_id}/payouts", tags=["marketplace"])
stripe_webhook_router = APIRouter(prefix="/webhooks", tags=["marketplace"])


def _require_stripe_configured() -> None:
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=501, detail="Stripe is not configured on this deployment")
    stripe.api_key = settings.stripe_secret_key


class CheckoutOut(BaseModel):
    checkout_url: str


@checkout_router.post("/{listing_id}/checkout", response_model=CheckoutOut, status_code=201)
async def create_checkout_session(
    workspace_id: UUID, listing_id: UUID, user_id: UUID = Depends(get_current_user_id)
):
    _require_stripe_configured()
    pool = await get_pool()
    async with pool.acquire() as conn:
        listing = await conn.fetchrow(
            """
            select l.id, l.name, l.price_cents, l.currency,
                   w.stripe_connect_account_id, w.stripe_connect_payouts_enabled
            from listings l join workspaces w on w.id = l.workspace_id
            where l.id = $1 and l.status = 'published'
            """,
            listing_id,
        )
        if listing is None:
            raise HTTPException(status_code=404, detail="listing not found or not published")
        if listing["price_cents"] <= 0:
            raise HTTPException(
                status_code=400, detail="this listing is free — install it directly, no checkout needed"
            )
        if not listing["stripe_connect_payouts_enabled"] or not listing["stripe_connect_account_id"]:
            # Defense in depth, not the primary gate -- create_listing/update_listing
            # (marketplace.py) already refuse to price a listing without a connected,
            # payouts-enabled workspace, so this should be unreachable in practice.
            # Still checked here because Connect status can change *after* publish (an
            # account can be disabled by Stripe for risk/compliance reasons at any time),
            # and a sale with nowhere for the publisher's share to go is worse than a
            # buyer seeing a clear error.
            raise HTTPException(
                status_code=409,
                detail="this listing's publisher hasn't completed payout setup — checkout isn't available right now",
            )

        already_installed = await conn.fetchval(
            "select exists(select 1 from installs where listing_id = $1 and installing_workspace_id = $2)",
            listing_id,
            workspace_id,
        )
        if already_installed:
            raise HTTPException(status_code=409, detail="this workspace has already installed this listing")

        application_fee_cents = round(listing["price_cents"] * settings.platform_fee_bps / 10_000)

        # price_data inline rather than a pre-created Stripe Price/Product per
        # listing -- a listing's price can change (ListingUpdate, below), and
        # this way every new Checkout Session just reads whatever's in
        # listings.price_cents right now, with nothing to keep in sync on the
        # Stripe side when it does.
        try:
            session = stripe.checkout.Session.create(
                mode="payment",
                line_items=[
                    {
                        "price_data": {
                            "currency": listing["currency"],
                            "unit_amount": listing["price_cents"],
                            "product_data": {"name": listing["name"]},
                        },
                        "quantity": 1,
                    }
                ],
                # Destination charge: Stripe moves application_fee_cents to the
                # platform's own balance and the rest straight to the connected
                # account, at charge time -- no transfer this project has to
                # initiate itself.
                payment_intent_data={
                    "application_fee_amount": application_fee_cents,
                    "transfer_data": {"destination": listing["stripe_connect_account_id"]},
                },
                success_url=f"{settings.frontend_url}/marketplace/{listing_id}?purchase=success&session_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{settings.frontend_url}/marketplace/{listing_id}?purchase=cancelled",
                metadata={"listing_id": str(listing_id), "workspace_id": str(workspace_id), "user_id": str(user_id)},
            )
        except stripe.error.StripeError as exc:
            raise HTTPException(status_code=502, detail=f"Stripe checkout session creation failed: {exc}") from None

        await conn.execute(
            """
            insert into listing_purchases
                (listing_id, purchasing_workspace_id, amount_cents, currency, stripe_checkout_session_id,
                 purchased_by, application_fee_cents, stripe_connect_account_id)
            values ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            listing_id,
            workspace_id,
            listing["price_cents"],
            listing["currency"],
            session.id,
            user_id,
            application_fee_cents,
            listing["stripe_connect_account_id"],
        )
    return CheckoutOut(checkout_url=session.url)


class PurchaseStatusOut(BaseModel):
    paid: bool


@checkout_router.get("/{listing_id}/purchase", response_model=PurchaseStatusOut)
async def get_purchase_status(workspace_id: UUID, listing_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    """Whether this workspace has a completed purchase of this listing — the
    frontend polls this right after a Checkout redirect-back (the webhook that
    actually flips a purchase to 'paid' races the browser's own redirect, so
    the UI can't just trust `?purchase=success` on its own) and also checks it
    on a normal page load, so a workspace that paid earlier but never finished
    installing isn't asked to pay a second time.
    """
    pool = await get_pool()
    paid = await pool.fetchval(
        "select exists(select 1 from listing_purchases where listing_id = $1 and purchasing_workspace_id = $2 and status = 'paid')",
        listing_id,
        workspace_id,
    )
    return PurchaseStatusOut(paid=paid)


class OnboardingOut(BaseModel):
    onboarding_url: str


@payouts_router.post("/connect-onboarding", response_model=OnboardingOut, status_code=201)
async def create_connect_onboarding(workspace_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    """Find-or-create this workspace's Express account, then an Account Link (Stripe's
    own hosted onboarding flow — KYC, bank details, tax info, none of it touched by this
    project) pointed back at the marketplace page either way. Safe to call again for a
    workspace that already has an account but never finished onboarding (Stripe's Account
    Links are single-use and short-lived, so "onboarding again" just means "a fresh link
    for the same underlying account," not a second account).
    """
    _require_stripe_configured()
    pool = await get_pool()
    async with pool.acquire() as conn:
        workspace = await conn.fetchrow(
            "select stripe_connect_account_id from workspaces where id = $1", workspace_id
        )
        if workspace is None:
            raise HTTPException(status_code=404, detail="workspace not found")

        account_id = workspace["stripe_connect_account_id"]
        if account_id is None:
            try:
                account = stripe.Account.create(type="express")
            except stripe.error.StripeError as exc:
                raise HTTPException(status_code=502, detail=f"Stripe account creation failed: {exc}") from None
            account_id = account.id
            await conn.execute(
                "update workspaces set stripe_connect_account_id = $2 where id = $1", workspace_id, account_id
            )

        try:
            link = stripe.AccountLink.create(
                account=account_id,
                type="account_onboarding",
                refresh_url=f"{settings.frontend_url}/marketplace?payouts=refresh",
                return_url=f"{settings.frontend_url}/marketplace?payouts=return",
            )
        except stripe.error.StripeError as exc:
            raise HTTPException(status_code=502, detail=f"Stripe onboarding link creation failed: {exc}") from None
    return OnboardingOut(onboarding_url=link.url)


class PayoutsStatusOut(BaseModel):
    connected: bool
    payouts_enabled: bool


@payouts_router.get("/status", response_model=PayoutsStatusOut)
async def get_payouts_status(workspace_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    """Self-healing, not just a cache read: right after an onboarding redirect-back,
    `workspaces.stripe_connect_payouts_enabled` may not have caught up yet (the
    `account.updated` webhook below races the browser's own redirect, same shape the
    purchase-confirmation flow already has to handle) — so when a connected account isn't
    yet marked enabled, this does one live `stripe.Account.retrieve` and updates the cache
    before answering, rather than making the frontend poll and hope. Once enabled, trusts
    the cache (no Stripe round trip needed on every status check from then on).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        workspace = await conn.fetchrow(
            "select stripe_connect_account_id, stripe_connect_payouts_enabled from workspaces where id = $1",
            workspace_id,
        )
        if workspace is None:
            raise HTTPException(status_code=404, detail="workspace not found")

        account_id = workspace["stripe_connect_account_id"]
        if account_id is None:
            return PayoutsStatusOut(connected=False, payouts_enabled=False)

        payouts_enabled = workspace["stripe_connect_payouts_enabled"]
        if not payouts_enabled and settings.stripe_secret_key:
            stripe.api_key = settings.stripe_secret_key
            try:
                account = stripe.Account.retrieve(account_id)
            except stripe.error.StripeError:
                # A transient Stripe-side issue shouldn't break the status page --
                # fall back to the (possibly stale) cached value instead of 502ing.
                pass
            else:
                payouts_enabled = bool(account.payouts_enabled)
                if payouts_enabled:
                    await conn.execute(
                        "update workspaces set stripe_connect_payouts_enabled = true where id = $1", workspace_id
                    )
    return PayoutsStatusOut(connected=True, payouts_enabled=payouts_enabled)


class StripeWebhookOut(BaseModel):
    received: bool


@stripe_webhook_router.post("/stripe", response_model=StripeWebhookOut)
async def stripe_webhook(request: Request, stripe_signature: str | None = Header(default=None, alias="Stripe-Signature")):
    if not settings.stripe_secret_key or not settings.stripe_webhook_secret:
        raise HTTPException(status_code=501, detail="Stripe is not configured on this deployment")

    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload, stripe_signature, settings.stripe_webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        # Same posture as the trigger webhook's own secret check (webhooks.py):
        # reject outright rather than process a payload that didn't come from
        # Stripe, instead of trying to partially trust it.
        raise HTTPException(status_code=400, detail=f"invalid Stripe webhook payload: {exc}") from None

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        pool = await get_pool()
        # `status = 'pending'` in the where clause, not just the session id,
        # makes a retried webhook delivery (Stripe's own at-least-once
        # guarantee) a no-op the second time rather than re-stamping paid_at.
        await pool.execute(
            """
            update listing_purchases
            set status = 'paid', paid_at = now(), stripe_payment_intent_id = $2
            where stripe_checkout_session_id = $1 and status = 'pending'
            """,
            session["id"],
            session.get("payment_intent"),
        )
    elif event["type"] == "account.updated":
        # The authoritative update to workspaces.stripe_connect_payouts_enabled --
        # get_payouts_status's own live-lookup fallback exists only to cover the
        # window before this arrives, not to replace it.
        account = event["data"]["object"]
        pool = await get_pool()
        await pool.execute(
            "update workspaces set stripe_connect_payouts_enabled = $2 where stripe_connect_account_id = $1",
            account["id"],
            bool(account.get("payouts_enabled")),
        )
    return StripeWebhookOut(received=True)
