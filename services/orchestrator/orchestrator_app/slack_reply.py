"""Replies to the Slack channel that triggered a flow run, once the run finishes (both
success and failure) — services/api/app/slack_client.py's twin on the orchestrator side,
and otherwise near-identical to orchestrator_app/telegram_reply.py (same best-effort,
never-raises-into-the-run shape, same `_summarize` logic for turning a flow's final
`node_outputs` into reply text). Kept as its own file rather than sharing code with
telegram_reply.py — each platform's reply module stays readable on its own, and the two are
genuinely small; see docs/04-frontend-builder.md's Slack section for the reasoning.
"""

import json
from typing import Any
from uuid import UUID

import asyncpg
import httpx

from orchestrator_app.secrets import decrypt_secret

_BASE = "https://slack.com/api"


async def _post_message(bot_token: str, channel: str, text: str) -> None:
    url = f"{_BASE}/chat.postMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.post(
                url, headers={"Authorization": f"Bearer {bot_token}"}, json={"channel": channel, "text": text}
            )
        except httpx.HTTPError:
            pass


def _summarize(output: dict[str, Any] | None) -> str:
    """Best-effort reply text from a flow run's final output — see
    telegram_reply._summarize, identical logic."""
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


async def reply_if_slack_triggered(
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
    trigger = await conn.fetchrow("select config from triggers where id = $1 and kind = 'slack'", trigger_id)
    if trigger is None:
        return  # this run's trigger isn't a slack one -- nothing to do

    channel = run_input.get("slack_channel") if isinstance(run_input, dict) else None
    if channel is None:
        return

    secret_row = await conn.fetchrow(
        "select secret_encrypted from trigger_credential_secrets where trigger_id = $1", trigger_id
    )
    if secret_row is None:
        return
    bot_token = decrypt_secret(bytes(secret_row["secret_encrypted"]))

    text = f"⚠️ {error}" if status == "failed" else _summarize(output)
    await _post_message(bot_token, channel, text)
