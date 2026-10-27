# Agent Ecosystem — Flow Builder Canvas Scope v0.1

Status: draft for review. Scopes the visual flow builder — the node/edge canvas that
compiles to the `flow_versions.spec` shape `flow_executor.py` already executes (architecture
doc §4). `03-frontend.md`'s functional core shipped forms/tables for everything *except*
flows, which were explicitly deferred here (§8 of that doc) because a flow spec is a graph,
not a record a plain form can edit comfortably.

The backend is otherwise ready for this: `flow_executor.py`'s node/edge/condition semantics
were built and tested against hand-written specs months ago and haven't changed. This doc is
almost entirely about the canvas UI and the handful of backend read endpoints it needs that
happen not to exist yet (write endpoints — create flow, create flow version — already do).

## 1. What this phase covers

Everything needed to build a flow by dragging nodes onto a canvas, wiring edges between
them, configuring each node in a side panel, saving it as a new `flow_version`, and running
it — reusing the run list/inspector `03-frontend.md` already built, unchanged.

1. `/flows` — list flows in the current workspace, create one (name only, same pattern as
   `/agents`)
2. `/flows/[id]` — the canvas: load the latest version's spec (or a blank trigger node for a
   brand-new flow), add/remove/connect nodes, edit each node's config in a side panel, save
   as a new version, see version history, start a run against the latest version
3. Running a saved flow version and watching it execute: no new UI — `/runs` and
   `/runs/[id]` already handle `flow_id` runs exactly like `agent_id` runs (the start-run
   drawer just needs a flow option alongside the agent picker, §7 step 3)

Explicitly **not** in this phase (§8 has detail on each):
- A true "test run" of an *unsaved* draft spec. v1 test-running means saving a version
  first (cheap — version history already supports many versions per flow) and running that,
  the same two-step "publish a version, then run" flow agents already use. A dry-run
  endpoint that executes a spec without persisting it is real backend work of its own.
- Webhook/schedule triggers (the `triggers` table). The canvas's trigger node stays what
  `flow_executor.py` already treats it as — a fixed start marker that seeds the flow's
  input — not a connection to that table. See §4's trigger node entry for why these are two
  unrelated things today.
- Marketplace flow listings (`marketplace.py` explicitly 400s on these — out of scope until
  that gap closes, a separate project per `03-frontend.md` §8).
- Canvas-level undo/redo, multi-select, copy/paste, keyboard shortcuts beyond what the
  chosen library gives for free. A v1 editor that only supports click-to-add,
  drag-to-connect, and delete is complete for "I can build and run a flow."

## 2. Canvas library

**`@xyflow/react`** (the maintained successor to `reactflow`, same project) — the library
the architecture doc already named (§4: "React Flow or similar"), and the only serious
option for a React node/edge canvas: pan/zoom, drag-to-connect handles, a `Controls`/
`MiniMap` add-on, and a `Background` grid all ship built in, so the canvas mechanics are
close to zero custom code. Nodes and edges are plain data (`{id, type, position, data}` /
`{id, source, target}`) that the app owns and renders through custom node components — it
never generates code or owns the spec itself, which matches the architecture doc's "the
builder is a view over the spec, not the source of truth" framing directly.

## 3. The canvas-state / flow-spec gap (read before building)

This is the one real design problem in this phase, so it's called out before the page list
rather than buried in it.

`flow_versions.spec` (validated by `FlowSpec`/`FlowNode`/`FlowEdge` in
`services/api/app/routers/flows.py`) has **no position field** — a node is just
`{id, type, agent_id?, tool_id?, config}`. Pydantic's default `extra="ignore"` behavior means
any field the canvas sent that isn't in `FlowNode` — like `position: {x, y}` — would be
silently dropped the moment it round-trips through `FlowVersionCreate` validation, not
rejected with an error. A spec saved from the canvas and reloaded would snap every node back
to some default layout, which defeats the point of a canvas editor.

