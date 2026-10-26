from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth import AuthError, verify_token

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
