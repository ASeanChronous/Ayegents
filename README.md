# Agent Ecosystem

Build, run, and discover AI agents — no-code builder, multi-agent orchestration, marketplace.

Design docs: [`docs/01-architecture.md`](docs/01-architecture.md), [`docs/02-schema.md`](docs/02-schema.md).

## Layout

```
apps/web/            Next.js frontend — builder UI, marketplace, run inspector
services/api/         FastAPI — CRUD for agents/flows/runs/listings, enqueues runs
services/orchestrator/ Polls the Postgres job queue, runs single-agent and multi-agent-flow runs
supabase/migrations/   Schema + RLS, matches docs/02-schema.md exactly
```

One Postgres-native job queue (`run_queue`, `SELECT ... FOR UPDATE SKIP LOCKED`) connects
the API and the orchestrator — deliberately not pg-boss (Node-only client) or Redis, per
the cost-efficiency stack in `docs/01-architecture.md` §9.

## Status

Scaffold only — proves the shape of the MVP loop (single-agent runs, tool dispatch) and V1's
multi-agent flows. Both are tested against a real Postgres + mocked Claude responses (no
`ANTHROPIC_API_KEY` is available in this dev environment):

- **Single-agent**: create an agent, create a version with a system prompt + attached
  tools, enqueue a run, the orchestrator executes the Claude tool-use loop and dispatches
  `builtin`/`http`/`code_exec`/`agent` tool calls through the Tool Gateway, every model/tool
  call lands in `run_steps`.
- **Multi-agent flows** (`flow_executor.py`, built on LangGraph): a flow spec's nodes/edges
  compile into a graph supporting sequential handoff, parallel fan-out/fan-in (each
  concurrent branch gets its own pooled DB connection — a single connection can't serve two
  queries at once, found by testing a real concurrent fan-out, not by inspection), and
  conditional branching (a tiny `"<node_id>.output.<key>"` condition language, including a
  fallback that parses an agent's JSON-formatted reply when the condition needs to read it).
- **Egress allow-listing** (`tools.allowed_hosts`, migration `0004`): set at tool-creation
  time (`POST /workspaces/{id}/tools`), not in the per-agent config that supplies the actual
  URL — that distinction matters once marketplace installs exist, since config is exactly
  what an untrusted listing would control. `http` tools get an application-level hostname
  check (exact match or `*.example.com` wildcard); `code_exec` tools pass the same host list
  to E2B's own sandbox firewall (`network.allow_out`), enforced by E2B's infrastructure, not
  by this code. None/empty stays unrestricted (unchanged default for every existing tool).
- **Auth** (`app/auth.py`): every request verifies its bearer token by calling the configured
  Supabase project's own `GET /auth/v1/user` and trusting a 200 response — this works
  regardless of whether the project signs tokens with the legacy HS256 shared secret or a
  newer asymmetric key, without this app needing to implement either signature scheme or
  JWKS fetching/caching itself. The user id comes from the token's own `sub` claim (read
  locally, unverified — safe only because trust comes from the remote 200, not from this
  read). Tested against a stand-in HTTP server implementing that same 200/non-200 contract,
  confirmed end to end that the identity attached to a created workspace exactly matches the
  JWT's `sub`, not just that requests were accepted.
- **Marketplace publish/install** (`marketplace.py`): scoped to agent listings only, not
  flows — a flow's spec references agent nodes by id, and installing one would need to
  recursively fork every agent it depends on, real additional work rather than an oversight.
  Publishing snapshots the agent's current version's spec into an immutable
  `listing_versions.spec`, deriving `required_tool_categories` (`"{kind}:{key}"` per attached
  tool) from what the agent actually uses rather than letting the publisher self-declare it —
  self-declaration could understate the real footprint, derivation can't. Two things fail
  publishing outright rather than producing a listing that can't actually install: a
  workspace-private tool (unresolvable by another workspace's RLS) or an agent-as-tool
  sub-agent (would need its own recursive install, not yet supported). Installing is a real
  OAuth-consent gate, not just a formality — refuses with 403 if the installer's approved
  categories don't cover every required one — then forks a brand-new agent + version +
  tool-attachments into the installing workspace, re-resolving each tool by key against that
  workspace's own global tools rather than copying a tool_id that wouldn't resolve under RLS.

Not yet implemented:

- Flow checkpointing/resume — a flow run executes as one in-memory LangGraph `ainvoke` call;
  an orchestrator crash mid-flow loses the run, same as every other run in this scaffold
- A richer condition language (comparisons, arithmetic) — today's is lookup + truthiness only
- Flow listings and agent-as-tool listings (see marketplace.py's module docstring)
- The visual flow builder UI and marketplace storefront UI — `apps/web` is still a placeholder

## Running locally

Requires **Python 3.11+** (this machine's default `python3` is 3.9 — install a newer one,
e.g. via `pyenv` or `uv`, before setting up the services below) and Node 20+ (already
satisfied here).

1. **Database**: create a Supabase project (or run `supabase start` locally with the CLI),
   then apply the `supabase/migrations/*.sql` files in order (`0001` through `0004`).
2. **Env**: copy `.env.example` to `services/api/.env` and `services/orchestrator/.env`,
   filling in `DATABASE_URL` and `ANTHROPIC_API_KEY`. Generate `ENCRYPTION_KEY` once (see the
   comment in `.env.example`) and use the *same* value in both files — the API encrypts tool
   credentials with it, the orchestrator decrypts them. `E2B_API_KEY` (orchestrator only) is
   needed only if an agent has the `run_python` code_exec tool attached. `SUPABASE_URL` and
   `SUPABASE_ANON_KEY` (API only) are required — every request is verified against that
   project's own Auth server (`app/auth.py`), so callers need a real Supabase user access
   token (`Authorization: Bearer <token>`), not an arbitrary header. `CORS_ALLOWED_ORIGINS`
   (API only) defaults to the local Next.js dev server; the browser rejects every API call
   outright if this doesn't list wherever `apps/web` is actually running.
3. **API**:
   ```bash
   cd services/api && python3.11 -m venv .venv && source .venv/bin/activate
   pip install -e ".[dev]"
   uvicorn app.main:app --reload
   ```
4. **Orchestrator**:
   ```bash
   cd services/orchestrator && python3.11 -m venv .venv && source .venv/bin/activate
   pip install -e ".[dev]"
   python -m orchestrator_app.main
   ```
5. **Web**: copy `.env.example` to `apps/web/.env.local` too, filling in
   `NEXT_PUBLIC_SUPABASE_URL`/`NEXT_PUBLIC_SUPABASE_ANON_KEY` (same Supabase project as
   above) and `NEXT_PUBLIC_API_URL`. With the API running, regenerate the typed client
   once (and again whenever the API's routes/schemas change):
   ```bash
   npm install
   npm run gen:api --workspace=apps/web   # needs the API reachable at NEXT_PUBLIC_API_URL
   npm run dev --workspace=apps/web
   ```
