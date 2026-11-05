"""Regression suite for the orchestrator-layer half of §20/§22
(docs/01-architecture.md): run_single_agent, run_flow, and run_agent_as_tool
are the dispatch points every runs row/sub-agent call actually goes through
regardless of how it got there, so each has its own ownership check as
defense-in-depth behind the API-layer checks (create_run's §22 check,
create_agent_version's §20 check). These tests bypass the API entirely --
hand-inserting a runs row or calling run_agent_as_tool directly, the same
way this session's insert_malicious_runs.sql / test_dispatch_*.py scripts
did by hand -- to verify the orchestrator's own checks independently.

Each dispatch function's ownership check runs and returns (or raises)
before any Anthropic API call, so no real model call happens here.
"""

import pytest

from orchestrator_app.executor import run_agent_as_tool, run_single_agent
from orchestrator_app.flow_executor import run_flow
from orchestrator_app.tool_gateway import ToolDispatchError


@pytest.mark.asyncio
async def test_run_single_agent_fails_cross_workspace_run(pool, factory):
    attacker = await factory.workspace()
    victim = await factory.workspace()
    victim_agent_id = await factory.agent(victim["id"])
    run_id = await factory.run(attacker["id"], agent_id=victim_agent_id)

    async with pool.acquire() as conn:
        await run_single_agent(conn, run_id)
        row = await conn.fetchrow("select status, error from runs where id = $1", run_id)

    assert row["status"] == "failed"
    assert "not found in this workspace" in row["error"]


@pytest.mark.asyncio
async def test_run_flow_fails_cross_workspace_run(pool, factory):
    attacker = await factory.workspace()
    victim = await factory.workspace()
    victim_flow_id = await factory.flow(victim["id"])
    run_id = await factory.run(attacker["id"], flow_id=victim_flow_id)

    # The ownership check returns before the checkpointer is ever touched,
    # so a real AsyncPostgresSaver isn't needed to exercise it.
    await run_flow(pool, run_id, None)

    async with pool.acquire() as conn:
        row = await conn.fetchrow("select status, error from runs where id = $1", run_id)

    assert row["status"] == "failed"
    assert "not found in this workspace" in row["error"]


@pytest.mark.asyncio
async def test_run_agent_as_tool_rejects_cross_workspace_sub_agent(pool, factory):
    attacker = await factory.workspace()
    victim = await factory.workspace()
    victim_agent_id = await factory.agent(victim["id"])
    attacker_agent_id = await factory.agent(attacker["id"])
    parent_run_id = await factory.run(attacker["id"], agent_id=attacker_agent_id, status="running")

    async with pool.acquire() as conn:
        with pytest.raises(ToolDispatchError, match="not found in this workspace"):
            await run_agent_as_tool(
                conn,
                parent_run_id=parent_run_id,
                sub_agent_id=victim_agent_id,
                tool_input={"message": "hi"},
                depth=0,
            )

