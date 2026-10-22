from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_pool
from app.deps import get_current_user_id

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    name: str


class WorkspaceOut(BaseModel):
    id: UUID
    name: str
    plan_tier: str


@router.get("", response_model=list[WorkspaceOut])
async def list_my_workspaces(user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    rows = await pool.fetch(
        """
        select w.id, w.name, w.plan_tier
        from workspaces w
        join workspace_members m on m.workspace_id = w.id
        where m.user_id = $1
        order by w.created_at desc
        """,
        user_id,
    )
    return [WorkspaceOut(**dict(r)) for r in rows]


@router.post("", response_model=WorkspaceOut, status_code=201)
async def create_workspace(body: WorkspaceCreate, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # `handle_new_workspace` (the DB trigger from 0001_init_schema.sql) auto-adds
            # the creator as owner via auth.uid() -- that only resolves correctly when
            # this connection carries the Supabase JWT claim, which a direct service-role
            # connection (what this scaffold uses today) does not provide. Until real
            # Supabase auth is wired in (see app/deps.py's TODO), set it explicitly so the
            # trigger and this insert agree on who the owner is. `true` scopes the setting
            # to this transaction -- it must run in the same transaction as the insert
            # (pooled connections are reused by other requests, so this must not leak).
            await conn.execute("select set_config('request.jwt.claim.sub', $1, true)", str(user_id))
            row = await conn.fetchrow(
                "insert into workspaces (name) values ($1) returning id, name, plan_tier",
                body.name,
            )
    return WorkspaceOut(**dict(row))
