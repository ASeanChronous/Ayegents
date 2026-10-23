"""Supabase JWT verification.

A real Supabase user token's `sub` claim is the user's UUID, but the token
can be signed either way Supabase supports: the shared HS256 secret
(legacy, still valid on older projects) or an asymmetric key (current
default, rotatable via a JWKS endpoint). Re-implementing both signature
schemes ourselves -- plus JWKS fetching, caching, and key-rotation handling
-- is exactly the complexity Supabase's own docs say to avoid: verify by
asking the Auth server itself, `GET /auth/v1/user`. A 200 response is
Supabase's own definition of "valid," regardless of which algorithm signed
it; anything else is rejected outright.

The `sub` is read from the token's own payload (a JWT payload is base64,
not encrypted — no secret needed to read it), not parsed out of the
/auth/v1/user response body: the response's exact schema isn't part of
Supabase's documented contract the way the 200/non-200 status is. Reading
the payload without re-verifying its signature is safe here specifically
because trust comes from the remote call succeeding, not from this decode —
this function never accepts a token the remote call didn't first approve.

_fetch_auth_user is a separate function (rather than inlined) so tests can
monkeypatch it and verify this module's own logic — header construction,
payload extraction, error handling — without a real Supabase project.
"""

import httpx
import jwt

from app.config import settings


class AuthError(Exception):
    """Raised for any invalid, missing, or unverifiable token."""


async def _fetch_auth_user(token: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=10) as client:
        return await client.get(
            f"{settings.supabase_url}/auth/v1/user",
            headers={"apikey": settings.supabase_anon_key, "Authorization": f"Bearer {token}"},
        )


async def verify_token(token: str) -> str:
    """Returns the verified user's id (as a string) or raises AuthError."""
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
    except jwt.exceptions.DecodeError as exc:
        raise AuthError(f"malformed token: {exc}") from exc

    sub = payload.get("sub")
    if not sub:
        raise AuthError("token has no 'sub' claim")

    try:
        response = await _fetch_auth_user(token)
    except httpx.HTTPError as exc:
        raise AuthError(f"could not reach Supabase Auth to verify token: {exc}") from exc

    if response.status_code != 200:
        raise AuthError(f"Supabase rejected this token (status {response.status_code})")

    return sub
