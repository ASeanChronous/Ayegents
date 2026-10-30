-- Slack trigger (second chat-platform integration, after Telegram). A message posted in a
-- channel a connected Slack bot is in starts a flow run, and the run's result is posted
-- back to that channel once it finishes. services/api/app/routers/webhooks.py's own
-- docstring has the full design; it reuses Telegram's `trigger_credential_secrets` table
-- (already generic: one encrypted credential per trigger_id) for the bot token, so no new
-- table is needed here -- just the enum value.
alter type trigger_kind add value 'slack';
