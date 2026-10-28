"""Webhook/schedule triggers (docs/01-architecture.md's V1 scope, picked up out of
docs/04-frontend-builder.md §10's open items). A trigger is flow-level config for *how a
run gets started*, separate from the flow spec's own trigger node (which only ever seeds a
run's input -- it's agnostic to whether the run was started manually, by a webhook POST, or
by a schedule firing). That's why this lives in its own router keyed on flow_id, not as
part of the FlowSpec the canvas edits.

Three kinds handled here: 'manual' (a placeholder row with nothing to configure -- exists
so a flow's trigger list can show "this flow can also be started directly," matching how
Runs already lets you start a flow by hand, step 7 of the build order), 'webhook', and
'schedule'. 'agent_event' (the fourth trigger_kind the schema enum allows, for one flow
chaining into another) is explicitly out of scope here -- nothing names it in §10's open
item, and it depends on flow-to-flow wiring this project hasn't designed yet.

Both webhook and schedule config live in `triggers.config` jsonb, per docs/02-schema.md §3
("webhook secret, cron expr, etc.") -- no new secrets-split table the way tool credentials
get one (services/api/app/routers/tools.py): a webhook secret only has to be hard to guess,
not survive the same threat model as a third-party API key, so the extra machinery isn't
justified here.
"""

import secrets
from datetime import datetime
from typing import Literal
from uuid import UUID

import asyncpg
from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_pool
from app.deps import get_current_user_id

router = APIRouter(prefix="/workspaces/{workspace_id}/flows/{flow_id}/triggers", tags=["triggers"])

TriggerKind = Literal["manual", "webhook", "schedule"]


class TriggerCreate(BaseModel):
    kind: TriggerKind
    # Schedule only: a standard 5-field cron expression, validated below. Ignored for
    # 'manual' and 'webhook' -- a webhook's secret is server-generated, never caller-
    # supplied (a client-chosen secret could be weak or reused elsewhere).
    cron: str | None = None


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


async def _get_flow_or_404(conn, workspace_id: UUID, flow_id: UUID) -> None:
    flow = await conn.fetchrow("select id from flows where id = $1 and workspace_id = $2", flow_id, workspace_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="flow not found")


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
    else:
        config = {}

    pool = await get_pool()
    async with pool.acquire() as conn:
        await _get_flow_or_404(conn, workspace_id, flow_id)
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
