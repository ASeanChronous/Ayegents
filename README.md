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

Not yet implemented:

- Flow checkpointing/resume — a flow run executes as one in-memory LangGraph `ainvoke` call;
  an orchestrator crash mid-flow loses the run, same as every other run in this scaffold
- A richer condition language (comparisons, arithmetic) — today's is lookup + truthiness only
- Auth (`services/api/app/deps.py` stubs the current-user lookup via a header instead of
  verifying a Supabase JWT)
- Marketplace install/fork flow, the visual flow builder UI

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
   needed only if an agent has the `run_python` code_exec tool attached.
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
5. **Web**:
   ```bash
   npm install
   npm run dev --workspace=apps/web
   ```
