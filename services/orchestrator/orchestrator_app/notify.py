"""Postgres NOTIFY for run/run_steps changes (docs/03-frontend.md §6) -- same
postgres-native instinct as run_queue's own SKIP LOCKED job queue in main.py:
no separate pub/sub service, just a channel on the database already here.

The payload is only the run_id, never the row itself -- a listener re-fetches
the real row through the normal REST endpoints rather than trusting a
notify payload that could in principle race ahead of (or behind) what's
actually committed.
"""

import asyncpg


async def notify_run(conn: asyncpg.Connection, run_id) -> None:
    await conn.execute("select pg_notify('run_events', $1)", str(run_id))
