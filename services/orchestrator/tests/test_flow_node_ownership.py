"""Regression suite for §21's orchestrator-layer half (docs/01-architecture.md):
flow_executor.py's _make_node_fn checks an 'agent' node's agent_id and a
'tool' node's tool_id against the run's own workspace_id before ever loading
the agent version or dispatching the tool -- the fix that actually matters,
since a flow spec reaches this code regardless of whether it was saved
through create_flow_version (which has its own, API-layer copy of this
check -- see services/api/tests/test_cross_workspace_flow_node_ownership.py)
or written directly into flow_versions.

Calls _make_node_fn directly to get the node's own async callable, then
calls that with an empty state -- the ownership check is the first thing
either branch does, before loading an agent version or dispatching a tool,
so this never reaches a real Anthropic call or tool_gateway.dispatch_tool.
"""

from uuid import uuid4

import pytest

from orchestrator_app.flow_executor import FlowExecutionError, _make_node_fn


@pytest.mark.asyncio
async def test_agent_node_rejects_cross_workspace_agent_id(pool, factory):
    attacker = await factory.workspace()
    victim = await factory.workspace()
    victim_agent_id = await factory.agent(victim["id"])

    node = {"id": "a", "type": "agent", "agent_id": str(victim_agent_id), "config": {}}
    node_fn = _make_node_fn(
        node,
        pool=pool,
        workspace_id=attacker["id"],
        run_id=uuid4(),
        initial_input={},
        max_steps=20,
        max_tokens_budget=50000,
        max_wall_clock_seconds=120,
    )

    with pytest.raises(FlowExecutionError, match="not found in this workspace"):
        await node_fn({})


@pytest.mark.asyncio
async def test_tool_node_rejects_cross_workspace_tool_id(pool, factory):
    attacker = await factory.workspace()
    victim = await factory.workspace()
    victim_tool_id = await factory.tool(victim["id"])

    node = {"id": "a", "type": "tool", "tool_id": str(victim_tool_id), "config": {}}
    node_fn = _make_node_fn(
        node,
        pool=pool,
        workspace_id=attacker["id"],
        run_id=uuid4(),
        initial_input={},
        max_steps=20,
        max_tokens_budget=50000,
        max_wall_clock_seconds=120,
    )

    with pytest.raises(FlowExecutionError, match="not found"):
        await node_fn({})


@pytest.mark.asyncio
async def test_tool_node_allows_global_builtin_tool(pool, factory):
    # workspace_id is null = global builtin, same "unowned is fine" scoping
    # tools.py::list_tools already uses -- must not false-positive as a gap.
    # Uses the real "current_time" builtin (tool_gateway.BUILTIN_TOOLS) so the
    # ownership check passing is proven by the node actually dispatching and
    # succeeding, not just by which error message comes back.
    attacker = await factory.workspace()
    builtin_tool_id = await factory.tool(None, kind="builtin", key="current_time")
    run_id = await factory.run(attacker["id"], flow_id=await factory.flow(attacker["id"]), status="running")

    node = {"id": "a", "type": "tool", "tool_id": str(builtin_tool_id), "config": {}}
    node_fn = _make_node_fn(
        node,
        pool=pool,
        workspace_id=attacker["id"],
        run_id=run_id,
        initial_input={},
        max_steps=20,
        max_tokens_budget=50000,
        max_wall_clock_seconds=120,
    )

    result = await node_fn({})
    assert "iso8601" in result["node_outputs"]["a"]["output"]

    async with pool.acquire() as conn:
        # run_steps.tool_id has no ON DELETE CASCADE of its own -- _log_tool_step
        # wrote one for this call, and it outlives the run/workspace teardown's
        # cascade the same way agent_version_tools/_subagents do (see conftest.py).
        await conn.execute("delete from run_steps where tool_id = $1", builtin_tool_id)
        await conn.execute("delete from tools where id = $1", builtin_tool_id)
