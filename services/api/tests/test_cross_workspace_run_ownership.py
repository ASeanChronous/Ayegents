"""Regression suite for §22 (docs/01-architecture.md): create_run must reject
an agent_id/flow_id that belongs to a different workspace than the caller's
own, rather than inserting a runs row the orchestrator would only reject
later. Calls the real, unmodified create_run directly against the real
Postgres -- this is the automated version of this session's throwaway
test_cross_workspace_run.py verification script.
"""

import pytest
from fastapi import HTTPException

from app.routers.runs import RunCreate, create_run


@pytest.mark.asyncio
async def test_rejects_cross_workspace_agent_id(factory):
    attacker = await factory.workspace()
    victim = await factory.workspace()
    victim_agent_id = await factory.agent(victim["id"])

    with pytest.raises(HTTPException) as exc_info:
        await create_run(
            workspace_id=attacker["id"],
            body=RunCreate(agent_id=victim_agent_id),
            user_id=attacker["id"],  # unused inside create_run beyond the Depends() gate
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_rejects_cross_workspace_flow_id(factory):
    attacker = await factory.workspace()
    victim = await factory.workspace()
    victim_flow_id = await factory.flow(victim["id"])

    with pytest.raises(HTTPException) as exc_info:
        await create_run(
            workspace_id=attacker["id"],
            body=RunCreate(flow_id=victim_flow_id),
            user_id=attacker["id"],
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_allows_own_workspace_agent_id(factory):
    ws = await factory.workspace()
    agent_id = await factory.agent(ws["id"])

    run = await create_run(
        workspace_id=ws["id"],
        body=RunCreate(agent_id=agent_id),
        user_id=ws["id"],
    )
    assert run.status == "queued"


@pytest.mark.asyncio
async def test_allows_own_workspace_flow_id(factory):
    ws = await factory.workspace()
    flow_id = await factory.flow(ws["id"])

    run = await create_run(
        workspace_id=ws["id"],
        body=RunCreate(flow_id=flow_id),
        user_id=ws["id"],
    )
    assert run.status == "queued"
