"""Tool Gateway — the only place a tool call actually executes.

Every dispatch goes through here rather than inline in the response-handling
loop, so credential decryption, the agent-as-tool recursion cap, and (later)
egress allow-listing all live in one spot (architecture doc §7).

TODO: egress allow-listing for `http` tools isn't implemented yet — any URL
in the tool's config is called as-is. Needed before this handles untrusted
marketplace-installed tools. The code_exec sandbox below has the same gap:
E2B sandboxes have outbound internet access by default and this doesn't
restrict it — architecture doc §7's "no network by default" isn't enforced
yet, it needs a custom E2B template with egress locked down.
"""

from uuid import UUID

import asyncpg
import httpx

from orchestrator_app.config import settings
from orchestrator_app.secrets import decrypt_secret

MAX_AGENT_TOOL_DEPTH = 5


class ToolDispatchError(Exception):
    """Raised for any dispatch failure; caught by the executor and turned into
    a tool_result with is_error=True so the model can see and react to it."""


async def dispatch_tool(
    conn: asyncpg.Connection,
    *,
    tool: asyncpg.Record,
    config: dict,
    tool_input: dict,
    workspace_id: UUID,
    run_id: UUID,
    depth: int,
) -> dict:
    kind = tool["kind"]
    if kind == "builtin":
        return await _dispatch_builtin(tool["key"], tool_input)
    if kind == "http":
        return await _dispatch_http(conn, tool, config, tool_input, workspace_id)
    if kind == "agent":
        if depth >= MAX_AGENT_TOOL_DEPTH:
            raise ToolDispatchError(
                f"agent-as-tool recursion depth exceeded ({MAX_AGENT_TOOL_DEPTH})"
            )
        from orchestrator_app.executor import run_agent_as_tool  # local import: avoids a circular import

        sub_agent_id = UUID(config["sub_agent_id"])
        return await run_agent_as_tool(
            conn,
            parent_run_id=run_id,
            sub_agent_id=sub_agent_id,
            tool_input=tool_input,
            depth=depth,
        )
    if kind == "code_exec":
        return await _dispatch_code_exec(config, tool_input)
    raise ToolDispatchError(f"unknown tool kind: {kind!r}")


# ---------------------------------------------------------------------------
# builtin tools
# ---------------------------------------------------------------------------

BUILTIN_TOOLS: dict[str, callable] = {}


def builtin(key: str):
    def register(fn):
        BUILTIN_TOOLS[key] = fn
        return fn

    return register


@builtin("current_time")
async def _current_time(tool_input: dict) -> dict:
    from datetime import datetime, timezone

    return {"iso8601": datetime.now(timezone.utc).isoformat()}


async def _dispatch_builtin(key: str, tool_input: dict) -> dict:
    fn = BUILTIN_TOOLS.get(key)
    if fn is None:
        raise ToolDispatchError(f"no builtin tool registered for key {key!r}")
    return await fn(tool_input)


# ---------------------------------------------------------------------------
# http tools
# ---------------------------------------------------------------------------


async def _dispatch_http(
    conn: asyncpg.Connection, tool: asyncpg.Record, config: dict, tool_input: dict, workspace_id: UUID
) -> dict:
    url = config.get("url")
    if not url:
        raise ToolDispatchError("http tool config is missing 'url'")
    method = config.get("method", "GET").upper()
    headers = dict(config.get("headers", {}))

    if tool["requires_credential"]:
        secret = await _load_credential_secret(conn, tool_id=tool["id"], workspace_id=workspace_id)
        header_name = config.get("auth_header", "Authorization")
        headers[header_name] = config.get("auth_scheme", "Bearer {}").format(secret)

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.request(
            method,
            url,
            headers=headers,
            json=tool_input if method != "GET" else None,
            params=tool_input if method == "GET" else None,
        )
    resp.raise_for_status()
    if "application/json" in resp.headers.get("content-type", ""):
        return resp.json()
    return {"text": resp.text}


# ---------------------------------------------------------------------------
# code_exec tools — pay-per-use sandboxes (E2B), not warm containers
# (architecture doc §9: cost scales with actual runs, $0 when idle)
# ---------------------------------------------------------------------------


async def _dispatch_code_exec(config: dict, tool_input: dict) -> dict:
    code = tool_input.get("code")
    if not code:
        raise ToolDispatchError("code_exec tool call is missing 'code'")
    if not settings.e2b_api_key:
        raise ToolDispatchError(
            "code_exec tool was called but E2B_API_KEY is not configured on this orchestrator"
        )

    from e2b_code_interpreter import AsyncSandbox

    timeout_seconds = config.get("timeout_seconds", 30)
    create_kwargs = {"api_key": settings.e2b_api_key, "timeout": timeout_seconds}
    if config.get("template"):
        create_kwargs["template"] = config["template"]

    sandbox = await AsyncSandbox.create(**create_kwargs)
    try:
        execution = await sandbox.run_code(code, timeout=timeout_seconds)
    finally:
        # Disposable per call — no state (filesystem, installed packages, running
        # processes) ever survives from one tool call to the next.
        await sandbox.kill()

    if execution.error:
        raise ToolDispatchError(f"{execution.error.name}: {execution.error.value}")

    return {
        "stdout": "".join(execution.logs.stdout),
        "stderr": "".join(execution.logs.stderr),
        "results": [r.text for r in execution.results if getattr(r, "text", None)],
    }


async def _load_credential_secret(conn: asyncpg.Connection, *, tool_id: UUID, workspace_id: UUID) -> str:
    row = await conn.fetchrow(
        """
        select s.secret_encrypted
        from tool_credentials c
        join tool_credential_secrets s on s.credential_id = c.id
        where c.tool_id = $1 and c.workspace_id = $2
        order by c.created_at desc
        limit 1
        """,
        tool_id,
        workspace_id,
    )
    if row is None:
        raise ToolDispatchError("tool requires a credential but none is configured for this workspace")
    return decrypt_secret(bytes(row["secret_encrypted"]))
