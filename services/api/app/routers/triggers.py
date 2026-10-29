"""Webhook/schedule/agent-event triggers (docs/01-architecture.md's V1 scope, picked up out
of docs/04-frontend-builder.md §10's open items). A trigger is flow-level config for *how a
run gets started*, separate from the flow spec's own trigger node (which only ever seeds a
run's input -- it's agnostic to whether the run was started manually, by a webhook POST, or
by a schedule firing). That's why this lives in its own router keyed on flow_id, not as
part of the FlowSpec the canvas edits.

Four kinds handled here: 'manual' (a placeholder row with nothing to configure -- exists
so a flow's trigger list can show "this flow can also be started directly," matching how
Runs already lets you start a flow by hand, step 7 of the build order), 'webhook',
'schedule', and 'agent_event' -- the fourth trigger_kind the schema enum allows, for one
flow starting automatically when another agent's or flow's run reaches a given terminal
status (the "flow-to-flow wiring" the webhook/schedule increment deliberately deferred).

Both webhook/schedule config and agent_event's source live in `triggers.config` jsonb, per
docs/02-schema.md §3 ("webhook secret, cron expr, etc.") -- no new secrets-split table the
way tool credentials get one (services/api/app/routers/tools.py): a webhook secret only has
to be hard to guess, not survive the same threat model as a third-party API key, so the
extra machinery isn't justified here.
"""

import secrets
from datetime import datetime
from typing import Literal
from uuid import UUID

import asyncpg
from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, model_validator

from app.db import get_pool
from app.deps import get_current_user_id

router = APIRouter(prefix="/workspaces/{workspace_id}/flows/{flow_id}/triggers", tags=["triggers"])

TriggerKind = Literal["manual", "webhook", "schedule", "agent_event"]
RunStatus = Literal["succeeded", "failed", "budget_exceeded"]


class TriggerCreate(BaseModel):
    kind: TriggerKind
    # Schedule only: a standard 5-field cron expression, validated below. Ignored for
    # 'manual' and 'webhook' -- a webhook's secret is server-generated, never caller-
    # supplied (a client-chosen secret could be weak or reused elsewhere).
    cron: str | None = None
    # agent_event only: exactly one of these names the upstream agent/flow whose run
    # completing starts *this* flow. Both workspace-scoped and resolved at create time
    # (below), not taken on faith -- a run's agent_id/flow_id is how the orchestrator
    # matches a finished run back to a trigger, so a dangling or cross-workspace id would
    # silently never fire rather than error loudly, which is worse than rejecting it here.
    source_agent_id: UUID | None = None
    source_flow_id: UUID | None = None
    on_status: RunStatus = "succeeded"

    @model_validator(mode="after")
    def _agent_event_source(self) -> "TriggerCreate":
        if self.kind == "agent_event":
            if (self.source_agent_id is None) == (self.source_flow_id is None):
                raise ValueError("agent_event triggers need exactly one of source_agent_id or source_flow_id")
        return self


class TriggerOut(BaseModel):
    id: UUID
    kind: TriggerKind
    config: dict
    enabled: bool
    last_fired_at: datetime | None
    created_at: datetime


class TriggerUpdate(BaseModel):
    enabled: bool | None = None
    # Schedule only -- lets a cron expression be edited without deleting and
    # recreating the trigger (which would also forget last_fired_at).
    cron: str | None = None
    # agent_event only -- the source (source_agent_id/source_flow_id) is immutable once
    # created (same reasoning as a webhook's secret: swapping it is a big enough semantic
    # change that delete-and-recreate is clearer than a partial edit), but which status
    # fires it is cheap to let change in place.
    on_status: RunStatus | None = None


async def _get_flow_or_404(conn, workspace_id: UUID, flow_id: UUID) -> None:
    flow = await conn.fetchrow("select id from flows where id = $1 and workspace_id = $2", flow_id, workspace_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="flow not found")


async def _resolve_agent_event_config(conn, *, workspace_id: UUID, flow_id: UUID, body: TriggerCreate) -> dict:
    if body.source_flow_id is not None:
        if body.source_flow_id == flow_id:
            raise HTTPException(status_code=400, detail="a flow can't trigger itself")
        source = await conn.fetchrow(
            "select id from flows where id = $1 and workspace_id = $2", body.source_flow_id, workspace_id
        )
        if source is None:
            raise HTTPException(status_code=404, detail=f"source flow {body.source_flow_id} not found in this workspace")
        return {"source_flow_id": str(body.source_flow_id), "on_status": body.on_status}

    source = await conn.fetchrow(
        "select id from agents where id = $1 and workspace_id = $2", body.source_agent_id, workspace_id
    )
    if source is None:
        raise HTTPException(status_code=404, detail=f"source agent {body.source_agent_id} not found in this workspace")
    return {"source_agent_id": str(body.source_agent_id), "on_status": body.on_status}


