-- Creator payouts (docs/05-frontend-marketplace.md §10's own deferral: "Funds settle into
-- the platform's own Stripe account -- there is no creator payout here"). Stripe Connect
-- Express accounts, one per workspace -- the publishing entity, same unit every other
-- marketplace interaction (listing, install, review) is already scoped to, not per user.
--
-- Destination charges, not a hand-rolled payout job: a Checkout Session for a priced
-- listing now carries `payment_intent_data.transfer_data.destination` (the publishing
-- workspace's connect account) and `application_fee_amount` (the platform's cut) --
-- Stripe splits the charge and pays the connected account out on its own schedule.
-- services/api/app/routers/payments.py's own docstring has the full design.

alter table workspaces add column stripe_connect_account_id text;
-- Cached, not computed on every read -- refreshed by the `account.updated` webhook
-- (payments.py), and self-healed by a live Stripe lookup whenever the payouts-status
-- endpoint is called (covers the redirect-back-before-webhook-arrives race, same shape
-- the purchase-confirmation flow already has to handle). This is what gates whether a
-- listing can be priced at all (create_listing/update_listing) -- a fast local check,
-- not a Stripe round trip on every listing write.
alter table workspaces add column stripe_connect_payouts_enabled boolean not null default false;

-- Accounting fields captured at purchase time, not recomputed later -- a platform fee
-- percentage change (services/api/app/config.py's platform_fee_bps) must never rewrite
-- what an earlier buyer's sale actually charged.
alter table listing_purchases add column application_fee_cents int;
alter table listing_purchases add column stripe_connect_account_id text;
