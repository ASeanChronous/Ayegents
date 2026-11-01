-- Workspace team members (docs/01-architecture.md §7's `require_workspace_member` fix
-- made workspace_members actually enforced -- but nothing has ever let a workspace add a
-- *second* member to it: `handle_new_workspace` only ever inserts the creator as 'owner',
-- and no router exposes workspace_members for reading or writing at all). Closing that gap
-- the same "bring your own distribution" way Telegram/Slack's trigger setup already does:
-- this platform generates an invite link and the inviter copies/sends it however they
-- want (Slack DM, email, whatever) -- no SMTP/SendGrid/Resend integration needed, and no
-- new secret-delivery problem to solve.

create table workspace_invites (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,
  email text not null,
  role workspace_role not null default 'member',
  -- Server-generated, never caller-supplied -- same posture as a trigger's webhook
  -- secret (0001_init_schema.sql's triggers.config, carried through Telegram/Slack).
  token text not null unique,
  invited_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(),
  expires_at timestamptz not null default (now() + interval '7 days'),
  accepted_at timestamptz
);

create index workspace_invites_workspace_id_idx on workspace_invites (workspace_id);

alter table workspace_invites enable row level security;
-- No select-by-anyone policy: an invite's own API route (services/api/app/routers/
-- members.py) is reached by token, not by workspace-scoped listing, for anyone who isn't
-- already an owner/admin of the inviting workspace -- same reasoning as a trigger's
-- webhook secret not being independently readable either.
create policy "owners/admins can manage invites" on workspace_invites for all
  using (workspace_role_of(workspace_id) in ('owner', 'admin'));
