from uuid import UUID

from fastapi import APIRouter, Depends
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


@router.get("", response_model=list[ListingOut])
async def browse_listings(category: str | None = None, user_id: UUID = Depends(get_current_user_id)):
    pool = await get_pool()
    rows = await pool.fetch(
        """
        select id, name, description, category, install_count, rating_avg
        from listings
        where status = 'published' and ($1::text is null or category = $1)
        order by install_count desc
        """,
        category,
    )
    return [ListingOut(**dict(r)) for r in rows]
