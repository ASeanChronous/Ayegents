from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_pool
from app.deps import get_current_user_id
from app.secrets import encrypt_secret

router = APIRouter(prefix="/workspaces/{workspace_id}/tools", tags=["tools"])


class ToolCreate(BaseModel):
    key: str
    name: str
    kind: str  # 'builtin' | 'http' | 'code_exec' | 'agent'
    config_schema: dict = {}  # the input schema the MODEL fills when calling this tool
    requires_credential: bool = False


class ToolOut(BaseModel):
    id: UUID
    key: str
    name: str
    kind: str
    requires_credential: bool


@router.get("", response_model=list[ToolOut])
async def list_tools(workspace_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    rows = await pool.fetch(
        """
        select id, key, name, kind, requires_credential from tools
        where workspace_id is null or workspace_id = $1
        order by name
        """,
        workspace_id,
    )
    return [ToolOut(**dict(r)) for r in rows]


@router.post("", response_model=ToolOut, status_code=201)
async def create_tool(workspace_id: UUID, body: ToolCreate, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        insert into tools (workspace_id, key, name, kind, config_schema, requires_credential)
        values ($1, $2, $3, $4, $5, $6)
        returning id, key, name, kind, requires_credential
        """,
        workspace_id,
        body.key,
        body.name,
        body.kind,
        body.config_schema,
        body.requires_credential,
    )
    return ToolOut(**dict(row))


class CredentialCreate(BaseModel):
    label: str
    secret: str  # plaintext, sent once; never stored or returned as-is


class CredentialOut(BaseModel):
    id: UUID
    label: str


@router.post("/{tool_id}/credentials", response_model=CredentialOut, status_code=201)
async def create_credential(
    workspace_id: UUID,
    tool_id: UUID,
    body: CredentialCreate,
    user_id: UUID = Depends(get_current_user_id),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            cred = await conn.fetchrow(
                """
                insert into tool_credentials (workspace_id, tool_id, label, created_by)
                values ($1, $2, $3, $4)
                returning id, label
                """,
                workspace_id,
                tool_id,
                body.label,
                user_id,
            )
            await conn.execute(
                "insert into tool_credential_secrets (credential_id, secret_encrypted) values ($1, $2)",
                cred["id"],
                encrypt_secret(body.secret),
            )
    return CredentialOut(**dict(cred))
