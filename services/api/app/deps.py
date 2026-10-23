from uuid import UUID

from fastapi import Header, HTTPException

from app.auth import AuthError, verify_token


async def get_current_user_id(authorization: str = Header(...)) -> UUID:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="expected 'Authorization: Bearer <token>'")
    token = authorization[len("Bearer "):]

    try:
        sub = await verify_token(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        return UUID(sub)
    except ValueError:
        raise HTTPException(status_code=401, detail="token 'sub' claim is not a valid UUID") from None
