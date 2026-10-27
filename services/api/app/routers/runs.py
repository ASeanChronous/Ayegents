from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_pool
from app.deps import get_current_user_id

router = APIRouter(prefix="/workspaces/{workspace_id}/runs", tags=["runs"])


class RunCreate(BaseModel):
    agent_id: UUID | None = None
    flow_id: UUID | None = None
    input: dict = {}


class RunOut(BaseModel):
    id: UUID
    status: str


class RunListItemOut(BaseModel):
    id: UUID
    status: str
    agent_id: UUID | None
    flow_id: UUID | None
    total_tokens: int
    created_at: datetime
    finished_at: datetime | None


class RunDetailOut(BaseModel):
    id: UUID
    status: str
    input: dict
    output: dict | None
    total_tokens: int
    # Always 0 today — no model-tier pricing table exists yet to compute this
    # from tokens (same gap as run_steps.cost_usd below). Exposed as-is
    # rather than hidden, since the column is real; just not meaningful yet.
    total_cost_usd: float
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


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
    cost_usd: float  # see RunDetailOut.total_cost_usd — always 0 today
    latency_ms: int | None
    status: str
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None


@router.get("", response_model=list[RunListItemOut])
async def list_runs(workspace_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    rows = await pool.fetch(
        """
        select id, status, agent_id, flow_id, total_tokens, created_at, finished_at
        from runs where workspace_id = $1
        order by created_at desc
        limit 100
        """,
        workspace_id,
    )
    return [RunListItemOut(**dict(r)) for r in rows]


@router.post("", response_model=RunOut, status_code=201)
async def create_run(
    workspace_id: UUID, body: RunCreate, user_id: UUID = Depends(get_current_user_id)
):
    if (body.agent_id is None) == (body.flow_id is None):
        raise HTTPException(status_code=400, detail="exactly one of agent_id or flow_id is required")

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            run = await conn.fetchrow(
                """
                insert into runs (workspace_id, agent_id, flow_id, input, status)
                values ($1, $2, $3, $4, 'queued')
                returning id, status
                """,
                workspace_id,
                body.agent_id,
                body.flow_id,
                body.input,
            )
            # Hand off to the orchestrator via the Postgres-native job queue
            # (SKIP LOCKED pattern, no Redis/pg-boss — see services/orchestrator).
            await conn.execute(
                "insert into run_queue (run_id) values ($1)", run["id"]
            )
    return RunOut(**dict(run))


@router.get("/{run_id}", response_model=RunDetailOut)
async def get_run(workspace_id: UUID, run_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        select id, status, input, output, total_tokens, total_cost_usd, error,
               started_at, finished_at, created_at
        from runs where id = $1 and workspace_id = $2
        """,
        run_id,
        workspace_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    return RunDetailOut(**dict(row))


@router.get("/{run_id}/steps", response_model=list[RunStepOut])
async def list_run_steps(workspace_id: UUID, run_id: UUID, user_id: UUID = Depends(get_current_user_id)):
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
