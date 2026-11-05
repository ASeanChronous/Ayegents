"""Workspace team members: viewing the roster, changing roles, removing a member, and
inviting someone new.

`workspace_members` (role: owner/admin/member) and its RLS have existed since
0001_init_schema.sql — `handle_new_workspace` inserts the creator as 'owner' on every
workspace create — but nothing ever let a *second* person join one: no router read or
wrote workspace_members at all. This closes that gap with the same "bring your own
distribution" posture Telegram/Slack's trigger setup already uses for secrets this
platform doesn't want to be in the business of delivering itself: inviting someone
generates a link (`frontend_url/invites/{token}`), and the inviter copies/sends it however
they want. No SMTP/SendGrid/Resend integration, no email-deliverability problem to own.

Role changes and removals are owner/admin-only (`require_workspace_admin`, app/deps.py) —
the first place in this API anything gates on *which* role a member has, not just whether
they're a member at all. Every one of them also protects the workspace's last owner: a
workspace with zero owners could never again have anyone promoted, demoted, or removed,
since every one of those actions requires being an owner or admin in the first place.

Promoting someone *to* owner (directly via a role update, or by inviting them straight to
it) is further restricted to existing owners -- an admin can manage members day-to-day,
but minting a new owner, including themselves, is a bigger grant than that role implies.
`update_member_role` and `create_invite` each check the caller's own role before allowing
`role == "owner"` in the body, on top of the `require_workspace_admin` dependency both
already use.
"""

import secrets
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.db import get_pool
from app.deps import get_current_user_id, require_workspace_admin, require_workspace_member

members_router = APIRouter(prefix="/workspaces/{workspace_id}/members", tags=["members"])
invites_router = APIRouter(prefix="/workspaces/{workspace_id}/invites", tags=["members"])
# Reached by token, not by workspace_id -- a pending invitee isn't a member of the
# workspace yet, so there's no `/workspaces/{workspace_id}/...` path for them to call.
public_invites_router = APIRouter(prefix="/invites", tags=["members"])


class MemberOut(BaseModel):
    user_id: UUID
    email: str
    role: str
    created_at: datetime


@members_router.get("", response_model=list[MemberOut])
async def list_members(workspace_id: UUID, user_id: UUID = Depends(require_workspace_member)):
    pool = await get_pool()
    rows = await pool.fetch(
        """
        select m.user_id, u.email, m.role, m.created_at
        from workspace_members m
        join auth.users u on u.id = m.user_id
        where m.workspace_id = $1
        order by m.created_at asc
        """,
        workspace_id,
    )
    return [MemberOut(**dict(r)) for r in rows]


async def _count_owners(conn, workspace_id: UUID) -> int:
    return await conn.fetchval(
        "select count(*) from workspace_members where workspace_id = $1 and role = 'owner'", workspace_id
    )


class RoleUpdate(BaseModel):
    role: str


@members_router.patch("/{user_id}", response_model=MemberOut)
async def update_member_role(
    workspace_id: UUID,
    user_id: UUID,
    body: RoleUpdate,
    admin_id: UUID = Depends(require_workspace_admin),
):
    if body.role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=400, detail="role must be one of: owner, admin, member")

    pool = await get_pool()
    async with pool.acquire() as conn:
        member = await conn.fetchrow(
            "select role from workspace_members where workspace_id = $1 and user_id = $2",
            workspace_id,
            user_id,
        )
        if member is None:
            raise HTTPException(status_code=404, detail="member not found")

        if member["role"] == "owner" and body.role != "owner" and await _count_owners(conn, workspace_id) <= 1:
            raise HTTPException(status_code=400, detail="a workspace must keep at least one owner")

        # require_workspace_admin lets an admin reach this endpoint at all, but minting a
        # new owner is a bigger grant than "can manage members" -- only an existing owner
        # can promote someone (including themselves) to owner.
        if body.role == "owner":
            caller_role = await conn.fetchval(
                "select role from workspace_members where workspace_id = $1 and user_id = $2",
                workspace_id,
                admin_id,
            )
            if caller_role != "owner":
                raise HTTPException(status_code=403, detail="only an owner can promote a member to owner")

        row = await conn.fetchrow(
            """
            update workspace_members set role = $3
            where workspace_id = $1 and user_id = $2
            returning user_id, role, created_at
            """,
            workspace_id,
            user_id,
            body.role,
        )
        email = await conn.fetchval("select email from auth.users where id = $1", user_id)
    return MemberOut(user_id=row["user_id"], email=email, role=row["role"], created_at=row["created_at"])


class DeleteOut(BaseModel):
    deleted: bool


