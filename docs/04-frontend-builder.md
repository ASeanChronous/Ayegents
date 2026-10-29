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
`condition` string. `flow_executor.py`'s grammar for that string (`_eval_condition`'s own
docstring has the full, current spec — expanded from an earlier, comparison-less version,
see "Flow condition language" under §10): `"<node_id>.output.<key...>"`, optionally
prefixed `"not "`, optionally followed by a comparison (`== != > < >= <=` against a JSON or
bare-word literal) — with no comparison, it's the referenced value's truthiness, same as
originally.

Rather than a free-text input (which lets someone type a condition the executor rejects at
run time with no client-side warning), the edge inspector builds the string from controls
that mirror the grammar exactly:
1. A negate toggle ("branch when" / "branch unless")
2. A source node dropdown, restricted to the edge's own `source` node (the condition can
   only reference the node the edge comes *from* — `_eval_condition` has no mechanism to
   reach any other node's output) — so really this is just shown as context, not a choice
3. A key-path text input for everything after `.output.` (free text, since the executor
   itself does unstructured dict-key traversal — `output.decision`, `output.category`, etc.)
4. A "Compare" select (is truthy / `==` / `!=` / `>` / `<` / `>=` / `<=`) and, when one of
   the comparison operators is picked, a value text input — added in the same pass that
   expanded the grammar, see §10.

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
4. **Done.** `app/(app)/flows/[id]/page.tsx` — the canvas page (§6's full-bleed route;
   `AppShell` now matches `/flows/[id]` by path and drops the standard centered/padded
   `<main>` only for it, every other route unchanged). Loads the flow's latest version via
   `canvasFromSpec` (a brand-new flow with no versions gets a single-trigger spec, per §1),
   renders it through `<FlowCanvas>` (wraps `@xyflow/react`'s `<ReactFlow>` +
   `Background`/`Controls`/`MiniMap`, `nodesDraggable={false}`/`nodesConnectable={false}` —
   genuinely read-only, not just undecorated) and the five node components from §4
   (`components/flows/nodeTypes.tsx`), each resolving its `agent_id`/`tool_id` to a name via
   a small `FlowLookupContext` fed from the same `GET .../agents`/`GET .../tools` calls
   other pages already make, rather than carrying agent/tool lists through every node's
   `data`. `<NodePalette>` renders the static four-entry list from §7 (drag-to-add itself is
   step 5). Verified in a real browser: built a 6-node version by hand via `curl` (trigger →
   agent → conditional → two tool nodes with no saved `ui_position`, one with `input_map`
   set, fanning into a merge) specifically to exercise all five node types, both fan-out and
   conditional edges, the resolved-name lookup (confirmed "Time Teller"/`current_time` shown,
   not raw ids), and the BFS auto-layout spacing fan-out siblings apart — all rendered
   correctly, including the `input_map` subtitle correctly differing between the two tool
   nodes. Also confirmed the blank-flow case (trigger only, `fitView` centers it) and that
   dragging a node pans the canvas instead of moving it, proving `nodesDraggable={false}`
   actually took effect rather than just being set. `tsc --noEmit`, `npm run build` (11
   routes), and `npm test` all clean.
