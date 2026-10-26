"""Marketplace publish (listing creation) and install (fork into another
workspace) flow — architecture doc §6.

Scoped to **agent listings only** for this version, not flows: a flow's
spec would reference agent nodes by agent_id, and installing it would need
to recursively fork every agent it depends on too. That's real additional
work, not an oversight — publishing a flow fails with a clear 400 rather
than silently producing a broken install.

Two other narrowing validations at publish time, both for the same reason
(a listing has to be installable by a workspace that has never seen the
publisher's private setup):
- every tool the agent uses must be a *global* tool (`tools.workspace_id is
  null`) — a workspace-private tool can't be resolved by RLS from another
  workspace, so a listing referencing one could never actually install.
- the agent can't have agent-as-tool sub-agents attached — installing a
  listing would need to recursively install each sub-agent too, and (unlike
  the flow case) there's no listing for them to install *from*. Left for a
  future version.

required_tool_categories (the OAuth-consent-style list the installer must
approve, architecture doc §6) is *derived* from the agent's actually
attached tools at publish time (`"{kind}:{key}"` per tool), not
self-declared by the publisher — self-declaration could understate what the
agent actually uses; derivation can't.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_pool
from app.deps import get_current_user_id

listings_router = APIRouter(prefix="/workspaces/{workspace_id}/listings", tags=["marketplace"])
install_router = APIRouter(prefix="/workspaces/{workspace_id}/marketplace", tags=["marketplace"])


class ListingCreate(BaseModel):
    source_agent_id: UUID
    name: str
    description: str | None = None
    category: str | None = None


class ListingOut(BaseModel):
    id: UUID
    name: str
    status: str


@listings_router.post("", response_model=ListingOut, status_code=201)
async def create_listing(workspace_id: UUID, body: ListingCreate, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        agent = await conn.fetchrow(
            "select id from agents where id = $1 and workspace_id = $2", body.source_agent_id, workspace_id
        )
        if agent is None:
            raise HTTPException(status_code=404, detail="source agent not found in this workspace")

        row = await conn.fetchrow(
            """
            insert into listings (workspace_id, source_agent_id, name, description, category)
            values ($1, $2, $3, $4, $5)
            returning id, name, status
            """,
            workspace_id,
            body.source_agent_id,
            body.name,
            body.description,
            body.category,
        )
    return ListingOut(**dict(row))


class ListingVersionOut(BaseModel):
    id: UUID
    version: int
    required_tool_categories: list[str]


@listings_router.post("/{listing_id}/versions", response_model=ListingVersionOut, status_code=201)
async def publish_listing_version(
    workspace_id: UUID, listing_id: UUID, user_id: UUID = Depends(get_current_user_id)
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        listing = await conn.fetchrow(
            "select * from listings where id = $1 and workspace_id = $2", listing_id, workspace_id
        )
        if listing is None:
            raise HTTPException(status_code=404, detail="listing not found")
        if listing["source_flow_id"] is not None:
            raise HTTPException(
                status_code=400, detail="flow listings are not supported yet — only agent listings can be published"
            )

        agent_version = await conn.fetchrow(
            "select * from agent_versions where agent_id = $1 order by version desc limit 1",
            listing["source_agent_id"],
        )
        if agent_version is None:
            raise HTTPException(status_code=400, detail="the source agent has no version to publish")

        has_subagents = await conn.fetchval(
            "select exists(select 1 from agent_version_subagents where agent_version_id = $1)",
            agent_version["id"],
        )
        if has_subagents:
            raise HTTPException(
                status_code=400,
                detail="agents that delegate to sub-agents (agent-as-tool) can't be published as a listing yet",
            )

        tool_rows = await conn.fetch(
            """
            select t.key, t.kind, t.workspace_id, avt.config
            from agent_version_tools avt
            join tools t on t.id = avt.tool_id
            where avt.agent_version_id = $1
            """,
            agent_version["id"],
        )
        private_tools = [t["key"] for t in tool_rows if t["workspace_id"] is not None]
        if private_tools:
            raise HTTPException(
                status_code=400,
                detail=(
                    "agent uses workspace-private tools that another workspace couldn't install "
                    f"({', '.join(private_tools)}) — only global tools can appear in a listing"
                ),
            )

        spec = {
            "type": "agent",
            "system_prompt": agent_version["system_prompt"],
            "model_tier": agent_version["model_tier"],
            "max_steps": agent_version["max_steps"],
            "max_tokens": agent_version["max_tokens"],
            "tools": [{"key": t["key"], "kind": t["kind"], "config": t["config"]} for t in tool_rows],
        }
        required_tool_categories = sorted({f"{t['kind']}:{t['key']}" for t in tool_rows})

        async with conn.transaction():
            next_version = await conn.fetchval(
                "select coalesce(max(version), 0) + 1 from listing_versions where listing_id = $1", listing_id
            )
            version_row = await conn.fetchrow(
                """
                insert into listing_versions (listing_id, version, spec)
                values ($1, $2, $3)
                returning id, version
                """,
                listing_id,
                next_version,
                spec,
            )
            await conn.execute(
                "update listings set status = 'published', required_tool_categories = $2, updated_at = now() where id = $1",
                listing_id,
                required_tool_categories,
            )
    return ListingVersionOut(id=version_row["id"], version=version_row["version"], required_tool_categories=required_tool_categories)


class InstallCreate(BaseModel):
    listing_id: UUID
    # OAuth-consent-style approval, architecture doc §6: must cover every
    # category the listing's required_tool_categories declares, or the
    # install is refused outright rather than silently installing a subset.
    approved_tool_categories: list[str] = []


class InstallOut(BaseModel):
    id: UUID
    installed_agent_id: UUID


@install_router.post("/installs", response_model=InstallOut, status_code=201)
async def install_listing(workspace_id: UUID, body: InstallCreate, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        listing = await conn.fetchrow(
            "select * from listings where id = $1 and status = 'published'", body.listing_id
        )
        if listing is None:
            raise HTTPException(status_code=404, detail="listing not found or not published")

        listing_version = await conn.fetchrow(
            "select * from listing_versions where listing_id = $1 order by version desc limit 1", listing["id"]
        )
        if listing_version is None:
            raise HTTPException(status_code=409, detail="listing has no published version")

        missing = set(listing["required_tool_categories"]) - set(body.approved_tool_categories)
        if missing:
            raise HTTPException(
                status_code=403,
                detail=f"must approve all required tool categories before installing: {sorted(missing)}",
            )

        spec = listing_version["spec"]
        if spec.get("type") != "agent":
            raise HTTPException(status_code=400, detail=f"unsupported listing spec type: {spec.get('type')!r}")

        async with conn.transaction():
            agent = await conn.fetchrow(
                """
                insert into agents (workspace_id, name, description, created_by)
                values ($1, $2, $3, $4)
                returning id
                """,
                workspace_id,
                listing["name"],
                listing["description"],
                user_id,
            )
            agent_version = await conn.fetchrow(
                """
                insert into agent_versions (agent_id, version, system_prompt, model_tier, max_steps, max_tokens)
                values ($1, 1, $2, $3, $4, $5)
                returning id
                """,
                agent["id"],
                spec["system_prompt"],
                spec["model_tier"],
                spec.get("max_steps"),
                spec.get("max_tokens"),
            )
            for tool_spec in spec.get("tools", []):
                tool_row = await conn.fetchrow(
                    "select id from tools where workspace_id is null and key = $1 and kind = $2",
                    tool_spec["key"],
                    tool_spec["kind"],
                )
                if tool_row is None:
                    # Raising inside this transaction rolls back the agent/version
                    # insert above too -- never leave a half-installed agent behind.
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"listing references {tool_spec['kind']}:{tool_spec['key']}, which is not available "
                            "as a global tool on this platform"
                        ),
                    )
                await conn.execute(
                    "insert into agent_version_tools (agent_version_id, tool_id, config) values ($1, $2, $3)",
                    agent_version["id"],
                    tool_row["id"],
                    tool_spec["config"],
                )

            install = await conn.fetchrow(
                """
                insert into installs
                    (listing_id, listing_version_id, installing_workspace_id, installed_agent_id,
                     approved_tool_categories, installed_by)
                values ($1, $2, $3, $4, $5, $6)
                returning id
                """,
                listing["id"],
                listing_version["id"],
                workspace_id,
                agent["id"],
                body.approved_tool_categories,
                user_id,
            )
            await conn.execute("update listings set install_count = install_count + 1 where id = $1", listing["id"])

    return InstallOut(id=install["id"], installed_agent_id=agent["id"])
