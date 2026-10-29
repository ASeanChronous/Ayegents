"""Marketplace monetization: paid listings via Stripe Checkout (architecture doc §6's
"Monetization ... deferred" note, and docs/05-frontend-marketplace.md's last open item,
picked back up here).

Scoped deliberately narrow: a listing can carry a price (`listings.price_cents`); buying
one is a Stripe Checkout Session the buyer completes, confirmed via webhook, which then
gates `POST .../marketplace/installs` the same way tool-category approval already does
(`marketplace.py`). Funds settle into the *platform's own* Stripe account — there is no
creator payout here. Stripe Connect onboarding (so a listing's publisher actually receives
a revenue share) needs KYC, tax-form collection, and payout-schedule handling, none of
which this closes; architecture doc §10 already calls "monetized marketplace listings" out
as V2 scope, distinct from "listings can have a price and be bought," which is what this
is. Revisit creator payouts as a separate, later pass once there's real demand for it.

Two routers, same public/authenticated split `webhooks.py` already established for the
trigger-firing side: `checkout_router` is ordinary authenticated workspace API (create a
Checkout Session, check whether this workspace already paid); `stripe_webhook_router` is
public, outside `/workspaces/...` and `get_current_user_id` — Stripe itself is the caller,
verified by its own request signature (`stripe.Webhook.construct_event`) rather than a
Supabase session, the same reason the trigger webhook verifies an `X-Webhook-Secret`
instead.
"""

from uuid import UUID

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app.config import settings
from app.db import get_pool
from app.deps import get_current_user_id

checkout_router = APIRouter(prefix="/workspaces/{workspace_id}/marketplace/listings", tags=["marketplace"])
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
            "select id, name, price_cents, currency from listings where id = $1 and status = 'published'",
            listing_id,
        )
        if listing is None:
            raise HTTPException(status_code=404, detail="listing not found or not published")
        if listing["price_cents"] <= 0:
            raise HTTPException(
                status_code=400, detail="this listing is free — install it directly, no checkout needed"
            )

        already_installed = await conn.fetchval(
            "select exists(select 1 from installs where listing_id = $1 and installing_workspace_id = $2)",
            listing_id,
            workspace_id,
        )
        if already_installed:
            raise HTTPException(status_code=409, detail="this workspace has already installed this listing")

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
                success_url=f"{settings.frontend_url}/marketplace/{listing_id}?purchase=success&session_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{settings.frontend_url}/marketplace/{listing_id}?purchase=cancelled",
                metadata={"listing_id": str(listing_id), "workspace_id": str(workspace_id), "user_id": str(user_id)},
            )
        except stripe.error.StripeError as exc:
            raise HTTPException(status_code=502, detail=f"Stripe checkout session creation failed: {exc}") from None

        await conn.execute(
            """
            insert into listing_purchases
                (listing_id, purchasing_workspace_id, amount_cents, currency, stripe_checkout_session_id, purchased_by)
            values ($1, $2, $3, $4, $5, $6)
            """,
            listing_id,
            workspace_id,
            listing["price_cents"],
            listing["currency"],
            session.id,
            user_id,
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
    return StripeWebhookOut(received=True)