**Resolution**: canvas layout lives inside `node.config`, which is a free-form dict the
backend passes straight to `flow_executor.py` and never schema-validates — e.g.
`config: {"ui_position": {"x": 320, "y": 140}, ...node's real config fields}`. The canvas
reads/writes `config.ui_position` for layout and leaves the rest of `config` alone (it's
already per-node-type data the inspector panel edits, §4). `flow_executor.py` never looks at
`config.ui_position`, so this is purely additive — no backend or executor change needed, just
a convention the frontend owns. Noted here so it isn't rediscovered as a "why did my layout
reset" bug later.

(The same question doesn't arise for edges — `FlowEdge` is `{from, to, condition}`, and
React Flow doesn't need any extra per-edge display state beyond what those three already
give it.)

## 4. Node types and their side-panel editors

One canvas node component per `FlowNode.type`, each rendering compactly on the canvas
(label + a one-line status of its config) and expanding into a form in a side panel when
selected — not inline editing on the canvas itself, which gets cramped fast with real config
(a JSON tool config, a system-prompt-sized agent, etc.).

- **trigger**: exactly one per flow (enforced both client-side — can't add a second — and
  server-side, `FlowSpec._exactly_one_trigger`). No config to edit in v1 (§1 — no
  webhook/schedule wiring yet); the side panel just explains "this flow starts here, seeded
  with the run's input." Auto-added when a new flow's canvas opens empty, not something the
  user drags in.
- **agent**: side panel is an agent picker (`GET /workspaces/{id}/agents`, workspace-scoped
  — same list the Runs start-form already fetches) plus the `input_map` control described
  below. Needs the agent to have at least one published version to be runnable, same
  constraint the Runs start-form already surfaces as a hint.
- **tool**: side panel is a tool picker (`GET /workspaces/{id}/tools`) plus a JSON config
  textarea — the exact same two controls the agent version drawer's tool-attachment
  checklist already built (`app/(app)/agents/[id]/page.tsx`), lifted into a single node's
  panel instead of a per-tool-in-a-list checkbox.
- **conditional**: a passthrough node in the executor (§5's note on where conditions
  actually live) — the side panel is just a label/note field. Its purpose on the canvas is
  visual: a branch point readers can see, even though the condition logic itself lives on
  the *outgoing edges* from whatever node precedes it, not on this node.
- **merge**: same as conditional — passthrough in the executor, label-only panel, visual
  join point. LangGraph's default "wait for every predecessor" join (flow_executor.py's
  comment on this) is what actually makes it wait; the node itself does nothing.

**`input_map`** (agent and tool nodes only): `flow_executor.py`'s `_resolve_input_dict`
supports exactly one thing — `config.input_map = {"from": "<node_id>"}`, taking that
predecessor's output wholesale, or falling back to the flow's original trigger input when
omitted. The side panel exposes this as a single dropdown: "Input: ⌄ Flow input / a specific
upstream node's output" populated from the node's actual incoming edges (not every node in
the flow — picking a node that isn't a predecessor would silently fall back to trigger input
at run time with no error, worth preventing in the UI even though the backend doesn't
enforce it).

## 5. Edges and the condition editor

An edge is a drag-to-connect line between two node handles, carrying an optional
`condition` string. `flow_executor.py`'s grammar for that string is deliberately tiny
(§ intro of that file): `"<node_id>.output.<key...>"`, optionally prefixed `"not "`,
resolved by dict lookup and truthiness — no comparisons, no operators.

Rather than a free-text input (which lets someone type a condition the executor rejects at
run time with no client-side warning), the edge inspector builds the string from three
controls that mirror the grammar exactly:
1. A negate toggle ("branch when" / "branch unless")
2. A source node dropdown, restricted to the edge's own `source` node (the condition can
   only reference the node the edge comes *from* — `_eval_condition` has no mechanism to
   reach any other node's output) — so really this is just shown as context, not a choice
