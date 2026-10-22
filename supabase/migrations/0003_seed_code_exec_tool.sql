-- Global code_exec tool, dispatched via E2B in
-- services/orchestrator/orchestrator_app/tool_gateway.py. config_schema is
-- the schema the MODEL fills in when calling the tool (see docs/02-schema.md
-- §5's note on config_schema vs. agent_version_tools.config); the sandbox's
-- own settings (timeout_seconds, template) live in agent_version_tools.config
-- instead, since those are fixed per agent, not something the model chooses.

insert into tools (workspace_id, key, name, kind, config_schema, requires_credential) values
  (null, 'run_python', 'Run Python code in a sandboxed environment', 'code_exec',
   '{"type": "object", "properties": {"code": {"type": "string", "description": "Python code to execute"}}, "required": ["code"]}',
   false);
