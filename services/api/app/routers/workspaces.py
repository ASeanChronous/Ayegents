from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
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


@router.get("/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace(workspace_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    # Scoped to membership, same as list_my_workspaces — without this join, any
    # authenticated user could fetch any workspace's name/plan_tier by guessing a UUID.
    row = await pool.fetchrow(
        """
        select w.id, w.name, w.plan_tier
        from workspaces w
        join workspace_members m on m.workspace_id = w.id
        where w.id = $1 and m.user_id = $2
        """,
        workspace_id,
        user_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    return WorkspaceOut(**dict(row))


@router.post("", response_model=WorkspaceOut, status_code=201)
async def create_workspace(body: WorkspaceCreate, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # `handle_new_workspace` (the DB trigger from 0001_init_schema.sql) auto-adds
            # the creator as owner via auth.uid() -- that reads the 'request.jwt.claim.sub'
            # session setting (see supabase/local-dev-auth-shim.sql), which a direct
            # service-role Postgres connection (what this scaffold uses, as opposed to
            # going through PostgREST/Supabase's data API) never carries on its own,
            # regardless of how the HTTP layer authenticated the caller (app/auth.py does
            # that part for real). Set it explicitly so the trigger and this insert agree
            # on who the owner is. `true` scopes the setting to this transaction -- it must
            # run in the same transaction as the insert (pooled connections are reused by
            # other requests, so this must not leak).
            await conn.execute("select set_config('request.jwt.claim.sub', $1, true)", str(user_id))
            row = await conn.fetchrow(
                "insert into workspaces (name) values ($1) returning id, name, plan_tier",
                body.name,
            )
    return WorkspaceOut(**dict(row))
