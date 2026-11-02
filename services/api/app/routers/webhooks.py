"""The public side of webhook, telegram, and slack triggers (triggers.py handles the
authenticated create/list/update/delete side for all three). Deliberately outside
`/workspaces/{workspace_id}/...` and outside `get_current_user_id` -- the caller here is
some external system (GitHub, a cron SaaS, a user's own script, Telegram, or Slack), not a
logged-in workspace member, so there's no Supabase session to check. A generic webhook
trigger's `config.secret` is the only thing standing between "the flow owner's URL" and
"anyone on the internet"; a telegram trigger uses `config.webhook_secret` the same way,
checked against the `X-Telegram-Bot-Api-Secret-Token` header Telegram's own `setWebhook`
call registers and sends on every delivery. Slack verifies differently, since Slack signs
every request itself rather than sending back a secret we handed it: `config.signing_secret`
(the Slack App's own, pasted in at trigger-create time) feeds an HMAC-SHA256 check against
the `X-Slack-Signature`/`X-Slack-Request-Timestamp` headers, Slack's own documented scheme --
see `fire_slack_webhook` below for the exact algorithm and the replay-window check.

All three start a run exactly the way `POST /workspaces/{workspace_id}/runs` does (runs.py)
-- insert into `runs`, hand off via the `run_queue` table -- just reached from a different
front door, with `trigger_id` set and `workspace_id`/`flow_id` resolved from the trigger row
instead of taken from the request. `POST /webhooks/telegram/{trigger_id}` and `POST
/webhooks/slack/{trigger_id}` are each a distinct path *shape* from `POST
/webhooks/{trigger_id}` (two segments vs. one), so -- unlike `routers/payments.py`'s own
`POST /webhooks/stripe`, which needed registration-order care to avoid being shadowed --
there's no router-ordering concern between any of these routes.
"""

import hashlib
import hmac
import time
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request

from app.db import get_pool
from app.limits import enforce_daily_run_budget

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
        # compare_digest, not `!=` -- same reason fire_slack_webhook's HMAC check
        # below already uses it: a naive `!=` short-circuits on the first
        # mismatched byte, so its timing leaks how many leading characters of a
        # guess were right, letting an attacker recover the secret byte-by-byte
        # over repeated requests instead of needing the whole thing in one try.
        if (
            trigger is None
            or trigger["kind"] != "webhook"
            or not x_webhook_secret
            or not hmac.compare_digest(x_webhook_secret, trigger["config"].get("secret") or "")
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
            # Same cap as POST /workspaces/{workspace_id}/runs (app/limits.py) -- without
            # this, a workspace could blow straight past its plan's daily run budget for
            # free just by firing this webhook instead of using the Runs UI. Raising here
            # is fine for this one: unlike the secret check above, it only runs *after*
            # that check already passed, so a 429 reveals nothing a valid caller didn't
            # already know.
            await enforce_daily_run_budget(conn, trigger["workspace_id"])

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
        # Same 404-for-everything posture as fire_webhook above, same reason --
        # and same compare_digest fix, same reason (a timing side-channel on a
        # bot-controlled secret token is no less exploitable than on the
        # generic webhook's).
        if (
            trigger is None
            or trigger["kind"] != "telegram"
            or not x_telegram_bot_api_secret_token
            or not hmac.compare_digest(
                x_telegram_bot_api_secret_token, trigger["config"].get("webhook_secret") or ""
            )
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
            # Same daily cap as the generic webhook above (app/limits.py) -- but unlike
            # that one, this must still ack with 200 rather than raise: Telegram retries
            # a delivery that doesn't get a prompt 200, and the budget won't have cleared
            # by the next retry, so raising here would just mean Telegram hammering this
            # endpoint until its own retry budget runs out. Same "ack an uninteresting
            # update, don't error" posture already used above for a disabled trigger or a
            # non-text update.
            try:
                await enforce_daily_run_budget(conn, trigger["workspace_id"])
            except HTTPException:
                return {"ok": True}

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


def _slack_signature_valid(signing_secret: str, timestamp: str | None, signature: str | None, raw_body: bytes) -> bool:
    """Slack's own documented verification scheme: HMAC-SHA256 of `v0:{timestamp}:{raw
    body}`, keyed by the Slack App's Signing Secret, compared against `X-Slack-Signature`
    (format `v0=<hex digest>`) in constant time. The timestamp check guards against replaying
    a captured, validly-signed request long after the fact -- Slack's own docs recommend
    this same 5-minute window.
    """
    if not timestamp or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > 60 * 5:
            return False
    except ValueError:
        return False
    basestring = b"v0:" + timestamp.encode() + b":" + raw_body
    expected = "v0=" + hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/slack/{trigger_id}")
async def fire_slack_webhook(
    trigger_id: UUID,
    request: Request,
    x_slack_signature: str | None = Header(default=None, alias="X-Slack-Signature"),
    x_slack_request_timestamp: str | None = Header(default=None, alias="X-Slack-Request-Timestamp"),
    x_slack_retry_num: str | None = Header(default=None, alias="X-Slack-Retry-Num"),
):
    raw_body = await request.body()

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
        # Same 404-for-everything posture as the other two handlers above, same reason.
        if trigger is None or trigger["kind"] != "slack":
            raise HTTPException(status_code=404, detail="not found")
        if not _slack_signature_valid(
            trigger["config"].get("signing_secret", ""), x_slack_request_timestamp, x_slack_signature, raw_body
        ):
            raise HTTPException(status_code=404, detail="not found")

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 -- not a payload we understand, ack and drop
            return {"ok": True}

        # Slack's own handshake, the very first time a Request URL is saved in the Event
        # Subscriptions UI: echo the challenge back verbatim, no run involved. Still has to
        # pass signature verification above -- Slack signs this request too.
        if body.get("type") == "url_verification":
            return {"challenge": body.get("challenge")}

        if not trigger["enabled"]:
            return {"ok": True}

        # Slack retries a delivery that doesn't get a prompt 200 (same posture as Telegram,
        # same reasoning) -- X-Slack-Retry-Num present means this is a retry of a delivery
        # already accepted once, so trust that first attempt rather than risk a duplicate
        # run from processing the same event twice.
        if x_slack_retry_num:
            return {"ok": True}

        if body.get("type") != "event_callback":
            return {"ok": True}
        event = body.get("event")
        if not isinstance(event, dict) or event.get("type") != "message" or not isinstance(event.get("text"), str):
            return {"ok": True}
        # A message the bot itself posted (its own reply, or any other message from it)
        # shows up as an event too -- without this check, a flow that replies in the same
        # channel it listens on would trigger itself forever. `bot_id` catches *any* bot's
        # message (including other integrations'); the explicit user-id comparison is the
        # one that specifically catches this trigger's own bot.
        if event.get("bot_id") or event.get("user") == trigger["config"].get("bot_user_id"):
            return {"ok": True}

        run_input = {"message": event["text"], "slack_channel": event["channel"]}

        async with conn.transaction():
            # Same ack-don't-error posture as the Telegram handler above, same reason:
            # Slack retries an un-200'd delivery too.
            try:
                await enforce_daily_run_budget(conn, trigger["workspace_id"])
            except HTTPException:
                return {"ok": True}

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
