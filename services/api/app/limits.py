from uuid import UUID

import asyncpg
from fastapi import HTTPException


async def enforce_daily_run_budget(conn: asyncpg.Connection, workspace_id: UUID) -> None:
    """`plan_tier_limits.max_runs_per_day` is the one budget that bounds a workspace's
    total daily spend rather than a single run's (architecture doc §9a/§12.2: the
    platform pays the Anthropic bill, so this is pure margin protection, not a courtesy
    to the workspace). Shared by every place a run gets inserted -- routers/runs.py's
    own "Start run" button, and the three trigger-fired paths in routers/webhooks.py --
    so a workspace can't dodge its cap just by firing a trigger instead. Rolling 24h
    window, not calendar day, same reasoning as when this was first added to runs.py:
    matches what "N runs per day" actually protects against (sustained volume), not a
    specific wall-clock date. Caller must run this inside the same transaction as the
    `runs` insert it's gating, same as before this was extracted.
    """
    run_count = await conn.fetchval(
        """
        select count(*) from runs
        where workspace_id = $1 and created_at > now() - interval '24 hours'
        """,
        workspace_id,
    )
    limit = await conn.fetchval(
        """
        select l.max_runs_per_day from plan_tier_limits l
        join workspaces w on w.plan_tier = l.plan_tier
        where w.id = $1
        """,
        workspace_id,
    )
    if run_count >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"this workspace's plan allows {limit} runs per day — try again later or upgrade",
        )
