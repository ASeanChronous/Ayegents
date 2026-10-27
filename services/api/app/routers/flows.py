from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db import get_pool
from app.deps import get_current_user_id

router = APIRouter(prefix="/workspaces/{workspace_id}/flows", tags=["flows"])


class FlowCreate(BaseModel):
    name: str


class FlowOut(BaseModel):
    id: UUID
    name: str
    status: str


@router.get("", response_model=list[FlowOut])
async def list_flows(workspace_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    rows = await pool.fetch(
        "select id, name, status from flows where workspace_id = $1 order by created_at desc",
        workspace_id,
    )
    return [FlowOut(**dict(r)) for r in rows]


@router.get("/{flow_id}", response_model=FlowOut)
async def get_flow(workspace_id: UUID, flow_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    row = await pool.fetchrow(
        "select id, name, status from flows where id = $1 and workspace_id = $2",
        flow_id,
        workspace_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="flow not found")
    return FlowOut(**dict(row))


@router.post("", response_model=FlowOut, status_code=201)
async def create_flow(workspace_id: UUID, body: FlowCreate, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        insert into flows (workspace_id, name, created_by)
        values ($1, $2, $3)
        returning id, name, status
        """,
        workspace_id,
        body.name,
        user_id,
    )
    return FlowOut(**dict(row))


# --- Flow spec: intentionally loose beyond the shape a flow genuinely needs
# (architecture doc §4) -- full per-node-type field validation (e.g. an
# 'agent' node must carry agent_id) is left to the orchestrator at run time
# (flow_executor.py raises FlowExecutionError with a clear message), not
# duplicated here as a parallel, easily-drifting schema.


class FlowNode(BaseModel):
    id: str
    type: Literal["trigger", "agent", "tool", "conditional", "merge"]
    agent_id: str | None = None
    tool_id: str | None = None
    config: dict = {}


class FlowEdge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    condition: str | None = None


class FlowSpec(BaseModel):
    nodes: list[FlowNode]
    edges: list[FlowEdge]

    @field_validator("nodes")
    @classmethod
    def _exactly_one_trigger(cls, nodes: list[FlowNode]) -> list[FlowNode]:
        triggers = [n for n in nodes if n.type == "trigger"]
        if len(triggers) != 1:
            raise ValueError(f"flow spec must have exactly one trigger node, found {len(triggers)}")
        return nodes


class FlowVersionCreate(BaseModel):
    spec: FlowSpec


class FlowVersionOut(BaseModel):
    id: UUID
    version: int


class FlowVersionDetailOut(BaseModel):
    id: UUID
    version: int
    spec: FlowSpec
    created_at: datetime


@router.get("/{flow_id}/versions", response_model=list[FlowVersionDetailOut])
async def list_flow_versions(
    workspace_id: UUID, flow_id: UUID, user_id: UUID = Depends(get_current_user_id)
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        flow = await conn.fetchrow(
            "select id from flows where id = $1 and workspace_id = $2", flow_id, workspace_id
        )
        if flow is None:
            raise HTTPException(status_code=404, detail="flow not found")

        rows = await conn.fetch(
            "select id, version, spec, created_at from flow_versions where flow_id = $1 order by version desc",
            flow_id,
        )
    return [FlowVersionDetailOut(**dict(r)) for r in rows]


@router.post("/{flow_id}/versions", response_model=FlowVersionOut, status_code=201)
async def create_flow_version(
    workspace_id: UUID,
    flow_id: UUID,
    body: FlowVersionCreate,
    user_id: UUID = Depends(get_current_user_id),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        flow = await conn.fetchrow(
            "select id from flows where id = $1 and workspace_id = $2", flow_id, workspace_id
        )
        if flow is None:
            raise HTTPException(status_code=404, detail="flow not found")

        async with conn.transaction():
            next_version = await conn.fetchval(
                "select coalesce(max(version), 0) + 1 from flow_versions where flow_id = $1",
                flow_id,
            )
            # by_alias=True so edges serialize with the "from" key flow_executor.py
            # actually reads (edge["from"]) -- "from_" only exists to dodge the
            # Python keyword on this side.
            spec_json = body.spec.model_dump(mode="json", by_alias=True)
            row = await conn.fetchrow(
                """
                insert into flow_versions (flow_id, version, spec)
                values ($1, $2, $3)
                returning id, version
                """,
                flow_id,
                next_version,
                spec_json,
            )
    return FlowVersionOut(**dict(row))
