-- Paid listings (architecture doc §6: "Monetization (revenue share, paid listings)
-- deferred -- needs payment/compliance work, out of scope for MVP"; docs/05-frontend-
-- marketplace.md's last open item, "Monetization UI"). Scoped deliberately narrow: a
-- listing can carry a price and be bought via Stripe Checkout, which gates install the
-- same way tool-category approval already does. Funds settle into the *platform's*
-- Stripe account -- there is no creator payout here (Stripe Connect onboarding, KYC,
-- tax forms, revenue-share accounting). That's a separate, later phase; this closes
-- "listings can have a price and be bought," not "creators get paid a share of it"
-- (services/api/app/routers/payments.py's own docstring has the full reasoning).

alter table listings add column price_cents int not null default 0 check (price_cents >= 0);
alter table listings add column currency text not null default 'usd';

-- One row per Stripe Checkout Session, not per successful purchase -- a session that's
-- abandoned or expires stays 'pending' forever rather than never having existed, which
-- matters for debugging ("did the buyer even reach Stripe?") and for making
-- stripe_checkout_session_id a reliable idempotency key for the webhook (a retried
-- webhook delivery for the same session just no-ops the second time, status already
-- 'paid'). amount_cents/currency are captured at purchase time, not read back from
-- listings, so a later price change never rewrites history for an earlier buyer.
create table listing_purchases (
  id uuid primary key default gen_random_uuid(),
  listing_id uuid not null references listings(id),
  purchasing_workspace_id uuid not null references workspaces(id) on delete cascade,
  amount_cents int not null,
  currency text not null,
  stripe_checkout_session_id text not null unique,
  stripe_payment_intent_id text,
  status text not null default 'pending' check (status in ('pending', 'paid')),
  purchased_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(),
  paid_at timestamptz
);

alter table listing_purchases enable row level security;

-- Read-only from the client's side, same shape tool_credential_secrets' own
-- default-deny takes further -- every write here goes through the API's own pooled
-- connection (checkout-session creation, and the Stripe webhook, which has no
-- end-user session to run RLS as at all), never a client-authenticated insert/update.
create policy "a workspace can read its own purchases" on listing_purchases for select
  using (is_workspace_member(purchasing_workspace_id));
