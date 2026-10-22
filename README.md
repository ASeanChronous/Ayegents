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

Scaffold only — proves the shape of the MVP loop (create agent → enqueue run → orchestrator
executes a single-agent Claude tool-use loop → inspect `run_steps`). Not yet implemented:

- Tool dispatch (the agent loop stops at the first `tool_use` block — see the TODO in
  `services/orchestrator/app/executor.py`)
- Multi-agent flow execution via LangGraph (sequential/parallel/agent-as-tool)
- Auth (`services/api/app/deps.py` stubs the current-user lookup via a header instead of
  verifying a Supabase JWT)
- Marketplace install/fork flow, the visual flow builder UI

## Running locally

Requires **Python 3.11+** (this machine's default `python3` is 3.9 — install a newer one,
e.g. via `pyenv` or `uv`, before setting up the services below) and Node 20+ (already
satisfied here).

1. **Database**: create a Supabase project (or run `supabase start` locally with the CLI),
   then apply `supabase/migrations/0001_init_schema.sql`.
2. **Env**: copy `.env.example` to `.env` in the repo root, and to `services/api/.env` and
   `services/orchestrator/.env`, filling in `DATABASE_URL` and `ANTHROPIC_API_KEY`.
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
   python -m app.main
   ```
5. **Web**:
   ```bash
   npm install
   npm run dev --workspace=apps/web
   ```
