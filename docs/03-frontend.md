# Agent Ecosystem — Frontend Scope v0.1 (Functional Core)

Status: draft for review. Scopes **only** the first phase: plain forms/tables that make
everything already built (auth, agents, tools, runs) usable from a browser. The visual flow
builder canvas and the marketplace storefront are real UI projects of their own — each gets
its own doc (`04-frontend-builder.md`, `05-frontend-marketplace.md`) once this phase ships,
not a section bolted onto this one.

`apps/web` had a single placeholder page and no auth, data fetching, or styling setup when
this doc was written — §7 step 1 (auth, styling, the generated API client) is now done; the
rest of this doc is still the plan for the pages built on top of it.

## 1. What "functional core" covers

Every screen needed to go from "nothing" to "a run I can watch execute," using only forms
and tables — no canvas, no drag-and-drop:

1. Sign in (Supabase Auth)
2. Pick or create a workspace
3. Create a tool, optionally attach a credential
4. Create an agent, publish a version (system prompt, model tier, attached tools)
5. Start a run, watch it move through `queued → running → succeeded/failed`, inspect every
   step (`run_steps`) it took

Explicitly **not** in this phase: the flow builder canvas, flow creation UI (API-only for
now), marketplace browse/publish/install UI, workspace member management beyond "I'm the
owner," billing/plan-tier UI.

## 2. Auth

- `@supabase/supabase-js` on the client — email/password or magic-link sign-in against the
  same Supabase project the API verifies tokens against (`services/api/app/auth.py`).
- The Supabase client's session gives a JWT access token; every API call attaches it as
  `Authorization: Bearer <token>`. No separate frontend session/cookie scheme — Supabase's
  client already handles refresh.
- A thin `useSession()` hook wraps `supabase.auth.onAuthStateChange`, redirecting to
  `/login` when there's no session. Pages under `app/(app)/` assume a session exists; `/login`
  is the only route outside that group.

## 3. Data fetching & API client

- **Typed client, generated, not hand-written**: run `openapi-typescript` against the API's
  own `/openapi.json` at build/dev time to generate request/response types directly from the
  FastAPI/Pydantic models. Hand-duplicating those shapes as separate TypeScript interfaces is
  exactly the kind of drift this project has already paid for once (the orchestrator/api
  package-name collision, the `_load_tools` column-list bug) — generate from the single
  source of truth instead.
- A small wrapper (`lib/api.ts`) around `fetch` reads the generated types, attaches the
  Bearer token from the current Supabase session, and throws a typed error on non-2xx so
  components don't each reimplement status-code handling.
- **SWR** for data fetching/caching over React Query: smaller footprint, and its
  `refreshInterval` is a natural fit for polling a run's status without adding a realtime
  layer yet (see §6).

## 4. Pages

```
app/
  login/page.tsx                          Supabase sign-in
  (app)/layout.tsx                        Top nav: workspace switcher, user menu, sign-out
  (app)/workspaces/page.tsx               List workspaces, create one
  (app)/agents/page.tsx                   List agents in the current workspace, create one
  (app)/agents/[id]/page.tsx              Agent detail: versions, publish a new version
                                           (system prompt, model tier, tool picker)
  (app)/tools/page.tsx                    List tools (global + workspace), create one
  (app)/tools/[id]/credentials/page.tsx   Add a credential to a tool that requires one
  (app)/runs/page.tsx                     List runs, "start a run" form (pick agent + input)
  (app)/runs/[id]/page.tsx                Run inspector: status, input/output, step timeline
```

Current workspace is a query param or a value in `localStorage`, not a route segment — avoids
`/workspaces/{id}/agents/...` everywhere for a single-workspace-at-a-time UI; switching
workspaces just changes what the same pages fetch.

## 5. Component architecture

Small, shared, un-opinionated — no design system investment yet (that's what
`04`/`05` are for once there's a canvas and a storefront to make consistent with it):

- `<DataTable>` — list agents/tools/runs, no sorting/filtering beyond what SWR already gives for free
- `<FormDrawer>` — side-panel create forms (new agent, new tool, new credential, start a run)
- `<StatusBadge>` — run/listing status as a colored pill (`queued`/`running`/`succeeded`/`failed`/`budget_exceeded`)
- `<RunStepTimeline>` — one row per `run_steps` entry: kind, node_id, tokens, latency, status, expandable input/output

Styling: **Tailwind CSS**, added to `apps/web` (not present yet) — fastest path to functional
forms/tables without hand-rolling CSS, and the conventional choice if `04`/`05` later want a
component library (shadcn/ui, etc.) built on it.

## 6. Run inspector: realtime, not polling

**Done.** `/runs/[id]` originally polled `GET /workspaces/{id}/runs/{run_id}` via SWR's
`refreshInterval` (2s) while status was `queued`/`running`. Replaced with a push-based update:
the orchestrator fires a Postgres `NOTIFY` on channel `run_events` (payload: the run_id)
immediately after every write to `runs.status` and every `run_steps` insert
(`services/orchestrator/orchestrator_app/notify.py`, called from `executor.py` and
`flow_executor.py` at every status/step write site), and a new API endpoint, `GET
/workspaces/{id}/runs/{run_id}/events`, turns that into a Server-Sent Events stream the
frontend subscribes to.

Deliberately **not** Supabase's own hosted Realtime service (subscribing to `runs`/`run_steps`
changes directly via `supabase-js`), despite that being this section's original "better
long-term answer" framing. Two reasons: it's the same postgres-native instinct this project
already applied to `run_queue` over pg-boss/Redis — one less managed service, and NOTIFY/LISTEN
is already sitting right there on the same database — and, concretely, this project's local dev
stack runs plain Postgres with a GoTrue-only auth shim (`supabase/local-dev-auth-shim.sql`), not
hosted Supabase, so a design that depended on Supabase's actual Realtime server couldn't be
verified end to end the way everything else in this project has been.

