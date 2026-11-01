from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_pool
from app.deps import require_workspace_member

router = APIRouter(prefix="/workspaces/{workspace_id}/agents", tags=["agents"])


class AgentCreate(BaseModel):
    name: str
    description: str | None = None


class AgentOut(BaseModel):
    id: UUID
    name: str
    description: str | None
    status: str


@router.get("", response_model=list[AgentOut])
async def list_agents(workspace_id: UUID, user_id: UUID = Depends(require_workspace_member)):
    pool = await get_pool()
    rows = await pool.fetch(
        "select id, name, description, status from agents where workspace_id = $1 order by created_at desc",
        workspace_id,
    )
    return [AgentOut(**dict(r)) for r in rows]


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(workspace_id: UUID, agent_id: UUID, user_id: UUID = Depends(require_workspace_member)):
    pool = await get_pool()
    row = await pool.fetchrow(
        "select id, name, description, status from agents where id = $1 and workspace_id = $2",
        agent_id,
        workspace_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return AgentOut(**dict(row))


@router.post("", response_model=AgentOut, status_code=201)
async def create_agent(
    workspace_id: UUID, body: AgentCreate, user_id: UUID = Depends(require_workspace_member)
):
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        insert into agents (workspace_id, name, description, created_by)
        values ($1, $2, $3, $4)
        returning id, name, description, status
        """,
        workspace_id,
        body.name,
        body.description,
        user_id,
    )
    return AgentOut(**dict(row))
