# Agent Ecosystem — Database Schema v0.1

Status: draft for review, implements the data model sketched in `01-architecture.md` §8,
with the resolved decisions from §12 (multi-tenant from day one, platform-subsidized flat
cost tiers, declared-capability agent-as-tool permissions) baked in.

Postgres via Supabase. `auth.users` is Supabase's own table — never modified here, only
referenced by `user_id uuid references auth.users(id)`.

## 1. Entity overview

```
workspaces ──┬── workspace_members ── auth.users
             ├── agents ── agent_versions ──┬── agent_version_tools ── tools
             │                              └── agent_version_subagents ── agents (self-ref)
             ├── flows ── flow_versions
             │        └── triggers
             ├── tool_credentials (secrets split out, service_role only)
             ├── runs ── run_steps
             └── listings (publish) ── listing_versions
                      ↑
               installs ── workspaces (installer)
```

Key design choices carried over from the architecture doc:
- **No circular FK for "current version"**: `agents`/`flows` don't store a `current_version_id`.
  The current version is "the row in `agent_versions`/`flow_versions` with the highest
  `version` for that parent" — queried, not stored, so publishing a new version is a
  single `INSERT`, never an `UPDATE` racing another writer.
- **Secrets are a separate table from their metadata** (`tool_credentials` vs.
  `tool_credential_secrets`), so RLS can let workspace members *see that a credential
  exists* without ever granting SELECT on the encrypted value — only the Tool Gateway's
  `service_role` connection touches the secrets table, mirroring the TradersEdge
  tier-1-write pattern.
- **Listings snapshot a spec at publish time** (`listing_versions.spec`), independent of
  the source agent/flow continuing to change — installs fork from the snapshot, never
  from a live reference (architecture doc §6).

## 2. Enums

```sql
create type workspace_role as enum ('owner', 'admin', 'member');
create type plan_tier as enum ('free', 'pro', 'enterprise');
create type publish_status as enum ('draft', 'published', 'archived');
create type model_tier as enum ('haiku', 'sonnet', 'opus');
create type trigger_kind as enum ('manual', 'webhook', 'schedule', 'agent_event');
create type run_status as enum ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'budget_exceeded');
create type step_status as enum ('succeeded', 'failed');
create type tool_kind as enum ('builtin', 'http', 'code_exec', 'agent');
```

## 3. Core tables

