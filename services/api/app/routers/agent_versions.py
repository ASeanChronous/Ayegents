from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_pool
from app.deps import get_current_user_id

router = APIRouter(prefix="/workspaces/{workspace_id}/agents/{agent_id}/versions", tags=["agents"])


class ToolAttachment(BaseModel):
    tool_id: UUID
    config: dict = {}


class AgentVersionCreate(BaseModel):
    system_prompt: str
    model_tier: str = "sonnet"
    max_steps: int | None = None
    max_tokens: int | None = None
    tools: list[ToolAttachment] = []
    sub_agent_ids: list[UUID] = []


class AgentVersionOut(BaseModel):
    id: UUID
    version: int
    model_tier: str


@router.post("", response_model=AgentVersionOut, status_code=201)
async def create_agent_version(
    workspace_id: UUID,
    agent_id: UUID,
    body: AgentVersionCreate,
    user_id: UUID = Depends(get_current_user_id),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        agent = await conn.fetchrow(
            "select id from agents where id = $1 and workspace_id = $2", agent_id, workspace_id
        )
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")

        async with conn.transaction():
            next_version = await conn.fetchval(
                "select coalesce(max(version), 0) + 1 from agent_versions where agent_id = $1",
                agent_id,
            )
            version_row = await conn.fetchrow(
                """
                insert into agent_versions (agent_id, version, system_prompt, model_tier, max_steps, max_tokens)
                values ($1, $2, $3, $4, $5, $6)
                returning id, version, model_tier
                """,
                agent_id,
                next_version,
                body.system_prompt,
                body.model_tier,
                body.max_steps,
                body.max_tokens,
            )
            for tool in body.tools:
                await conn.execute(
                    "insert into agent_version_tools (agent_version_id, tool_id, config) values ($1, $2, $3)",
                    version_row["id"],
                    tool.tool_id,
                    tool.config,
                )
            for sub_agent_id in body.sub_agent_ids:
                await conn.execute(
                    "insert into agent_version_subagents (agent_version_id, sub_agent_id) values ($1, $2)",
                    version_row["id"],
                    sub_agent_id,
                )
    return AgentVersionOut(**dict(version_row))
