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
  - **Agent-as-tool**: an agent can call another agent synchronously as if it were a tool (for sub-task delegation), capped at a configurable recursion depth to prevent runaway loops.
- State: each Run persists a step log (agent/tool, input, output, tokens, latency, error) to Postgres as it executes — not just a final result — so a run is inspectable/resumable mid-flight.
- Model-provider abstraction: thin adapter layer so the engine isn't hard-wired to one provider, but Claude is the default/first-class target.

## 6. Marketplace

- **Listing** = a published, immutable version of an Agent or Flow + metadata (name, description, category, price if monetized, required tool permissions declared up front).
- **Install** = copies the listing's spec into the installing workspace as a new Agent/Flow instance, owned by that workspace from then on (fork, not reference) — avoids a published update silently changing behavior for installs that haven't opted in.
- Permissions model: a listing must declare which tool *categories* it needs (e.g. "web search", "sends email", "reads your files"); installer approves those categories at install time, same pattern as OAuth consent screens.
- Secrets (API keys the installed agent needs, e.g. the installer's own Slack token) are supplied by the installer post-install and stored workspace-scoped — never bundled into or visible to the listing publisher.
- V1 marketplace: free listings only, ratings + install counts. Monetization (revenue share, paid listings) deferred — needs payment/compliance work, out of scope for MVP.

## 7. Security & sandboxing

- Agent tool execution never runs in the API or orchestrator process. Each run's tool calls go through a **Tool Gateway**: allow-listed egress, per-workspace secrets injected at call time (never exposed to the agent's own context), and code-exec tools (if offered) run in a disposable container with no network by default.
- Per-workspace RLS in Postgres: every table scoped by `workspace_id`, mirroring the TradersEdge RLS pattern already in use.
- Run budgets: max steps, max tokens, max wall-clock time, max concurrent runs — enforced per workspace tier, hard-stop with a logged reason, not a silent timeout.
- Marketplace installs never carry the publisher's credentials forward (see §6).

## 8. Data model (initial sketch)

Core tables: `workspaces`, `users`, `workspace_members`, `agents`, `agent_versions`, `flows`, `flow_versions`, `tools`, `tool_credentials` (secrets, workspace-scoped, encrypted at rest), `runs`, `run_steps`, `listings`, `listing_versions`, `installs`.

Full DDL deferred to a follow-up doc (`02-schema.md`) once this shape is confirmed.

## 9. Stack

- **API**: Python, FastAPI, Pydantic v2.
- **Orchestrator/executors**: Python async workers (built on the API's agent-loop code, not a separate language) — consider LangGraph for the graph-execution primitives rather than hand-rolling one, final call after a short spike.
- **DB**: Postgres via Supabase (auth, RLS, storage, realtime subscriptions for live run status in the UI).
- **Queue**: pg-boss (Postgres-backed, one less moving part than Redis) unless throughput needs prove otherwise.
- **Frontend**: Next.js + TypeScript, React Flow for the builder canvas.
- **Model provider**: Anthropic API (Claude) as first-class/default; adapter layer allows others later.

## 10. Phased roadmap

- **MVP**: single-agent builder (no multi-agent yet), manual trigger only, no marketplace — prove the agent config → run → inspect loop end-to-end.
- **V1**: multi-agent flows (sequential + agent-as-tool), webhook/schedule triggers, marketplace (free listings, install/fork model).
- **V2**: parallel fan-out/fan-in, monetized listings, more triggers, usage-based billing.

## 11. Open decisions (need your input before/while building)

1. Multi-tenant from day one, or single-workspace MVP first? (Affects whether RLS/workspace scoping is built now or retrofitted.)
2. Self-hosted LLM cost pass-through vs. platform-subsidized — affects run budget design in §7.
3. Does "another agent as a tool" need approval at *build* time or just declared capability — security/UX tradeoff.
4. LangGraph vs. hand-rolled graph executor — worth a half-day spike before committing.