```sql
-- Tenancy -----------------------------------------------------------------

create table workspaces (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  plan_tier plan_tier not null default 'free',
  budget_override jsonb,            -- rare per-workspace override of plan_tier_limits
  created_at timestamptz not null default now()
);

create table workspace_members (
  workspace_id uuid not null references workspaces(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role workspace_role not null default 'member',
  created_at timestamptz not null default now(),
  primary key (workspace_id, user_id)
);

-- Platform-subsidized flat budgets (architecture §7/§9a/§12), admin-managed, not workspace-scoped
create table plan_tier_limits (
  plan_tier plan_tier primary key,
  max_steps_per_run int not null,
  max_tokens_per_run int not null,
  max_wall_clock_seconds int not null,
  max_concurrent_runs int not null,
  max_runs_per_day int not null
);

-- Agents --------------------------------------------------------------------

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
  model_tier model_tier not null default 'sonnet',  -- cost/capability slider, §9a
  max_steps int,              -- optional override of plan_tier_limits.max_steps_per_run
  max_tokens int,             -- optional override of plan_tier_limits.max_tokens_per_run
  created_at timestamptz not null default now(),
  unique (agent_id, version)
);

create table tools (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid references workspaces(id) on delete cascade,  -- null = global/builtin tool
  key text not null,          -- slug, e.g. "web_search", "slack_post"
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

-- Agent-as-tool: declared capability only (resolved decision, architecture §5/§12) —
-- no separate approval row/table, the join itself IS the declaration.
create table agent_version_subagents (
  agent_version_id uuid not null references agent_versions(id) on delete cascade,
  sub_agent_id uuid not null references agents(id),
  primary key (agent_version_id, sub_agent_id)
);

-- Flows (no-code builder output, architecture §4) ----------------------------

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
  spec jsonb not null,        -- the {nodes, edges} graph spec, architecture §4
  created_at timestamptz not null default now(),
  unique (flow_id, version)
);

create table triggers (
  id uuid primary key default gen_random_uuid(),
  flow_id uuid not null references flows(id) on delete cascade,
  kind trigger_kind not null,
  config jsonb not null default '{}',   -- webhook secret, cron expr, etc.
  enabled boolean not null default true,
  created_at timestamptz not null default now()
);

-- Credentials (secrets physically split from metadata) ----------------------

create table tool_credentials (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,
  tool_id uuid not null references tools(id),
  label text not null,
  created_by uuid not null references auth.users(id),
  created_at timestamptz not null default now()
);

-- No RLS policy grants SELECT here at all except service_role (RLS default-denies
-- when enabled with zero policies) — only the Tool Gateway's service_role connection
-- reads/writes this table.
create table tool_credential_secrets (
  credential_id uuid primary key references tool_credentials(id) on delete cascade,
  secret_encrypted bytea not null       -- encrypted via pgsodium/Vault, never plaintext
);

-- Runs ------------------------------------------------------------------------

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
  node_id text,               -- flow node id; null for a plain single-agent run
  kind text not null,         -- 'agent_call' | 'tool_call'
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

-- Marketplace -------------------------------------------------------------

create table listings (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,  -- publisher
  source_agent_id uuid references agents(id),
  source_flow_id uuid references flows(id),
  name text not null,
  description text,
  category text,
  status publish_status not null default 'draft',
  required_tool_categories text[] not null default '{}',   -- OAuth-style declared perms, §6
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
  spec jsonb not null,        -- frozen snapshot, immutable once published
  published_at timestamptz not null default now(),
  unique (listing_id, version)
);

create table installs (
  id uuid primary key default gen_random_uuid(),
  listing_id uuid not null references listings(id),
  listing_version_id uuid not null references listing_versions(id),
  installing_workspace_id uuid not null references workspaces(id) on delete cascade,
  installed_agent_id uuid references agents(id),   -- the forked copy, owned by installer
  installed_flow_id uuid references flows(id),
  approved_tool_categories text[] not null default '{}',  -- consent given at install time, §6
  installed_by uuid not null references auth.users(id),
  created_at timestamptz not null default now()
);
```

## 4. RLS strategy

Helper functions (as `security definer`, owned by a migration-only role):

```sql
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
```

Policy pattern applied to every workspace-scoped table (`agents`, `agent_versions` via join,
`flows`, `flow_versions`, `tools` where `workspace_id is not null`, `runs`, `run_steps` via
join, `listings`, `tool_credentials`):

```sql
alter table agents enable row level security;

create policy "members can read workspace agents"
  on agents for select
  using (is_workspace_member(workspace_id));

create policy "members can write workspace agents"
  on agents for insert with check (is_workspace_member(workspace_id));

create policy "members can update workspace agents"
  on agents for update using (is_workspace_member(workspace_id));

create policy "admins can delete workspace agents"
  on agents for delete
  using (workspace_role_of(workspace_id) in ('owner', 'admin'));
```

Exceptions to the standard pattern:

- **`tool_credential_secrets`**: RLS enabled, **zero policies** — default-deny for every
  role except `service_role` (which bypasses RLS entirely). The Tool Gateway is the only
  thing that ever reads a plaintext secret.
- **`tools`** where `workspace_id is null` (global/builtin tools): public `select` for any
  authenticated user, no insert/update/delete outside migrations.
- **`listings`**/**`listing_versions`** where `status = 'published'`: public `select` for
  any authenticated user (marketplace browsing crosses workspace boundaries by design) —
  write access stays workspace-scoped to the publisher.
- **`plan_tier_limits`**: public `select`, no client-side writes (admin/migration only).

## 5. Open items for `03-api-contract.md`

- Exact shape of `flow_versions.spec` and `agent_version_tools.config` JSON (currently
  "a JSON object", needs a Pydantic schema once the builder's node types are finalized).
- Whether `run_steps.tokens_input/output` needs a breakdown by model tier for the cost
  dashboard (§9a) or the per-step `cost_usd` is enough.
- Indexing pass once query patterns from the API layer are known (obvious candidates:
  `runs(workspace_id, status, created_at)`, `listings(status, category)`).
