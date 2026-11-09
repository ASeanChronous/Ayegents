"""Shared fixtures for the regression suite against the real Docker Postgres
(aeflow-pg) -- no mocking of the database or the endpoint functions under
test, per this project's testing posture. Every fixture creates throwaway
rows directly via SQL (the same seed pattern used by this session's one-off
manual verification scripts) and deletes them again in teardown.

`workspace_factory` bypasses `on_workspace_created` (0001_init_schema.sql)
with `session_replication_role = replica` while inserting a workspace --
that trigger inserts the creator as 'owner' via `auth.uid()`, which has no
meaning on a plain asyncpg connection outside a real Supabase-authenticated
session.
"""

import json
import uuid
from types import SimpleNamespace

import asyncpg
import httpx
import pytest_asyncio

import app.db as app_db
from app.config import settings
from app.main import app as fastapi_app


@pytest_asyncio.fixture(autouse=True)
async def _reset_app_pool():
    """Every endpoint under test (create_run, update_member_role, ...) calls
    app.db.get_pool() itself, which lazily caches a single asyncpg.Pool in a
    module-level global the first time it's called. pytest-asyncio gives each
    test function its own event loop, so without this, the first test to
    touch any endpoint would bind that global pool to its own loop and every
    later test would crash with "Future attached to a different loop" the
    moment it called a different endpoint. Resetting the global before and
    after each test keeps it scoped to that test's own loop, matching how it
    behaves in the real single-process API server (one pool per process).
    """
    app_db._pool = None
    yield
    await app_db.close_pool()


@pytest_asyncio.fixture
async def async_client():
    """An httpx client wired directly to the FastAPI app's ASGI callable (no real
    socket, no running uvicorn process needed) -- for §16/§19's webhook/trigger tests,
    which take `Request`/`Header`-typed FastAPI parameters that aren't practical to
    construct by calling the endpoint function directly, unlike every other test in
    this suite."""
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def pool():
    async def _init(conn: asyncpg.Connection) -> None:
        for pg_type in ("jsonb", "json"):
            await conn.set_type_codec(
                pg_type, encoder=json.dumps, decoder=json.loads, schema="pg_catalog", format="text"
            )

    p = await asyncpg.create_pool(settings.database_url, init=_init)
    yield p
    await p.close()


