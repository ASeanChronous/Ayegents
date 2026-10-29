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
- Per-workspace RLS in Postgres: every table scoped by `workspace_id` — **multi-tenant from day one** (resolved, see §12), mirroring the TradersEdge RLS pattern already in use. Built in now rather than retrofitted once real users/data exist.
- Run budgets: max steps, max tokens, max wall-clock time, max concurrent runs — enforced per workspace tier. Cost model is **platform-subsidized flat tiers** (resolved, see §12): the platform pays the Anthropic bill, so these budgets are the margin-protection mechanism, not just an abuse guard — hard-stop with a logged reason, not a silent timeout, when a tier's cap is hit.
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
- **Observability without a paid APM vendor**: the `run_steps` table (§8) already captures tokens/cost/latency per step — build a simple cost dashboard from it directly rather than paying for Datadog/Langfuse Cloud. Self-hosted Langfuse (OSS) is a fallback if the in-house dashboard isn't enough, still $0 license cost.

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