5. **Done.** `<NodeInspector>` (`components/flows/NodeInspector.tsx`) per node type (§4):
   trigger gets a static explanation (no delete — it's the one node that can't be removed);
   agent/tool get a picker plus the `input_map` dropdown (§4, populated from the node's
   actual incoming edges, not every node in the flow); tool also gets the JSON config
   textarea (same pattern as the agent version drawer's tool checklist), buffered in local
   state so invalid JSON shows an inline error instead of corrupting `config` or crashing;
   conditional/merge get a label-only note. `FlowCanvas` is now fully controlled
   (`nodesDraggable`/`nodesConnectable` on, `onNodesChange`/`onEdgesChange`/`onConnect`
   forwarded to state the page owns) and `<NodePalette>`'s four entries are real HTML5
   drag sources — dropping one calls `screenToFlowPosition` and `createCanvasNode` (new in
   `lib/flowSpec.ts`) to add it. Deleting a node (the inspector's Delete button, or the
   default Backspace/Delete keybinding React Flow gives selected nodes/edges) also removes
   any edges touching it, not just the node.

   Verified in a real browser (same Docker Postgres + API + fake-GoTrue setup every pass in
   this project has used): dragged an Agent node from the palette onto the canvas, selected
   it, picked "Time Teller" from the inspector's agent dropdown and watched the canvas node's
   title update live; dragged trigger → agent to drag-connect an edge, confirmed the agent's
   `input_map` dropdown immediately offered the trigger as a source once the edge existed;
   selected that edge and built a condition through the three controls (when/unless toggle,
   the read-only source-node label, the key-path input), confirming the live preview text and
   that deselecting and reselecting the edge correctly re-parsed the condition back into the
   same toggle/key-path state; added a Tool node, selected `current_time`, typed intentionally
   broken JSON into the config textarea and confirmed the inline "Invalid JSON" error with no
   crash, then fixed it and confirmed the error cleared; deleted the agent node and confirmed
   its edge to the trigger disappeared with it (0 edges, 2 nodes left). `tsc --noEmit`,
   `npm run build` (11 routes, unchanged), and `npm test` (9 tests, unaffected) all clean.

   One real bug found and fixed during this pass, unrelated to the interactivity work itself:
   rewriting `FlowCanvas.tsx` to add the controlled-state props dropped the
   `import "@xyflow/react/dist/style.css"` line step 4 had. React Flow degrades silently
   without it — nodes/edges still render (via Tailwind classes and inline SVG), but anything
   relying on the library's own stylesheet does not: handles came out `position: static`
   instead of absolutely positioned on each node's edge, which didn't block drag-to-connect
   outright but made handles land in the wrong spot and made the whole canvas look subtly
   broken (no connection-line styling, no proper z-stacking). Caught via the dev console's
   own `[React Flow]: It seems that you haven't loaded the styles` warning plus a `position:
   static` check on a handle element — restoring the import fixed both the warning and the
   handle positions, confirmed by inspecting the rendered CSS before/after. A good argument
   for always re-reading a file's import list after a full-file rewrite rather than assuming
   a targeted diff.
6. **Done.** Edge condition editor (§5) was done as part of step 5 above (the inspector's
   condition UI and the edge-building logic shipped together — splitting them didn't make
   sense once writing the inspector). What was left here: Save version, wired to `POST
   .../versions` via `specFromCanvas`. A "Save version" button in the top bar builds the
   spec from the current canvas state and posts it; the bar's status text switches between
   "Unsaved changes" / "Saving…" / "All changes saved" / an inline error, tracked by a
   `dirty` flag set on every real edit (node/edge add, delete, data change, a completed
   drag, a new connection) and cleared whenever the canvas is (re)seeded from the server —
   both the initial load and the reload a successful save triggers. That reload is also the
   verification mechanism: it reads back the version just written, so a broken round trip
   would show up immediately as the canvas snapping to something wrong, not just as a 201
   from the API. Also closed a small gap found while building this: the trigger node had no
   inspector Delete button, but React Flow's own Backspace/Delete keybinding acts on
   whatever's selected regardless of the inspector — `deletable: false` on trigger nodes
   (`canvasFromSpec`, `lib/flowSpec.ts`) closes that path too, instead of leaving it to 400
   against the server's exactly-one-trigger validator.

   Verified in a real browser (same Docker Postgres + API + fake-GoTrue setup): opened a
   brand-new flow (confirmed "All changes saved" on the untouched single-trigger canvas),
   dragged on an Agent node (confirmed the status flipped to "Unsaved changes"), selected
   "Time Teller", dragged it to a deliberate position, clicked Save version (status returned
   to "All changes saved"), confirmed directly in Postgres that `flow_versions` now had a
   version 1 row with the agent's `agent_id` set and the exact dragged `ui_position`, then
   did a hard page reload and confirmed the canvas came back with the same node at the same
   position from a cold load — the full save/reload round trip, not just the POST. Also
   confirmed the trigger-node deletion gap: selected the trigger and pressed Backspace (node
   count stayed at 2, trigger survived), then did the same on the agent node (count dropped
   to 1, confirming normal nodes are still deletable via the keybinding — only the trigger is
   protected). `tsc --noEmit`, `npm run build` (11 routes, unchanged), and `npm test` (9
   tests, unaffected) all clean.
7. **Done.** Version picker: a `<select>` in the canvas top bar lists every version newest
   first (same `GET .../versions` call already loaded), picking one loads it into the editor
   via `canvasFromSpec` — not read-only, and saving always creates version max+1 regardless
   of which one is loaded (the "load into the editor, re-save as new" default this doc
   picked ahead of time, matching the agent version drawer's own prefill pattern). The
   seeding logic that used to run once off a `latestSpec` memo is now a small `loadSpec`
   helper called from two places: an effect keyed on `versions` itself (initial load, or the
   refetch after a save — both cases should jump to showing the newest version), and
   `handleVersionPick` for an explicit dropdown choice (which doesn't touch SWR's cache at
   all, so it can't fight with the other effect). Flow option in the Runs start-form
   (`app/(app)/runs/page.tsx`): a two-button "An agent" / "A flow" toggle ahead of the
   existing agent picker, swapping in a flow picker when "A flow" is active; the backend
   already both accepted `flow_id` in `RunCreate` and enforced exactly-one-of agent/flow
   server-side, so this was purely additive on the frontend. The run list's "Agent" column
   is now "Agent / Flow" and resolves a flow run's name the same way it already resolved an
   agent run's, instead of showing a raw id prefix.

   Verified in a real browser (same Docker setup): saved a flow's v1 (trigger → agent),
   dragged on a second node and saved again, confirmed the picker showed both versions with
   v2 auto-selected as newest; picked v1 from the dropdown and confirmed the canvas snapped
   back to the 2-node spec (not the 3-node one); saved from that loaded v1 and confirmed it
   became v3 (not an overwrite of v1), again auto-selected. In the Runs start-form: switched
   the toggle to "A flow," picked the flow, started a run, confirmed in Postgres that the new
   row had `flow_id` set and `agent_id` null, and confirmed the run list resolved "Step 7
   Flow (flow)" instead of a raw id; switched back to "An agent" and confirmed that path
   still worked unchanged. `tsc --noEmit`, `npm run build` (11 routes, unchanged — Runs and
   the flow canvas are both existing routes), and `npm test` (9 tests, unaffected) all clean.
8. **Done.** End-to-end verification, scoped: no step in this build order has required a
   real `ANTHROPIC_API_KEY` before now, since every prior Docker verification pass ran with
   `ANTHROPIC_API_KEY=sk-ant-dummy` and never reached a real model call. Flow execution does
   reach one — `_execute_agent_loop` (`services/orchestrator/orchestrator_app/executor.py`)
   calls `AsyncAnthropic` for the *first* agent node — so a from-scratch run, without a real
   key, can prove everything up to that call but not what happens after it.

   Built a real flow in the browser via drag-and-drop (not raw API calls): dragged on an
   Agent node and a Tool node (the seeded `current_time` builtin, no credential needed),
   wired trigger→agent and agent→tool by dragging between handles, and set a condition on
   the agent→tool edge (`agent-fa70e553.output.decision`, via the "when" branch control) —
   the one-conditional-branch part of this step's scope. Saved it; confirmed in Postgres the
   persisted spec matches exactly (real `agent_id`/`tool_id`, the typed condition string,
   `ui_position` for both dragged nodes).

   Started a run against the flow from the Runs start-form (the real UI control added in
   step 7, not a raw POST): the list showed `queued` immediately with `flow_id` set and the
   flow's name resolved. Ran this with the orchestrator worker live (same Docker network,
   `ANTHROPIC_API_KEY=sk-ant-dummy`) rather than leaving it unstarted, specifically to see
   what a real flow run does without a real key instead of leaving that question open: the
   worker picked up the run within its 2s poll interval, moved it to `running`, and the
   trigger node passed through with no model call (as `flow_executor.py`'s docstring says).
   The agent node then hit the real Anthropic auth check and failed fast with a clean 401,
   which `run_flow`'s `except Exception` caught and surfaced verbatim on the run row. The
   run inspector showed this correctly: `failed`, the exact `invalid x-api-key` error in the
   banner, `finished_at` set — no hang, no crash, no stuck `running` state.

   **What this does and doesn't confirm.** Confirmed: the full build→save→run pipeline is
   correct end to end, including the pieces step 8 names specifically (a real conditional
   edge persists and round-trips correctly; a flow run is reachable and inspectable from the
   UI exactly like an agent run). Not confirmed, and not obtainable without a real key: the
   tool node ever executing, the condition's truthy/falsy branches ever actually being
   evaluated against a real agent output, or the inspector's per-node step list ever showing
   more than zero rows for a flow run (no `run_steps` row is written until a node's call
   completes, and none did here). That last piece — "confirm the inspector shows every
   node's step" as originally scoped — needs a real `ANTHROPIC_API_KEY` in the Docker
   environment to close out; flagging it here rather than claiming it's covered.

   **Gap noticed in passing, not part of this step's code:** the run inspector's empty
   steps state (`app/(app)/runs/[id]/page.tsx`, "No steps yet — they appear here as the
   orchestrator executes this run") reads fine for an in-progress run but is misleading for
   a *finished* run with zero steps, like this one — it implies more are still coming. Pre-
   existing from the inspector's original build, not touched here; worth a one-line fix
   (branch on `run.status` ∈ {succeeded, failed} to say "no steps were recorded" instead)
   next time that file is touched, not urgent enough on its own to justify a step for it.

   `tsc --noEmit`, `npm run build` (11 routes, unchanged), and `npm test` (9 tests,
   unaffected) all clean — no application code changed in this step, verification only.

## 10. Open items for `05`/later

- ~~Webhook/schedule triggers (the `triggers` table)~~ **Done** — see below.
- ~~True draft dry-run (execute an unsaved spec)~~ **Done** — see below.
- ~~Canvas-level auto-layout beyond "new node with no position yet" (§7), undo/redo,
  multi-select~~ **Done** — see below.
- ~~Marketplace flow listings, once `marketplace.py`'s flow-listing gap closes~~ **Done** —
  see below. (Backend-only: there's still no marketplace storefront UI — `05-frontend-
  marketplace.md` per `03-frontend.md` §8 — this just closes the gap that doc's own
  endpoints already had.)

### Canvas UX polish (undo/redo, multi-select, auto-layout)

Pure frontend, no backend or spec-shape changes — all three build on state the canvas
already owned (`nodes`/`edges` in `app/(app)/flows/[id]/page.tsx`).

- **Auto-layout.** `autoLayoutPositions` (`lib/flowSpec.ts`) was already doing BFS-depth
  layout as `canvasFromSpec`'s per-node fallback for a loaded spec with no `ui_position`
  (§7); exported it and added a toolbar button that re-lays-out every node on demand via
  the same function, by round-tripping the current canvas through `specFromCanvas` first.
- **Undo/redo.** A page-level `{nodes, edges}` snapshot stack, pushed just *before* each
  committed edit — add/delete, a finished drag (not every intermediate drag frame, which
  would make one drag take dozens of undo steps), a new connection, a data/condition change
  — via a `pushHistory()` call threaded into every existing mutating handler. Toolbar
  buttons plus Cmd/Ctrl+Z / Cmd/Ctrl+Shift+Z, guarded against an input/textarea/select
  having focus so those keep their own native undo instead of the canvas hijacking it.
  Switching flow versions clears both stacks (undoing past a version switch would
  resurrect a version the user just navigated away from).
  **Known limitation, not fixed:** `onUpdateNodeData` — called on every keystroke in the
  tool node's JSON config textarea and on every agent/tool select change — pushes a
  history entry every time, same granularity the existing `dirty` flag already used. Undo
  works correctly, just needs one press per keystroke to fully revert one text edit;
  coalescing that into one history entry per pause would need a debounce this scope didn't
  ask for.
- **Multi-select.** React Flow's own box-select (Shift-drag) and Cmd/Ctrl-click were
  already enabled by default — nothing to turn on. What was missing: the inspector only
  ever looked at `selNodes[0]`/`selEdges[0]`, silently ignoring the rest of a multi-selection.
  Changed `selectedNodeId`/`selectedEdgeId` to arrays throughout, and added a
  `MultiSelectPanel` (`NodeInspector.tsx`) for when more than one element is selected: shows
  the count and a single "Delete N selected" bulk action. The trigger is excluded from a
  bulk delete the same way it's excluded from the single-node panel's Delete button (no
  button there) — the panel shows "The trigger node can't be deleted" rather than silently
  dropping it from the count with no explanation.

  Verified in a real browser (same Docker Postgres + API setup, no orchestrator needed —
  pure frontend). Undo/redo: added a node, undid it (node count back to the pre-add state),
  redid it (back), confirmed both the toolbar buttons and the Cmd+Z / Cmd+Shift+Z shortcuts
  work; confirmed Cmd+Z while the JSON config textarea had focus did *not* touch the canvas
  (node count unchanged); confirmed undo also reverts an Auto-layout pass, not just
  add/delete. Auto-layout: clicked it, confirmed node positions changed to depth-based
  columns. Multi-select: React Flow tracks `selectionKeyCode`/`multiSelectionKeyCode` via
  its own `window` keydown/keyup listener, not the click event's own modifier flags — so
  driving it from here needed a real `KeyboardEvent('keydown', {key: 'Meta'})` dispatched on
  `window` around the click, not just a modifier-flag on the click itself (confirmed first
  that a plain modifier-flag click only *replaced* the selection, never added to it, which
  is what a real user would see too if the browser never actually saw a held key — not an
  app bug, a limitation of scripting a "held key + click" gesture). With that, Cmd-clicking
  a second node showed "2 nodes, 0 edges selected" and a "Delete 2 selected" button in the
  inspector; clicking it removed both in one history step, and Undo brought both back
  together. Selecting the trigger plus one other node and bulk-deleting showed "The trigger
  node can't be deleted" and correctly removed only the other node. Saved the result and
  confirmed in Postgres the persisted spec matched exactly what survived on the canvas.
  `tsc --noEmit`, `npm run build` (11 routes, unchanged), and `npm test` (9 tests,
  unaffected) all clean.

### Webhook/schedule triggers

The `triggers` table (`0001_init_schema.sql`) and `runs.trigger_id` already existed —
nothing about the schema needed inventing, just the router, the public firing endpoint,
and a scheduler to decide when a schedule trigger is due. One migration
(`0005_trigger_last_fired.sql`) adds `triggers.last_fired_at`, the one real gap: a cron
schedule needs an anchor timestamp to compute its *next* fire from, and nothing recorded
one. Both webhook and schedule config live in `triggers.config` jsonb per
`docs/02-schema.md` §3's own "webhook secret, cron expr, etc." — no new secrets-split
table the way tool credentials get one; a webhook secret only has to be hard to guess, not
survive the same threat model as a third-party API key.

A trigger is flow-level config for *how a run starts*, deliberately kept separate from the
canvas's own trigger node, which still only ever seeds a run's input regardless of what
started it (`services/api/app/routers/triggers.py`'s own docstring has the full reasoning).
That's why triggers get their own drawer (`TriggersDrawer.tsx`, opened from a "Triggers"
button in the flow page's top bar) instead of a form on the trigger node itself — the
NodeInspector's trigger panel text now says as much instead of the old "later phase" note.

- **`services/api/app/routers/triggers.py`** — authenticated CRUD under
  `/workspaces/{workspace_id}/flows/{flow_id}/triggers`. `kind` is `manual` | `webhook` |
  `schedule` at this point in the build (the schema's fourth kind, `agent_event`, was still
  out of scope here — nothing in this open item named it, and it needed flow-to-flow wiring
  this project hadn't designed yet; closed in a later pass, see "agent_event trigger kind"
  below). A webhook's secret is always server-generated (`secrets.token_urlsafe(32)`), never
  caller-supplied. A schedule's cron expression is validated with `croniter.is_valid` at
  both create and update time, rejected with a 400 before it ever reaches the scheduler.
- **`services/api/app/routers/webhooks.py`** — the public side, `POST /webhooks/{trigger_id}`,
  deliberately outside `/workspaces/...` and `get_current_user_id`: the caller is an
  external system, not a logged-in member. Validated by an `X-Webhook-Secret` header
  against `config.secret`; a missing trigger, a non-webhook trigger, and a wrong secret
  all return the *same* 404, so a guessed trigger id can't be confirmed as real without
  also knowing its secret. Starts a run exactly the way `POST .../runs` does — insert into
  `runs`, hand off via `run_queue` — just reached from a different front door.
- **`services/orchestrator/orchestrator_app/scheduler.py`** — runs as a second
  `asyncio` task in the existing orchestrator process (`main.py`'s `asyncio.gather`), not a
  separate deployable, since it only ever creates `run_queue` rows and never consumes them.
  Polls every `schedule_poll_interval_seconds` (30s default — a schedule's own granularity
  is minutes, so checking as often as the 2s run-queue poll would be pure waste), and for
  each enabled schedule trigger checks whether `croniter`'s next fire time after
  `last_fired_at` (or `created_at`, if it has never fired) has passed. `for update of t
  skip locked` on the `triggers` row — not just on `run_queue`, which only governs runs
  already created — is what stops two orchestrator replicas from both firing the same due
  trigger twice.

Verified against the real Docker Postgres + API + orchestrator setup (the orchestrator
container specifically, not just API — this is the first open item in this phase that
needed it running against real trigger rows). Created a webhook trigger from the UI,
fired it with `curl` using the exact URL/secret shown, confirmed the resulting `runs` row
had the right `flow_id`/`trigger_id`/`input`, confirmed a missing/wrong secret and a
nonexistent trigger id all return the identical 404, and confirmed disabling the trigger
turns a correct request into a 409 ("trigger is disabled") rather than silently succeeding
or 404ing. Created a schedule trigger with `* * * * *` from the UI, started the
orchestrator with `SCHEDULE_POLL_INTERVAL_SECONDS=5` for a fast feedback loop, and watched
it fire exactly once per real minute boundary (confirmed no double-fire across several 5s
poll ticks inside the same minute, and a genuine second fire at the next boundary) — both
fires correctly failed with "flow has no published version" until a version was saved,
then a re-fired webhook run went all the way to `succeeded` with `node_outputs` carrying
the webhook body through.

**Bug found and fixed during this pass:** deleting a trigger that already has runs
referencing it (`runs.trigger_id`, no `ON DELETE` clause in `0001_init_schema.sql`) raised
`asyncpg.exceptions.ForeignKeyViolationError`, surfaced to the browser as a raw, CORS-less
500 — the frontend's delete button looked like it silently did nothing. Fixed by catching
that specific exception in `delete_trigger` and returning a clean 409 pointing at the
right alternative ("disable it instead," which is also the semantically correct move —
deleting only ever made sense for a trigger that never fired). Also added an `actionError`
state to `TriggersDrawer` for toggle/delete failures generally, which had no error surface
at all before this (only the add-trigger form did) — confirmed the 409 now renders as a
plain-language message in the drawer instead of a console-only exception.

`tsc --noEmit`, `npm run build` (11 routes, unchanged — Triggers is a drawer on the
existing flow page, not a new route), and `npm test` (9 tests, unaffected) all clean.

### agent_event trigger kind

**Done.** Closes the gap the webhook/schedule increment above left open: `agent_event`, the
schema's fourth `trigger_kind`, for a flow that starts automatically when another agent's
or flow's *top-level* run reaches a given terminal status — the "flow-to-flow wiring" that
increment explicitly deferred for not being designed yet.

Design:
- `triggers.config` for an `agent_event` trigger holds exactly one of `source_agent_id` or
  `source_flow_id` (whichever kind of thing it watches), plus `on_status`
  (`succeeded` | `failed` | `budget_exceeded`, default `succeeded`) — same "everything
  trigger-specific lives in `config` jsonb" convention webhook's `secret` and schedule's
  `cron` already use, no new table.
- Both the source and `on_status` are resolved and validated at create time
  (`triggers.py`'s `_resolve_agent_event_config`), not taken on faith: the source must
  belong to the *same workspace* as the trigger's own flow (404 otherwise — the same
  cross-tenant gap class the agent-as-tool increment found and fixed for `sub_agent_ids`),
  and a flow naming itself as its own source is rejected outright (400, "a flow can't
  trigger itself") — the one immediate cycle that's cheap to catch here, same spirit as the
  agent-as-tool self-reference check. A longer cycle (flow A's trigger fires B, B's fires
  A) is **not** detected — a deliberate, documented scope cut
  (`orchestrator_app/agent_event.py`'s own docstring has the full reasoning), same category
  as flow_executor.py's own "no checkpointing, tiny condition language" notes: real, left
  for later, not an oversight. It would show up immediately as runs piling up in the Runs
  list, and the existing "disable this trigger" lever already handles it once noticed.
- The source (`source_agent_id`/`source_flow_id`) is immutable once set — `PATCH` only
  allows changing `on_status` (and `enabled`, as before). Swapping the source is a big
  enough semantic change that delete-and-recreate is clearer, same reasoning the existing
  webhook secret's immutability already uses.
- `services/orchestrator/orchestrator_app/agent_event.py` (new) — `fire_agent_event_triggers`,
  the firing side (`triggers.py` only builds/validates the row, same webhooks.py/scheduler.py
  split the other two kinds already have). Matches enabled `agent_event` triggers whose
  `config.source_agent_id`/`source_flow_id` and `on_status` match the run that just
  finished, and for each one inserts a new `runs` row (`trigger_id` set, same as a webhook
  or schedule firing) plus a `run_queue` row, and bumps `last_fired_at` — identical
  mechanics to `scheduler.py`'s `fire_due_triggers`, just event-driven instead of polled.
  The new run's `input` is the source run's `output` taken wholesale, the same
  "predecessor's output becomes the next thing's input" convention a flow's own tool-node
  `input_map` already follows (`flow_executor.py`'s `_resolve_input_dict`) — not a new
  convention invented for this.
- Called from exactly two places, both already finalizing a run's terminal status:
  `executor.py`'s `run_single_agent` (source_agent_id = the run's `agent_id`) and
  `flow_executor.py`'s `run_flow`, on both its success *and* failure paths
  (source_flow_id = the run's `flow_id`). Deliberately **not** called from
  `run_agent_as_tool` — a sub-agent delegation reuses the parent run's row rather than
  writing its own `runs.status` (executor.py's own docstring), so it was already naturally
  excluded without a special case: only a *published agent's own top-level run* can be an
  `agent_event` source, never one of its sub-agent calls.
- Frontend (`TriggersDrawer.tsx`): a third "Agent/flow event" tab alongside Webhook/Schedule
  in the add-trigger form, offered only when the flow page has agents and/or other flows to
  pick from (`agents`/`flows`, newly passed down from `flows/[id]/page.tsx`, which already
  loaded `agents` for the canvas and now also loads `flows` for this). A source-type toggle
  (an agent vs. another flow) picks which half of the union the select below it fills in;
  "another flow" excludes the current flow from its own list client-side too, so the form
  never even offers the choice the backend would reject. An existing trigger's row shows
  "When **\<name\>** (agent/flow) runs and `on_status` — last fired \<time\>/never fired
  yet," resolving the id to a name via the same `agents`/`flows` lists.

Verified against the real Docker Postgres + API + orchestrator setup. `curl`: created a
valid `agent_event` trigger, confirmed the self-reference rejection (400) and the
cross-workspace rejection (404), and confirmed sending both/neither of
`source_agent_id`/`source_flow_id` is rejected (422, pydantic's own `model_validator`).
Started the real orchestrator (Anthropic client mocked with known usage) against a
published source agent and started a run of it through the API — confirmed in Postgres
that the agent's run succeeding automatically inserted a *second* `runs` row for the
downstream flow, with the right `trigger_id` and `input` set to the source run's exact
output, that this new run executed and itself succeeded, and that the trigger's
`last_fired_at` was stamped. Then disabled the trigger and started a second source-agent
run — confirmed no second flow run was created. Confirmed a trigger that has already fired
can't be deleted (409, "disable it instead"), same as webhook/schedule. Finally, in a real
browser: created an agent and a flow in the same workspace, opened the flow's Triggers
drawer, confirmed the "Agent/flow event" tab appears, filled in the form (source agent +
`on_status`), submitted, and confirmed the new trigger renders with the resolved agent
name and status in the list — screenshotted for the record.

`tsc --noEmit`, `npm run build` (13 routes, unchanged), and `npm test` (9 tests,
unaffected) all clean.

### Flow condition language

**Done** (partially — see below). Expands `flow_executor.py`'s condition grammar beyond
pure truthiness: `"[not ]<node_id>.output.<key...>[ <op> <value>]"`, where `<op>` is one of
`== != > < >= <=` and `<value>` is a JSON literal (`"approved"`, `0.5`, `true`, `null`) or a
bare unquoted word taken as a string (`approved` without quotes also works, so a condition
author doesn't have to think about escaping in a form field). With no `<op> <value>`, the
condition is exactly what it always was — the referenced value's truthiness — so every
existing condition in the schema still parses and evaluates identically; this is additive,
not a breaking grammar change.

- `_COMPARISON_PATTERN` splits `<op> <value>` off the end of the condition text (the
  `<node_id>.output.<key...>` part has no spaces, so a regex on whitespace is enough — no
  real parser needed for a grammar this small).
- `_parse_condition_literal` tries `json.loads` first (covers numbers, `true`/`false`/
  `null`, and double-quoted strings), accepts single-quoted strings too (JSON doesn't, and
  hand-typing `\"` into a form field is awkward), and falls back to the raw text as a bare
  string otherwise.
- `>`/`<`/`>=`/`<=` require both sides to be actual numbers (`_as_number`, which explicitly
  rejects `bool` even though it's an `int` subclass in Python — `true > 0` being valid
  Python is a footgun for a condition language users write by hand, not a feature) — a
  condition that tries to compare a string or `None` with an order operator raises
  `FlowExecutionError` rather than silently coercing, same as the existing "unsupported
  condition syntax" error for malformed conditions.
- `==`/`!=` work across any comparable types with no such restriction — `status == approved`
  against a string output just works.
- Still, deliberately, **no arithmetic and no referencing anything but one predecessor's
  output** — not relaxed by this pass, called out explicitly in `_eval_condition`'s own
  docstring as staying that way. A condition this small is meant to pick a branch, not
  compute one; real logic belongs in an upstream agent or tool node. (The module's other
  scope cut, no checkpointing, is also unchanged — still open, still exactly as described
  in the module docstring.)
- Frontend (`NodeInspector.tsx`'s `EdgePanel`): the three-control editor (negate toggle,
  key-path input) gained a fourth control, a "Compare" select (`is truthy` plus the six
  operators), and a value input that only appears once an operator other than "is truthy"
  is picked. The parsing regex that reconstructs UI state from an existing `condition`
  string on edge-select was extended to optionally capture the operator/value suffix,
  so opening an edge that already has a comparison (including one set by hand via the API,
  not just one built in this UI) correctly populates all four controls, not just the first
  two. The caption below the controls was extended similarly, and adds "Both sides must be
  numbers" for the four order operators specifically.

Verified against the real Docker Postgres + API + orchestrator stack. First, the pure
condition-evaluation logic in isolation (no DB): truthiness (unchanged), `==`/`!=` against
both quoted and bare-word strings, `>`/`<`/`>=` against numbers, negation combined with a
comparison, the non-numeric-order-comparison error, and the pre-existing malformed-syntax
error — all as expected. Then end-to-end: built a real flow (trigger → classifier agent →
two tool nodes) with the two outgoing edges from the classifier conditioned on
`classifier.output.score > 0.5` and its negation, ran it through the real orchestrator
(Anthropic client mocked) once with `score: 0.8` and once with `score: 0.2`, and confirmed
in Postgres that exactly one branch's tool node appears in `node_outputs` each time — the
correct one. Then patched one edge's condition to compare a non-existent (non-numeric) key
with `>` and confirmed the run fails cleanly with `runs.error` set to the comparison's own
error message, not an orchestrator crash. Finally, in a real browser: opened that flow's
canvas, selected an edge with an existing comparison condition and confirmed the Compare
select and Value input populated correctly from it (`unless` / `score` / `>` / `0.5`),
changed it to `==` with a new value through the UI, saved a new version, and confirmed in
Postgres that the persisted `condition` string matched exactly what the UI showed.

`tsc --noEmit`, `npm run build` (13 routes, unchanged), and `npm test` (9 tests,
unaffected) all clean.

### Marketplace flow listings

Backend-only, entirely inside `services/api/app/routers/marketplace.py` (and a one-field
addition to `listings.py`, the public browse router) — no frontend work, since there's
still no marketplace storefront UI to drive it from (`03-frontend.md` §8's
`05-frontend-marketplace.md` is still unbuilt). `marketplace.py`'s own docstring already
explained this was a deliberate scope cut, not a bug: a flow's spec references agent
nodes by `agent_id`, and installing one means recursively forking every agent it depends
on — real work, not an oversight. This closes that gap.

The schema already modeled a flow listing as a first-class case — `listings.source_flow_id`
(xor `source_agent_id`, a check constraint) and `installs.installed_flow_id` both existed
in `0001_init_schema.sql` from day one — so no migration was needed, only the router logic
`marketplace.py`'s docstring said was missing:

- `ListingCreate` now accepts `source_flow_id` as an alternative to `source_agent_id`,
  enforced as an xor by a pydantic `model_validator` (422 if neither or both are given) —
  the same shape the schema's own check constraint already enforces at the DB layer, just
  surfaced as a clean validation error before a bad insert is even attempted.
- **Publish** (`_flow_listing_spec`, new): snapshots the flow's latest `flow_versions.spec`
  verbatim (node ids, edges, everything — `flow_executor.py` needs the real shape back at
  run time), then, for every distinct `agent_id` an `agent` node references, snapshots that
  agent the same way the plain agent-listing path always has
  (`_agent_listing_snapshot`, extracted from the pre-existing inline code so both paths
  share one set of checks) — including this module's existing no-subagents and
  global-tools-only rules, now phrased per-agent instead of "the one agent this listing
  is." For every distinct `tool_id` a `tool` node references *directly* (bypassing any
  agent), the same global-tools-only check applies — a flow's own tool node is a new way to
  reach a workspace-private tool that the agent-only version of this file never had to
  consider. `required_tool_categories` is the union of every agent's tool categories and
  every direct tool node's category, still derived rather than self-declared.
- **Install** (`_install_agent_snapshot`, extracted the same way): forks one new agent (new
  `agents`/`agent_versions`/`agent_version_tools` rows) per distinct agent snapshot the
  publish step recorded, resolving each tool by `(kind, key)` against the installing
  workspace's global tools — the same lookup the plain agent install already did, just run
  once per referenced agent instead of once total. The installed flow's spec is the
  published `flow_spec` with every `agent` node's `agent_id` rewritten to its newly-forked
  counterpart; `tool` nodes are left untouched, since a global tool's row (and id) is the
  same one shared row in every workspace — there's nothing to fork. The whole install still
  runs inside one transaction, so a tool that's gone missing from the platform since
  publish time rolls back every agent already forked in that same install, not just the one
  that hit it.
- `listings.py` (the public browse/get router) gained a derived `kind: "agent" | "flow"`
  field (a one-line `case` in the query) — unused by anything yet, same as the rest of that
  router, but a storefront UI browsing listings that can now be either kind needs some way
  to tell them apart without inspecting `required_tool_categories` to guess.

Verified against the real Docker Postgres + API setup (no orchestrator needed — nothing
here starts a run). Built two agents in a publisher workspace, each with one global tool
attached (`current_time`, `run_python` — the two builtin tools `0002`/`0003` seed), wired
them into a flow alongside a third node that references a global tool *directly* (no
agent in between), and published it as a flow listing — confirmed
`required_tool_categories` came back as the union of all three (`builtin:current_time`
from both the agent and the direct tool node, deduplicated, plus `code_exec:run_python`).
Installed it into a second workspace: confirmed a partial tool-category approval still
403s (unchanged from the agent-listing path), and a full approval produced one new flow
and two new agents in the installing workspace, with the flow's `agent` nodes correctly
rewritten to the new agents' ids and its `tool` node left pointing at the original global
tool row. Confirmed the existing plain-agent-listing path (publish + install) still works
unchanged after the refactor into shared helpers. Confirmed both new-to-flows rejections:
republishing after attaching a sub-agent to one of the flow's agents 400s with
"delegates to sub-agents," and a flow referencing a workspace-private tool node 400s with
"only global tools can appear in a listing." Confirmed `ListingCreate`'s xor validator
422s on both "neither source" and "both sources."

No application-level tests exist for `services/api` (this project verifies the API via
Docker + curl/browser, not a pytest suite) and no frontend code touches these endpoints
yet, so there's nothing to typecheck or run beyond the manual verification above.

### True draft dry-run

The last of the §1 "explicitly not in this phase" items and the last of §10's open
items. A real gap, not just a nice-to-have: without it, trying a flow meant saving a
version first every time, including for a typo-fix-and-retry loop on something that
never needs to be kept.

The schema needed one new nullable column; nothing else about how a run executes or is
displayed had to change, because a dry-run is a completely ordinary `runs` row in every
other respect — real tokens, real tool calls, real `run_steps`, shows up in the Runs list
and this flow's own history. The only thing that's not real is where its spec came from.

- **`supabase/migrations/0006_run_draft_spec.sql`** — `runs.draft_spec jsonb`, nullable.
  Null (the existing, unchanged case) means "look up `flow_version_id`, or the latest
  `flow_versions` row for `flow_id`" exactly as before; non-null means "this is the spec,
  don't touch `flow_versions` at all."
- **`services/api/app/routers/runs.py`** — `RunCreate` gained `draft_spec: FlowSpec | None`
  (imported straight from `flows.py` — the exact same Pydantic model `POST
  .../flows/{flow_id}/versions` validates against, so a dry-run spec gets the identical
  "exactly one trigger node" check for free, no duplicated validation). Rejected with a
  400 if paired with `agent_id` instead of `flow_id` — an agent has no separate "unsaved
  spec" of its own the way a flow's canvas does, so this is flow-only. `RunListItemOut` and
  `RunDetailOut` both gained a derived `is_draft: bool` (`draft_spec is not null`), since a
  dry-run's `flow_id` can point at a flow with a completely different (or no) saved
  version, and the Runs UI needed some way to say so rather than silently looking like an
  ordinary flow run.
- **`services/orchestrator/orchestrator_app/flow_executor.py`** — `run_flow` now branches
  three ways instead of two: `draft_spec` present → use it directly; else
  `flow_version_id` set → load that exact version (unchanged); else → load the latest
  version for `flow_id` (unchanged). Everything downstream (`execute_flow`, node
  execution, `run_steps` logging) is identical regardless of which branch supplied the
  spec — it was already just handed a `spec: dict`, so it never needed to know or care
  where one came from.
- **Frontend**: `components/flows/TestRunDrawer.tsx` (new) — a "Test run" button in the
  canvas toolbar next to Triggers/Undo/Redo/Auto-layout/Save version, opening a small
  drawer (same JSON-input-textarea pattern the Runs page's own start-run drawer already
  uses) that calls `specFromCanvas(nodes, edges)` — the exact function `handleSave` already
  uses to build what gets POSTed as a saved version — and sends it as `draft_spec` on an
  ordinary `POST .../runs` instead, then navigates to `/runs/{id}` to watch it, reusing the
  run detail page as-is. The Runs list and Run detail page both got a small
  `is_draft`-driven label ("test run, unsaved spec" / a gray "test run — unsaved spec"
  badge) so a dry-run never looks indistinguishable from a real flow run in either view.

Verified against the real Docker Postgres + API + orchestrator setup. Via `curl`: created a
flow with zero saved versions, POSTed a run with `draft_spec` set directly and confirmed it
executed successfully and produced real output (a `current_time` tool node's real
timestamp) despite `GET .../flows/{id}/versions` returning `[]` the whole time; saved a real
version afterward and confirmed an ordinary run (no `draft_spec`) still works unchanged and
comes back `is_draft: false`; confirmed `draft_spec` + `agent_id` together 400s, and that a
`draft_spec` with no trigger node 422s via the shared `FlowSpec` validator. Then end-to-end
in the browser against the same stack: signed in, built a brand-new flow, dragged a Tool
node onto the canvas, wired it to the trigger, picked `current_time` — all without ever
clicking "Save version" (toolbar showed "Unsaved changes" throughout) — clicked "Test run,"
entered input JSON, submitted, and landed on `/runs/{id}` showing `succeeded` with the real
tool output and a "test run — unsaved spec" badge; confirmed the same run appears in the
Runs list labeled "(test run, unsaved spec)."

`tsc --noEmit`, `npm run build` (11 routes, unchanged — Test run is a drawer, not a new
route), and `npm test` (9 tests, unaffected) all clean.