Design, deliberately kept small:
- `GET .../runs/{id}/events` (`services/api/app/routers/runs.py`) opens one dedicated
  `asyncpg` connection (outside the shared pool — `LISTEN` holds a connection open for the
  life of the subscription, which the pool's short-lived checkouts aren't meant for),
  `LISTEN`s on `run_events`, and streams `event: update\ndata: {"status": "..."}\n\n` once
  immediately (so a client that connects after the run already finished still gets one event,
  not silence) and again on every notify whose payload matches this run_id, closing once
  status is terminal. A 15s idle timeout sends an SSE comment line as a keepalive, since an
  idle proxy/load balancer can otherwise drop a connection with no traffic on it.
- The event payload is only a status string, never the full run/steps body — the frontend
  re-fetches through the existing `GET .../runs/{id}` / `.../steps` endpoints on every event,
  so the stream's only job is "something changed, go look," not re-deriving those endpoints'
  serialization a second time.
- `apps/web/lib/useRunEvents.ts` (new): subscribes via `fetch`'s `ReadableStream`, not the
  browser's native `EventSource` — `EventSource` can't attach an `Authorization` header, and
  this API has no cookie session to fall back on (every other call site attaches the Supabase
  access token as a bearer header, `lib/api.ts`). Reconnects on any drop with a short delay
  rather than leaving the inspector stuck on stale data; stops for good once a terminal status
  comes through or the component unmounts.
- `/runs/[id]/page.tsx`: `useRunEvents` calls both SWR `mutate()`s on every update. The old
  2s `refreshInterval` is now a 20s fallback only — cheap insurance against a stream that never
  connects or silently drops (a proxy that blocks streaming entirely, say), not the real update
  mechanism anymore.

Verified against the real Docker Postgres + API + orchestrator stack: `curl -N` against
`.../events` while a run was still `queued` showed the live sequence `queued` → `running` →
`running` (a `run_steps` insert mid-flow) → `succeeded` arrive with no polling at all, each
event pushed the instant the corresponding row committed. Then in a real browser: navigated to
a run's inspector *before* the orchestrator had claimed it (confirmed `Loading…`/`queued`), and
without any reload or re-navigation watched it flip to `succeeded` with the output and step
timeline populated — confirmed via the network panel that this came from the single `/events`
connection firing its `update` event (which triggered exactly one re-fetch of the run and its
steps), not the 20s fallback poll, which hadn't elapsed yet.

The run inspector also needs `GET /workspaces/{id}/runs/{run_id}/steps` to list `run_steps`
— **done**: added alongside `GET /workspaces/{id}` (§7), both tested end to end against a
real run (queued → executed via a mocked Claude response → fetched back through both
endpoints, confirming correct serialization of the run's final output and every step's
tokens/status/input/output). `GET .../runs/{id}` was also expanded from `{id, status}` to the
full detail the inspector needs (input, output, total_tokens, error, timestamps).

