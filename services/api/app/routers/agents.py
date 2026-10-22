from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_pool
from app.deps import get_current_user_id

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
async def list_agents(workspace_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    rows = await pool.fetch(
        "select id, name, description, status from agents where workspace_id = $1 order by created_at desc",
        workspace_id,
    )
    return [AgentOut(**dict(r)) for r in rows]


@router.post("", response_model=AgentOut, status_code=201)
async def create_agent(
    workspace_id: UUID, body: AgentCreate, user_id: UUID = Depends(get_current_user_id)
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
