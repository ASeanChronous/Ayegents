import asyncio
import json
from datetime import datetime
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import settings
from app.db import get_pool
from app.deps import require_workspace_member
from app.routers.flows import FlowSpec

router = APIRouter(prefix="/workspaces/{workspace_id}/runs", tags=["runs"])

# Mirrors the frontend's own TERMINAL_STATUSES (apps/web/app/(app)/runs/[id]/page.tsx)
# -- once a run reaches one of these there's nothing further to notify about,
# so the SSE stream below closes instead of idling forever.
_TERMINAL_STATUSES = {"succeeded", "failed", "budget_exceeded"}


class RunCreate(BaseModel):
    agent_id: UUID | None = None
    flow_id: UUID | None = None
    input: dict = {}
    # A true draft dry-run (docs/04-frontend-builder.md §10): the flow canvas's
    # current, possibly-never-saved nodes/edges, run exactly as-is instead of
    # whatever flow_versions currently holds for flow_id. Flow-only -- an
    # agent has no separate "unsaved spec" of its own to dry-run the same way.
    draft_spec: FlowSpec | None = None


class RunOut(BaseModel):
    id: UUID
    status: str


class RunListItemOut(BaseModel):
    id: UUID
    status: str
    agent_id: UUID | None
    flow_id: UUID | None
    is_draft: bool
    total_tokens: int
    total_cost_usd: float
    created_at: datetime
    finished_at: datetime | None


class RunDetailOut(BaseModel):
    id: UUID
    status: str
    input: dict
    output: dict | None
    total_tokens: int
    # Computed from each agent_call step's cost_usd (run_steps), which the
    # orchestrator derives from orchestrator_app/model_router.py's
    # per-tier $/token table -- a list-price estimate (no prompt-caching
    # discount applied), not a billing-accurate figure.
    total_cost_usd: float
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    is_draft: bool


class RunStepOut(BaseModel):
    id: UUID
    step_index: int
    kind: str
    node_id: str | None
    agent_id: UUID | None
    tool_id: UUID | None
    input: dict | None
    output: dict | None
    tokens_input: int
    tokens_output: int
    cost_usd: float  # see RunDetailOut.total_cost_usd for how this is derived
    latency_ms: int | None
    status: str
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None


@router.get("", response_model=list[RunListItemOut])
async def list_runs(workspace_id: UUID, user_id: UUID = Depends(require_workspace_member)):
    pool = await get_pool()
    rows = await pool.fetch(
        """
        select id, status, agent_id, flow_id, draft_spec is not null as is_draft,
               total_tokens, total_cost_usd, created_at, finished_at
        from runs where workspace_id = $1
        order by created_at desc
        limit 100
        """,
        workspace_id,
    )
    return [RunListItemOut(**dict(r)) for r in rows]


@router.post("", response_model=RunOut, status_code=201)
async def create_run(
    workspace_id: UUID, body: RunCreate, user_id: UUID = Depends(require_workspace_member)
):
    if (body.agent_id is None) == (body.flow_id is None):
        raise HTTPException(status_code=400, detail="exactly one of agent_id or flow_id is required")
    if body.draft_spec is not None and body.agent_id is not None:
        raise HTTPException(status_code=400, detail="draft_spec can only be used with flow_id, not agent_id")

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # plan_tier_limits.max_runs_per_day was declared since the first migration and
            # never checked anywhere -- under the platform-subsidized flat-tier cost model
            # (architecture doc §9a/§12.2: the platform pays the Anthropic bill, not the
            # workspace), this is the one budget that bounds total daily spend rather than
            # a single run's, so leaving it unchecked meant a workspace could create an
            # unbounded number of runs in a day with nothing stopping it short of whatever
            # the other four budgets cost per run. Rolling 24h window, not calendar day --
            # simpler than tracking a reset boundary and matches what "N runs per day"
            # actually protects against (sustained volume), not a specific wall-clock date.
            run_count = await conn.fetchval(
                """
                select count(*) from runs
                where workspace_id = $1 and created_at > now() - interval '24 hours'
                """,
                workspace_id,
            )
            limit = await conn.fetchval(
                """
                select l.max_runs_per_day from plan_tier_limits l
                join workspaces w on w.plan_tier = l.plan_tier
                where w.id = $1
                """,
                workspace_id,
            )
            if run_count >= limit:
                raise HTTPException(
                    status_code=429,
                    detail=f"this workspace's plan allows {limit} runs per day — try again later or upgrade",
                )

            run = await conn.fetchrow(
                """
                insert into runs (workspace_id, agent_id, flow_id, input, draft_spec, status)
                values ($1, $2, $3, $4, $5, 'queued')
                returning id, status
                """,
                workspace_id,
                body.agent_id,
                body.flow_id,
                body.input,
                body.draft_spec.model_dump(mode="json", by_alias=True) if body.draft_spec else None,
            )
            # Hand off to the orchestrator via the Postgres-native job queue
            # (SKIP LOCKED pattern, no Redis/pg-boss — see services/orchestrator).
            await conn.execute(
                "insert into run_queue (run_id) values ($1)", run["id"]
            )
    return RunOut(**dict(run))