Two things noticed while building this, not fixed (out of scope of adding the endpoints
themselves, flagged so the inspector UI doesn't present them as meaningful): `total_cost_usd`
and every step's `cost_usd` are always `0` — no model-tier pricing table exists yet to
compute them from token counts. And `run_steps.started_at`/`finished_at` are always `null` —
the orchestrator only ever populates `latency_ms`, never those two columns.

## 7. Phased build order

1. **Done.** Tailwind, `lib/supabaseClient.ts` + `useSession()`, `/login` (sign-in/sign-up),
   `AppShell` gating everything under `app/(app)/`, and the generated client
   (`npm run gen:api` → `lib/api-types.ts`, wrapped by `lib/api.ts`). Verified in a real
   browser against a minimal GoTrue-compatible stand-in: sign-up, sign-in, wrong-password
   rejection, sign-out, the `/` → `/login` redirect, and — through the actual generated
   client, not a raw fetch — an authenticated `GET /workspaces` round trip against the real
   API and Postgres. Found and fixed two real bugs this way: the API had no CORS middleware
   at all (added, origin configurable via `CORS_ALLOWED_ORIGINS`), and the login page always
   showed "check your email" after sign-up even when Supabase returns a live session.
2. **Done.** `lib/useWorkspace.tsx` (`WorkspaceProvider`/`useWorkspace`): fetches the user's
   workspaces via SWR, tracks the current one in `localStorage` (not a route segment, per
   §4), defaults to the first workspace when none is selected or the stored id no longer
   exists. `WorkspaceSwitcher` (a `<select>` in the nav) and `/workspaces` (list + create
   drawer, using the new shared `DataTable`/`FormDrawer` components) both read/write it.
   Along the way, fixed a real backend design bug the generated client surfaced immediately:
   `deps.py` used `authorization: str = Header(...)`, which OpenAPI represents as a required
   per-endpoint header *parameter* rather than a security scheme — the generated client
   would have required every call site to pass the token explicitly, defeating the
   middleware-based auto-attach from phase 1. Switched to FastAPI's `HTTPBearer` security
   scheme and regenerated the client. Verified in a real browser: sign-up, create two
   workspaces, switch between them via both the table's "switch" action and the nav
   dropdown, and confirmed the selection survives a full page reload.
3. **Done.** `/tools`: list (global + workspace tools together, no visual distinction yet —
   `ToolOut` doesn't expose `workspace_id`), create (`key`/`name`/`kind`/egress allow-list/
   requires-credential), and a credential drawer for any tool that needs one. `kind` is
   restricted to `http`/`code_exec`/`agent` in the create form — `builtin` is excluded
   because a builtin tool's key has to match an entry in the orchestrator's own
   `BUILTIN_TOOLS` registry, so creating one generically here would just produce a tool that
   fails every dispatch. New shared `Badge` component for `kind`. Verified in a real browser:
   the two migration-seeded global tools appear correctly, created a new `http` tool with an
   egress allow-list and `requires_credential`, added a credential to it, and confirmed
   directly in Postgres that the stored `secret_encrypted` bytes don't contain the plaintext
   secret (Fernet-encrypted, not just "the UI says it worked").
4. **Done.** `/agents`: list + create. `/agents/[id]`: agent header, a version history
   table, and a publish-version drawer (system prompt, model tier, optional max steps, and a
   tool-attachment picker — each available tool is a checkbox that reveals a JSON config
   textarea when checked). The drawer prefills from the agent's latest version when reopened,
   so publishing a tweak doesn't mean retyping the whole prompt and re-picking every tool.
   Needed two more backend endpoints the detail page genuinely couldn't work without —
   `GET .../agents/{id}` and `GET .../agents/{id}/versions` (nesting each version's attached
   tools and `sub_agent_ids`) — added the same way as the run-inspector gaps earlier.
   Agent-as-tool (`sub_agent_ids`) is deliberately not in this form: wiring it up for real
   needs a matching `agent`-kind tool with `config.sub_agent_id` too
   (`orchestrator_app/tool_gateway.py`), a two-step setup left API-only for now. Verified in a
   real browser: created an agent, published a version with the `current_time` tool attached,
   confirmed the version/tool attachment directly in Postgres (not just the UI's table), and
   confirmed reopening the drawer correctly prefilled from that version.
5. **Done.** `/runs`: run list (agent name, status badge, tokens, started time) and a
   start-a-run drawer (pick an agent, JSON input). `/runs/[id]`: run inspector — status
   badge, input/output JSON panels, total tokens, finished time, and an expandable
   `run_steps` timeline (kind, node, token counts, latency, status; clicking a row reveals
   its JSON input/output and error). Polls `GET .../runs/{id}` and `.../runs/{id}/steps`
   every 2s while the run is `queued`/`running`, stopping once it reaches a terminal status
   (§6) — SWR's `refreshInterval` as a function of the latest data, not a fixed interval.
   Needed one more backend endpoint the list page couldn't work without: `GET
   /workspaces/{id}/runs` (list, no listing endpoint existed before this — only
   create/get-one/get-steps). Verified in a real browser: created an agent, published a
   version with the `current_time` tool, started a run, confirmed it appeared `queued` in
   the list and inspector. Since no orchestrator was running in this pass (would need a
   real `ANTHROPIC_API_KEY`), drove the run through `running` → `succeeded` directly in
   Postgres (status, two `run_steps` rows, output, token count) to verify the actual thing
   this page exists to prove: the inspector picks up every change via polling with no
   reload — status badge, step rows, and the output panel all updated live — a step row
   expanded to show its JSON input/output, and polling genuinely stopped (no further
   requests) once the run hit `succeeded`.

## 8. Open items for `04`/`05`

- Visual flow builder: node/edge canvas (React Flow), compiling to the `flow_versions.spec`
  shape `flow_executor.py` already consumes — the backend is ready for this, no orchestrator
  changes needed, purely a frontend + flow-CRUD-UI project. **Scoped and done**: see
  `04-frontend-builder.md`.
- Marketplace storefront: browse/detail/install-with-consent UI over the `marketplace.py`
  endpoints already built and tested. **Done**: see `05-frontend-marketplace.md`.

All three items originally listed here (including realtime run updates, §6) are now done.
