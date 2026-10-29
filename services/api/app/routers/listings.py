from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_pool
from app.deps import get_current_user_id

router = APIRouter(prefix="/marketplace/listings", tags=["marketplace"])


class ListingOut(BaseModel):
    id: UUID
    kind: str
    name: str
    description: str | None
    category: str | None
    install_count: int
    rating_avg: float | None
    rating_count: int
    required_tool_categories: list[str]


class ReviewOut(BaseModel):
    id: UUID
    reviewing_workspace_id: UUID
    reviewing_workspace_name: str
    rating: int
    comment: str | None
    created_at: datetime
    updated_at: datetime


# source_agent_id/source_flow_id is a storage-level xor (the schema's check
# constraint, 0001_init_schema.sql) -- browsing/installing only cares about it
# as a one-word "kind" to pick a label or icon by, not which column is set.
_KIND_SQL = "case when source_agent_id is not null then 'agent' else 'flow' end as kind"


@router.get("", response_model=list[ListingOut])
async def browse_listings(category: str | None = None, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    rows = await pool.fetch(
        f"""
        select id, {_KIND_SQL}, name, description, category, install_count, rating_avg, rating_count,
               required_tool_categories
        from listings
        where status = 'published' and ($1::text is null or category = $1)
        order by install_count desc
        """,
        category,
    )
    return [ListingOut(**dict(r)) for r in rows]


@router.get("/{listing_id}", response_model=ListingOut)
async def get_listing(listing_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    row = await pool.fetchrow(
        f"""
        select id, {_KIND_SQL}, name, description, category, install_count, rating_avg, rating_count,
               required_tool_categories
        from listings
        where id = $1 and status = 'published'
        """,
        listing_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="listing not found or not published")
    return ListingOut(**dict(row))


@router.get("/{listing_id}/reviews", response_model=list[ReviewOut])
async def list_reviews(listing_id: UUID, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    listing_id_found = await pool.fetchval(
        "select id from listings where id = $1 and status = 'published'", listing_id
    )
    if listing_id_found is None:
        raise HTTPException(status_code=404, detail="listing not found or not published")

    rows = await pool.fetch(
        """
        select r.id, r.reviewing_workspace_id, w.name as reviewing_workspace_name,
               r.rating, r.comment, r.created_at, r.updated_at
        from listing_reviews r
        join workspaces w on w.id = r.reviewing_workspace_id
        where r.listing_id = $1
        order by r.created_at desc
        """,
        listing_id,
    )
    return [ReviewOut(**dict(r)) for r in rows]
