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
- Ratings/reviews UI. `listings.rating_avg`/`rating_count` exist in the schema and are
  rendered read-only where already populated, but nothing in `marketplace.py` lets anyone
  write a rating yet — that's its own backend project, not a frontend gap.
- An "installs" / library history page (what this workspace has installed, from where). The
  installed agent/flow already shows up in `/agents` or `/flows` under its own name the same
  as anything built locally; a dedicated provenance view is a nice-to-have, not a blocker for
  "I can publish and install things," and `installs` rows exist in the schema whenever this
  does get built.
- Editing a listing's name/description/category after creation, or unpublishing one.
  `marketplace.py` has no endpoints for either — out of scope at the backend layer, so
  necessarily out of scope here too.
- Monetization UI (price, payment). Architecture doc §6: "V1 marketplace: free listings only
  ... Monetization ... deferred," unchanged.

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
