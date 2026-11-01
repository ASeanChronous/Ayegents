"""Webhook/schedule/agent-event/telegram/slack triggers (docs/01-architecture.md's V1
scope, picked up out of docs/04-frontend-builder.md §10's open items). A trigger is
flow-level config for *how a run gets started*, separate from the flow spec's own trigger
node (which only ever seeds a run's input -- it's agnostic to whether the run was started
manually, by a webhook POST, or by a schedule firing). That's why this lives in its own
router keyed on flow_id, not as part of the FlowSpec the canvas edits.

Six kinds handled here: 'manual' (a placeholder row with nothing to configure -- exists so
a flow's trigger list can show "this flow can also be started directly," matching how Runs
already lets you start a flow by hand, step 7 of the build order), 'webhook', 'schedule',
'agent_event' (the schema's fourth kind, for one flow starting automatically when another
agent's or flow's run reaches a given terminal status), 'telegram' (the first chat-platform
trigger), and 'slack' (the second) -- a message sent to a connected bot starts this flow,
and the run's result is sent back to that chat/channel once it finishes
(services/orchestrator/orchestrator_app/telegram_reply.py / slack_reply.py).

Both chat platforms are deliberately "bring your own bot," not a shared, multi-tenant OAuth
app the way a public Slack-Directory or Teams-Store listing would be: each workspace
creates its own Telegram bot (@BotFather) or custom Slack App (api.slack.com) and pastes in
a bot token here, the same shape a tool credential already has. That's a conscious scope
line, not an oversight -- a shared public app needs OAuth install flows, per-installation
token storage, and (for Slack specifically) app-directory review; "paste your own bot's
token" sidesteps all of that at the cost of a little more setup work per workspace. See
docs/04-frontend-builder.md's Telegram and Slack sections for the full reasoning and, for
Slack specifically, the one real extra manual step it needs that Telegram doesn't (there's
no Slack equivalent of Telegram's `setWebhook` call -- the user pastes this trigger's
webhook URL into their own Slack App's Event Subscriptions config themselves).

Webhook/schedule config and agent_event's source live in `triggers.config` jsonb, per
docs/02-schema.md §3 ("webhook secret, cron expr, etc.") -- no new secrets-split table the
way tool credentials get one: a webhook secret only has to be hard to guess, not survive the
same threat model as a third-party API key. A bot token (Telegram or Slack) *is* exactly
that third-party-API-key threat model (whoever has it can impersonate the bot anywhere, not
just on this trigger), so it does not live in `config` -- it's Fernet-encrypted into
`trigger_credential_secrets` (migration `0010_telegram_trigger.sql`, reused as-is for Slack:
already generic, one encrypted credential per trigger_id), the same split
`tool_credential_secrets` already uses, via `app.secrets.encrypt_secret`. A platform's own
per-request verification secret -- Telegram's server-generated `config.webhook_secret`, or
Slack's own user-supplied `config.signing_secret` (Slack signs every request itself; there's
nothing for this server to generate) -- stays in plain `config` like a webhook trigger's own
secret: workspace-scoped and internal (the platform↔this server only), the same lower threat
tier the module docstring above already reasons about for webhook secrets, not a third-party
credential that works anywhere on that platform.
"""

import secrets
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

import asyncpg
from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, model_validator

from app.config import settings
from app.db import get_pool
from app.deps import require_workspace_member
from app.secrets import encrypt_secret
from app.slack_client import SlackAPIError, auth_test
from app.telegram_client import TelegramAPIError, get_me, set_webhook

router = APIRouter(prefix="/workspaces/{workspace_id}/flows/{flow_id}/triggers", tags=["triggers"])

