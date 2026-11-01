from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth import AuthError, verify_token
from app.db import get_pool

# A proper security scheme (not `authorization: str = Header(...)`) matters
# beyond self-documentation: FastAPI represents a plain Header() dependency
# in the OpenAPI schema as a required per-endpoint header *parameter*, which
# makes openapi-typescript generate a client where every call site must pass
# the token explicitly -- defeating the point of attaching it once via
# openapi-fetch middleware (apps/web/lib/api.ts). HTTPBearer shows up as a
# `security` requirement instead, which the generated client doesn't fight.
bearer_scheme = HTTPBearer()


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> UUID:
    try:
        sub = await verify_token(credentials.credentials)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        return UUID(sub)
    except ValueError:
        raise HTTPException(status_code=401, detail="token 'sub' claim is not a valid UUID") from None


async def require_workspace_member(
    workspace_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
) -> UUID:
    """Every router mounted under `/workspaces/{workspace_id}/...` took `workspace_id`
    straight off the URL and never checked the caller was actually a member of it --
    `get_pool()` connects with the service role (app/db.py), which bypasses the
    `workspace_members` RLS policies entirely, so nothing else was enforcing tenant
    isolation for any of those routes. Any authenticated user who knew or guessed another
    workspace's id could list, create, or mutate its agents/flows/tools/triggers/runs/
    listings/payouts. `workspace_id` is read here as a path parameter in its own right --
    FastAPI resolves it from the same URL segment the route's own path param does -- so
    swapping `Depends(get_current_user_id)` for `Depends(require_workspace_member)` at
    each call site is the entire fix, with no change to any route body.

    404, not 403: matches the existing convention for a cross-workspace lookup elsewhere
    in this API (agent_versions.py's sub_agent_id check) -- a non-member gets the same
    response as a workspace that doesn't exist, rather than a 403 confirming it does.
    """
    pool = await get_pool()
    row = await pool.fetchrow(
        "select 1 from workspace_members where workspace_id = $1 and user_id = $2",
        workspace_id,
        user_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    return user_id
