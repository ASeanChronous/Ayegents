"""Regression suite for §21's API-layer half (docs/01-architecture.md):
create_flow_version walks every node in the submitted spec and checks an
'agent' node's agent_id / a 'tool' node's tool_id against the caller's own
workspace before saving the version -- 404ing at save time instead of only
failing once a run actually reaches that node. §21's orchestrator-layer half
(flow_executor.py's _make_node_fn doing the same check at dispatch time,
the fix that actually matters since a flow spec can be edited directly
through the API without ever resaving through this endpoint) has its own
regression test in services/orchestrator/tests/test_flow_node_ownership.py.
"""

import pytest
from fastapi import HTTPException

from app.routers.flows import FlowVersionCreate, create_flow_version
from app.routers.tools import ToolCreate, create_tool


def _spec_with(node: dict) -> dict:
    return {
        "nodes": [{"id": "t", "type": "trigger"}, node],
        "edges": [{"from": "t", "to": node["id"]}],
    }


@pytest.mark.asyncio
async def test_rejects_cross_workspace_agent_node(factory):
    attacker = await factory.workspace()
    victim = await factory.workspace()
    flow_id = await factory.flow(attacker["id"])
    victim_agent_id = await factory.agent(victim["id"])

    with pytest.raises(HTTPException) as exc_info:
        await create_flow_version(
            workspace_id=attacker["id"],
            flow_id=flow_id,
            body=FlowVersionCreate(
                spec=_spec_with({"id": "a", "type": "agent", "agent_id": str(victim_agent_id)})
            ),
            user_id=attacker["id"],
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_rejects_cross_workspace_tool_node(factory):
    attacker = await factory.workspace()
    victim = await factory.workspace()
    flow_id = await factory.flow(attacker["id"])
    victim_tool = await create_tool(
        workspace_id=victim["id"],
        body=ToolCreate(key="victim-tool", name="Victim Tool", kind="http"),
        user_id=victim["id"],
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_flow_version(
            workspace_id=attacker["id"],
            flow_id=flow_id,
            body=FlowVersionCreate(
                spec=_spec_with({"id": "a", "type": "tool", "tool_id": str(victim_tool.id)})
            ),
            user_id=attacker["id"],
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_allows_same_workspace_agent_node(factory):
    ws = await factory.workspace()
    flow_id = await factory.flow(ws["id"])
    agent_id = await factory.agent(ws["id"])

    version = await create_flow_version(
        workspace_id=ws["id"],
        flow_id=flow_id,
        body=FlowVersionCreate(spec=_spec_with({"id": "a", "type": "agent", "agent_id": str(agent_id)})),
        user_id=ws["id"],
    )
    assert version.id is not None
