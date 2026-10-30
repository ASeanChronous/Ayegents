"""Thin Telegram Bot API client — plain HTTPS POSTs to
https://api.telegram.org/bot<token>/<method>, no SDK, the same reason
tool_gateway.py's own http-kind tool dispatch doesn't use one: it's a handful of JSON
calls, not worth a dependency. Used only by routers/triggers.py at trigger create/validate
time (getMe, setWebhook); the reply side (sendMessage) is the orchestrator's own twin of
this file, orchestrator_app/telegram_reply.py — this half never sends a message, the
orchestrator half never registers a webhook, mirroring the encrypt-on-API/decrypt-on-
orchestrator split app/secrets.py already has for tool credentials.
"""

import httpx

_BASE = "https://api.telegram.org"


class TelegramAPIError(Exception):
    """Raised when Telegram's API reports `ok: false`, or the HTTP call itself fails."""


async def _call(bot_token: str, method: str, **params) -> dict:
    url = f"{_BASE}/bot{bot_token}/{method}"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(url, json=params)
        except httpx.HTTPError as exc:
            raise TelegramAPIError(f"could not reach Telegram: {exc}") from None
    body = resp.json()
    if not body.get("ok"):
        raise TelegramAPIError(body.get("description") or f"Telegram API call to {method!r} failed")
    return body["result"]


async def get_me(bot_token: str) -> dict:
    """Validates the token (a typo'd or revoked one fails here, not silently later) and
    returns the bot's own user info — just its `username`, for display."""
    return await _call(bot_token, "getMe")


async def set_webhook(bot_token: str, url: str, secret_token: str) -> None:
    await _call(bot_token, "setWebhook", url=url, secret_token=secret_token)