3. A key-path text input for everything after `.output.` (free text, since the executor
   itself does unstructured dict-key traversal — `output.decision`, `output.category`, etc.)

An edge with no condition set is unconditional (always taken). When a source node has
*multiple* outgoing edges and none of them have a condition, `flow_executor.py` takes all of
them (parallel fan-out) — the inspector should make this distinction visible (e.g. a small
"fan-out" vs "conditional branch" label on the node once it has >1 outgoing edge), since it's
easy to add a second edge by accident while meaning to replace the first.

## 6. Pages, routes, and layout

```
app/
  (app)/flows/page.tsx            List flows in the current workspace, create one
  (app)/flows/[id]/page.tsx       The canvas
```

The canvas is the one page in this app that needs real screen space — `AppShell`'s existing
`<main className="mx-auto max-w-5xl p-6">` wrapper (every other page) would cramp a graph
editor into a ~800px column. `/flows/[id]` renders full-bleed within the nav instead: a
three-pane layout (left: a thin node-type palette to drag from; center: the React Flow
canvas; right: the selected node/edge's inspector panel, collapsed when nothing's selected).
A top bar above the canvas (not the global nav, which stays) holds the flow name, a version
picker, "Save version," and "Start a run."

## 7. Component architecture

- `<FlowCanvas>` — wraps `@xyflow/react`'s `<ReactFlow>`, owns `nodes`/`edges` state,
  renders `<Background>`/`<Controls>`/`<MiniMap>`, registers the five custom node
  components (§4) via `nodeTypes`
- `<NodePalette>` — four draggable entries (agent/tool/conditional/merge — not trigger,
  §4), drop-to-add onto the canvas
- `<NodeInspector>` — switches on the selected node's `type`, renders that type's form
  (§4); for an edge selection, renders the condition editor (§5) instead
- `<FlowToolbar>` — name (inline-editable, same pattern as nowhere else in this app yet —
  everywhere else a name is set once at creation and never renamed inline, worth deciding
  consistently when built rather than one-off here), version dropdown (`GET
  .../flows/{id}/versions`, §8), Save version, Start a run (opens the same run-start flow
  `/runs` uses, pre-selected to this flow)

Serialization is two pure functions, not spread across the components above:
- `specFromCanvas(nodes, edges) → FlowSpec` — strips React Flow's own fields (`selected`,
  `dragging`, computed handle positions) down to what the backend's `FlowNode`/`FlowEdge`
  actually accept, folding `position` into `config.ui_position` (§3)
- `canvasFromSpec(spec) → {nodes, edges}` — the inverse, reading `config.ui_position` back
  out (defaulting to an auto-layout — e.g. a simple left-to-right pass by graph depth — for
  nodes that don't have one yet, like every node in a spec written by hand or by an older
  version of this UI before `ui_position` existed)

## 8. Needed backend endpoints

**Done.** `flows.py` had only `POST /flows` and `POST /flows/{id}/versions` — write-only,
because nothing needed to read a flow back until now. Added the three reads the canvas can't
load an existing flow without (same shape as the agent-detail gap `03-frontend.md` §7 step 4
hit and closed):

- `GET /workspaces/{id}/flows` — list, for `/flows`
- `GET /workspaces/{id}/flows/{flow_id}` — one flow's `{id, name, status}`, for the canvas
  page header
- `GET /workspaces/{id}/flows/{flow_id}/versions` — version history for the toolbar's
  version picker, each with its full `spec` so `canvasFromSpec` can load any of them, not
  just the latest

No executor or spec-validation changes were needed — `FlowSpec`/`FlowNode`/`FlowEdge` already
accept everything the canvas will produce once `config.ui_position` carries layout (§3).
Verified against the real API (Docker Postgres + migrations, a minimal GoTrue stand-in for
real JWTs — same setup every backend/frontend pass in this project has used): created a
workspace, created a flow (`GET .../flows` correctly empty-then-one, `GET
.../flows/{id}` 200, a bogus id 404), posted a version whose spec put `{"x": 0, "y": 0}` and
`{"x": 200, "y": 0}` under each node's `config.ui_position` — confirming the exact mechanism
§3 designed — and fetched it back through `GET .../versions`, round-tripping both the
positions and the `edges[].from` alias correctly. All five `/flows*` routes also confirmed
present and correctly shaped in `/openapi.json`, so client-generation in the next phase has
real types to generate from.

