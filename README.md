# Agent Ecosystem

Build, run, and discover AI agents — no-code builder, multi-agent orchestration, marketplace.

Design docs: [`docs/01-architecture.md`](docs/01-architecture.md), [`docs/02-schema.md`](docs/02-schema.md).

## Layout

```
apps/web/            Next.js frontend — builder UI, marketplace, run inspector
services/api/         FastAPI — CRUD for agents/flows/runs/listings, enqueues runs
services/orchestrator/ Polls the Postgres job queue, runs the Claude tool-use loop per agent
supabase/migrations/   Schema + RLS, matches docs/02-schema.md exactly
```

One Postgres-native job queue (`run_queue`, `SELECT ... FOR UPDATE SKIP LOCKED`) connects
the API and the orchestrator — deliberately not pg-boss (Node-only client) or Redis, per
the cost-efficiency stack in `docs/01-architecture.md` §9.

## Status

Scaffold only — proves the shape of the MVP loop: create an agent, create a version with a
system prompt + attached tools, enqueue a run, the orchestrator executes the Claude
tool-use loop and dispatches `builtin`/`http`/`agent` tool calls through the Tool Gateway,
every model/tool call lands in `run_steps`. Not yet implemented:

- `code_exec` tools (stubbed in `tool_gateway.py` — needs a sandboxed runner, e.g. E2B)
- Egress allow-listing for `http` tools (any URL in a tool's config is called as-is)
- Multi-agent *flow* execution via LangGraph (sequential/parallel fan-out) — agent-as-tool
  works today, but only as a direct call, not as a node in a larger flow graph
- Auth (`services/api/app/deps.py` stubs the current-user lookup via a header instead of
  verifying a Supabase JWT)
- Marketplace install/fork flow, the visual flow builder UI

## Running locally

Requires **Python 3.11+** (this machine's default `python3` is 3.9 — install a newer one,
e.g. via `pyenv` or `uv`, before setting up the services below) and Node 20+ (already
satisfied here).

1. **Database**: create a Supabase project (or run `supabase start` locally with the CLI),
   then apply `supabase/migrations/0001_init_schema.sql` and `0002_seed_builtin_tools.sql` in order.
2. **Env**: copy `.env.example` to `services/api/.env` and `services/orchestrator/.env`,
   filling in `DATABASE_URL` and `ANTHROPIC_API_KEY`. Generate `ENCRYPTION_KEY` once (see the
   comment in `.env.example`) and use the *same* value in both files — the API encrypts tool
   credentials with it, the orchestrator decrypts them.
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
