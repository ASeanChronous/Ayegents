"""Workspace usage: this tier's budgets (`plan_tier_limits`) alongside how close this
workspace actually is to each one.

Every one of these five budgets is now a real hard-stop (docs/01-architecture.md §7's
"real gap found and closed" update — `max_steps_per_run` was the only one ever enforced
before that), but nothing surfaced them anywhere: a workspace could hit a brand-new 429 on
`max_runs_per_day`, or watch a run land `budget_exceeded` from `max_tokens_per_run` or
`max_wall_clock_seconds`, with no way to see it coming or understand why afterward. This
is read-only — it doesn't change what's enforced, only makes the enforcement visible
before and after the fact.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_pool
from app.deps import require_workspace_member

router = APIRouter(prefix="/workspaces/{workspace_id}/usage", tags=["usage"])


class UsageOut(BaseModel):
    plan_tier: str
    max_steps_per_run: int
    max_tokens_per_run: int
    max_wall_clock_seconds: int
    max_concurrent_runs: int
    max_runs_per_day: int
    # "Today" everywhere below is a rolling 24h window, not a calendar day --
    # matches what create_run (routers/runs.py) actually checks against
    # max_runs_per_day, so this never shows a workspace as under budget
    # right before its next run call 429s.
    runs_today: int
    concurrent_running: int
    cost_today_usd: float
    cost_this_month_usd: float
    budget_exceeded_today: int


@router.get("", response_model=UsageOut)
async def get_usage(workspace_id: UUID, user_id: UUID = Depends(require_workspace_member)):
    pool = await get_pool()
    limits = await pool.fetchrow(
        """
        select l.* from plan_tier_limits l
        join workspaces w on w.plan_tier = l.plan_tier
        where w.id = $1
        """,
        workspace_id,
    )
    counts = await pool.fetchrow(
        """
        select
            count(*) filter (where created_at > now() - interval '24 hours') as runs_today,
            count(*) filter (where status = 'running') as concurrent_running,
            count(*) filter (
                where status = 'budget_exceeded' and created_at > now() - interval '24 hours'
            ) as budget_exceeded_today,
            coalesce(sum(total_cost_usd) filter (where created_at > now() - interval '24 hours'), 0) as cost_today_usd,
            coalesce(sum(total_cost_usd) filter (where created_at > date_trunc('month', now())), 0) as cost_this_month_usd
        from runs
        where workspace_id = $1
        """,
        workspace_id,
    )
    plan_tier = await pool.fetchval("select plan_tier from workspaces where id = $1", workspace_id)
    return UsageOut(
        plan_tier=plan_tier,
        max_steps_per_run=limits["max_steps_per_run"],
        max_tokens_per_run=limits["max_tokens_per_run"],
        max_wall_clock_seconds=limits["max_wall_clock_seconds"],
        max_concurrent_runs=limits["max_concurrent_runs"],
        max_runs_per_day=limits["max_runs_per_day"],
        runs_today=counts["runs_today"],
        concurrent_running=counts["concurrent_running"],
        cost_today_usd=float(counts["cost_today_usd"]),
        cost_this_month_usd=float(counts["cost_this_month_usd"]),
        budget_exceeded_today=counts["budget_exceeded_today"],
    )
