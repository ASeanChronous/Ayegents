-- Agent Ecosystem — initial schema
-- Implements docs/02-schema.md. Run against a fresh Supabase project.

-- ============================================================================
-- Enums
-- ============================================================================

create type workspace_role as enum ('owner', 'admin', 'member');
create type plan_tier as enum ('free', 'pro', 'enterprise');
create type publish_status as enum ('draft', 'published', 'archived');
create type model_tier as enum ('haiku', 'sonnet', 'opus');
create type trigger_kind as enum ('manual', 'webhook', 'schedule', 'agent_event');
create type run_status as enum ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'budget_exceeded');
create type step_status as enum ('succeeded', 'failed');
create type tool_kind as enum ('builtin', 'http', 'code_exec', 'agent');

-- ============================================================================
-- Tenancy
-- ============================================================================

create table workspaces (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  plan_tier plan_tier not null default 'free',
  budget_override jsonb,
  created_at timestamptz not null default now()
);

create table workspace_members (
  workspace_id uuid not null references workspaces(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role workspace_role not null default 'member',
  created_at timestamptz not null default now(),
  primary key (workspace_id, user_id)
);

create table plan_tier_limits (
  plan_tier plan_tier primary key,
  max_steps_per_run int not null,
  max_tokens_per_run int not null,
  max_wall_clock_seconds int not null,
  max_concurrent_runs int not null,
  max_runs_per_day int not null
);

insert into plan_tier_limits (plan_tier, max_steps_per_run, max_tokens_per_run, max_wall_clock_seconds, max_concurrent_runs, max_runs_per_day) values
  ('free', 20, 50000, 120, 2, 50),
  ('pro', 100, 500000, 600, 10, 1000),
  ('enterprise', 500, 5000000, 3600, 50, 100000);

-- New workspace auto-creates its creator as owner.
create or replace function handle_new_workspace()
returns trigger language plpgsql security definer as $$
begin
  insert into workspace_members (workspace_id, user_id, role)
  values (new.id, auth.uid(), 'owner');
  return new;
end;
$$;

create trigger on_workspace_created
  after insert on workspaces
  for each row execute function handle_new_workspace();

-- ============================================================================
-- Agents
-- ============================================================================

create table agents (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,
  name text not null,
  description text,
  status publish_status not null default 'draft',
  created_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table agent_versions (
  id uuid primary key default gen_random_uuid(),
  agent_id uuid not null references agents(id) on delete cascade,
  version int not null,
  system_prompt text not null,
  model_tier model_tier not null default 'sonnet',
  max_steps int,
  max_tokens int,
  created_at timestamptz not null default now(),
  unique (agent_id, version)
);

create table tools (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid references workspaces(id) on delete cascade,
  key text not null,
  name text not null,
  kind tool_kind not null,
  config_schema jsonb not null default '{}',
  requires_credential boolean not null default false,
  created_at timestamptz not null default now(),
  unique (workspace_id, key)
);

create table agent_version_tools (
  agent_version_id uuid not null references agent_versions(id) on delete cascade,
  tool_id uuid not null references tools(id),
  config jsonb not null default '{}',
  primary key (agent_version_id, tool_id)
);

create table agent_version_subagents (
  agent_version_id uuid not null references agent_versions(id) on delete cascade,
  sub_agent_id uuid not null references agents(id),
  primary key (agent_version_id, sub_agent_id)
);

-- ============================================================================
-- Flows
-- ============================================================================

create table flows (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,
  name text not null,
  status publish_status not null default 'draft',
  created_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table flow_versions (
  id uuid primary key default gen_random_uuid(),
  flow_id uuid not null references flows(id) on delete cascade,
  version int not null,
  spec jsonb not null,
  created_at timestamptz not null default now(),
  unique (flow_id, version)
);

create table triggers (
  id uuid primary key default gen_random_uuid(),
  flow_id uuid not null references flows(id) on delete cascade,
  kind trigger_kind not null,
  config jsonb not null default '{}',
  enabled boolean not null default true,
  created_at timestamptz not null default now()
);

-- ============================================================================
-- Credentials — secrets physically split from metadata
-- ============================================================================

create table tool_credentials (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,
  tool_id uuid not null references tools(id),
  label text not null,
  created_by uuid not null references auth.users(id),
  created_at timestamptz not null default now()
);

create table tool_credential_secrets (
  credential_id uuid primary key references tool_credentials(id) on delete cascade,
  secret_encrypted bytea not null
);

-- ============================================================================
-- Runs
-- ============================================================================

create table runs (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,
  flow_id uuid references flows(id),
  flow_version_id uuid references flow_versions(id),
  agent_id uuid references agents(id),
  agent_version_id uuid references agent_versions(id),
  trigger_id uuid references triggers(id),
  status run_status not null default 'queued',
  input jsonb not null default '{}',
  output jsonb,
  total_tokens int not null default 0,
  total_cost_usd numeric(10,4) not null default 0,
  error text,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now(),
  constraint run_is_flow_xor_agent check (
    (flow_id is not null and agent_id is null) or
    (flow_id is null and agent_id is not null)
  )
);

create table run_steps (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references runs(id) on delete cascade,
  step_index int not null,
  node_id text,
  kind text not null,
  agent_id uuid references agents(id),
  tool_id uuid references tools(id),
  input jsonb,
  output jsonb,
  tokens_input int not null default 0,
  tokens_output int not null default 0,
  cost_usd numeric(10,4) not null default 0,
  latency_ms int,
  status step_status not null,
  error text,
  started_at timestamptz,
  finished_at timestamptz,
  unique (run_id, step_index)
);

-- Postgres-native job queue (SKIP LOCKED), driven from Python — no pg-boss (Node-only
-- client), no Redis. The orchestrator polls this table; see services/orchestrator.
create table run_queue (
  run_id uuid primary key references runs(id) on delete cascade,
  enqueued_at timestamptz not null default now(),
  locked_at timestamptz,
  locked_by text
);

-- ============================================================================
-- Marketplace
-- ============================================================================

create table listings (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,
  source_agent_id uuid references agents(id),
  source_flow_id uuid references flows(id),
  name text not null,
  description text,
  category text,
  status publish_status not null default 'draft',
  required_tool_categories text[] not null default '{}',
  install_count int not null default 0,
  rating_avg numeric(3,2),
  rating_count int not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint listing_is_agent_xor_flow check (
    (source_agent_id is not null and source_flow_id is null) or
    (source_agent_id is null and source_flow_id is not null)
  )
);

create table listing_versions (
  id uuid primary key default gen_random_uuid(),
  listing_id uuid not null references listings(id) on delete cascade,
  version int not null,
  spec jsonb not null,
  published_at timestamptz not null default now(),
  unique (listing_id, version)
);

create table installs (
  id uuid primary key default gen_random_uuid(),
  listing_id uuid not null references listings(id),
  listing_version_id uuid not null references listing_versions(id),
  installing_workspace_id uuid not null references workspaces(id) on delete cascade,
  installed_agent_id uuid references agents(id),
  installed_flow_id uuid references flows(id),
  approved_tool_categories text[] not null default '{}',
  installed_by uuid not null references auth.users(id),
  created_at timestamptz not null default now()
);

-- ============================================================================
-- RLS helper functions
-- ============================================================================

create or replace function is_workspace_member(ws_id uuid)
returns boolean language sql security definer stable as $$
  select exists (
    select 1 from workspace_members
    where workspace_id = ws_id and user_id = auth.uid()
  );
$$;

create or replace function workspace_role_of(ws_id uuid)
returns workspace_role language sql security definer stable as $$
  select role from workspace_members
  where workspace_id = ws_id and user_id = auth.uid();
$$;

-- ============================================================================
-- RLS policies
-- ============================================================================

alter table workspaces enable row level security;
create policy "members can read their workspace" on workspaces for select
  using (is_workspace_member(id));
create policy "any authenticated user can create a workspace" on workspaces for insert
  with check (auth.uid() is not null);
create policy "owners/admins can update workspace" on workspaces for update
  using (workspace_role_of(id) in ('owner', 'admin'));

alter table workspace_members enable row level security;
create policy "members can read membership" on workspace_members for select
  using (is_workspace_member(workspace_id));
create policy "owners/admins can manage membership" on workspace_members for all
  using (workspace_role_of(workspace_id) in ('owner', 'admin'));

alter table plan_tier_limits enable row level security;
create policy "anyone authenticated can read plan limits" on plan_tier_limits for select
  using (auth.uid() is not null);

alter table agents enable row level security;
create policy "members can read agents" on agents for select
  using (is_workspace_member(workspace_id));
create policy "members can write agents" on agents for insert
  with check (is_workspace_member(workspace_id));
create policy "members can update agents" on agents for update
  using (is_workspace_member(workspace_id));
create policy "admins can delete agents" on agents for delete
  using (workspace_role_of(workspace_id) in ('owner', 'admin'));

alter table agent_versions enable row level security;
create policy "members can read agent_versions" on agent_versions for select
  using (exists (select 1 from agents a where a.id = agent_id and is_workspace_member(a.workspace_id)));
create policy "members can create agent_versions" on agent_versions for insert
  with check (exists (select 1 from agents a where a.id = agent_id and is_workspace_member(a.workspace_id)));
-- no update/delete policy: versions are immutable once created

alter table tools enable row level security;
create policy "global tools are publicly readable" on tools for select
  using (workspace_id is null or is_workspace_member(workspace_id));
create policy "members can create workspace tools" on tools for insert
  with check (workspace_id is not null and is_workspace_member(workspace_id));
create policy "members can update workspace tools" on tools for update
  using (workspace_id is not null and is_workspace_member(workspace_id));

alter table agent_version_tools enable row level security;
create policy "members can read agent_version_tools" on agent_version_tools for select
  using (exists (
    select 1 from agent_versions av join agents a on a.id = av.agent_id
    where av.id = agent_version_id and is_workspace_member(a.workspace_id)
  ));
create policy "members can write agent_version_tools" on agent_version_tools for insert
  with check (exists (
    select 1 from agent_versions av join agents a on a.id = av.agent_id
    where av.id = agent_version_id and is_workspace_member(a.workspace_id)
  ));

alter table agent_version_subagents enable row level security;
create policy "members can read agent_version_subagents" on agent_version_subagents for select
  using (exists (
    select 1 from agent_versions av join agents a on a.id = av.agent_id
    where av.id = agent_version_id and is_workspace_member(a.workspace_id)
  ));
create policy "members can write agent_version_subagents" on agent_version_subagents for insert
  with check (exists (
    select 1 from agent_versions av join agents a on a.id = av.agent_id
    where av.id = agent_version_id and is_workspace_member(a.workspace_id)
  ));

alter table flows enable row level security;
create policy "members can read flows" on flows for select
  using (is_workspace_member(workspace_id));
create policy "members can write flows" on flows for insert
  with check (is_workspace_member(workspace_id));
create policy "members can update flows" on flows for update
  using (is_workspace_member(workspace_id));
create policy "admins can delete flows" on flows for delete
  using (workspace_role_of(workspace_id) in ('owner', 'admin'));

alter table flow_versions enable row level security;
create policy "members can read flow_versions" on flow_versions for select
  using (exists (select 1 from flows f where f.id = flow_id and is_workspace_member(f.workspace_id)));
create policy "members can create flow_versions" on flow_versions for insert
  with check (exists (select 1 from flows f where f.id = flow_id and is_workspace_member(f.workspace_id)));

alter table triggers enable row level security;
create policy "members can read triggers" on triggers for select
  using (exists (select 1 from flows f where f.id = flow_id and is_workspace_member(f.workspace_id)));
create policy "members can write triggers" on triggers for insert
  with check (exists (select 1 from flows f where f.id = flow_id and is_workspace_member(f.workspace_id)));
create policy "members can update triggers" on triggers for update
  using (exists (select 1 from flows f where f.id = flow_id and is_workspace_member(f.workspace_id)));

alter table tool_credentials enable row level security;
create policy "members can read credential metadata" on tool_credentials for select
  using (is_workspace_member(workspace_id));
create policy "members can create credentials" on tool_credentials for insert
  with check (is_workspace_member(workspace_id));
create policy "admins can delete credentials" on tool_credentials for delete
  using (workspace_role_of(workspace_id) in ('owner', 'admin'));

-- tool_credential_secrets: RLS enabled, zero policies => default-deny for every
-- role except service_role (which bypasses RLS). Only the Tool Gateway touches this.
alter table tool_credential_secrets enable row level security;

alter table runs enable row level security;
create policy "members can read runs" on runs for select
  using (is_workspace_member(workspace_id));
create policy "members can start runs" on runs for insert
  with check (is_workspace_member(workspace_id));
-- no update/delete policy for clients: only the orchestrator (service_role) transitions status

alter table run_steps enable row level security;
create policy "members can read run_steps" on run_steps for select
  using (exists (select 1 from runs r where r.id = run_id and is_workspace_member(r.workspace_id)));
-- writes are service_role only (orchestrator)

alter table run_queue enable row level security;
-- no policies: service_role only (orchestrator dequeue loop)

alter table listings enable row level security;
create policy "published listings are publicly readable" on listings for select
  using (status = 'published' or is_workspace_member(workspace_id));
create policy "members can create listings" on listings for insert
  with check (is_workspace_member(workspace_id));
create policy "members can update their listings" on listings for update
  using (is_workspace_member(workspace_id));

alter table listing_versions enable row level security;
create policy "listing_versions readable with parent listing" on listing_versions for select
  using (exists (
    select 1 from listings l where l.id = listing_id
    and (l.status = 'published' or is_workspace_member(l.workspace_id))
  ));
create policy "publishers can create listing_versions" on listing_versions for insert
  with check (exists (select 1 from listings l where l.id = listing_id and is_workspace_member(l.workspace_id)));

alter table installs enable row level security;
create policy "installing workspace can read its installs" on installs for select
  using (is_workspace_member(installing_workspace_id));
create policy "members can install listings" on installs for insert
  with check (is_workspace_member(installing_workspace_id));
