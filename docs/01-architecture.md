# Agent Ecosystem — Architecture v0.1

Status: draft for review. No code written yet.

## 1. Vision

A platform where users can:
1. **Build** custom AI agents with a no-code/low-code visual editor.
2. **Discover & install** pre-built agents from a marketplace.
3. Have agents **collaborate** — one agent can call another, hand off a task, or run as part of a multi-agent pipeline, coordinated by an orchestration engine.

Three products, one data model. An agent built in the editor is the same kind of object as one installed from the marketplace, and either kind can be a participant in a multi-agent run.

## 2. Core concepts

| Concept | Definition |
|---|---|
| **Agent** | A configured unit: system prompt/instructions, a model, a set of tools it's allowed to call, and optional sub-agents it can delegate to. Versioned. |
| **Tool** | A typed capability an agent can invoke: an HTTP call, a code-exec sandbox, a built-in (web search, file read), or another Agent exposed as a tool. |
| **Flow** | A no-code graph of nodes (agents, tools, conditionals, triggers) wired together — what the visual builder produces. Compiles to an **Agent** or a **multi-agent pipeline** definition. |
| **Trigger** | What starts a run: manual, webhook, schedule, or an event from another agent. |
| **Run** | One execution instance of an agent/flow: inputs, the sequence of steps taken, tool calls made, outputs, cost, status. The audit trail. |
| **Workspace** | Tenant boundary. Every Agent, Flow, Run, and installed marketplace item belongs to exactly one workspace. |
| **Listing** | A published, versioned snapshot of an Agent/Flow in the marketplace, installable into other workspaces. |

## 3. System overview

```
                         ┌─────────────────────┐
                         │   Web App (Next.js) │
                         │  - Visual builder    │
                         │  - Marketplace UI    │
                         │  - Run inspector      │
                         └──────────┬───────────┘
                                    │ REST/WS
                         ┌──────────▼───────────┐
                         │   API (FastAPI)      │
                         │  - Auth/workspace     │
                         │  - Agent/Flow CRUD    │
                         │  - Marketplace CRUD   │
                         └──────────┬───────────┘
                                    │ enqueue run
                         ┌──────────▼───────────┐
                         │  Orchestrator        │
                         │  (async worker pool) │
                         │  - plans step graph  │
                         │  - dispatches to      │
                         │    agent executors    │
                         └──────────┬───────────┘
                                    │
                 ┌──────────────────┼──────────────────┐
                 ▼                  ▼                  ▼
         ┌───────────────┐  ┌───────────────┐  ┌───────────────┐
         │ Agent Executor│  │ Agent Executor│  │ Agent Executor│
         │ (sandboxed)   │  │ (sandboxed)   │  │ (sandboxed)   │
         └───────┬───────┘  └───────┬───────┘  └───────┬───────┘
                 │                  │                  │
                 ▼                  ▼                  ▼
         ┌─────────────────────────────────────────────────┐
         │     Tool Gateway (allow-listed egress, secrets)  │
         └─────────────────────────────────────────────────┘

         Postgres (Supabase): workspaces, agents, flows, runs, tools,
         listings, installs — RLS-scoped per workspace.
         Object storage: run artifacts, uploaded files.
         Queue (Redis/pg-boss): run step dispatch.
```

Why a separate Orchestrator from the API: run execution is long-running, retryable, and needs backpressure (concurrency limits, per-workspace rate limits) — it should never share a process with request/response API handlers.

## 4. Agent builder (no-code)

- Node-based flow editor (React Flow or similar) — nodes are Agents, Tools, Conditionals, Triggers, Merge/Branch.
- Every flow compiles to a declarative JSON spec (not generated code) — this is what's versioned, diffed, and executed. The builder is a view over this spec, not the source of truth.
- Spec sketch:
```json
{
  "id": "flow_abc",
  "version": 3,
  "nodes": [
    {"id": "n1", "type": "trigger", "config": {"kind": "webhook"}},
    {"id": "n2", "type": "agent", "agent_id": "agent_123", "config": {"input_map": {...}}},
    {"id": "n3", "type": "tool", "tool_id": "tool_slack_post"}
  ],
  "edges": [{"from": "n1", "to": "n2"}, {"from": "n2", "to": "n3", "condition": "n2.output.success"}]
}
```
- Test-run mode in the builder: execute against the sandboxed executor with verbose step tracing, before publishing.

