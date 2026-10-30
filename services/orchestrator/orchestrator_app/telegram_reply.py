"""Replies to the Telegram chat that triggered a flow run, once the run finishes (both
success and failure) -- services/api/app/telegram_client.py's twin on the orchestrator
side. That half only ever calls `getMe`/`setWebhook` (trigger setup); this half only ever
calls `sendMessage` (the reply), mirroring the encrypt-on-API/decrypt-on-orchestrator split
`orchestrator_app/secrets.py` already has for tool credentials -- a Telegram bot token is
exactly that kind of third-party credential, not a webhook trigger's own low-stakes secret
(services/api/app/routers/triggers.py's own docstring has the full reasoning).

Called from flow_executor.py's `run_flow`, right alongside `fire_agent_event_triggers` --
same "after the run's own status write, best-effort, never raises back into the run" shape.
A reply failure (Telegram unreachable, the bot was removed from the chat, ...) must never
turn an otherwise-successful run into a failed one; it's logged nowhere beyond being
silently skipped, which is an accepted, deliberate gap for a first pass at this -- a flow's
own `run_steps`/output already has the real record of what happened, Telegram is just a
notification channel on top of that, not the source of truth.
"""

import json
from typing import Any
from uuid import UUID

import asyncpg
import httpx

from orchestrator_app.secrets import decrypt_secret

_BASE = "https://api.telegram.org"


async def _send_message(bot_token: str, chat_id: Any, text: str) -> None:
    url = f"{_BASE}/bot{bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            # Telegram caps message length at 4096 chars -- truncated, not
            # chunked into multiple messages, for a first pass at this.
            await client.post(url, json={"chat_id": chat_id, "text": text[:4096]})
        except httpx.HTTPError:
            pass


def _summarize(output: dict[str, Any] | None) -> str:
    """Best-effort reply text from a flow run's final output. Looks for every
    `{"output": {"text": "..."}}` shaped node (an agent node's own output contract,
    `_execute_agent_loop`'s own docstring) across `node_outputs` and joins them -- handles
    both a single-agent flow and a short multi-agent chain the same way. Falls back to a
    truncated JSON dump when nothing text-shaped is found (a flow that ends on a bare tool
    node, say), so a reply is always sent, even if it isn't pretty.
    """
    if not output:
        return "(no output)"
    node_outputs = output.get("node_outputs")
    if isinstance(node_outputs, dict):
        texts = [
            v["output"]["text"]
            for v in node_outputs.values()
            if isinstance(v, dict) and isinstance(v.get("output"), dict) and isinstance(v["output"].get("text"), str)
        ]
        if texts:
            return "\n\n".join(texts)
    if isinstance(output.get("text"), str):
        return output["text"]
    return json.dumps(output)[:4000]


async def reply_if_telegram_triggered(
    conn: asyncpg.Connection,
    *,
    trigger_id: UUID | None,
    run_input: dict,
    status: str,
    output: dict | None,
    error: str | None,
) -> None:
    if trigger_id is None:
        return
    trigger = await conn.fetchrow("select config from triggers where id = $1 and kind = 'telegram'", trigger_id)
    if trigger is None:
        return  # this run's trigger isn't a telegram one -- nothing to do

    chat_id = run_input.get("telegram_chat_id") if isinstance(run_input, dict) else None
    if chat_id is None:
        return

    secret_row = await conn.fetchrow(
        "select secret_encrypted from trigger_credential_secrets where trigger_id = $1", trigger_id
    )
    if secret_row is None:
        return
    bot_token = decrypt_secret(bytes(secret_row["secret_encrypted"]))

    text = f"⚠️ {error}" if status == "failed" else _summarize(output)
    await _send_message(bot_token, chat_id, text)
