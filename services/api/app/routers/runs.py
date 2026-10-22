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


@router.get("/{run_id}", response_model=RunOut)
async def get_run(workspace_id: UUID, run_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    row = await pool.fetchrow(
        "select id, status from runs where id = $1 and workspace_id = $2", run_id, workspace_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    return RunOut(**dict(row))
