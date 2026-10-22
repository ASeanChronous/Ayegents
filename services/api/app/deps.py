from uuid import UUID

from fastapi import Header, HTTPException

# TODO: replace with real Supabase JWT verification (decode the bearer token,
# extract `sub` as the user id). Stubbed as a header for scaffold purposes only.


async def get_current_user_id(x_user_id: str = Header(...)) -> UUID:
    try:
        return UUID(x_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid user id")
