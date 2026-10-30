-- Telegram trigger (first chat-platform integration): a message sent to a connected
-- Telegram bot starts a flow run, and the run's output is sent back to that chat once it
-- finishes. services/api/app/routers/telegram.py's own docstring has the full design.

alter type trigger_kind add value 'telegram';

-- A bot token is a third-party credential (whoever has it can impersonate the bot, read
-- and send messages as it) -- the same threat model tool_credential_secrets already
-- exists for, not the webhook trigger's plain triggers.config secret (triggers.py's own
-- docstring: "a webhook secret only has to be hard to guess, not survive the same threat
-- model as a third-party API key" -- a bot token has to survive exactly that threat
-- model). One row per trigger, not a separate credentials-metadata table the way tools
-- get one: a trigger row already *is* the metadata, and there's exactly one bot token per
-- Telegram trigger, never more than one credential per trigger the way a tool can
-- accumulate several.
create table trigger_credential_secrets (
  trigger_id uuid primary key references triggers(id) on delete cascade,
  secret_encrypted bytea not null
);

alter table trigger_credential_secrets enable row level security;
-- Default-deny for every client role, same posture tool_credential_secrets already has --
-- every read (orchestrator, decrypting to call sendMessage) and write (triggers.py,
-- encrypting on create) goes through a pooled, non-RLS'd connection, never a
-- client-authenticated request.
