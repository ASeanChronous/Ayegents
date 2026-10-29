# Agent Ecosystem — Marketplace Storefront Scope v0.1

Status: draft for review. Scopes the marketplace storefront UI — browse, listing detail, and
install-with-consent — over the `marketplace.py`/`listings.py` endpoints `03-frontend.md` §8
named as the last unscoped piece of functional-core-and-beyond work, and that were sitting
fully built and tested (including, as of this project, flow listings) with no UI in front of
them at all.

The backend is almost entirely ready for this the same way flows' backend was ready for
`04-frontend-builder.md`: `marketplace.py`'s publish/install flow and `listings.py`'s public
browse were built and verified by curl months ago. This doc is about the UI and the one
backend read gap it needs that happens not to exist yet (an owner's view of their own
listings, draft or published — everything else is either already a public read or an existing
write).

## 1. What this phase covers

Everything needed to publish an agent or flow as a listing, browse what other workspaces have
published, and install one — reusing the Agents/Flows pages this project already has to show
the result, unchanged.

1. `/marketplace` — two views on one page: **Browse** (every published listing, any
   workspace) and **My listings** (this workspace's own, draft or published) — publish a new
   listing from either an agent or a flow, or publish a new version of one already published.
2. `/marketplace/[id]` — listing detail: what it is, what tool categories it needs, and the
   install-with-consent flow (architecture doc §6's "OAuth-consent-style" approval) that forks
   it into the current workspace as a brand-new agent or flow.
3. Landing on the result: no new UI — installing lands on the freshly-created agent/flow at
   `/agents/[id]` or `/flows/[id]`, both of which already show it correctly the moment it
   exists, exactly like anything built by hand in this workspace.

Explicitly **not** in this phase:
- Ratings/reviews UI. **Done** — see §8 below; moved out of this deferred list once built.
- An "installs" / library history page. **Done** — see §7 below; moved out of this deferred
  list once built.
- Editing a listing's name/description/category after creation, or unpublishing one.
  **Done** — see §9 below; moved out of this deferred list once built.
- Monetization UI (price, payment). **Done** — see §10 below; moved out of this deferred
  list once built, originally scoped narrower than architecture doc §6's full "revenue
  share, paid listings" (buyer-side only, no creator payout) — the other half, creator
  payouts via Stripe Connect, is **also done**, see §11.

## 2. Needed backend endpoints

One gap, the same shape as every other "the writes exist, a read doesn't" gap this project has
hit (`04-frontend-builder.md` §8, `03-frontend.md` §7 step 4): `marketplace.py`'s
`listings_router` has `POST` (create) and `POST .../versions` (publish) but no `GET` at all —
there was never a reason to read a listing back until something needed to render a management
table. Public browse (`listings.py`) only ever shows `status = 'published'` rows, by design
(an unpublished listing has nothing to browse: no spec, no `required_tool_categories`), so it
can't stand in for "show me my own listings including the ones I haven't published yet."

- `GET /workspaces/{workspace_id}/listings` — every listing this workspace owns, any status,
  newest first. Same response shape `listings.py`'s public `ListingOut` already uses (`kind`,
  `name`, `description`, `category`, `install_count`, `rating_avg`,
  `required_tool_categories`) plus `status`, since My listings is exactly the place "draft vs
  published" needs to be visible.

Nothing else is missing: listing detail reuses the existing public `GET
/marketplace/listings/{id}`, publish/create reuse the existing `POST` endpoints unchanged,
install reuses the existing `POST .../marketplace/installs` unchanged, and the agent/flow
picker for "publish a new listing" reuses `GET /workspaces/{id}/agents` /
`GET /workspaces/{id}/flows`, both long since built.

## 3. The install-with-consent flow (read before building)

The one real design question in this phase, so it's called out before the page list.

`POST /workspaces/{workspace_id}/marketplace/installs` takes `approved_tool_categories: 
list[str]` and 403s with the missing categories if it isn't a superset of the listing's
`required_tool_categories` (`marketplace.py`'s own docstring: "OAuth-consent-style ... same
pattern as OAuth consent screens"). The UI's job is to make that approval a real choice, not a
single all-or-nothing "Install" button that happens to send every required category because
that's the only thing that would ever succeed:

- The detail page lists every required category as its own checkbox, unchecked by default —
  the architecture doc's own framing ("sends email," "reads your files") is per-category for a
  reason; a listing that needs three things should show three things to approve, not one
  bundled "yes."
- "Install" stays disabled until every box is checked. This is enforced client-side as a UX
  nicety only — the 403 is still the real enforcement, and the button's disabled state is
  just saving the round trip a partial approval would otherwise need to fail first.
- A listing with zero required categories (an agent with no tools, or a flow whose nodes
  reference none) skips the checklist entirely — nothing to approve, so "Install" is live
  immediately. `required_tool_categories: []` already makes `missing` empty at the API layer;
  the UI mirrors that rather than rendering an empty, pointless checklist.

## 4. Pages, routes, and layout

```
app/
  (app)/marketplace/page.tsx      Browse + My listings (two views, one page)
  (app)/marketplace/[id]/page.tsx Listing detail + install
```

Both pages use the standard centered column layout (`AppShell`'s existing wrapper) — unlike
the flow canvas, nothing here needs full-bleed screen space; a listing is a record to read and
a form to fill, the same shape every other non-canvas page in this app already has.

## 5. Component architecture

- `/marketplace/page.tsx` — a `Browse` / `My listings` tab switcher (same local-state pattern
  `TriggersDrawer`'s webhook/schedule toggle and the Runs start-form's agent/flow toggle
  already use — two buttons, one active at a time, no router/URL involvement since neither
  view is worth deep-linking to on its own). Browse is a `DataTable` over `GET
  /marketplace/listings`; My listings is a `DataTable` over the new `GET
  /workspaces/{id}/listings`, each row showing a status badge and, for a published row, a
  "Publish new version" action that just calls `POST .../versions` again — no form, since a
  republish takes no input of its own (`marketplace.py`'s publish endpoint always re-snapshots
  the *current* source agent/flow). A "New listing" button opens...
- `<PublishListingDrawer>` (new) — the `FormDrawer` pattern every other create flow in this
  app uses. Source-type toggle (agent/flow, mirroring the Runs start-form's own toggle) drives
  which picker (`GET .../agents` or `GET .../flows`) populates a dropdown; name/description/
  category are plain fields. Submits as `POST .../listings` immediately followed by `POST
  .../listings/{id}/versions` in the same handler — a listing with no published version is
  useless to browse, so there's no reason to expose "create" and "publish" as two separate
  user-facing steps the way the backend's own two endpoints might suggest. Both calls' errors
  (not just the first) need to reach the form — publish-time validation
  (`_agent_listing_snapshot`/`_flow_listing_spec`'s sub-agent and private-tool checks,
  `services/api/app/routers/marketplace.py`) only happens on the second call, so a listing
  can be validly *created* and still fail to *publish*.
- `/marketplace/[id]/page.tsx` — listing detail from `GET /marketplace/listings/{id}` (name,
  description, category, kind badge, install count, rating if present) plus the consent
  checklist (§3) over `required_tool_categories`, submitting `POST
  /workspaces/{currentWorkspace}/marketplace/installs`. On success, a link to the installed
  result (`installed_agent_id` → `/agents/{id}`, `installed_flow_id` → `/flows/{id}`) rather
  than a redirect — the person browsing the marketplace may still want to look at the listing
  page a moment longer (install another workspace's copy, read the description again) before
  moving on, unlike a wizard-style flow that's done the moment one step finishes.

`AppShell`'s nav gains a "Marketplace" link, same list every other top-level page is already
in.

## 6. Phased build order

1. **Done.** Backend: the one `GET` endpoint (§2).
2. **Done.** `/marketplace` page: Browse view only (read-only, no publish yet) — confirms the
   list/detail read path end-to-end before building any write UI on top of it.
3. **Done.** `<PublishListingDrawer>` + My listings view — the create-and-publish path, for
   both an agent and a flow source.
4. **Done.** `/marketplace/[id]` detail page + install-with-consent (§3) — the last piece,
   since it's the one that needs a real published listing (from step 3) to test against.
5. **Done.** End-to-end verification — see below.

All four built in one pass rather than as separate increments, since each one's own
verification needed the one before it (a Browse page needs a published listing to show;
an install page needs a real listing id to install) — splitting them further would've just
meant re-deriving the same setup each time.

### Build notes and verification

**Backend** (`services/api/app/routers/marketplace.py`): `GET /workspaces/{workspace_id}/listings`
added as `list_my_listings`, right above the existing `create_listing` on the same router — a
`WorkspaceListingOut` model (the public `ListingOut`'s fields plus `status`) over a query that,
unlike the public browse, has no `status = 'published'` filter at all. The `kind` derivation
(`case when source_agent_id is not null then 'agent' else 'flow' end`) is duplicated from
`listings.py` rather than imported, matching this project's existing convention of each router
staying self-contained.

**Frontend**: `components/marketplace/PublishListingDrawer.tsx` (new), `app/(app)/marketplace/
page.tsx` (new, Browse/My listings tabs), `app/(app)/marketplace/[id]/page.tsx` (new, detail +
install). `AppShell.tsx` gained the "Marketplace" nav link. `app/(app)/page.tsx`'s stale
dashboard copy ("the visual flow builder and marketplace storefront come later") was also
fixed while touching this area — both have existed for a while now and the old text was
actively wrong, not just dated.

Verified against the real Docker Postgres + API setup (no orchestrator needed — nothing here
executes a run) via `curl` first: confirmed `GET /workspaces/{id}/listings` returns `[]` for a
fresh workspace, a created-but-unpublished listing as `status: "draft"`, and the same listing
as `status: "published"` with real `required_tool_categories` once published — all correctly
invisible to/visible from the public `GET /marketplace/listings` at each stage.

Then end-to-end in the browser against the same stack: signed in as one user, published an
agent listing (with one global tool) and a flow listing (an agent node plus a direct tool
node) from one workspace, used "Publish new version" on the existing listing and confirmed a
second `listing_versions` row landed. Published a third, tool-less listing and confirmed its
detail page skips the consent checklist entirely and installs immediately. Signed in as a
second user in a second workspace and installed both the agent and the flow listing there:
confirmed "Install" stays disabled until the one required category is checked, confirmed a
disabled-button click is a genuine no-op (no install occurs), checked it, installed, and
confirmed both landed as real, correctly-named, fully-functional new records — the installed
flow opened at `/flows/[id]` with its trigger→tool wiring intact and a real saved version,
exactly as `marketplace.py`'s recursive-agent-forking was designed to produce.

**Bug found and fixed during this pass:** publishing a new listing (or a new version of an
existing one) updated the My listings view (`mutateMine`) but never revalidated the Browse
view's own separate SWR cache (`"/marketplace/listings"`) — a listing that just became newly
installable sat invisible on the Browse tab until an unrelated full page navigation happened
to refetch it. Fixed by giving `MarketplacePage` a single `refreshListings()` that revalidates
both SWR keys together, called from every path that can change what Browse should show
(`PublishListingDrawer`'s `onPublished` and the My listings "Publish new version" handler) —
confirmed a freshly-published listing now appears on Browse immediately, in the same session,
with no navigation needed.

`tsc --noEmit`, `npm run build` (13 routes, two new — `/marketplace` and `/marketplace/[id]`),
and `npm test` (9 tests, unaffected) all clean.

## 7. Installs / library history page

**Done.** The one item from §1's deferred list that got built after all — what this workspace
has installed, and which listing each came from.

No new page: a third tab, "My installs", on `/marketplace` next to Browse and My listings — the
same tab-switcher pattern those two already use, rather than a fourth top-level nav entry for
one more read-only table. Each row links out to the source listing (`/marketplace/{listing_id}`)
and to the installed result (`/agents/{id}` or `/flows/{id}`, whichever `installed_agent_id`/
`installed_flow_id` is set) — this page is purely the provenance trail connecting the two, not a
duplicate of either.

Backend gap, same shape as every other one in this project: `marketplace.py`'s `install_router`
had `POST /installs` but no `GET` at all. Added `GET /workspaces/{workspace_id}/marketplace/
installs`, joining `installs` to `listings` for the listing's name and kind (installs has no
denormalized copy of either), ordered newest first, scoped to `installing_workspace_id` — the
same column RLS already keys its one `installs` read policy on.

Verified against the real Docker Postgres + API stack: installed one agent listing and one flow
listing (as in §6's own verification pass) into a workspace via `curl`, confirmed `GET .../
installs` returned both rows with the correct listing name/kind and `installed_agent_id`/
`installed_flow_id`, then in a real browser clicked into the My installs tab and confirmed both
rows render with working links — the listing link opens the correct `/marketplace/[id]` detail
page, and "open it" opens the correct `/agents/[id]` or `/flows/[id]`, the same forked records
§6's verification already confirmed were fully functional.

## 8. Ratings/reviews

**Done.** The last item from §1's deferred list. `listings.rating_avg`/`rating_count` have
existed since `0001_init_schema.sql` as aggregate columns with nothing to aggregate — this adds
the table of individual reviews that actually feeds them, plus the endpoints and UI to write and
read one.

Schema (`0007_listing_reviews.sql`): `listing_reviews`, one row per `(listing_id,
reviewing_workspace_id)` — a workspace, not a user, is the reviewing unit, matching every other
marketplace interaction's own scope (install, "my listings"), and it keeps one listing from
being review-bombed by several members of the same workspace voting the same opinion
repeatedly. Writing one is gated on an `installs` row existing for that exact pair — reviewing
something your workspace hasn't actually installed is refused, both by an RLS policy on the
table and (the one that actually runs, since the API connects with pooled, not per-request RLS'd
credentials — same split every other table in this project already has) a check in the write
endpoint itself.

Backend:
- `GET /marketplace/listings/{listing_id}/reviews` (`listings.py`, public like browse/detail) —
  every review on a published listing, newest first, each with the reviewing workspace's name
  (not anonymous) and whether it's been edited.
- `GET` / `PUT /workspaces/{workspace_id}/marketplace/listings/{listing_id}/review`
  (`marketplace.py`) — this workspace's own review (`GET` returns `null` if none yet, so the
  frontend can tell "leave a review" from "edit your review" apart and prefill the form) and the
  upsert itself (`insert ... on conflict (listing_id, reviewing_workspace_id) do update`, so a
  second submission from the same workspace edits in place rather than adding a second row).
  Every write recomputes `listings.rating_avg`/`rating_count` from `listing_reviews` directly in
  the same transaction, rather than incrementally adjusting the aggregate — an edit changes an
  existing rating, which an incremental approach would have to special-case anyway.
- `ListingOut`/`WorkspaceListingOut` both gained `rating_count` (only `rating_avg` was exposed
  before this).

Frontend: no new page — a `<ReviewsSection>` (new) at the bottom of `/marketplace/[id]`, the
listing detail page already built in §5/§6. Shows every public review (reviewer workspace name,
star rating, comment, edited-or-not) and, for whichever workspace is currently selected, a
leave-or-edit form. The form shows for any selected workspace rather than first checking install
status itself — the API's 403 for a workspace that hasn't installed yet is the real
enforcement, surfaced as the form's own error message rather than re-derived client-side. The
rating shown on the detail page and both marketplace list views (Browse, My listings) changed
from a bare average to `"4.2 (3 reviews)"` / `"No reviews yet"`.

Verified against the real Docker Postgres + API stack: `curl` confirmed `PUT .../review` 403s
for a workspace that hasn't installed the listing, succeeds once it has, a second `PUT` from the
same workspace edits the existing row rather than creating a second one, and `listings.rating_avg`/
`rating_count` update correctly after each write. Then in a real browser: installed a listing
into a workspace, left a 4-star review with a comment, confirmed it appeared in the public
reviews list with the reviewing workspace's real name, edited it to 5 stars, confirmed the
review updated in place (not duplicated) and the listing's displayed rating changed from "4.0 (1
review)" to "5.0 (1 review)" without a page reload.

## 9. Edit / unpublish a listing

**Done.** The last item from §1's original deferred list.

Backend (`marketplace.py`):
- `PATCH /workspaces/{workspace_id}/listings/{listing_id}` — edits `name`/`description`/
  `category`. Partial: `body.model_dump(exclude_unset=True)` means only fields actually present
  in the request are touched, so an edit that only changes the description leaves name alone
  rather than clearing it. Deliberately separate from "publish a new version"
  (`POST .../versions`), which re-snapshots the *source* agent/flow and has nothing to do with
  the listing's own metadata.
- `POST /workspaces/{workspace_id}/listings/{listing_id}/unpublish` — takes a published listing
  off the public browse by setting `status = 'archived'`, the third `publish_status` enum value
  that's existed since `0001_init_schema.sql` with nothing ever setting it until now. 400s on
  anything not currently `published` (unpublishing a draft is a no-op that would just be
  confusing to allow). Deleting the row outright was never an option — `installs.listing_id` and
  `listing_reviews.listing_id` both still reference it, and an unpublish doesn't touch anything
  it already installed elsewhere anyway (an install forked its own independent copy at install
  time). No new "re-publish" endpoint needed: `POST .../versions` already sets `status =
  'published'` unconditionally regardless of what it was before, so it already re-lists an
  archived listing for free.

Frontend: `<EditListingDrawer>` (new) — the same `FormDrawer` pattern every other form here
uses, keyed by the listing's id so switching which row is being edited remounts the form with
fresh field values instead of inheriting a previous edit's leftover state. My listings' action
column gained "Edit" (always) and "Unpublish" (only on a published row, red to mark it as the
one state-changing action in that row that isn't reversible by just clicking it again — undoing
it means publishing a new version). Both wire into the same `refreshListings()` every other
write path here already calls, since unpublishing (like publishing) changes what Browse should
show.

Verified against the real Docker Postgres + API stack: `curl` confirmed unpublishing a draft
400s, `PATCH` with only `{"name": ...}` left an existing `category` untouched, and a published
listing disappeared from `GET /marketplace/listings` immediately after unpublish while still
showing up (as `archived`) in the owner's own `GET .../listings` — then publishing a new version
on it brought it back to `published` and back into Browse, with no new endpoint needed to do so.
In a real browser: edited a listing's description, confirmed it updated on the public detail
page; unpublished a published listing and confirmed, without any reload, its row flipped to
`archived` with the Unpublish button replaced by "Publish" and it vanished from the Browse tab
at the same time.

## 10. Monetization UI

**Done, scoped narrower than architecture doc §6's full "revenue share, paid listings"
at the time this section was written.** A listing can carry a price and be bought via
Stripe Checkout, gating install the same way tool-category approval already does. What
this pass did **not** build: creator payouts — funds settled entirely into the
*platform's own* Stripe account, with a listing's publisher receiving no share. **Update:
that gap is now closed, see §11** (Stripe Connect). Everything below describes this
section's own original increment as built and verified at the time; §11 is additive on
top of it, not a rewrite — the Checkout/webhook/purchase-gating mechanics described here
are unchanged, §11 just adds a destination-charge split to the same Checkout Session.

`services/api/app/routers/payments.py`'s own docstring has the full reasoning for both
passes together.

Design:
- `listings.price_cents`/`listings.currency` (migration `0008_listing_pricing.sql`) — 0 is
  free, the only value every listing had before this. A new `listing_purchases` table is one
  row per Stripe Checkout Session, not per successful purchase, so an abandoned or expired
  session stays `'pending'` rather than never having existed (useful for debugging, and makes
  `stripe_checkout_session_id` a reliable webhook idempotency key — see below).
- `POST .../marketplace/listings/{id}/checkout` creates a Checkout Session with `price_data`
  built inline from the listing's *current* `price_cents`/`currency` at call time, not a
  pre-created Stripe Price/Product kept in sync with listing edits — a listing's price can
  change (`PATCH`, below), and this way every new session just reads whatever's in the
  database right now. Blocks a free listing (400, "install it directly"), an already-installed
  workspace (409), and reports a Stripe-side failure as a clean 502 rather than a raw
  exception.
- `POST /webhooks/stripe` confirms the purchase: verifies the request is really from Stripe
  (`stripe.Webhook.construct_event` against a configured webhook secret, same posture the
  existing trigger webhook's `X-Webhook-Secret` check already has) and flips the matching
  `listing_purchases` row to `'paid'` on `checkout.session.completed`, gated on
  `status = 'pending'` in the `where` clause so a retried webhook delivery (Stripe's own
  at-least-once guarantee) is a no-op the second time rather than re-stamping `paid_at`.
  **Real bug found and fixed while wiring this up, not through testing**: this route has to
  be registered *before* the existing trigger-webhook router in `main.py` — `POST
  /webhooks/{trigger_id}` is a path parameter that would otherwise greedily match "stripe" as
  a `trigger_id` and shadow `POST /webhooks/stripe` entirely, since FastAPI matches routes in
  registration order. Caught immediately once end-to-end testing actually exercised the
  webhook path (a 422 "invalid UUID" instead of the expected 200 made the collision obvious),
  fixed by reordering the two `include_router` calls, with a comment explaining why the order
  matters so it doesn't regress if a third webhook route is ever added later.
- `GET .../marketplace/listings/{id}/purchase` — whether this workspace has a completed
  purchase. The frontend polls this right after a Checkout redirect-back (the webhook that
  actually confirms payment races the browser's own redirect, so `?purchase=success` in the
  URL alone can't be trusted) and also checks it on a normal page load, so a workspace that
  paid earlier but never finished installing isn't asked to pay a second time.
- `POST .../marketplace/installs` gates a priced listing's install on a `'paid'`
  `listing_purchases` row for that exact `(listing_id, workspace_id)` pair, the same shape the
  existing tool-category-approval gate already has — refuse outright (402) rather than let an
  unpaid install through.
- `ListingCreate`/`ListingUpdate` gained `price_cents` (validated `>= 0`); `currency` isn't a
  client-settable field yet, fixed at `'usd'` — multi-currency pricing (which currencies, how
  a buyer's Checkout session would pick one) is a real product decision this pass didn't make,
  not an oversight.
- Frontend: `PublishListingDrawer`/`EditListingDrawer` gained a "Price (USD)" dollars input
  (converted to `price_cents` only at submit time), blank meaning free. The listing detail
  page (`/marketplace/[id]`) branches on `price_cents > 0 && !purchase?.paid`: a priced,
  unpurchased listing shows a "Buy for $X.XX" button instead of the approve-and-install panel;
  clicking it redirects to Stripe's own hosted Checkout page (`window.location.href`, not a
  fetch — a real cross-origin redirect is the point). Handles all three return states a
  Checkout redirect-back can leave the page in: `?purchase=success` with payment not yet
  confirmed ("Confirming your payment…", via the polling `useSWR` above),
  `?purchase=cancelled` ("Checkout cancelled — no charge was made," then the Buy button
  again), and a purchase already confirmed paid (the normal install panel, unchanged from
  before this pass). Both marketplace tables (`Browse`, `My listings`) gained a "Price"
  column showing "Free" or a formatted currency amount.

Verified against the real Docker Postgres + API stack, Stripe itself mocked (`stripe.checkout.
Session.create` and `stripe.Webhook.construct_event` monkeypatched) rather than hit for real,
since this pass has no live Stripe account to test against — the real, unmocked signature
verification was exercised separately (a forged `Stripe-Signature` header against the actual
`construct_event` correctly 400s). Via an in-process ASGI client against the real app and real
database: created a Checkout session for a priced listing, confirmed the resulting
`listing_purchases` row and that install was blocked (402) before payment; fired a mocked
`checkout.session.completed` webhook event and confirmed the purchase flipped to `'paid'` and
install then succeeded (201, correct `installed_agent_id`); fired the identical webhook event
a second time and confirmed no error and no double-processing (idempotency); confirmed
checkout on an already-installed listing now 409s. Confirmed free listings are completely
unaffected — checkout rejects them (400), direct install still works exactly as before. This
same pass is what surfaced and fixed the webhook route-shadowing bug above — the test failed
with an "invalid UUID" 422 before the fix and passed cleanly after. Then in a real browser:
published a priced and a free listing, confirmed the Price column renders correctly on both
marketplace tables; opened the priced listing from a workspace that hadn't bought it and
confirmed the Buy gate (clicking Buy against the dummy Stripe key surfaced the Stripe error
in the UI instead of failing silently); switched to a workspace with a confirmed purchase and
confirmed it shows the normal install panel instead; navigated directly to
`?purchase=success` and `?purchase=cancelled` and confirmed both render the right in-between
state; opened both listings' Edit drawers and confirmed the Price field prefills correctly
(blank for free, "9.99" for a $9.99 listing).

`tsc --noEmit`, `npm run build` (13 routes, unchanged), and `npm test` (9 tests, unaffected)
all clean.

## 11. Creator payouts (Stripe Connect)

**Done.** Closes the gap §10 deliberately left open: a listing's publisher now actually
receives a share of each sale, not the platform keeping all of it.

Design: Stripe Connect **Express** accounts, one per workspace (the publishing entity, same
unit every other marketplace interaction is already scoped to) — chosen over
Standard/Custom because Stripe hosts almost the entire onboarding UI (KYC, bank details, tax
info), which is the whole point: this platform owns none of that compliance surface itself,
just an account id and a cached `payouts_enabled` flag. **Destination charges**, not a
hand-rolled payout job: a priced listing's Checkout Session now also carries
`payment_intent_data.transfer_data.destination` (the connected account) and
`application_fee_amount` (the platform's cut, `settings.platform_fee_bps` — **15%**, a real
product/business number picked deliberately rather than invented in passing, of the sale
price) — Stripe splits the charge at capture time and pays the connected account out on its
own payout schedule. There is no code in this project that ever moves money between
accounts; Stripe does that part entirely.

- `workspaces.stripe_connect_account_id`/`stripe_connect_payouts_enabled` (migration
  `0009_stripe_connect.sql`) — the latter is a cached flag, not computed on every read,
  kept current by the `account.updated` webhook and self-healed by a live
  `stripe.Account.retrieve` on every `GET .../payouts/status` call (covers the same
  "redirect-back races the webhook" gap the purchase-confirmation flow (§10) already had to
  handle, this time for "did Stripe finish verifying this account" instead of "did Stripe
  finish charging this card"). `listing_purchases` gained `application_fee_cents`/
  `stripe_connect_account_id`, captured at purchase time so a later platform-fee change
  never rewrites an earlier sale's accounting.
- `POST .../payouts/connect-onboarding` finds-or-creates the workspace's Express account,
  then an Account Link (Stripe's hosted onboarding flow) pointed back at `/marketplace`
  either way — safe to call again for an account that exists but never finished onboarding,
  since Account Links are single-use/short-lived but the underlying account isn't recreated.
- `GET .../payouts/status` is the self-healing read described above — `{connected,
  payouts_enabled}`.
- **A listing can't carry a nonzero price unless its workspace has finished Connect
  onboarding** (`create_listing`/`update_listing`'s shared `_require_payouts_enabled`
  check, marketplace.py) — avoids ever publishing a priced listing with nowhere for its
  share of a sale to go. `create_checkout_session` (§10) re-checks this at checkout time
  too, as defense in depth: a Connect account can be disabled by Stripe for risk/compliance
  reasons at any time *after* a listing was published, and a sale with nowhere for the
  publisher's share to go is worse than a buyer seeing a clear error.
- The Stripe webhook handler (§10, same endpoint) gained an `account.updated` branch,
  updating the cached `payouts_enabled` flag by matching `stripe_connect_account_id`.
- Frontend: new `<PayoutsPanel>`, shown above the My listings table (exactly where pricing a
  listing would otherwise 400) — three states: not connected ("Connect with Stripe"),
  connected but not yet finished onboarding ("Finish Stripe onboarding," the same button,
  since re-onboarding just gets a fresh Account Link for the same account), and payouts
  enabled (a small green confirmation, no button). `PublishListingDrawer`'s price-field
  caption was updated to point here instead of the now-stale "no payouts yet" text §10 had.

Verified against the real Docker Postgres + API stack, Stripe itself mocked (`stripe.
Account.create`, `stripe.AccountLink.create`, `stripe.Account.retrieve`, `stripe.checkout.
Session.create`, `stripe.Webhook.construct_event`) for the same reason §10's verification
mocked it — no live Stripe account to test against. Via `curl` against a real (unmocked)
server process: confirmed pricing a listing before connecting 400s, and that
`connect-onboarding` against an invalid key fails as a clean 502, not a raw exception. Via an
in-process ASGI client against the real app and database: onboarded a workspace (mocked
account creation), confirmed `GET .../status` immediately showed `payouts_enabled: true` via
the self-healing live lookup (before any webhook), confirmed a priced listing could now be
created where it couldn't before, confirmed the resulting Checkout Session's
`payment_intent_data` carried the correct destination account and exactly 15% of the price
as `application_fee_amount` (2000 → 300), confirmed those exact figures landed on the
`listing_purchases` row, and ran the full purchase → webhook → install sequence through to a
successful install. Separately, fired a real `account.updated` webhook event and confirmed
it flipped the cached `payouts_enabled` flag from false to true. Then in a real browser:
a connected workspace's My listings tab showed the green "Payouts connected" confirmation;
switching to a workspace that had never connected showed the "Connect with Stripe" gate, and
clicking it surfaced the mocked-out Stripe error in the UI instead of failing silently.

`tsc --noEmit`, `npm run build` (13 routes, unchanged), and `npm test` (9 tests, unaffected)
all clean.
