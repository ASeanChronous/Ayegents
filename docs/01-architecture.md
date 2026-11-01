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

## 14. Agent/flow rename and archive

Both `agents` and `flows` have carried a `status` column (`publish_status`: draft/published/archived) since the first migration, but nothing ever wrote to it after creation and there was no way to rename either one — every agent or flow created in this platform's history was permanently stuck with its original name and sat in its workspace's list forever, with no undo for a mistaken create. Listings already had exactly this problem solved for a different table (`unpublish_listing`, `routers/marketplace.py` — 'archived' instead of deleting, because install history and reviews still reference the row); agents and flows just never got the same treatment finished.

`PATCH /workspaces/{workspace_id}/agents/{agent_id}` and the equivalent for flows (`routers/agents.py`, `routers/flows.py`) take any of `name`/`description`/`status` (`exclude_unset`, so a rename never has to also resend status), 400 on an invalid status value, and 404 if the id doesn't belong to this workspace. Archiving never deletes anything — `agent_versions`, `run_steps`, and any marketplace listing snapshot all still reference the row, same reasoning as listings.

The one design decision that makes archiving actually *do* something, not just repaint a badge: every picker in this app that offers "which agent" or "which flow" — the run start drawer, a flow canvas's agent node, the agent-as-tool sub-agent checklist, the marketplace publish source picker — reuses the exact same `list_agents`/`list_flows` endpoint rather than each having its own query. `GET .../agents` and `GET .../flows` now take `include_archived` (default `false`), so excluding archived rows there is the entire fix for keeping an archived agent or flow out of every one of those pickers at once, with nothing to change at any individual call site. The only caller that needs archived rows back is the Agents/Flows list page's own "Show archived" toggle, to unarchive one.

Frontend: both list pages gained a "Show archived" checkbox, a Rename button opening a small drawer (prefilled, remounted fresh per row via a `key={agent.id}` so one long-lived drawer instance never leaks a previous row's edited-but-uncommitted state into the next), and an Archive/Unarchive button that flips `status` in place.

Verified against the real Docker stack: renamed and archived the demo workspace's Weather Scout agent via the API, confirmed it disappeared from the default `GET .../agents` list and reappeared with `include_archived=true`, confirmed an invalid status value 400s, then restored it. Same sequence for a throwaway flow (rename, archive, list-filtering both ways), then deleted it. In a real browser: archived Weather Scout from `/agents` and watched it vanish from the list immediately, toggled "Show archived" and confirmed it reappeared with the right status and an "Unarchive" button, unarchived it, then renamed it twice through the UI (once to a test name, once back) and confirmed each save landed. `tsc --noEmit`, `npm run build` (15 routes, unchanged count — no new routes, just new actions on existing pages), and `npm test` (9 tests, unaffected) all clean.

## 15. Tool credential list and delete

`CredentialCreate.secret` was always "sent once; never stored or returned as-is" by design — but that promise also meant invisible: `POST .../tools/{tool_id}/credentials` was the *only* route touching `tool_credentials`, so once a credential was created there was no way to see it existed, confirm it was still there, or remove it. `tool_gateway.py`'s `_load_credential_secret` has always picked the most-recently-created row for a given `(tool_id, workspace_id)` — so creating a new credential already silently "rotates" which one is live, and every older row just sat there untouched, un-killable, forever. Not a security hole (default-deny RLS, encrypted at rest, decrypted only orchestrator-side — same posture every credential in this project uses) but a real usability dead end: rotating a leaked key, or cleaning up a typo'd one, had no path.

`GET .../tools/{tool_id}/credentials` (`routers/tools.py`) lists `id`/`label`/`created_at` only — the secret itself is never in this response or any other, matching the original design. `DELETE .../tools/{tool_id}/credentials/{credential_id}` removes one; `tool_credential_secrets.credential_id` is `on delete cascade` (0001_init_schema.sql), so the encrypted secret goes with it in the same statement.

Frontend: the Tools page's "Add credential" button became "Manage credentials" — the drawer now lists every existing credential for that tool (label, created-at, a Delete button) above the existing add-new form, with the one `_load_credential_secret` would actually pick right now marked "● in use" so rotating or cleaning up isn't a guessing game.

Verified against the real Docker stack: created a throwaway http tool requiring a credential, confirmed its credential list started empty, created two credentials in sequence (simulating a rotation), confirmed both listed newest-first with no secret value anywhere in the response, deleted the stale first one and confirmed only the second remained, confirmed deleting a nonexistent credential id 404s. In a real browser: created a tool through the UI, opened "Manage credentials," added a credential and watched it appear immediately marked "in use" with the add form cleared and ready for another. (The Delete button's `confirm()` — the same native-dialog pattern `/team`'s "Remove member" already uses — couldn't be driven through this session's browser automation, which auto-dismisses native dialogs; the delete path itself was already verified directly against the API above.) `tsc --noEmit`, `npm run build` (15 routes, unchanged — new actions on an existing page), and `npm test` (9 tests, unaffected) all clean.

## 16. Daily run budget was only enforced on one of four paths that create a run

§14 (run budgets, this doc's earlier pass through `plan_tier_limits`) added the `max_runs_per_day` check to `POST /workspaces/{workspace_id}/runs` (`routers/runs.py`'s `create_run`) — but that's not the only place a `runs` row gets inserted. `routers/webhooks.py`'s three trigger-fired paths (`POST /webhooks/{trigger_id}`, `POST /webhooks/telegram/{trigger_id}`, `POST /webhooks/slack/{trigger_id}`) each insert straight into `runs`/`run_queue` on their own, with no budget check at all. Under the platform-subsidized flat-tier cost model (§9a/§12.2 — the platform pays the Anthropic bill, so `max_runs_per_day` is margin protection, not a courtesy), this meant the entire daily cap could be skipped for free: wire a flow's webhook trigger into a cron job (or a Telegram bot, or a Slack channel) instead of clicking "Start run," and nothing stopped it past whatever the other four per-run budgets cost individually. The three trigger paths were never in scope for the original authorization sweep either (§13's `require_workspace_member` swap) — they're deliberately public, unauthenticated endpoints (the caller is Telegram/Slack/a script, not a logged-in member), so this gap had nothing to do with that fix and needed its own.

