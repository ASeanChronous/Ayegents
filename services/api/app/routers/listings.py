from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_pool
from app.deps import get_current_user_id

router = APIRouter(prefix="/marketplace/listings", tags=["marketplace"])


class ListingOut(BaseModel):
    id: UUID
    name: str
    description: str | None
    category: str | None
    install_count: int
    rating_avg: float | None
    required_tool_categories: list[str]


@router.get("", response_model=list[ListingOut])
async def browse_listings(category: str | None = None, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    rows = await pool.fetch(
        """
        select id, name, description, category, install_count, rating_avg, required_tool_categories
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
        """
        select id, name, description, category, install_count, rating_avg, required_tool_categories
        from listings
        where id = $1 and status = 'published'
        """,
        listing_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="listing not found or not published")
    return ListingOut(**dict(row))