@members_router.delete("/{user_id}", response_model=DeleteOut)
async def remove_member(
    workspace_id: UUID, user_id: UUID, admin_id: UUID = Depends(require_workspace_admin)
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        member = await conn.fetchrow(
            "select role from workspace_members where workspace_id = $1 and user_id = $2",
            workspace_id,
            user_id,
        )
        if member is None:
            raise HTTPException(status_code=404, detail="member not found")

        if member["role"] == "owner" and await _count_owners(conn, workspace_id) <= 1:
            raise HTTPException(status_code=400, detail="a workspace must keep at least one owner")

        await conn.execute(
            "delete from workspace_members where workspace_id = $1 and user_id = $2", workspace_id, user_id
        )
    return DeleteOut(deleted=True)


class InviteCreate(BaseModel):
    email: str
    role: str = "member"


class InviteOut(BaseModel):
    id: UUID
    email: str
    role: str
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None
    # Only ever populated on the create response -- the one moment the inviter needs it
    # to copy/send it themselves. Listing pending invites later (GET "") omits it, the
    # same way a trigger's webhook secret is shown once and never echoed back again.
    invite_url: str | None = None


@invites_router.get("", response_model=list[InviteOut])
async def list_invites(workspace_id: UUID, admin_id: UUID = Depends(require_workspace_admin)):
    pool = await get_pool()
    rows = await pool.fetch(
        """
        select id, email, role, created_at, expires_at, accepted_at
        from workspace_invites
        where workspace_id = $1 and accepted_at is null and expires_at > now()
        order by created_at desc
        """,
        workspace_id,
    )
    return [InviteOut(**dict(r)) for r in rows]


@invites_router.post("", response_model=InviteOut, status_code=201)
async def create_invite(
    workspace_id: UUID, body: InviteCreate, admin_id: UUID = Depends(require_workspace_admin)
):
    if body.role not in ("owner", "admin", "member"):
        raise HTTPException(status_code=400, detail="role must be one of: owner, admin, member")

    pool = await get_pool()
    # Same rule as update_member_role -- an admin can manage members, but minting a new
    # owner (even via an invite that hasn't been accepted yet) takes an existing owner.
    if body.role == "owner":
        caller_role = await pool.fetchval(
            "select role from workspace_members where workspace_id = $1 and user_id = $2",
            workspace_id,
            admin_id,
        )
        if caller_role != "owner":
            raise HTTPException(status_code=403, detail="only an owner can invite someone as owner")

    token = secrets.token_urlsafe(32)
    row = await pool.fetchrow(
        """
        insert into workspace_invites (workspace_id, email, role, token, invited_by)
        values ($1, $2, $3, $4, $5)
        returning id, email, role, created_at, expires_at, accepted_at
        """,
        workspace_id,
        body.email.strip().lower(),
        body.role,
        token,
        admin_id,
    )
    return InviteOut(**dict(row), invite_url=f"{settings.frontend_url}/invites/{token}")


@invites_router.delete("/{invite_id}", response_model=DeleteOut)
async def revoke_invite(
    workspace_id: UUID, invite_id: UUID, admin_id: UUID = Depends(require_workspace_admin)
):
    pool = await get_pool()
    deleted = await pool.fetchval(
        "delete from workspace_invites where id = $1 and workspace_id = $2 returning id",
        invite_id,
        workspace_id,
    )
    if deleted is None:
        raise HTTPException(status_code=404, detail="invite not found")
    return DeleteOut(deleted=True)


class InviteLookupOut(BaseModel):
    workspace_id: UUID
    workspace_name: str
    role: str
    email: str
    # Lets the frontend tell "you're signed in as the wrong account" apart from a plain
    # accept button, without the accept call itself being the first place that surfaces it.
    matches_current_user: bool
    expired: bool
    accepted: bool


@public_invites_router.get("/{token}", response_model=InviteLookupOut)
async def get_invite(token: str, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        select i.workspace_id, w.name as workspace_name, i.role, i.email, i.expires_at, i.accepted_at
        from workspace_invites i
        join workspaces w on w.id = i.workspace_id
        where i.token = $1
        """,
        token,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="invite not found")

    current_email = await pool.fetchval("select email from auth.users where id = $1", user_id)
    return InviteLookupOut(
        workspace_id=row["workspace_id"],
        workspace_name=row["workspace_name"],
        role=row["role"],
        email=row["email"],
        matches_current_user=(current_email or "").strip().lower() == row["email"],
        expired=row["expires_at"] < datetime.now(row["expires_at"].tzinfo),
        accepted=row["accepted_at"] is not None,
    )


@public_invites_router.post("/{token}/accept", response_model=MemberOut)
async def accept_invite(token: str, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        invite = await conn.fetchrow(
            "select * from workspace_invites where token = $1",
            token,
        )
        if invite is None:
            raise HTTPException(status_code=404, detail="invite not found")
        if invite["accepted_at"] is not None:
            raise HTTPException(status_code=400, detail="this invite has already been accepted")
        if invite["expires_at"] < datetime.now(invite["expires_at"].tzinfo):
            raise HTTPException(status_code=400, detail="this invite has expired")

        current_email = await conn.fetchval("select email from auth.users where id = $1", user_id)
        if (current_email or "").strip().lower() != invite["email"]:
            raise HTTPException(
                status_code=403,
                detail=f"this invite was sent to {invite['email']} — sign in as that account to accept it",
            )

        async with conn.transaction():
            member = await conn.fetchrow(
                """
                insert into workspace_members (workspace_id, user_id, role)
                values ($1, $2, $3)
                on conflict (workspace_id, user_id) do update set role = excluded.role
                returning user_id, role, created_at
                """,
                invite["workspace_id"],
                user_id,
                invite["role"],
            )
            await conn.execute(
                "update workspace_invites set accepted_at = now() where id = $1", invite["id"]
            )
    return MemberOut(user_id=member["user_id"], email=current_email, role=member["role"], created_at=member["created_at"])
