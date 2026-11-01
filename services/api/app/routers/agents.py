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
async def list_agents(
    workspace_id: UUID,
    include_archived: bool = False,
    user_id: UUID = Depends(require_workspace_member),
):
    # Every picker in this app that offers "which agent" (the run start drawer, a flow
    # canvas's agent node, the agent-as-tool sub-agent checklist, the marketplace publish
    # source picker) reuses this exact endpoint rather than having its own query -- so
    # excluding archived by default here is the entire fix for keeping an archived agent
    # out of every one of those, with nothing to change at each call site. The one caller
    # that needs archived agents back (this list's own "Show archived" toggle, to unarchive
    # one) passes include_archived=true explicitly.
    pool = await get_pool()
    rows = await pool.fetch(
        """
        select id, name, description, status from agents
        where workspace_id = $1 and ($2 or status != 'archived')
        order by created_at desc
        """,
        workspace_id,
        include_archived,
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


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(
    workspace_id: UUID, agent_id: UUID, body: AgentUpdate, user_id: UUID = Depends(require_workspace_member)
):
    """Rename/re-describe an agent, or archive/unarchive it -- the `status`
    column (publish_status: draft/published/archived) has existed since the first
    migration but nothing ever wrote to it after creation. Archiving doesn't delete
    anything (agent_versions, run_steps, and any listing snapshot all still reference
    this row) -- same "archived, never deleted" posture a marketplace listing's own
    unpublish already uses, for the same reason: history keeps referencing it.
    """
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")
    if "status" in updates and updates["status"] not in ("draft", "published", "archived"):
        raise HTTPException(status_code=400, detail="status must be one of: draft, published, archived")

    pool = await get_pool()
    set_clauses = [f"{key} = ${i}" for i, key in enumerate(updates, start=1)]
    row = await pool.fetchrow(
        f"""
        update agents set {", ".join(set_clauses)}, updated_at = now()
        where id = ${len(updates) + 1} and workspace_id = ${len(updates) + 2}
        returning id, name, description, status
        """,
        *updates.values(),
        agent_id,
        workspace_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return AgentOut(**dict(row))
