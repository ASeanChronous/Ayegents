"""Mirrors services/api/app/limits.py's daily-run-cap check -- same query, same
reasoning (plan_tier_limits.max_runs_per_day is margin protection under the
platform-subsidized flat-tier cost model, not a courtesy: see
docs/01-architecture.md §9a/§12.2). Can't import that module directly -- the API and the
orchestrator are two separate deployables with their own dependency sets, not one shared
package -- so the query is duplicated here rather than reaching across that boundary.

Returns a bool instead of raising, unlike the API's version: scheduler.py and
agent_event.py start runs from a cron tick or another run finishing, not from an HTTP
request, so there's no response to send back. Callers check the bool and simply skip
firing that trigger this tick.
"""

from uuid import UUID

import asyncpg


async def daily_run_budget_exceeded(conn: asyncpg.Connection, workspace_id: UUID) -> bool:
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
    return run_count >= limit
