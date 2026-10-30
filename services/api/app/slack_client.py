"""Thin Slack Web API client — plain HTTPS POSTs to https://slack.com/api/<method>, no
SDK, same reason telegram_client.py doesn't use one. Used only by routers/triggers.py at
trigger create/validate time (`auth.test`); the reply side (`chat.postMessage`) is the
orchestrator's own twin of this file, orchestrator_app/slack_reply.py — this half never
posts a message, the orchestrator half never validates a token, mirroring the
encrypt-on-API/decrypt-on-orchestrator split app/secrets.py already has for every other
credential in this project.
"""

import httpx

_BASE = "https://slack.com/api"


class SlackAPIError(Exception):
    """Raised when Slack's API reports `ok: false`, or the HTTP call itself fails."""


async def _call(bot_token: str, method: str, **params) -> dict:
    url = f"{_BASE}/{method}"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(url, headers={"Authorization": f"Bearer {bot_token}"}, json=params)
        except httpx.HTTPError as exc:
            raise SlackAPIError(f"could not reach Slack: {exc}") from None
    body = resp.json()
    if not body.get("ok"):
        raise SlackAPIError(body.get("error") or f"Slack API call to {method!r} failed")
    return body


async def auth_test(bot_token: str) -> dict:
    """Validates the token (a typo'd or revoked one fails here, not silently on the first
    real event) and returns `{bot_id, user_id, team, team_id, ...}`. `user_id` here is the
    bot's *own* Slack user id — stored on the trigger and used by the incoming webhook
    handler to filter the bot's own messages back out of the event stream, or a flow that
    replies in the same channel it listens on would trigger itself forever.
    """
    return await _call(bot_token, "auth.test")