@router.get("/{run_id}", response_model=RunDetailOut)
async def get_run(workspace_id: UUID, run_id: UUID, user_id: UUID = Depends(require_workspace_member)):
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        select id, status, input, output, total_tokens, total_cost_usd, error,
               started_at, finished_at, created_at, draft_spec is not null as is_draft
        from runs where id = $1 and workspace_id = $2
        """,
        run_id,
        workspace_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    return RunDetailOut(**dict(row))


@router.get("/{run_id}/events")
async def stream_run_events(workspace_id: UUID, run_id: UUID, user_id: UUID = Depends(require_workspace_member)):
    """Server-Sent Events replacement for the run inspector's old fixed-interval
    polling (docs/03-frontend.md §6), backed by the orchestrator's Postgres
    NOTIFY on the 'run_events' channel (services/orchestrator/orchestrator_app/
    notify.py) rather than Supabase's own hosted Realtime service -- same
    postgres-native choice this project already made for run_queue instead of
    pg-boss/Redis, and it works against the plain local Postgres this project
    runs in dev without a separate Realtime server to stand up.

    Events carry only a status string, never the full run/steps payload --
    the client re-fetches through the normal GET endpoints on every event, so
    this stream's only job is "something changed, go look," not re-deriving
    those endpoints' serialization here too.
    """
    pool = await get_pool()
    run = await pool.fetchrow("select status from runs where id = $1 and workspace_id = $2", run_id, workspace_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    async def event_stream():
        # A dedicated connection outside the pool -- LISTEN holds the
        # connection open for as long as this subscription lives, which the
        # pool (shared, short-lived checkouts) isn't meant for.
        conn = await asyncpg.connect(settings.database_url)
        queue: asyncio.Queue[None] = asyncio.Queue()

        def _on_notify(_connection, _pid, _channel, payload: str) -> None:
            if payload == str(run_id):
                queue.put_nowait(None)

        await conn.add_listener("run_events", _on_notify)
        try:
            status = await conn.fetchval("select status from runs where id = $1", run_id)
            yield f"event: update\ndata: {json.dumps({'status': status})}\n\n"
            while status not in _TERMINAL_STATUSES:
                try:
                    await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    # Idle proxies/load balancers can drop a connection with
                    # no traffic on it for too long -- this comment line is
                    # ignored by every SSE reader but keeps bytes flowing.
                    yield ": keepalive\n\n"
                    continue
                status = await conn.fetchval("select status from runs where id = $1", run_id)
                yield f"event: update\ndata: {json.dumps({'status': status})}\n\n"
        finally:
            await conn.remove_listener("run_events", _on_notify)
            await conn.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/{run_id}/steps", response_model=list[RunStepOut])
async def list_run_steps(workspace_id: UUID, run_id: UUID, user_id: UUID = Depends(require_workspace_member)):
    pool = await get_pool()
    # Confirm the run belongs to this workspace before listing its steps —
    # run_steps has no workspace_id column of its own (see docs/02-schema.md
    # §4), so this check is the only thing standing between "my workspace's
    # run" and "any run_id a caller happens to guess."
    run = await pool.fetchrow("select id from runs where id = $1 and workspace_id = $2", run_id, workspace_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    rows = await pool.fetch(
        """
        select id, step_index, kind, node_id, agent_id, tool_id, input, output,
               tokens_input, tokens_output, cost_usd, latency_ms, status, error,
               started_at, finished_at
        from run_steps
        where run_id = $1
        order by step_index
        """,
        run_id,
    )
    return [RunStepOut(**dict(r)) for r in rows]
