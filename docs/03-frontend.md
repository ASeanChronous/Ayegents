# Agent Ecosystem — Frontend Scope v0.1 (Functional Core)

Status: draft for review. Scopes **only** the first phase: plain forms/tables that make
everything already built (auth, agents, tools, runs) usable from a browser. The visual flow
builder canvas and the marketplace storefront are real UI projects of their own — each gets
its own doc (`04-frontend-builder.md`, `05-frontend-marketplace.md`) once this phase ships,
not a section bolted onto this one.

`apps/web` currently has a single placeholder page (`app/page.tsx`) and no auth, data
fetching, or styling setup — this doc is the plan for replacing that.

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

## 6. Run inspector: polling, not realtime (for now)

`/runs/[id]` polls `GET /workspaces/{id}/runs/{run_id}` via SWR's `refreshInterval` (e.g. 2s)
while status is `queued`/`running`, stopping once it reaches a terminal state. Supabase
Realtime (subscribing to `runs`/`run_steps` changes directly) is the better long-term answer —
no polling, no SWR interval to tune — but it's a separate chunk of work (Realtime channel
setup, RLS-aware subscriptions) that polling avoids needing right now. Noted here so it's a
deliberate deferral, not a gap someone finds later and wonders about.

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

1. Tailwind setup + Supabase auth client + `/login` + session-gated layout + API client
   codegen wired into the dev/build scripts
2. Workspace switcher + workspaces page (`GET /workspaces/{id}` now exists, see §6)
3. Tools: list/create, credential form
4. Agents: list/create, version publish form with a tool-attachment picker
5. Runs: start-a-run form, run list, run inspector (needs the `run_steps` listing endpoint
   from §6)

## 8. Open items for `04`/`05`

- Visual flow builder: node/edge canvas (React Flow), compiling to the `flow_versions.spec`
  shape `flow_executor.py` already consumes — the backend is ready for this, no orchestrator
  changes needed, purely a frontend + flow-CRUD-UI project.
- Marketplace storefront: browse/detail/install-with-consent UI over the `marketplace.py`
  endpoints already built and tested.
- Realtime run updates (§6), once polling's rough edges (interval tuning, wasted requests on
  long-running flows) are actually felt rather than anticipated.
