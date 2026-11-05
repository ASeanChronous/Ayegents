"""Regression suite for §23 (docs/01-architecture.md): promoting someone to
'owner' -- directly via update_member_role, or by inviting them straight to
it -- takes an existing owner, not just the ordinary admin check
require_workspace_admin already gates every other role change with. Calls
the real, unmodified update_member_role/create_invite directly against the
real Postgres -- the automated version of this session's throwaway
test_owner_promotion.py verification script.
"""

import pytest
from fastapi import HTTPException

from app.routers.members import InviteCreate, RoleUpdate, create_invite, update_member_role


@pytest.fixture
def three_roles():
    return {"owner": "owner", "admin": "admin", "member": "member"}


@pytest.mark.asyncio
async def test_admin_cannot_self_promote_to_owner(factory, three_roles):
    ws = await factory.workspace(members=three_roles)

    with pytest.raises(HTTPException) as exc_info:
        await update_member_role(
            workspace_id=ws["id"], user_id=ws["admin"], body=RoleUpdate(role="owner"), admin_id=ws["admin"]
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_promote_member_to_owner(factory, three_roles):
    ws = await factory.workspace(members=three_roles)

    with pytest.raises(HTTPException) as exc_info:
        await update_member_role(
            workspace_id=ws["id"], user_id=ws["member"], body=RoleUpdate(role="owner"), admin_id=ws["admin"]
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_invite_as_owner(factory, three_roles):
    ws = await factory.workspace(members=three_roles)

    with pytest.raises(HTTPException) as exc_info:
        await create_invite(
            workspace_id=ws["id"], body=InviteCreate(email="someone@test.invalid", role="owner"), admin_id=ws["admin"]
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_still_promote_to_admin(factory, three_roles):
    ws = await factory.workspace(members=three_roles)

    result = await update_member_role(
        workspace_id=ws["id"], user_id=ws["member"], body=RoleUpdate(role="admin"), admin_id=ws["admin"]
    )
    assert result.role == "admin"


@pytest.mark.asyncio
async def test_owner_can_promote_member_to_owner(factory, three_roles):
    ws = await factory.workspace(members=three_roles)

    result = await update_member_role(
        workspace_id=ws["id"], user_id=ws["member"], body=RoleUpdate(role="owner"), admin_id=ws["owner"]
    )
    assert result.role == "owner"


@pytest.mark.asyncio
async def test_owner_can_invite_as_owner(factory, three_roles):
    ws = await factory.workspace(members=three_roles)

    result = await create_invite(
        workspace_id=ws["id"], body=InviteCreate(email="someone-else@test.invalid", role="owner"), admin_id=ws["owner"]
    )
    assert result.role == "owner"