@pytest_asyncio.fixture
async def factory(pool):
    """`factory.workspace(members={"owner": "owner", ...})` creates a throwaway
    workspace, optionally with throwaway auth.users rows seated as members at
    the given roles, and returns {"id": ws_id, <label>: user_id, ...}.
    `factory.agent(workspace_id)` / `factory.flow(workspace_id)` create a
    throwaway agent/flow (with its own throwaway `created_by` user) owned by
    that workspace and return its id.
    """
    workspace_ids: list[uuid.UUID] = []
    user_ids: list[uuid.UUID] = []
    global_tool_ids: list[uuid.UUID] = []

    async def _new_user(conn, label: str) -> uuid.UUID:
        user_id = uuid.uuid4()
        await conn.execute(
            "insert into auth.users (id, email) values ($1, $2)",
            user_id,
            f"{label}-{user_id}@test.invalid",
        )
        user_ids.append(user_id)
        return user_id

    async def workspace(*, members: dict[str, str] | None = None) -> dict:
        async with pool.acquire() as conn:
            await conn.execute("set session_replication_role = replica")
            try:
                ws_id = await conn.fetchval(
                    "insert into workspaces (name, plan_tier) values ($1, 'free') returning id",
                    f"test-ws-{uuid.uuid4()}",
                )
                workspace_ids.append(ws_id)
                result = {"id": ws_id}
                for label, role in (members or {}).items():
                    user_id = await _new_user(conn, label)
                    await conn.execute(
                        "insert into workspace_members (workspace_id, user_id, role) values ($1, $2, $3)",
                        ws_id,
                        user_id,
                        role,
                    )
                    result[label] = user_id
            finally:
                await conn.execute("set session_replication_role = default")
        return result

    async def agent(workspace_id: uuid.UUID) -> uuid.UUID:
        async with pool.acquire() as conn:
            creator = await _new_user(conn, "creator")
            return await conn.fetchval(
                "insert into agents (workspace_id, name, created_by) values ($1, $2, $3) returning id",
                workspace_id,
                f"test-agent-{uuid.uuid4()}",
                creator,
            )

    async def flow(workspace_id: uuid.UUID) -> uuid.UUID:
        async with pool.acquire() as conn:
            creator = await _new_user(conn, "creator")
            return await conn.fetchval(
                "insert into flows (workspace_id, name, created_by) values ($1, $2, $3) returning id",
                workspace_id,
                f"test-flow-{uuid.uuid4()}",
                creator,
            )

    async def trigger(flow_id: uuid.UUID, *, kind: str, config: dict, enabled: bool = True) -> uuid.UUID:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "insert into triggers (flow_id, kind, config, enabled) values ($1, $2, $3, $4) returning id",
                flow_id,
                kind,
                config,
                enabled,
            )

    async def tool(
        workspace_id: uuid.UUID | None, *, kind: str = "http", key: str | None = None
    ) -> uuid.UUID:
        key = key or f"test-tool-{uuid.uuid4()}"
        async with pool.acquire() as conn:
            tool_id = await conn.fetchval(
                "insert into tools (workspace_id, key, name, kind) values ($1, $2, $3, $4) returning id",
                workspace_id,
                key,
                key,
                kind,
            )
        if workspace_id is None:
            # Not covered by any workspace's own cascade -- tracked separately for teardown.
            global_tool_ids.append(tool_id)
        return tool_id

    async def agent_version(
        agent_id: uuid.UUID, *, tool_ids: list[uuid.UUID] | None = None, sub_agent_ids: list[uuid.UUID] | None = None
    ) -> uuid.UUID:
        async with pool.acquire() as conn:
            version_id = await conn.fetchval(
                """
                insert into agent_versions (agent_id, version, system_prompt)
                values ($1, 1, 'you are a test agent')
                returning id
                """,
                agent_id,
            )
            for tool_id in tool_ids or []:
                await conn.execute(
                    "insert into agent_version_tools (agent_version_id, tool_id) values ($1, $2)",
                    version_id,
                    tool_id,
                )
            for sub_agent_id in sub_agent_ids or []:
                await conn.execute(
                    "insert into agent_version_subagents (agent_version_id, sub_agent_id) values ($1, $2)",
                    version_id,
                    sub_agent_id,
                )
            return version_id

    async def flow_version(flow_id: uuid.UUID, spec: dict) -> uuid.UUID:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "insert into flow_versions (flow_id, version, spec) values ($1, 1, $2) returning id",
                flow_id,
                spec,
            )

    async def listing(
        workspace_id: uuid.UUID,
        *,
        source_agent_id: uuid.UUID | None = None,
        source_flow_id: uuid.UUID | None = None,
        price_cents: int = 0,
        status: str = "draft",
        required_tool_categories: list[str] | None = None,
    ) -> uuid.UUID:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                """
                insert into listings
                    (workspace_id, source_agent_id, source_flow_id, name, price_cents, status, required_tool_categories)
                values ($1, $2, $3, $4, $5, $6, $7)
                returning id
                """,
                workspace_id,
                source_agent_id,
                source_flow_id,
                f"test-listing-{uuid.uuid4()}",
                price_cents,
                status,
                required_tool_categories or [],
            )

    async def listing_version(listing_id: uuid.UUID, spec: dict) -> uuid.UUID:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "insert into listing_versions (listing_id, version, spec) values ($1, 1, $2) returning id",
                listing_id,
                spec,
            )

    async def install(
        listing_id: uuid.UUID, listing_version_id: uuid.UUID, installing_workspace_id: uuid.UUID, installed_by: uuid.UUID
    ) -> uuid.UUID:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                """
                insert into installs (listing_id, listing_version_id, installing_workspace_id, installed_by)
                values ($1, $2, $3, $4)
                returning id
                """,
                listing_id,
                listing_version_id,
                installing_workspace_id,
                installed_by,
            )

    async def set_payouts_enabled(workspace_id: uuid.UUID, *, enabled: bool = True) -> None:
        async with pool.acquire() as conn:
            await conn.execute(
                "update workspaces set stripe_connect_payouts_enabled = $2 where id = $1", workspace_id, enabled
            )

    async def purchase(listing_id: uuid.UUID, purchasing_workspace_id: uuid.UUID, purchased_by: uuid.UUID, *, status: str = "paid") -> uuid.UUID:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                """
                insert into listing_purchases
                    (listing_id, purchasing_workspace_id, amount_cents, currency, stripe_checkout_session_id, purchased_by, status)
                values ($1, $2, 1000, 'usd', $3, $4, $5)
                returning id
                """,
                listing_id,
                purchasing_workspace_id,
                f"cs_test_{uuid.uuid4()}",
                purchased_by,
                status,
            )

    async def fill_runs(workspace_id: uuid.UUID, *, flow_id: uuid.UUID, count: int) -> None:
        """Bulk-inserts `count` already-finished runs created "now" -- for pushing a
        workspace to (or past) `plan_tier_limits.max_runs_per_day` (§16/§17's rolling
        24h window) without going through create_run/a trigger each time."""
        async with pool.acquire() as conn:
            await conn.executemany(
                "insert into runs (workspace_id, flow_id, status) values ($1, $2, 'succeeded')",
                [(workspace_id, flow_id) for _ in range(count)],
            )

    yield SimpleNamespace(
        workspace=workspace,
        agent=agent,
        flow=flow,
        trigger=trigger,
        fill_runs=fill_runs,
        tool=tool,
        agent_version=agent_version,
        flow_version=flow_version,
        listing=listing,
        listing_version=listing_version,
        install=install,
        set_payouts_enabled=set_payouts_enabled,
        purchase=purchase,
    )

    async with pool.acquire() as conn:
        # installs.listing_id and listing_purchases.listing_id (0001_init_schema.sql,
        # 0008_listing_pricing.sql) reference listings(id) with no ON DELETE CASCADE of
        # their own -- a marketplace test installing/purchasing listing A (owned by
        # workspace A) *from* workspace B leaves a row that outlives workspace A's own
        # listings-cascade the moment workspace A is torn down, the same FK-ordering
        # hazard as agent_version_tools/_subagents below, just one hop further down the
        # publish/install chain. Cleared for every workspace up front (listings owned by
        # *any* of this test's own workspaces), not just the one about to be deleted,
        # since the referencing install/purchase row commonly lives in a different
        # workspace than the listing it points at.
        for ws_id in workspace_ids:
            await conn.execute(
                "delete from installs where listing_id in (select id from listings where workspace_id = $1)",
                ws_id,
            )
            await conn.execute(
                "delete from listing_purchases where listing_id in (select id from listings where workspace_id = $1)",
                ws_id,
            )
        for ws_id in workspace_ids:
            # agent_version_tools.tool_id and agent_version_subagents.sub_agent_id
            # (0001_init_schema.sql) reference tools(id)/agents(id) with no ON DELETE
            # CASCADE of their own -- only the agent_version_id side cascades. Tests
            # that attach a tool/sub-agent (§20's suite) can end up with those rows
            # outliving the agent_versions cascade that's supposed to clear them,
            # tripping a FK violation on the workspace delete below. Clear them
            # explicitly first rather than relying on cascade ordering across two
            # independent FK paths converging on the same row.
            await conn.execute(
                "delete from agent_version_tools where tool_id in (select id from tools where workspace_id = $1)",
                ws_id,
            )
            await conn.execute(
                "delete from agent_version_subagents where sub_agent_id in (select id from agents where workspace_id = $1)",
                ws_id,
            )
            # Cascades away any agents/flows/runs created under it (0001_init_schema.sql).
            await conn.execute("delete from workspaces where id = $1", ws_id)
        if global_tool_ids:
            # Same no-cascade gap as above, but for a global (workspace_id is null)
            # tool a marketplace-install test resolved an agent's snapshot against --
            # nothing in the workspace-keyed cleanup above ever touches these.
            await conn.execute("delete from agent_version_tools where tool_id = any($1::uuid[])", global_tool_ids)
            await conn.execute("delete from tools where id = any($1::uuid[])", global_tool_ids)
        for user_id in user_ids:
            await conn.execute("delete from auth.users where id = $1", user_id)