@router.get("", response_model=list[TriggerOut])
async def list_triggers(workspace_id: UUID, flow_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _get_flow_or_404(conn, workspace_id, flow_id)
        rows = await conn.fetch(
            """
            select id, kind, config, enabled, last_fired_at, created_at
            from triggers where flow_id = $1 order by created_at desc
            """,
            flow_id,
        )
    return [TriggerOut(**dict(r)) for r in rows]


@router.post("", response_model=TriggerOut, status_code=201)
async def create_trigger(
    workspace_id: UUID, flow_id: UUID, body: TriggerCreate, user_id: UUID = Depends(get_current_user_id)
):
    config: dict
    if body.kind == "schedule":
        if not body.cron:
            raise HTTPException(status_code=400, detail="schedule triggers require a cron expression")
        if not croniter.is_valid(body.cron):
            raise HTTPException(status_code=400, detail=f"invalid cron expression: {body.cron!r}")
        config = {"cron": body.cron}
    elif body.kind == "webhook":
        # token_urlsafe(32) -- same order of entropy as the Fernet keys this
        # project already generates for ENCRYPTION_KEY, plenty to resist
        # guessing against a public endpoint (services/api/app/routers/webhooks.py).
        config = {"secret": secrets.token_urlsafe(32)}
    elif body.kind == "agent_event":
        config = None  # resolved below, once we're holding a connection to validate against
    else:
        config = {}

    pool = await get_pool()
    async with pool.acquire() as conn:
        await _get_flow_or_404(conn, workspace_id, flow_id)
        if config is None:
            config = await _resolve_agent_event_config(conn, workspace_id=workspace_id, flow_id=flow_id, body=body)
        row = await conn.fetchrow(
            """
            insert into triggers (flow_id, kind, config)
            values ($1, $2, $3)
            returning id, kind, config, enabled, last_fired_at, created_at
            """,
            flow_id,
            body.kind,
            config,
        )
    return TriggerOut(**dict(row))


@router.patch("/{trigger_id}", response_model=TriggerOut)
async def update_trigger(
    workspace_id: UUID,
    flow_id: UUID,
    trigger_id: UUID,
    body: TriggerUpdate,
    user_id: UUID = Depends(get_current_user_id),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _get_flow_or_404(conn, workspace_id, flow_id)
        trigger = await conn.fetchrow(
            "select id, kind, config from triggers where id = $1 and flow_id = $2", trigger_id, flow_id
        )
        if trigger is None:
            raise HTTPException(status_code=404, detail="trigger not found")

        config = dict(trigger["config"])
        if body.cron is not None:
            if trigger["kind"] != "schedule":
                raise HTTPException(status_code=400, detail="cron can only be set on a schedule trigger")
            if not croniter.is_valid(body.cron):
                raise HTTPException(status_code=400, detail=f"invalid cron expression: {body.cron!r}")
            config = {"cron": body.cron}
        if body.on_status is not None:
            if trigger["kind"] != "agent_event":
                raise HTTPException(status_code=400, detail="on_status can only be set on an agent_event trigger")
            config = {**config, "on_status": body.on_status}

        row = await conn.fetchrow(
            """
            update triggers set enabled = coalesce($3, enabled), config = $4
            where id = $1 and flow_id = $2
            returning id, kind, config, enabled, last_fired_at, created_at
            """,
            trigger_id,
            flow_id,
            body.enabled,
            config,
        )
    return TriggerOut(**dict(row))


class DeleteOut(BaseModel):
    deleted: bool


@router.delete("/{trigger_id}", response_model=DeleteOut)
async def delete_trigger(
    workspace_id: UUID, flow_id: UUID, trigger_id: UUID, user_id: UUID = Depends(get_current_user_id)
):
    # 200 + a small body rather than 204 -- every other endpoint in this API
    # returns JSON, and the frontend's `unwrap` helper (lib/api.ts) treats an
    # empty body as an error, so this keeps DELETE on the same footing as
    # everything else instead of being the one call site that has to special-case it.
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _get_flow_or_404(conn, workspace_id, flow_id)
        try:
            result = await conn.execute("delete from triggers where id = $1 and flow_id = $2", trigger_id, flow_id)
        except asyncpg.ForeignKeyViolationError:
            # runs.trigger_id references this row with no ON DELETE clause
            # (0001_init_schema.sql) -- a run is a permanent record of what
            # happened, so once a trigger has fired at least once it can be
            # disabled (PATCH, above) but not deleted out from under its own
            # run history. Disabling is also the right move for "stop this
            # trigger" anyway; deleting only makes sense for one that never fired.
            raise HTTPException(
                status_code=409,
                detail="can't delete a trigger that has started runs — disable it instead",
            ) from None
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="trigger not found")
    return DeleteOut(deleted=True)
