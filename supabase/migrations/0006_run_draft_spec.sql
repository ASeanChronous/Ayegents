-- A dry-run of an unsaved flow spec (docs/04-frontend-builder.md §10's "true
-- draft dry-run"): the canvas's current nodes/edges, run straight from the
-- API without saving a flow_versions row first. flow_executor.py's run_flow
-- uses this column instead of looking up flow_versions whenever it's set,
-- so this is the only schema change the feature needs -- everything else
-- (run_steps, token/cost tracking, the Runs UI) already works off a plain
-- `runs` row regardless of where its spec came from.
alter table runs add column draft_spec jsonb;
