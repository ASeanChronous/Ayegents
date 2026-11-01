from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_pool
from app.deps import require_workspace_member

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


class AttachedToolOut(BaseModel):
    tool_id: UUID
    key: str
    name: str
    kind: str
    config: dict


class AgentVersionDetailOut(BaseModel):
    id: UUID
    version: int
    system_prompt: str
    model_tier: str
    max_steps: int | None
    max_tokens: int | None
    created_at: datetime
    tools: list[AttachedToolOut]
    sub_agent_ids: list[UUID]


@router.get("", response_model=list[AgentVersionDetailOut])
async def list_agent_versions(
    workspace_id: UUID, agent_id: UUID, user_id: UUID = Depends(require_workspace_member)
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        agent = await conn.fetchrow(
            "select id from agents where id = $1 and workspace_id = $2", agent_id, workspace_id
        )
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")

        version_rows = await conn.fetch(
            """
            select id, version, system_prompt, model_tier, max_steps, max_tokens, created_at
            from agent_versions where agent_id = $1 order by version desc
            """,
            agent_id,
        )
        versions = []
        for v in version_rows:
            tool_rows = await conn.fetch(
                """
                select t.id as tool_id, t.key, t.name, t.kind, avt.config
                from agent_version_tools avt join tools t on t.id = avt.tool_id
                where avt.agent_version_id = $1
                """,
                v["id"],
            )
            sub_agent_rows = await conn.fetch(
                "select sub_agent_id from agent_version_subagents where agent_version_id = $1", v["id"]
            )
            versions.append(
                AgentVersionDetailOut(
                    **dict(v),
                    tools=[AttachedToolOut(**dict(t)) for t in tool_rows],
                    sub_agent_ids=[r["sub_agent_id"] for r in sub_agent_rows],
                )
            )
    return versions


async def _ensure_subagent_tool(conn, workspace_id: UUID, sub_agent_id: UUID) -> UUID:
    """Find-or-create the `kind = 'agent'` tool that makes a declared sub-agent
    actually callable -- closing the gap docs/03-frontend.md §7 step 4 flagged:
    `agent_version_subagents` alone only *records* the delegation, since
    tool_gateway.py's dispatch never reads that table, only `agent_version_tools`
    joined to a `tools` row of kind 'agent' carrying `config.sub_agent_id`. One
    tool row per (workspace, sub-agent) -- `tools`' own `unique(workspace_id,
    key)` constraint makes `key = f"agent-{sub_agent_id}"` double as the
    find-or-create lookup key, so publishing a second version that reuses the
    same sub-agent reuses the same tool row instead of creating a duplicate.

    The key uses a hyphen, not the colon a UUID-prefixed id might suggest --
    this key is sent to the real Anthropic API as the tool's `name`, which
    only allows `[a-zA-Z0-9_-]`.
    """
    key = f"agent-{sub_agent_id}"
    existing = await conn.fetchval(
        "select id from tools where workspace_id = $1 and key = $2", workspace_id, key
    )
    if existing is not None:
        return existing

    sub_agent = await conn.fetchrow("select name from agents where id = $1", sub_agent_id)
    tool = await conn.fetchrow(
        """
        insert into tools (workspace_id, key, name, kind, config_schema)
        values ($1, $2, $3, 'agent', $4)
        returning id
        """,
        workspace_id,
        key,
        f"Delegate a task to the '{sub_agent['name']}' agent",
        {
            "type": "object",
            "properties": {"message": {"type": "string", "description": "What to ask or tell the sub-agent"}},
            "required": ["message"],
        },
    )
    return tool["id"]


@router.post("", response_model=AgentVersionOut, status_code=201)
async def create_agent_version(
    workspace_id: UUID,
    agent_id: UUID,
    body: AgentVersionCreate,
    user_id: UUID = Depends(require_workspace_member),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        agent = await conn.fetchrow(
            "select id from agents where id = $1 and workspace_id = $2", agent_id, workspace_id
        )
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")

        for sub_agent_id in body.sub_agent_ids:
            if sub_agent_id == agent_id:
                raise HTTPException(status_code=400, detail="an agent can't delegate to itself")
            sub_agent = await conn.fetchrow(
                "select id from agents where id = $1 and workspace_id = $2", sub_agent_id, workspace_id
            )
            if sub_agent is None:
                raise HTTPException(
                    status_code=404, detail=f"sub-agent {sub_agent_id} not found in this workspace"
                )

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
                tool_id = await _ensure_subagent_tool(conn, workspace_id, sub_agent_id)
                await conn.execute(
                    "insert into agent_version_tools (agent_version_id, tool_id, config) values ($1, $2, $3)",
                    version_row["id"],
                    tool_id,
                    {"sub_agent_id": str(sub_agent_id)},
                )
                await conn.execute(
                    "insert into agent_version_subagents (agent_version_id, sub_agent_id) values ($1, $2)",
                    version_row["id"],
                    sub_agent_id,
                )
    return AgentVersionOut(**dict(version_row))
