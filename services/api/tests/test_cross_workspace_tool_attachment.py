"""Regression suite for §20's API-layer half (docs/01-architecture.md):
create_agent_version checks both attachment paths against the caller's own
workspace_id -- `sub_agent_ids` (each must be an agent in this workspace)
and `tools` (each tool_id must be a global builtin or a tool this workspace
owns) -- before inserting agent_version_tools rows. §20's orchestrator-layer
half (run_agent_as_tool's dispatch-time check) already has its own
regression test in test_cross_workspace_run_ownership.py's sibling suite
under services/orchestrator/tests/.
"""

import pytest
from fastapi import HTTPException

from app.routers.agent_versions import AgentVersionCreate, ToolAttachment, create_agent_version
from app.routers.tools import ToolCreate, create_tool


@pytest.mark.asyncio
async def test_rejects_cross_workspace_sub_agent_id(factory):
    attacker = await factory.workspace()
    victim = await factory.workspace()
    attacker_agent_id = await factory.agent(attacker["id"])
    victim_agent_id = await factory.agent(victim["id"])

    with pytest.raises(HTTPException) as exc_info:
        await create_agent_version(
            workspace_id=attacker["id"],
            agent_id=attacker_agent_id,
            body=AgentVersionCreate(system_prompt="hi", sub_agent_ids=[victim_agent_id]),
            user_id=attacker["id"],
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_rejects_cross_workspace_tool_id(factory):
    attacker = await factory.workspace()
    victim = await factory.workspace()
    attacker_agent_id = await factory.agent(attacker["id"])
    victim_tool = await create_tool(
        workspace_id=victim["id"],
        body=ToolCreate(key="victim-tool", name="Victim Tool", kind="http"),
        user_id=victim["id"],
    )

    with pytest.raises(HTTPException) as exc_info:
        await create_agent_version(
            workspace_id=attacker["id"],
            agent_id=attacker_agent_id,
            body=AgentVersionCreate(
                system_prompt="hi", tools=[ToolAttachment(tool_id=victim_tool.id, config={})]
            ),
            user_id=attacker["id"],
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_rejects_hand_made_agent_tool_naming_cross_workspace_sub_agent_id(factory):
    """The bypass §20 actually closed: attaching one's *own* kind='agent' tool
    whose config.sub_agent_id names another workspace's agent, instead of going
    through sub_agent_ids. create_agent_version's own tool_id check only validates
    ownership of the tool *row*, not its config contents -- so this attach itself
    succeeds, same as the architecture doc's own verification found. The actual
    exploit (running the victim's agent) is what run_agent_as_tool's dispatch-time
    check blocks instead -- covered in services/orchestrator/tests/."""
    attacker = await factory.workspace()
    victim = await factory.workspace()
    attacker_agent_id = await factory.agent(attacker["id"])
    victim_agent_id = await factory.agent(victim["id"])
    attacker_tool = await create_tool(
        workspace_id=attacker["id"],
        body=ToolCreate(key="sub-agent-tool", name="Sub Agent Tool", kind="agent"),
        user_id=attacker["id"],
    )

    version = await create_agent_version(
        workspace_id=attacker["id"],
        agent_id=attacker_agent_id,
        body=AgentVersionCreate(
            system_prompt="hi",
            tools=[ToolAttachment(tool_id=attacker_tool.id, config={"sub_agent_id": str(victim_agent_id)})],
        ),
        user_id=attacker["id"],
    )
    assert version.id is not None


@pytest.mark.asyncio
async def test_allows_same_workspace_sub_agent_id(factory):
    ws = await factory.workspace()
    parent_agent_id = await factory.agent(ws["id"])
    sub_agent_id = await factory.agent(ws["id"])

    version = await create_agent_version(
        workspace_id=ws["id"],
        agent_id=parent_agent_id,
        body=AgentVersionCreate(system_prompt="hi", sub_agent_ids=[sub_agent_id]),
        user_id=ws["id"],
    )
    assert version.id is not None
