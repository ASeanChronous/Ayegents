"""Shared fixtures for the orchestrator's dispatch-time regression suite,
mirroring services/api/tests/conftest.py -- real Docker Postgres (aeflow-pg),
no mocking of the database or the dispatch functions under test.
"""

import json
import uuid
from types import SimpleNamespace

import asyncpg
import pytest_asyncio

from orchestrator_app.config import settings


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
    """Same shape as services/api/tests/conftest.py's `factory`, plus `run()`
    for inserting a throwaway runs row directly -- the dispatch functions
    under test here are reached from `run_queue`, not from create_run, so
    a test has to hand-insert the runs row itself (the same way this
    session's insert_malicious_runs.sql bypassed the API's own §22 check to
    verify the orchestrator's dispatch-time check independently)."""
    workspace_ids: list[uuid.UUID] = []
    user_ids: list[uuid.UUID] = []

    async def _new_user(conn, label: str) -> uuid.UUID:
        user_id = uuid.uuid4()
        await conn.execute(
            "insert into auth.users (id, email) values ($1, $2)",
            user_id,
            f"{label}-{user_id}@test.invalid",
        )
        user_ids.append(user_id)
        return user_id

    async def workspace() -> dict:
        async with pool.acquire() as conn:
            await conn.execute("set session_replication_role = replica")
            try:
                ws_id = await conn.fetchval(
                    "insert into workspaces (name, plan_tier) values ($1, 'free') returning id",
                    f"test-ws-{uuid.uuid4()}",
                )
                workspace_ids.append(ws_id)
            finally:
                await conn.execute("set session_replication_role = default")
        return {"id": ws_id}

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

    async def run(workspace_id: uuid.UUID, *, agent_id=None, flow_id=None, status="queued") -> uuid.UUID:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "insert into runs (workspace_id, agent_id, flow_id, status) values ($1, $2, $3, $4) returning id",
                workspace_id,
                agent_id,
                flow_id,
                status,
            )

    yield SimpleNamespace(workspace=workspace, agent=agent, flow=flow, run=run)

    async with pool.acquire() as conn:
        for ws_id in workspace_ids:
            # Cascades away any agents/flows/runs created under it (0001_init_schema.sql).
            await conn.execute("delete from workspaces where id = $1", ws_id)
        for user_id in user_ids:
            await conn.execute("delete from auth.users where id = $1", user_id)