TriggerKind = Literal["manual", "webhook", "schedule", "agent_event", "telegram", "slack"]
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
    # telegram and slack: the bot's own token (from @BotFather, or a custom Slack App's
    # "Bot User OAuth Token"), validated against that platform's own API at create time and
    # never returned by any endpoint afterward -- same "sent once, never shown again"
    # posture tool credentials already have.
    bot_token: str | None = None
    # slack only: the Slack App's own Signing Secret (from its Basic Information page),
    # used to verify every incoming request really came from Slack
    # (services/api/app/routers/webhooks.py). Telegram has no equivalent -- it signs
    # nothing itself, which is why that kind gets a server-*generated* webhook_secret
    # instead (see the module docstring above).
    signing_secret: str | None = None

    @model_validator(mode="after")
    def _agent_event_source(self) -> "TriggerCreate":
        if self.kind == "agent_event":
            if (self.source_agent_id is None) == (self.source_flow_id is None):
                raise ValueError("agent_event triggers need exactly one of source_agent_id or source_flow_id")
        if self.kind == "telegram" and not self.bot_token:
            raise ValueError("telegram triggers require a bot_token")
        if self.kind == "slack" and not (self.bot_token and self.signing_secret):
            raise ValueError("slack triggers require both bot_token and signing_secret")
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
async def list_triggers(workspace_id: UUID, flow_id: UUID, user_id: UUID = Depends(require_workspace_member)):
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
    workspace_id: UUID, flow_id: UUID, body: TriggerCreate, user_id: UUID = Depends(require_workspace_member)
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
    elif body.kind == "telegram":
        if not settings.api_public_url:
            raise HTTPException(
                status_code=501, detail="Telegram triggers aren't available on this deployment"
            )
        config = None  # resolved below -- needs this trigger's own id first (webhook URL)
    elif body.kind == "slack":
        if not settings.api_public_url:
            raise HTTPException(status_code=501, detail="Slack triggers aren't available on this deployment")
        config = None  # resolved below
    else:
        config = {}

    # Generated here, not left to the table's own `default gen_random_uuid()`, so a
    # telegram trigger's webhook URL (https://.../webhooks/telegram/{trigger_id}) is known
    # *before* the insert -- Telegram's own setWebhook call needs it, and there's no
    # "insert, then go back and register the URL" two-step this way. Every kind gets its
    # id assigned the same way, rather than branching the insert itself by kind -- slack
    # doesn't strictly need this (there's no setWebhook-equivalent call to make), but the
    # returned trigger's id is still what the frontend builds its own displayed webhook
    # URL from, so there's no reason to special-case it.
    trigger_id = uuid4()

    pool = await get_pool()
    async with pool.acquire() as conn:
        await _get_flow_or_404(conn, workspace_id, flow_id)
        if body.kind == "agent_event":
            config = await _resolve_agent_event_config(conn, workspace_id=workspace_id, flow_id=flow_id, body=body)
        elif body.kind == "telegram":
            try:
                bot_info = await get_me(body.bot_token)
            except TelegramAPIError as exc:
                raise HTTPException(status_code=400, detail=f"invalid Telegram bot token: {exc}") from None
            webhook_secret = secrets.token_urlsafe(32)
            webhook_url = f"{settings.api_public_url}/webhooks/telegram/{trigger_id}"
            try:
                await set_webhook(body.bot_token, webhook_url, webhook_secret)
            except TelegramAPIError as exc:
                raise HTTPException(status_code=502, detail=f"Telegram setWebhook failed: {exc}") from None
            config = {"bot_username": bot_info.get("username"), "webhook_secret": webhook_secret}
        elif body.kind == "slack":
            try:
                auth = await auth_test(body.bot_token)
            except SlackAPIError as exc:
                raise HTTPException(status_code=400, detail=f"invalid Slack bot token: {exc}") from None
            config = {
                "team": auth.get("team"),
                "bot_user_id": auth.get("user_id"),
                "signing_secret": body.signing_secret,
            }

        async with conn.transaction():
            row = await conn.fetchrow(
                """
                insert into triggers (id, flow_id, kind, config)
                values ($1, $2, $3, $4)
                returning id, kind, config, enabled, last_fired_at, created_at
                """,
                trigger_id,
                flow_id,
                body.kind,
                config,
            )
            if body.kind in ("telegram", "slack"):
                await conn.execute(
                    "insert into trigger_credential_secrets (trigger_id, secret_encrypted) values ($1, $2)",
                    trigger_id,
                    encrypt_secret(body.bot_token),
                )
    return TriggerOut(**dict(row))


@router.patch("/{trigger_id}", response_model=TriggerOut)
async def update_trigger(
    workspace_id: UUID,
    flow_id: UUID,
    trigger_id: UUID,
    body: TriggerUpdate,
    user_id: UUID = Depends(require_workspace_member),
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
    workspace_id: UUID, flow_id: UUID, trigger_id: UUID, user_id: UUID = Depends(require_workspace_member)
):
    # 200 + a small body rather than 204 -- every other endpoint in this API
    # returns JSON, and the frontend's `unwrap` helper (lib/api.ts) treats an
    # empty body as an error, so this keeps DELETE on the same footing as
    # everything else instead of being the one call site that has to special-case it.
    #
    # Known gap, deliberately not closed here: deleting a telegram trigger doesn't call
    # Telegram's own deleteWebhook first (this API's own secrets.py only ever *encrypts* --
    # it has no decrypt_secret, by design, mirroring the orchestrator-only-decrypts split
    # tool credentials already use -- so reaching the bot token to make that call would mean
    # breaking that split for one cleanup call). Telegram will keep POSTing to a now-404ing
    # URL and back off/disable the webhook itself after enough consecutive failures --
    # harmless, just not instant. Slack has no equivalent concern at all: this server never
    # registers anything with Slack in the first place (the user pastes this trigger's
    # webhook URL into their own Slack App's dashboard themselves), so there's nothing of
    # ours to unregister -- a deleted slack trigger just starts 404ing, same as the
    # Telegram case ends up after its own backoff.
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
