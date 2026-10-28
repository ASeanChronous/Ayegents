-- Webhook/schedule triggers (docs/01-architecture.md §V1, docs/04-frontend-builder.md
-- §10). `triggers.config` already carries "webhook secret, cron expr, etc." per
-- docs/02-schema.md §3 -- no new jsonb shape needed there. The one real gap: a schedule
-- trigger needs an anchor to compute its *next* fire time from (`croniter` takes a
-- "fire after this timestamp" base), and nothing on the row recorded that.
--
-- null means "never fired yet" -- the scheduler (services/orchestrator/orchestrator_app/
-- scheduler.py) falls back to `created_at` as the base in that case, so a schedule
-- trigger's very first fire is relative to when it was created, not an arbitrary epoch.
alter table triggers add column last_fired_at timestamptz;