## 9. Phased build order

1. **Done.** Backend: the three `GET` endpoints (§8).
2. **Done.** `@xyflow/react` added to `apps/web`. `lib/flowSpec.ts` has `specFromCanvas`/
   `canvasFromSpec` (§7) plus the auto-layout fallback (§7, for a loaded spec with no
   `ui_position` yet) — `vitest` added as the project's first frontend test runner (none
   existed; `npm test`) and `lib/flowSpec.test.ts` covers the round trip.
   `flow_executor.py` had no existing test fixtures to reuse, so the fixture is the
   architecture doc's own spec sketch (§4 of `01-architecture.md`) instead — round-tripped
   byte-for-byte apart from the positions `ui_position` adds. Also covered: `ui_position`
   never leaks into a node's real `config`; BFS-by-depth auto-layout for positionless nodes,
   including giving fan-out siblings at the same depth distinct `y` so they don't stack;
   `agent_id`/`tool_id` null-vs-set preserved per node type; edge conditions (including
   `null`/unconditional) preserved; two edges sharing the same `from`/`to` (two branches of
   one conditional) get distinct ids; a dragged position overwrites the saved one on
   re-save. `npx tsc --noEmit` and `npm run build` both clean (9 routes, unchanged — this
   phase added no pages yet). One dependency-install snag worth recording: `vitest@5.0.2`'s
   installer crashed npm's arborist (`Cannot read properties of null (reading 'edgesOut')`)
   resolving its own optional peer browser-provider packages; landed on `vitest@5.0.5` +
   `vite@8.1.6` installed with `--legacy-peer-deps` instead of chasing that further.
3. **Done.** `/flows` list + create (same pattern as `/agents`/`/tools`; `FlowCreate` is
   name-only, so the drawer has no description field unlike the agent/tool ones). Each
   row links to `/flows/{id}` — the canvas route from §6, which doesn't exist until step 4,
   so that link 404s cleanly for now rather than being left unclickable; confirmed that's a
   plain 404, not a crash. Verified in a real browser: created a workspace, created a flow,
   confirmed the empty state, the `draft` status badge, and the row directly in Postgres
   (name/workspace/`created_by` all correct). `AppShell`'s nav gained a Flows link.
4. `<FlowCanvas>` + `<NodePalette>` + the five node components rendering read-only (load a
   spec, see it laid out, no editing yet) — proves `canvasFromSpec` + the library setup
   before adding interaction
5. `<NodeInspector>` per node type (§4), node add/delete, edge drag-to-connect
6. Edge condition editor (§5), Save version wired to `POST .../versions` via
   `specFromCanvas`
7. Version picker (load an older version read-only or into the editor — decide which when
   building; loading into the editor and re-saving as a new version is simpler and consistent
   with how the agent version drawer already works, so default to that unless it proves
   awkward), flow option in the Runs start-form
8. End-to-end verification: build a real flow in the browser (trigger → agent → tool, one
   conditional branch), save it, start a run, confirm the inspector shows every node's step
   — same verification bar every page in this project has been held to

## 10. Open items for `05`/later

- Webhook/schedule triggers (the `triggers` table) — real backend work (receiving webhooks,
  a scheduler) before there's anything for the canvas's trigger node to configure
- True draft dry-run (execute an unsaved spec) — needs an orchestrator entry point that
  takes a spec directly instead of loading one from `flow_versions`
- Canvas-level auto-layout beyond "new node with no position yet" (§7), undo/redo,
  multi-select
- Marketplace flow listings, once `marketplace.py`'s flow-listing gap closes
