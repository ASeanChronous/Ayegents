-- Global (workspace_id null) builtin tools available to every workspace.
-- Matches the registry in services/orchestrator/app/tool_gateway.py — adding
-- a new @builtin(key) there needs a matching row here before any agent can
-- declare it.

insert into tools (workspace_id, key, name, kind, config_schema, requires_credential) values
  (null, 'current_time', 'Get the current UTC time', 'builtin', '{"type": "object", "properties": {}}', false);