The check itself (`runs` count in the last 24h vs. `plan_tier_limits.max_runs_per_day`, joined through the workspace's `plan_tier`) moved out of `create_run` into a new shared `app/limits.py::enforce_daily_run_budget(conn, workspace_id)`, called from inside the same transaction as the `runs` insert at all four call sites now: `create_run` itself, and each of the three webhook handlers. The four sites don't all react to going over budget the same way, because they don't have the same caller on the other end. `create_run`'s caller is a logged-in person using the product, so it keeps raising a plain `429`. The generic webhook's caller is an arbitrary external script or cron job that already proved it has the trigger's secret (the budget check only runs after that check passes), so a `429` there reveals nothing a valid caller didn't already know — it also keeps raising. Telegram and Slack are different: both platforms retry any delivery that doesn't get a prompt `200`, and this is exactly the same "ack an uninteresting update instead of erroring" posture those two handlers already use for a disabled trigger or a malformed body — raising a `429` there would just mean Telegram or Slack hammering the endpoint on their own retry schedule until it gives up, since the budget won't have cleared by the next retry either. So both catch the exception and return `{"ok": True}` without creating a run, silently dropping the delivery exactly like they already silently drop a sticker or a bot's own message.

Verified against the real Docker stack: created a throwaway workspace (`free` tier, 50 runs/day) with one flow and all three trigger kinds (webhook, telegram, slack — the last two needed a valid `X-Telegram-Bot-Api-Secret-Token` and a correctly-HMAC'd `X-Slack-Signature`/timestamp respectively, computed the same way `fire_slack_webhook` checks them). Fired the generic webhook once while under budget and confirmed a normal `202` with a `run_id`. Bulk-inserted runs directly to bring the workspace to exactly 50/50 for the rolling 24h window, then: fired the generic webhook again and got `429` with the same message `create_run` gives, with the run count unchanged afterward; fired the Telegram webhook and got `200 {"ok": true}` with the run count still unchanged; fired the Slack webhook and got the same `200 {"ok": true}`, also unchanged. Confirmed `POST /workspaces/{workspace_id}/runs` itself still 429s correctly at the same cap after the check moved into the shared helper — no regression from the extraction. Deleted all throwaway runs, triggers, the flow, and the workspace afterward. No frontend changes in this fix (pure backend budget-enforcement gap), so no `tsc`/`npm run build`/`npm test` pass was needed.

## 17. Two more run-creation paths that never saw the daily budget either

§16 above closed the gap for the three *API-side* trigger paths (`routers/webhooks.py`), but `grep -rn "insert into runs"` across both services turned up two more call sites in the *orchestrator* itself that were never in scope for that pass: `orchestrator_app/scheduler.py` (a `schedule` trigger's cron poll) and `orchestrator_app/agent_event.py` (an `agent_event` trigger firing when another agent's or flow's run finishes). Neither checked `plan_tier_limits.max_runs_per_day` at all, and both are worse than the webhook case in the same specific way: a webhook, Telegram, or Slack trigger at least needs *somebody* (or some external system) to keep calling it to rack up runs. These two fire themselves, autonomously, forever, with zero external caller to rate-limit in the first place — an enabled schedule trigger with a tight cron (`* * * * *`) would blow through a `free`-tier workspace's entire day's budget in under an hour without anyone touching anything. `agent_event.py`'s own module docstring already flagged a related, deliberately-unclosed gap (an undetected `A`'s trigger fires `B`, `B`'s fires `A` cycle — "left for later, not an oversight," relying on an operator noticing runs piling up and disabling the trigger) — before this fix, that cycle wasn't just "piling up for someone to notice," it was unbounded with no backstop at all until they did.

Couldn't reuse `app/limits.py` here — the API and the orchestrator are two separate deployables with their own dependency sets, not one shared importable package, same boundary that already keeps `encrypt_secret`/`decrypt_secret` split the way they are between `tool_gateway.py` and the API's `app/secrets.py`. So the same query (`runs` count in the last 24h vs. `plan_tier_limits.max_runs_per_day`, joined through the workspace's `plan_tier`) is duplicated into a new `orchestrator_app/limits.py::daily_run_budget_exceeded(conn, workspace_id) -> bool`. It returns a bool rather than raising, unlike the API's version — there's no HTTP response to send back from a cron tick or another run's own completion, so both callers just check the bool and skip firing that trigger this time, with a `log.info` noting why.

The two callers skip slightly differently, matching what "skip" actually means for each kind. `scheduler.py` deliberately does *not* update `last_fired_at` when it skips — the trigger stays "due," so the very next poll (every 30s, same cycle) re-checks the budget and fires the instant the rolling 24h window frees up, rather than silently dropping a scheduled tick and waiting for the next cron-computed one. `agent_event.py` has no such "next due time" to defer to in the first place — an event either fires when its source run finishes or it doesn't — so a skip there is just a dropped firing, full stop; the trigger will fire normally the next time its source run finishes, assuming the workspace is back under budget by then.

Verified against the real Docker stack (orchestrator process restarted after the code change, confirmed clean startup via `/tmp/orch.log`). For the scheduler: created a throwaway workspace/flow with a `* * * * *` schedule trigger, waited one 30s poll cycle, and confirmed it fired a run normally. Bulk-inserted runs to bring the workspace to exactly 50/50, waited through two more poll cycles, and confirmed the run count stayed at 50 while `/tmp/orch.log` logged `schedule trigger ... due but workspace ... is over its daily run budget, skipping` on each tick — proving the retry-every-poll behavior, not just a one-time skip. For `agent_event`: called `fire_agent_event_triggers` directly against the live Postgres connection (a legitimate direct test of its own logic, not a mock) with a throwaway `agent_event` trigger — confirmed it created a run while under budget (40→41), then topped the workspace back up to exactly 50/50 and confirmed a second call created no run at all (50→50). Deleted all throwaway runs, triggers, the agent, the flow, and the workspace afterward. No frontend touched, so no `tsc`/`npm run build`/`npm test` pass was needed.
