"""The public side of webhook and telegram triggers (triggers.py handles the authenticated
create/list/update/delete side for both). Deliberately outside `/workspaces/{workspace_id}/...`
and outside `get_current_user_id` -- the caller here is some external system (GitHub, a cron
SaaS, a user's own script, or Telegram itself), not a logged-in workspace member, so there's
no Supabase session to check. A generic webhook trigger's `config.secret` is the only thing
standing between "the flow owner's URL" and "anyone on the internet"; a telegram trigger uses
`config.webhook_secret` the same way, checked against the `X-Telegram-Bot-Api-Secret-Token`
header Telegram's own `setWebhook` call registers and sends on every delivery.

Both start a run exactly the way `POST /workspaces/{workspace_id}/runs` does (runs.py) --
insert into `runs`, hand off via the `run_queue` table -- just reached from a different
front door, with `trigger_id` set and `workspace_id`/`flow_id` resolved from the trigger row
instead of taken from the request. `POST /webhooks/telegram/{trigger_id}` is a distinct path
*shape* from `POST /webhooks/{trigger_id}` (two segments vs. one), so -- unlike
`routers/payments.py`'s own `POST /webhooks/stripe`, which needed registration-order care to
avoid being shadowed -- there's no router-ordering concern between the two routes here.
"""

from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request

from app.db import get_pool

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/{trigger_id}", status_code=202)
async def fire_webhook(
    trigger_id: UUID,
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        trigger = await conn.fetchrow(
            """
            select t.id, t.kind, t.config, t.enabled, f.id as flow_id, f.workspace_id
            from triggers t join flows f on f.id = t.flow_id
            where t.id = $1
            """,
            trigger_id,
        )
        # Same 404 whether the trigger doesn't exist, isn't a webhook, or the
        # secret is wrong -- a 403/401 would confirm a guessed trigger_id is
        # real even without the secret, which a public endpoint shouldn't leak.
        if (
            trigger is None
            or trigger["kind"] != "webhook"
            or not x_webhook_secret
            or x_webhook_secret != trigger["config"].get("secret")
        ):
            raise HTTPException(status_code=404, detail="not found")
        if not trigger["enabled"]:
            raise HTTPException(status_code=409, detail="trigger is disabled")

        # Body is the run's input verbatim if it's a JSON object, {} otherwise
        # (a non-object body, or no body at all, still fires the flow -- same
        # as starting a run with no input from the Runs UI, runs.py's own
        # RunCreate.input default).
        try:
            payload = await request.json()
            run_input = payload if isinstance(payload, dict) else {}
        except Exception:  # noqa: BLE001 -- empty/non-JSON body is valid, not an error
            run_input = {}

        async with conn.transaction():
            run = await conn.fetchrow(
                """
                insert into runs (workspace_id, flow_id, trigger_id, input, status)
                values ($1, $2, $3, $4, 'queued')
                returning id
                """,
                trigger["workspace_id"],
                trigger["flow_id"],
                trigger_id,
                run_input,
            )
            await conn.execute("insert into run_queue (run_id) values ($1)", run["id"])

    return {"run_id": str(run["id"])}


@router.post("/telegram/{trigger_id}")
async def fire_telegram_webhook(
    trigger_id: UUID,
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        trigger = await conn.fetchrow(
            """
            select t.id, t.kind, t.config, t.enabled, f.id as flow_id, f.workspace_id
            from triggers t join flows f on f.id = t.flow_id
            where t.id = $1
            """,
            trigger_id,
        )
        # Same 404-for-everything posture as fire_webhook above, same reason.
        if (
            trigger is None
            or trigger["kind"] != "telegram"
            or not x_telegram_bot_api_secret_token
            or x_telegram_bot_api_secret_token != trigger["config"].get("webhook_secret")
        ):
            raise HTTPException(status_code=404, detail="not found")

        # Telegram retries a delivery that doesn't get a prompt 200 -- always ack,
        # never 4xx/5xx a well-authenticated-but-uninteresting update (trigger
        # disabled, a non-text update like a sticker or a chat-join event, a
        # malformed body), or Telegram will keep retrying and can eventually
        # disable the webhook over what isn't actually a failure.
        if not trigger["enabled"]:
            return {"ok": True}

        try:
            update = await request.json()
        except Exception:  # noqa: BLE001 -- not a payload we understand, ack and drop
            return {"ok": True}

        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict) or not isinstance(message.get("text"), str):
            return {"ok": True}

        run_input = {"message": message["text"], "telegram_chat_id": message["chat"]["id"]}

        async with conn.transaction():
            run = await conn.fetchrow(
                """
                insert into runs (workspace_id, flow_id, trigger_id, input, status)
                values ($1, $2, $3, $4, 'queued')
                returning id
                """,
                trigger["workspace_id"],
                trigger["flow_id"],
                trigger_id,
                run_input,
            )
            await conn.execute("insert into run_queue (run_id) values ($1)", run["id"])

    return {"ok": True}
