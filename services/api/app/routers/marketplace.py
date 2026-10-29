"""Marketplace publish (listing creation) and install (fork into another
workspace) flow — architecture doc §6.

Supports both listing kinds the schema already models (`listings.source_agent_id`
xor `source_flow_id`): a plain agent listing, and a flow listing, which snapshots
every agent and global tool the flow's nodes reference so an install can
recursively fork them all into the installing workspace without the installer
ever having installed those agents separately first.

Three narrowing validations at publish time, all for the same reason (a listing
has to be installable by a workspace that has never seen the publisher's private
setup):
- every tool referenced — directly by a flow's own tool nodes, or indirectly via
  an agent's attached tools — must be a *global* tool (`tools.workspace_id is
  null`). A workspace-private tool can't be resolved by RLS from another
  workspace, so a listing referencing one could never actually install.
- no agent referenced — the listing's own source agent, or any agent a flow
  node points at — can have agent-as-tool sub-agents attached. Installing one
  would need to recursively install each sub-agent too, and (unlike the
  flow-listing case itself) there's no listing for a bare sub-agent to install
  *from*. Left for a future version.
- (flow listings only) every node id the flow's trigger/conditional/merge logic
  depends on is irrelevant here — only `agent`/`tool` nodes carry a foreign
  reference, so those are the only two node types validated.

required_tool_categories (the OAuth-consent-style list the installer must
approve, architecture doc §6) is *derived* from what's actually referenced at
publish time (`"{kind}:{key}"` per tool, unioned across every agent and tool
node for a flow listing), not self-declared by the publisher — self-declaration
could understate what's actually used; derivation can't.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.db import get_pool
from app.deps import get_current_user_id

listings_router = APIRouter(prefix="/workspaces/{workspace_id}/listings", tags=["marketplace"])
install_router = APIRouter(prefix="/workspaces/{workspace_id}/marketplace", tags=["marketplace"])


class ListingCreate(BaseModel):
    source_agent_id: UUID | None = None
    source_flow_id: UUID | None = None
    name: str
    description: str | None = None
    category: str | None = None
    # 0 (the default) is a free listing -- installable directly, no Checkout
    # involved (routers/payments.py). currency is fixed at 'usd' for now, not
    # exposed as a field here: multi-currency pricing is a real product
    # decision (which currencies, how a buyer's Checkout picks one) this pass
    # doesn't make, not an oversight.
    price_cents: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "ListingCreate":
        if (self.source_agent_id is None) == (self.source_flow_id is None):
            raise ValueError("exactly one of source_agent_id or source_flow_id is required")
        return self


class ListingOut(BaseModel):
    id: UUID
    name: str
    status: str


class WorkspaceListingOut(BaseModel):
    id: UUID
    kind: str
    name: str
    description: str | None
    category: str | None
    status: str
    install_count: int
    rating_avg: float | None
    rating_count: int
    required_tool_categories: list[str]
    price_cents: int
    currency: str


# Same one-word "kind" listings.py's public browse derives (source_agent_id/
# source_flow_id is a storage-level xor, 0001_init_schema.sql's check
# constraint) -- duplicated here rather than imported so each router stays
# self-contained, matching every other router in this project.
_KIND_SQL = "case when source_agent_id is not null then 'agent' else 'flow' end as kind"

# Shared by every owner's-eye endpoint that returns a WorkspaceListingOut
# (list/update/unpublish) so the column list only has to change in one place.
_WORKSPACE_LISTING_COLUMNS = (
    f"id, {_KIND_SQL}, name, description, category, status, install_count, rating_avg, rating_count, "
    "required_tool_categories, price_cents, currency"
)


@listings_router.get("", response_model=list[WorkspaceListingOut])
async def list_my_listings(workspace_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    # The public GET /marketplace/listings (listings.py) only ever shows
    # status = 'published' rows -- by design, an unpublished listing has
    # nothing to browse (no spec, no required_tool_categories yet). This is
    # the owner's-eye view instead: every status, so a draft listing (created
    # but never published, or publish-time validation failed) is visible to
    # the workspace that owns it rather than silently invisible everywhere.
    pool = await get_pool()
    rows = await pool.fetch(
        f"select {_WORKSPACE_LISTING_COLUMNS} from listings where workspace_id = $1 order by created_at desc",
        workspace_id,
    )
    return [WorkspaceListingOut(**dict(r)) for r in rows]


class ListingUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    price_cents: int | None = Field(default=None, ge=0)


async def _require_payouts_enabled(conn, workspace_id: UUID) -> None:
    """Gates giving a listing a nonzero price (routers/payments.py's own docstring has
    the full Connect design) on the publishing workspace having finished Stripe Connect
    onboarding -- never publish a priced listing with nowhere for its share of a sale to
    go. Called from both create_listing and update_listing below, whichever one is about
    to set price_cents > 0.
    """
    payouts_enabled = await conn.fetchval(
        "select stripe_connect_payouts_enabled from workspaces where id = $1", workspace_id
    )
    if not payouts_enabled:
        raise HTTPException(
            status_code=400,
            detail="connect a payout account before pricing a listing (Workspace settings → Payouts)",
        )


@listings_router.patch("/{listing_id}", response_model=WorkspaceListingOut)
async def update_listing(
    workspace_id: UUID, listing_id: UUID, body: ListingUpdate, user_id: UUID = Depends(get_current_user_id)
):
    """Edit a listing's name/description/category -- the one thing publishing
    a new version (POST .../versions) was never meant to cover, since that
    re-snapshots the *source* agent/flow and has nothing to do with the
    listing's own metadata. Partial: only fields actually present in the
    request body are touched (model_dump(exclude_unset=True)), so passing
    description without name leaves name alone rather than clearing it.
    """
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")

    pool = await get_pool()
    async with pool.acquire() as conn:
        listing = await conn.fetchrow(
            "select id from listings where id = $1 and workspace_id = $2", listing_id, workspace_id
        )
        if listing is None:
            raise HTTPException(status_code=404, detail="listing not found")

        if updates.get("price_cents", 0) > 0:
            await _require_payouts_enabled(conn, workspace_id)

        set_clauses = [f"{key} = ${i}" for i, key in enumerate(updates, start=1)]
        row = await conn.fetchrow(
            f"""
            update listings set {", ".join(set_clauses)}, updated_at = now()
            where id = ${len(updates) + 1}
            returning {_WORKSPACE_LISTING_COLUMNS}
            """,
            *updates.values(),
            listing_id,
        )
    return WorkspaceListingOut(**dict(row))


@listings_router.post("/{listing_id}/unpublish", response_model=WorkspaceListingOut)
async def unpublish_listing(workspace_id: UUID, listing_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    """Takes a published listing off the public browse (listings.py's
    browse_listings/get_listing both filter status = 'published') without
    touching anything it already installed elsewhere -- an install already
    forked its own independent agent/flow copy at install time, so there's
    nothing live for this to retract. 'archived' (publish_status, already in
    the schema since 0001_init_schema.sql, unused until now) rather than
    deleting the row: install history (installs.listing_id) and existing
    reviews (listing_reviews.listing_id) both still reference it. Publishing
    a new version (already-existing endpoint, no change needed) re-lists it,
    since that endpoint sets status = 'published' unconditionally regardless
    of what it was before.
    """
    pool = await get_pool()
    listing = await pool.fetchrow(
        "select status from listings where id = $1 and workspace_id = $2", listing_id, workspace_id
    )
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")
    if listing["status"] != "published":
        raise HTTPException(status_code=400, detail="only a published listing can be unpublished")

    row = await pool.fetchrow(
        f"""
        update listings set status = 'archived', updated_at = now()
        where id = $1
        returning {_WORKSPACE_LISTING_COLUMNS}
        """,
        listing_id,
    )
    return WorkspaceListingOut(**dict(row))


@listings_router.post("", response_model=ListingOut, status_code=201)
async def create_listing(workspace_id: UUID, body: ListingCreate, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if body.price_cents > 0:
            await _require_payouts_enabled(conn, workspace_id)

        if body.source_agent_id is not None:
            agent = await conn.fetchrow(
                "select id from agents where id = $1 and workspace_id = $2", body.source_agent_id, workspace_id
            )
            if agent is None:
                raise HTTPException(status_code=404, detail="source agent not found in this workspace")
        else:
            flow = await conn.fetchrow(
                "select id from flows where id = $1 and workspace_id = $2", body.source_flow_id, workspace_id
            )
            if flow is None:
                raise HTTPException(status_code=404, detail="source flow not found in this workspace")

        row = await conn.fetchrow(
            """
            insert into listings (workspace_id, source_agent_id, source_flow_id, name, description, category, price_cents)
            values ($1, $2, $3, $4, $5, $6, $7)
            returning id, name, status
            """,
            workspace_id,
            body.source_agent_id,
            body.source_flow_id,
            body.name,
            body.description,
            body.category,
            body.price_cents,
        )
    return ListingOut(**dict(row))


async def _agent_listing_snapshot(conn, agent_id: UUID) -> tuple[dict, set[str]]:
    """The reusable part of publish-time snapshotting: a `{name, description,
    system_prompt, model_tier, max_steps, max_tokens, tools}` dict for one agent,
    plus the `{kind}:{key}` tool categories it needs, with the sub-agent and
    private-tool checks this module's docstring describes. Shared by the plain
    agent-listing path and the flow-listing path below, which calls this once per
    distinct agent a flow's `agent` nodes reference.
    """
    agent = await conn.fetchrow("select name, description from agents where id = $1", agent_id)
    if agent is None:
        raise HTTPException(status_code=400, detail=f"agent {agent_id} referenced by this listing no longer exists")

    agent_version = await conn.fetchrow(
        "select * from agent_versions where agent_id = $1 order by version desc limit 1", agent_id
    )
    if agent_version is None:
        raise HTTPException(status_code=400, detail=f"agent {agent_id} has no version to publish")

    has_subagents = await conn.fetchval(
        "select exists(select 1 from agent_version_subagents where agent_version_id = $1)",
        agent_version["id"],
    )
    if has_subagents:
        raise HTTPException(
            status_code=400,
            detail=(
                f"agent {agent_id} delegates to sub-agents (agent-as-tool) and can't be "
                "published as a listing yet"
            ),
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
                f"agent {agent_id} uses workspace-private tools that another workspace couldn't install "
                f"({', '.join(private_tools)}) — only global tools can appear in a listing"
            ),
        )

    snapshot = {
        "name": agent["name"],
        "description": agent["description"],
        "system_prompt": agent_version["system_prompt"],
        "model_tier": agent_version["model_tier"],
        "max_steps": agent_version["max_steps"],
        "max_tokens": agent_version["max_tokens"],
        "tools": [{"key": t["key"], "kind": t["kind"], "config": t["config"]} for t in tool_rows],
    }
    categories = {f"{t['kind']}:{t['key']}" for t in tool_rows}
    return snapshot, categories


async def _flow_listing_spec(conn, flow_id: UUID) -> tuple[dict, list[str]]:
    """Snapshots a flow for publishing: the flow's own spec (nodes/edges, as
    stored in `flow_versions.spec` — untouched, node ids and edges included
    verbatim) plus a `{kind}:{key}` tool category and an `_agent_listing_snapshot`
    for every distinct agent one of its `agent` nodes points at, and a
    `{key, kind}` record for every distinct tool one of its `tool` nodes points
    at directly (checked for global-only the same way, but with no sub-agent
    question since a tool node has no sub-agents of its own).

    Node ids (`FlowNode.agent_id`/`tool_id`) key the `agents`/`tools` dicts as
    strings, not remapped here — `install_listing` below does the remapping,
    once it knows what each referenced agent/tool actually becomes in the
    installing workspace.
    """
    flow_version = await conn.fetchrow(
        "select spec from flow_versions where flow_id = $1 order by version desc limit 1", flow_id
    )
    if flow_version is None:
        raise HTTPException(status_code=400, detail="the source flow has no version to publish")
    flow_spec = flow_version["spec"]

    agent_ids = {n["agent_id"] for n in flow_spec["nodes"] if n["type"] == "agent" and n.get("agent_id")}
    tool_ids = {n["tool_id"] for n in flow_spec["nodes"] if n["type"] == "tool" and n.get("tool_id")}

    agents_spec: dict[str, dict] = {}
    categories: set[str] = set()
    for agent_id_str in agent_ids:
        snapshot, agent_categories = await _agent_listing_snapshot(conn, UUID(agent_id_str))
        agents_spec[agent_id_str] = snapshot
        categories |= agent_categories

    tools_spec: dict[str, dict] = {}
    if tool_ids:
        tool_rows = await conn.fetch(
            "select id, key, kind, workspace_id from tools where id = any($1::uuid[])",
            [UUID(t) for t in tool_ids],
        )
        found = {str(r["id"]): r for r in tool_rows}
        missing = tool_ids - found.keys()
        if missing:
            raise HTTPException(
                status_code=400, detail=f"flow references tool(s) that no longer exist: {sorted(missing)}"
            )
        private_tools = [r["key"] for r in tool_rows if r["workspace_id"] is not None]
        if private_tools:
            raise HTTPException(
                status_code=400,
                detail=(
                    "flow uses workspace-private tools that another workspace couldn't install "
                    f"({', '.join(private_tools)}) — only global tools can appear in a listing"
                ),
            )
        for tool_id_str, row in found.items():
            tools_spec[tool_id_str] = {"key": row["key"], "kind": row["kind"]}
            categories.add(f"{row['kind']}:{row['key']}")

    spec = {"type": "flow", "flow_spec": flow_spec, "agents": agents_spec, "tools": tools_spec}
    return spec, sorted(categories)


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

        if listing["source_agent_id"] is not None:
            snapshot, categories = await _agent_listing_snapshot(conn, listing["source_agent_id"])
            spec = {"type": "agent", **snapshot}
            required_tool_categories = sorted(categories)
        else:
            spec, required_tool_categories = await _flow_listing_spec(conn, listing["source_flow_id"])

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
    installed_agent_id: UUID | None
    installed_flow_id: UUID | None


async def _install_agent_snapshot(conn, workspace_id: UUID, user_id: UUID, name: str, description: str | None, snapshot: dict) -> UUID:
    """Forks one agent snapshot (as `_agent_listing_snapshot` produced it) into
    `workspace_id` as a brand-new agent + version 1, resolving each tool by
    `(kind, key)` against the installing workspace's *global* tools (the same
    ones available in every workspace — publish already rejected anything
    private, so this can never need a workspace-specific row). Returns the new
    agent's id. Shared by the plain agent install and, once per referenced
    agent, the flow install below.
    """
    agent = await conn.fetchrow(
        """
        insert into agents (workspace_id, name, description, created_by)
        values ($1, $2, $3, $4)
        returning id
        """,
        workspace_id,
        name,
        description,
        user_id,
    )
    agent_version = await conn.fetchrow(
        """
        insert into agent_versions (agent_id, version, system_prompt, model_tier, max_steps, max_tokens)
        values ($1, 1, $2, $3, $4, $5)
        returning id
        """,
        agent["id"],
        snapshot["system_prompt"],
        snapshot["model_tier"],
        snapshot.get("max_steps"),
        snapshot.get("max_tokens"),
    )
    for tool_spec in snapshot.get("tools", []):
        tool_row = await conn.fetchrow(
            "select id from tools where workspace_id is null and key = $1 and kind = $2",
            tool_spec["key"],
            tool_spec["kind"],
        )
        if tool_row is None:
            # Raising inside this transaction rolls back the agent/version
            # insert above (and, for a flow install, every other agent already
            # forked in this same install) too -- never leave a half-installed
            # agent or flow behind.
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
    return agent["id"]


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

        if listing["price_cents"] > 0:
            # Same gate shape as the tool-category approval just above -- a
            # paid listing checks for a completed purchase (routers/payments.py)
            # the same way a free one checks for approved categories, both
            # refusing the install outright rather than letting it through
            # and hoping the client already did the right thing.
            paid = await conn.fetchval(
                """
                select exists(
                    select 1 from listing_purchases
                    where listing_id = $1 and purchasing_workspace_id = $2 and status = 'paid'
                )
                """,
                listing["id"],
                workspace_id,
            )
            if not paid:
                raise HTTPException(
                    status_code=402, detail="this listing requires payment — complete checkout before installing"
                )

        spec = listing_version["spec"]
        installed_agent_id: UUID | None = None
        installed_flow_id: UUID | None = None

        async with conn.transaction():
            if spec.get("type") == "agent":
                installed_agent_id = await _install_agent_snapshot(
                    conn, workspace_id, user_id, listing["name"], listing["description"], spec
                )
            elif spec.get("type") == "flow":
                # One new agent per distinct agent the flow referenced at publish
                # time, then the flow itself with every `agent` node's agent_id
                # rewritten to point at its newly-forked counterpart. Tool nodes
                # need no rewriting: publish already guaranteed every one of them
                # is a *global* tool, and a global tool's row (and id) is the same
                # across every workspace -- there's nothing workspace-specific to fork.
                agent_id_map: dict[str, str] = {}
                for old_agent_id, agent_snapshot in spec.get("agents", {}).items():
                    new_agent_id = await _install_agent_snapshot(
                        conn,
                        workspace_id,
                        user_id,
                        agent_snapshot["name"],
                        agent_snapshot["description"],
                        agent_snapshot,
                    )
                    agent_id_map[old_agent_id] = str(new_agent_id)

                installed_flow_spec = dict(spec["flow_spec"])
                installed_flow_spec["nodes"] = [
                    {**n, "agent_id": agent_id_map[n["agent_id"]]} if n["type"] == "agent" else n
                    for n in installed_flow_spec["nodes"]
                ]

                flow = await conn.fetchrow(
                    """
                    insert into flows (workspace_id, name, created_by)
                    values ($1, $2, $3)
                    returning id
                    """,
                    workspace_id,
                    listing["name"],
                    user_id,
                )
                await conn.execute(
                    "insert into flow_versions (flow_id, version, spec) values ($1, 1, $2)",
                    flow["id"],
                    installed_flow_spec,
                )
                installed_flow_id = flow["id"]
            else:
                raise HTTPException(status_code=400, detail=f"unsupported listing spec type: {spec.get('type')!r}")

            install = await conn.fetchrow(
                """
                insert into installs
                    (listing_id, listing_version_id, installing_workspace_id, installed_agent_id,
                     installed_flow_id, approved_tool_categories, installed_by)
                values ($1, $2, $3, $4, $5, $6, $7)
                returning id
                """,
                listing["id"],
                listing_version["id"],
                workspace_id,
                installed_agent_id,
                installed_flow_id,
                body.approved_tool_categories,
                user_id,
            )
            await conn.execute("update listings set install_count = install_count + 1 where id = $1", listing["id"])

    return InstallOut(id=install["id"], installed_agent_id=installed_agent_id, installed_flow_id=installed_flow_id)


class InstallHistoryItemOut(BaseModel):
    id: UUID
    listing_id: UUID
    listing_name: str
    listing_kind: str
    installed_agent_id: UUID | None
    installed_flow_id: UUID | None
    created_at: datetime


@install_router.get("/installs", response_model=list[InstallHistoryItemOut])
async def list_installs(workspace_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    """This workspace's install history (docs/05-frontend-marketplace.md §1's
    deferred "installs/library history page") -- what's been installed here
    and which listing it came from. The installed agent/flow already shows up
    in /agents or /flows under its own name the moment it exists, same as
    anything built locally; this is purely the provenance trail on top of
    that, not a second copy of either listing page.
    """
    pool = await get_pool()
    rows = await pool.fetch(
        """
        select i.id, i.listing_id, l.name as listing_name,
               case when l.source_agent_id is not null then 'agent' else 'flow' end as listing_kind,
               i.installed_agent_id, i.installed_flow_id, i.created_at
        from installs i
        join listings l on l.id = i.listing_id
        where i.installing_workspace_id = $1
        order by i.created_at desc
        """,
        workspace_id,
    )
    return [InstallHistoryItemOut(**dict(r)) for r in rows]


class ReviewUpsert(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str | None = None


class MyReviewOut(BaseModel):
    rating: int
    comment: str | None
    created_at: datetime
    updated_at: datetime


@install_router.get("/listings/{listing_id}/review", response_model=MyReviewOut | None)
async def get_my_review(workspace_id: UUID, listing_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    """This workspace's own review of a listing, if any -- lets the frontend
    tell "leave a review" from "edit your review" apart and prefill the form,
    without the caller having to scan the full public list for its own row."""
    pool = await get_pool()
    row = await pool.fetchrow(
        "select rating, comment, created_at, updated_at from listing_reviews where listing_id = $1 and reviewing_workspace_id = $2",
        listing_id,
        workspace_id,
    )
    return MyReviewOut(**dict(row)) if row else None


@install_router.put("/listings/{listing_id}/review", response_model=MyReviewOut)
async def upsert_review(
    workspace_id: UUID, listing_id: UUID, body: ReviewUpsert, user_id: UUID = Depends(get_current_user_id)
):
    """Create or edit this workspace's review of a listing -- one row per
    (listing, workspace) (listing_reviews' own unique constraint), so a
    second call from the same workspace edits its existing review rather
    than adding a second opinion from the same place. Gated on having
    actually installed the listing (an `installs` row for this exact pair),
    same "tried it first" bar the RLS policy on listing_reviews itself
    encodes -- checked again here since this connection writes with the
    pool's own privileges, not through RLS.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        listing = await conn.fetchrow("select id from listings where id = $1 and status = 'published'", listing_id)
        if listing is None:
            raise HTTPException(status_code=404, detail="listing not found or not published")

        installed = await conn.fetchval(
            "select exists(select 1 from installs where listing_id = $1 and installing_workspace_id = $2)",
            listing_id,
            workspace_id,
        )
        if not installed:
            raise HTTPException(status_code=403, detail="install this listing before reviewing it")

        async with conn.transaction():
            row = await conn.fetchrow(
                """
                insert into listing_reviews (listing_id, reviewing_workspace_id, rating, comment, created_by)
                values ($1, $2, $3, $4, $5)
                on conflict (listing_id, reviewing_workspace_id)
                do update set rating = excluded.rating, comment = excluded.comment, updated_at = now()
                returning rating, comment, created_at, updated_at
                """,
                listing_id,
                workspace_id,
                body.rating,
                body.comment,
                user_id,
            )
            # Recomputed from listing_reviews directly rather than incrementally
            # adjusted -- an edit changes an existing row's rating in place, which
            # an incremental "add the delta" update would have to special-case
            # anyway, so a plain aggregate is both simpler and can't drift.
            await conn.execute(
                """
                update listings set
                  rating_avg = (select avg(rating)::numeric(3,2) from listing_reviews where listing_id = $1),
                  rating_count = (select count(*) from listing_reviews where listing_id = $1)
                where id = $1
                """,
                listing_id,
            )
    return MyReviewOut(**dict(row))