## 5. Orchestration engine

- **Single agent run**: executor loads agent config, runs a tool-use loop against the model provider (Claude via Anthropic API), enforcing max steps/cost/time budgets per workspace plan tier.
- **Multi-agent run**: a Flow with multiple agent nodes. The orchestrator walks the compiled graph; each agent node's output is mapped to the next node's input per the edge config. Supports:
  - **Sequential handoff** (pipeline): A → B → C.
  - **Parallel fan-out/fan-in**: one node triggers N agent nodes concurrently, merge node waits on all/any.
  - **Agent-as-tool**: an agent can call another agent synchronously as if it were a tool (for sub-task delegation), capped at a configurable recursion depth to prevent runaway loops. Permission model: **declared capability only** — the flow spec marks the sub-agent dependency (same mechanism as the marketplace's tool-category declarations in §6), no separate build/publish-time approval step. One consistent permission model instead of two.
- State: each Run persists a step log (agent/tool, input, output, tokens, latency, error) to Postgres as it executes — not just a final result — so a run is inspectable/resumable mid-flight.
- Model-provider abstraction: thin adapter layer so the engine isn't hard-wired to one provider, but Claude is the default/first-class target.

## 6. Marketplace

- **Listing** = a published, immutable version of an Agent or Flow + metadata (name, description, category, price if monetized, required tool permissions declared up front).
- **Install** = copies the listing's spec into the installing workspace as a new Agent/Flow instance, owned by that workspace from then on (fork, not reference) — avoids a published update silently changing behavior for installs that haven't opted in.
- Permissions model: a listing must declare which tool *categories* it needs (e.g. "web search", "sends email", "reads your files"); installer approves those categories at install time, same pattern as OAuth consent screens.
- Secrets (API keys the installed agent needs, e.g. the installer's own Slack token) are supplied by the installer post-install and stored workspace-scoped — never bundled into or visible to the listing publisher.
- V1 marketplace: free listings only, ratings + install counts. Monetization (revenue share, paid listings) deferred — needs payment/compliance work, out of scope for MVP. **Update:** both halves are now done. Paid listings (Stripe Checkout gates install, docs/05-frontend-marketplace.md §10) — a buyer can pay for a listing. Revenue share (docs/05-frontend-marketplace.md §11) — a listing's publisher actually receives a cut, via Stripe Connect Express onboarding; Stripe itself owns the KYC/tax-form/payout-schedule compliance surface this note was originally worried about, not this project.

## 7. Security & sandboxing

- Agent tool execution never runs in the API or orchestrator process. Each run's tool calls go through a **Tool Gateway**: allow-listed egress, per-workspace secrets injected at call time (never exposed to the agent's own context), and code-exec tools (if offered) run in a disposable container with no network by default.
- Per-workspace RLS in Postgres: every table scoped by `workspace_id` — **multi-tenant from day one** (resolved, see §12), mirroring the TradersEdge RLS pattern already in use. Built in now rather than retrofitted once real users/data exist. **Update — real gap found and closed:** that RLS was never actually in the request path. `app/db.py`'s pool connects with the service role (`DATABASE_URL`, not a per-request Supabase client), which bypasses RLS entirely, and nothing else was checking that the caller belonged to the `workspace_id` on the URL — every router mounted under `/workspaces/{workspace_id}/...` (agents, agent versions, flows, tools, triggers, runs, marketplace listings/installs/reviews, checkout/payouts) read `workspace_id` straight off the path and trusted it. Any authenticated user who knew or guessed another workspace's id could list, create, or mutate its agents/flows/tools/triggers/runs, publish or unpublish its listings, or hit its Stripe Checkout/Connect endpoints — classic broken object-level authorization, not a theoretical one. Found by auditing every router for a `workspace_members` check while picking the next increment, not by a user report. Fixed with one new dependency, `require_workspace_member` (`app/deps.py`) — reads `workspace_id` as the same path parameter the route already declares, looks it up against `workspace_members`, and 404s (matching the existing cross-workspace convention elsewhere in this API, e.g. agent-as-tool's `sub_agent_id` check — a non-member gets the same response as a workspace that doesn't exist) if the caller isn't a member — then swapping `Depends(get_current_user_id)` for `Depends(require_workspace_member)` at every one of those call sites, with no route body changed. Verified against the real Docker stack: signed in as the existing demo user and a freshly created second user in a second workspace, confirmed each is blocked (404) from the other's agents, runs, and payouts endpoints, confirmed a hostile cross-workspace write created no row, and confirmed both users' own-workspace access still works unchanged.
- Run budgets: max steps, max tokens, max wall-clock time, max concurrent runs — enforced per workspace tier. Cost model is **platform-subsidized flat tiers** (resolved, see §12): the platform pays the Anthropic bill, so these budgets are the margin-protection mechanism, not just an abuse guard — hard-stop with a logged reason, not a silent timeout, when a tier's cap is hit. **Update — real gap found and closed:** `plan_tier_limits` has carried all five budgets (`max_steps_per_run`, `max_tokens_per_run`, `max_wall_clock_seconds`, `max_concurrent_runs`, `max_runs_per_day`) since the first migration, but only `max_steps_per_run` was ever actually checked anywhere — the other four were fetched into the orchestrator's `limits` record and never read. Under the platform-subsidized model this isn't cosmetic: these budgets are the entire margin-protection mechanism, and four of five weren't protecting anything. Found the same way as the §7 authorization gap above — auditing what the docs claimed against what the code actually did, not a user report or an incident. Closed all four: `max_tokens_per_run` (and `agent_versions.max_tokens`, itself a second instance of the exact same gap — stored and settable since day one, never read) now stops the loop before its next step once accumulated tokens reach the budget, checked after logging the step that crossed it so the run_steps trail stays complete, same shape `max_steps` already used; `max_wall_clock_seconds` is a `time.monotonic()` deadline set once per agent *call* (not once per run — an agent-as-tool delegation already got its own fresh `max_steps` budget per call, a pre-existing scoping choice this matches rather than redesigns) and checked at the top of each loop iteration; `max_concurrent_runs` is now enforced in the orchestrator's `claim_next_run` query itself (`main.py`) — a correlated subquery excludes a workspace's queued run entirely once it already has that many runs `status = 'running'`, so a workspace at its cap is skipped (delaying only its own queue) rather than claimed and failed; `max_runs_per_day` is checked at run creation (`routers/runs.py`), a rolling 24-hour count against the workspace, 429 once at the cap. Verified: the token and wall-clock checks directly, with a mocked Anthropic client returning fixed-size tool-use responses — confirmed the loop stops after exactly the right number of calls for a 100-token budget (2 calls × 60 tokens) and for a 1-second wall-clock budget with 0.3s-per-call latency (stopped at ~1.2s, not after `max_steps`'s far higher ceiling). The concurrency check against the real `claim_next_run` SQL with seeded `running`/`queued` rows — confirmed a workspace at its cap is unclaimable and becomes claimable the instant a slot frees up. The daily cap against the real API with the free tier's limit temporarily lowered to "current count + 1" — confirmed the run at the cap succeeds and the next one 429s with the real count and limit in the message.
- Marketplace installs never carry the publisher's credentials forward (see §6).

## 8. Data model (initial sketch)

Core tables: `workspaces`, `users`, `workspace_members`, `agents`, `agent_versions`, `flows`, `flow_versions`, `tools`, `tool_credentials` (secrets, workspace-scoped, encrypted at rest), `runs`, `run_steps`, `listings`, `listing_versions`, `installs`.

Full DDL deferred to a follow-up doc (`02-schema.md`) once this shape is confirmed.

## 9. Stack

Every choice below optimizes for **zero idle cost and no per-seat SaaS**, scaling up only under real load — nothing here requires a standing monthly bill until there's usage to justify it.

- **API**: Python, FastAPI, Pydantic v2.
- **Orchestrator/executors**: Python async workers, same codebase as the API (one language, one deploy artifact, no cross-language serialization tax). Built on **LangGraph** for the graph-execution primitives — it's free/OSS, and hand-rolling retries/checkpointing/branching is weeks of work to reinvent something battle-tested for $0 license cost.
- **DB**: Postgres via Supabase, **free tier to start**, paused when inactive on free projects. One database does four jobs here instead of four vendors: relational data + auth + file storage + **pgvector** for any embedding/semantic-search needs (no separate Pinecone/Weaviate subscription).
- **Queue**: pg-boss, backed by the same Postgres instance — no separate Redis service to pay for or operate.
- **Frontend**: Next.js + TypeScript on Vercel's free/hobby tier, React Flow for the builder canvas.
- **Sandboxed tool/code execution**: pay-per-second sandboxes (e.g. E2B, Modal) rather than keeping containers warm — cost scales exactly with actual agent runs, $0 when no one is using the platform.
- **Hosting (API/orchestrator)**: a scale-to-zero platform (Fly.io or Render) for staging/low-traffic; move to always-on only once sustained concurrent run volume demands it.
- **Model provider**: Anthropic API (Claude), with **cost-tiered routing** — see §9a — not a single model for every step.

### 9a. LLM cost strategy (the dominant variable-cost line item)

This matters more than infra choice — at any real usage volume, model tokens cost more than hosting.

- **Route by task complexity, not one model for everything**:
  - Cheap/fast model (Haiku) for classification, routing/triage nodes, input validation, and any step that's "pick A or B" rather than open-ended reasoning.
  - Mid-tier (Sonnet) as the default workhorse for most agent nodes — best capability-per-dollar for general tool-use loops.
  - Top-tier (Opus) only for nodes explicitly marked as needing deep multi-step planning — opt-in per agent config, not default.
  - This routing is a property of the **Agent** config (§3), not hardcoded — each node picks its tier, the builder UI exposes it as a simple "speed/cost vs. capability" slider.
- **Prompt caching**: system prompts, tool schemas, and marketplace-listing boilerplate are identical across many runs of the same agent — cache them (Anthropic's prompt caching) so repeat runs aren't repriced at full input-token cost.
- **Batch API** for anything non-interactive (marketplace listing re-validation, scheduled/offline agent runs, eval suites) — roughly half the cost of synchronous calls when latency doesn't matter.
- **Hard per-workspace token/cost budgets** (already in §7) double as a cost-control backstop, not just an abuse guard.
- **Observability without a paid APM vendor**: the `run_steps` table (§8) already captures tokens/cost/latency per step — build a simple cost dashboard from it directly rather than paying for Datadog/Langfuse Cloud. Self-hosted Langfuse (OSS) is a fallback if the in-house dashboard isn't enough, still $0 license cost. **Done:** the workspace-facing half of this (`GET /workspaces/{workspace_id}/usage`, `apps/web/app/(app)/usage`) — built right after §7's run-budget enforcement fix landed, since enforcing all five `plan_tier_limits` budgets with zero visibility into them meant a workspace's first signal of `max_runs_per_day` or `max_tokens_per_run` was a 429 or a `budget_exceeded` run with no way to see it coming or understand it after the fact. Pure read-only aggregation, no new tables: each tier's five limits alongside this workspace's actual usage against each one — a rolling-24h run count and concurrent-running count as progress meters (turns red past 80%), cost today/this month summed from `runs.total_cost_usd`, and a count of runs that actually hit `budget_exceeded` in the last 24h, surfaced as a banner rather than buried in the Runs list. "Today" is a rolling 24-hour window everywhere on this page, deliberately matching what `create_run`'s `max_runs_per_day` check actually measures (§7) rather than a calendar day, so the page never shows a workspace as under budget moments before its next run call 429s. Verified against the real Docker stack with the demo workspace's actual run history (2/50 runs, $0.09 cost) and confirmed `require_workspace_member` still gates it (404 for a non-member, matching every other workspace-scoped route). `tsc --noEmit`, `npm run build` (15 routes, one new), `npm test` (9 tests, unaffected) all clean.

## 10. Phased roadmap

- **MVP**: multi-tenant from the start (workspaces + RLS, §7/§12), single-agent builder (no multi-agent yet), manual trigger only, no marketplace, flat subsidized free/pro tiers with hard run budgets — prove the agent config → run → inspect loop end-to-end.
- **V1**: multi-agent flows (sequential + agent-as-tool, declared-capability permission model), webhook/schedule triggers, marketplace (free listings, install/fork model).
- **V2**: parallel fan-out/fan-in, monetized marketplace listings, more triggers. Revisit the cost model only if usage patterns show flat tiers are mis-priced (§12) — add metering/pass-through then, not before.

## 11. Open decisions (need your input before/while building)

None remaining — the three raised in the prior draft are resolved; see §12.

## 12. Resolved decisions log

1. **Multi-tenancy**: multi-tenant from day one. Retrofitting workspace scoping after real data exists is a far more expensive migration than building it in now, and the pattern is already proven in the TradersEdge RLS design.
2. **LLM cost model**: platform-subsidized flat tiers. The platform pays the Anthropic bill; free/pro tiers get a fixed run-budget cap (§7) instead of per-token billing. Simplest to launch — no Stripe metering/usage-dashboard build required for MVP. Trade-off: margin depends on the caps being set correctly, so budgets need real usage data to tune — revisit only if usage shows the flat tiers are mis-priced (§10 V2), not preemptively.
3. **Agent-as-tool permission**: declared capability only, no separate build/publish-time approval (§5). Matches the marketplace's existing tool-category declaration pattern (§6) — one permission model, not two.
4. **Graph executor**: LangGraph over hand-rolled (§9) — free OSS, avoids reinventing checkpointing/retries.

## 13. Workspace team members

`workspace_members` (role: owner/admin/member) and its RLS have existed since the very first migration — `handle_new_workspace` inserts the creator as 'owner' on every workspace create — but until now nothing ever let a *second* person join one: no router read or wrote `workspace_members` at all, so a workspace was permanently single-member no matter how many real people needed access to it. Closed once membership enforcement itself was real (§7's `require_workspace_member` fix) — adding team management before that fix would have meant building invite/role endpoints on top of a tenant boundary that didn't actually hold.

Same "bring your own distribution" posture Telegram/Slack's trigger setup already uses for anything this platform doesn't want to own the delivery of: inviting someone generates a link (`{frontend_url}/invites/{token}`, server-generated token, same `secrets.token_urlsafe(32)` entropy as a trigger's webhook secret) and the inviter copies/sends it however they want. No SMTP/SendGrid/Resend integration, no email-deliverability problem, no bounce handling — all deliberately out of scope rather than overlooked.

New table, `workspace_invites` (`0012_workspace_invites.sql`): `email`, `role`, `token` (unique), `invited_by`, `expires_at` (7 days), `accepted_at`. An invite is looked up and accepted by token (`GET`/`POST /invites/{token}[/accept]`), not by workspace-scoped listing — a pending invitee isn't a member of the workspace yet, so there's no `/workspaces/{workspace_id}/...` route for them to call. Accepting checks the signed-in user's email (read straight from `auth.users`, the same table `require_workspace_member`'s pool already has service-role access to) against the invite's email case-insensitively, and 403s with which account it actually needs rather than silently failing or, worse, letting anyone who merely has the link in hand join as whoever they're signed in as.

First place in this API that gates on *which* role a member has, not just whether they're a member at all: `require_workspace_admin` (`app/deps.py`), alongside `require_workspace_member` — same 404-for-non-member posture, but 403 (not 404) for a real member who isn't an owner/admin, since at that point the caller has already proven they belong to the workspace and there's nothing left to hide. Used to gate inviting, revoking an invite, changing a member's role, and removing a member. Every one of those last two also protects the workspace's last owner — a workspace with zero owners could never again promote, demote, or remove anyone, since every one of those actions itself requires being an owner or admin.

Frontend: a new "Team" nav tab (`apps/web/app/(app)/team`) — member list with an inline role `<select>` and a remove button, a pending-invites list with a revoke button, and an "Invite someone" drawer whose success state shows the invite link once (never refetched or persisted, same posture as a trigger's webhook secret) with a reminder that nothing is emailed automatically. `apps/web/app/(app)/invites/[token]` is the accept page a pasted link lands on — reachable with just a session (no workspace membership needed yet, consistent with `AppShell`'s own gating), and renders exactly one of five states: invalid link, already accepted, expired, signed in as the wrong account (names the right one), or a real accept button.

Verified against the real Docker Postgres + API stack: as the existing demo workspace's owner, listed its one member, created an invite, confirmed a freshly signed-up user with a *different* email got a 403 naming the right address when trying to accept it, confirmed the actual invitee could look up the invite (`matches_current_user: true`) and accept it, confirmed accepting twice 400s, confirmed a plain member gets 403 trying to change a role, confirmed the sole owner can't demote themselves (400, last-owner protection), confirmed promoting then removing the new member both succeed, and confirmed a user who was never invited gets the same 404 a nonexistent workspace would. Then in a real browser signed in as the demo user: opened `/team`, created an invite through the UI, watched it appear in Pending invites immediately, and revoked it. `tsc --noEmit`, `npm run build` (14 routes, two new), and `npm test` (9 tests, unaffected) all clean.
