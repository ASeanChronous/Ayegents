"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import useSWR from "swr";
import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type OnConnect,
  type OnEdgesChange,
  type OnNodesChange,
  type OnSelectionChangeFunc,
} from "@xyflow/react";

import { api, unwrap, ApiError } from "@/lib/api";
import { useWorkspace } from "@/lib/useWorkspace";
import {
  autoLayoutPositions,
  canvasFromSpec,
  createCanvasNode,
  specFromCanvas,
  type FlowCanvasEdge,
  type FlowCanvasNode,
  type FlowNodeType,
  type FlowSpec,
} from "@/lib/flowSpec";
import { FlowCanvas } from "@/components/flows/FlowCanvas";
import { NodePalette } from "@/components/flows/NodePalette";
import { NodeInspector } from "@/components/flows/NodeInspector";
import { FlowLookupProvider } from "@/components/flows/FlowLookupContext";
import { TriggersDrawer } from "@/components/flows/TriggersDrawer";

type Agent = { id: string; name: string };
type Tool = { id: string; key: string };

// A brand-new flow (no versions yet) opens with a single trigger node and
// nothing else -- docs/04-frontend-builder.md §1 and §4 (the trigger is
// auto-added, not something the user drags in).
const BLANK_SPEC: FlowSpec = {
  nodes: [{ id: "trigger", type: "trigger", agent_id: null, tool_id: null, config: {} }],
  edges: [],
};

export default function FlowCanvasPage() {
  const { id: flowId } = useParams<{ id: string }>();
  const { currentWorkspace } = useWorkspace();
  const workspaceId = currentWorkspace?.id;

  const { data: flow } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/flows/${flowId}` : null,
    () =>
      unwrap(
        api.GET("/workspaces/{workspace_id}/flows/{flow_id}", {
          params: { path: { workspace_id: workspaceId!, flow_id: flowId } },
        })
      )
  );
  const { data: versions, mutate: mutateVersions } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/flows/${flowId}/versions` : null,
    () =>
      unwrap(
        api.GET("/workspaces/{workspace_id}/flows/{flow_id}/versions", {
          params: { path: { workspace_id: workspaceId!, flow_id: flowId } },
        })
      )
  );
  const { data: agents } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/agents` : null,
    () => unwrap(api.GET("/workspaces/{workspace_id}/agents", { params: { path: { workspace_id: workspaceId! } } }))
  );
  const { data: tools } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/tools` : null,
    () => unwrap(api.GET("/workspaces/{workspace_id}/tools", { params: { path: { workspace_id: workspaceId! } } }))
  );

  // Canvas state is owned here, not derived every render -- step 5 makes the
  // canvas editable, so once seeded it has to survive re-renders (and SWR
  // revalidation) without snapping back.
  const [nodes, setNodes] = useState<FlowCanvasNode[] | null>(null);
  const [edges, setEdges] = useState<FlowCanvasEdge[] | null>(null);
  // Arrays, not single ids -- React Flow's own box-select (Shift-drag) and
  // cmd/ctrl-click can select more than one node and/or edge at once (§10's
  // "multi-select"), and the inspector needs to know the whole set to offer
  // a bulk delete instead of silently acting on whichever selected first.
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [selectedEdgeIds, setSelectedEdgeIds] = useState<string[]>([]);

  // Which version the picker (§9 step 7) currently has loaded -- null for a
  // brand-new flow with no versions yet. Saving always creates version
  // max+1 regardless of which one is loaded (same pattern the agent version
  // drawer already uses: load an old version, re-save as a new one, never
  // overwrite), so this is purely "what's the canvas currently showing,"
  // not "what will Save write to."
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);

  // Tracks whether the canvas has edits the server doesn't have yet. Set on
  // every real edit (not on mere selection or React Flow's own first-render
  // dimension measurement) and cleared whenever nodes/edges are (re)seeded
  // from a version -- picking one from the dropdown, the initial load, or
  // the reload a successful save triggers below.
  const [dirty, setDirty] = useState(false);

  // Undo/redo (§10): a stack of {nodes, edges} snapshots taken just *before*
  // each committed edit -- add/delete, a finished drag, a new connection, a
  // data/condition change -- not on every intermediate drag frame, which
  // would make one drag take dozens of undo steps. nodesRef/edgesRef exist
  // only so pushHistory (called from change handlers that are useCallback'd
  // with empty deps, to avoid recreating them on every keystroke) can read
  // the current canvas state without needing those deps -- handleUndo/Redo
  // below are plain functions, recreated every render, and read `nodes`/
  // `edges` directly instead.
  const nodesRef = useRef<FlowCanvasNode[] | null>(null);
  const edgesRef = useRef<FlowCanvasEdge[] | null>(null);
  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);
  useEffect(() => {
    edgesRef.current = edges;
  }, [edges]);

  type CanvasSnapshot = { nodes: FlowCanvasNode[]; edges: FlowCanvasEdge[] };
  const HISTORY_LIMIT = 50;
  const [undoStack, setUndoStack] = useState<CanvasSnapshot[]>([]);
  const [redoStack, setRedoStack] = useState<CanvasSnapshot[]>([]);

  const pushHistory = useCallback(() => {
    if (nodesRef.current === null || edgesRef.current === null) return;
    setUndoStack((stack) =>
      [...stack, { nodes: nodesRef.current!, edges: edgesRef.current! }].slice(-HISTORY_LIMIT)
    );
    setRedoStack([]);
  }, []);

  function handleUndo() {
    if (undoStack.length === 0 || nodes === null || edges === null) return;
    const previous = undoStack[undoStack.length - 1];
    setRedoStack((stack) => [...stack, { nodes, edges }].slice(-HISTORY_LIMIT));
    setUndoStack((stack) => stack.slice(0, -1));
    setNodes(previous.nodes);
    setEdges(previous.edges);
    setSelectedNodeIds([]);
    setSelectedEdgeIds([]);
    setDirty(true);
  }

  function handleRedo() {
    if (redoStack.length === 0 || nodes === null || edges === null) return;
    const next = redoStack[redoStack.length - 1];
    setUndoStack((stack) => [...stack, { nodes, edges }].slice(-HISTORY_LIMIT));
    setRedoStack((stack) => stack.slice(0, -1));
    setNodes(next.nodes);
    setEdges(next.edges);
    setSelectedNodeIds([]);
    setSelectedEdgeIds([]);
    setDirty(true);
  }

  // Keyboard shortcuts for the two buttons below. Guarded against an input
  // field having focus (the inspector's text/JSON fields, the version
  // picker) so Cmd/Ctrl-Z there does that field's own native undo instead
  // of hijacking it for the canvas.
  useEffect(() => {
    function isTextEntryFocused(): boolean {
      const el = document.activeElement;
      if (!el) return false;
      const tag = el.tagName;
      return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (el as HTMLElement).isContentEditable;
    }
    function handleKeyDown(e: KeyboardEvent) {
      if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== "z" || isTextEntryFocused()) return;
      e.preventDefault();
      if (e.shiftKey) {
        handleRedo();
      } else {
        handleUndo();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [undoStack, redoStack, nodes, edges]);

  const loadSpec = useCallback((spec: FlowSpec) => {
    const { nodes: seededNodes, edges: seededEdges } = canvasFromSpec(spec);
    setNodes(seededNodes);
    setEdges(seededEdges);
    setSelectedNodeIds([]);
    setSelectedEdgeIds([]);
    setDirty(false);
    // Switching versions (or the initial load) replaces the canvas wholesale
    // -- undoing past that point would resurrect a version the user just
    // navigated away from, which reads as broken rather than "undo."
    setUndoStack([]);
    setRedoStack([]);
  }, []);

  // versions is ordered newest-first (services/api/app/routers/flows.py), so
  // [0] is the latest. Runs only when `versions` itself changes identity --
  // the initial load, or a refetch after save -- not when the user merely
  // picks a different version from the dropdown (that's handleVersionPick
  // below, which doesn't touch SWR's data at all).
  useEffect(() => {
    if (versions === undefined) return;
    const newest = versions.length > 0 ? versions[0] : null;
    setSelectedVersionId(newest?.id ?? null);
    loadSpec(newest?.spec ?? BLANK_SPEC);
  }, [versions, loadSpec]);

  function handleVersionPick(versionId: string) {
    const picked = versions?.find((v) => v.id === versionId);
    if (!picked) return;
    setSelectedVersionId(versionId);
    loadSpec(picked.spec);
  }

  const onNodesChange: OnNodesChange<FlowCanvasNode> = useCallback(
    (changes) => {
      const isCommit = changes.some((c) => c.type === "remove" || (c.type === "position" && c.dragging === false));
      if (isCommit) pushHistory();
      setNodes((nds) => applyNodeChanges(changes, nds ?? []));
      if (isCommit) setDirty(true);
    },
    [pushHistory]
  );
  const onEdgesChange: OnEdgesChange<FlowCanvasEdge> = useCallback(
    (changes) => {
      const isCommit = changes.some((c) => c.type === "remove");
      if (isCommit) pushHistory();
      setEdges((eds) => applyEdgeChanges(changes, eds ?? []));
      if (isCommit) setDirty(true);
    },
    [pushHistory]
  );
  const onConnect: OnConnect = useCallback(
    (connection) => {
      pushHistory();
      setEdges((eds) => addEdge({ ...connection, data: { condition: null } }, eds ?? []));
      setDirty(true);
    },
    [pushHistory]
  );
  const onSelectionChange: OnSelectionChangeFunc = useCallback(({ nodes: selNodes, edges: selEdges }) => {
    setSelectedNodeIds(selNodes.map((n) => n.id));
    setSelectedEdgeIds(selEdges.map((e) => e.id));
  }, []);
  const onAddNode = useCallback(
    (type: FlowNodeType, position: { x: number; y: number }) => {
      pushHistory();
      setNodes((nds) => [...(nds ?? []), createCanvasNode(type, position)]);
      setDirty(true);
    },
    [pushHistory]
  );
  const onUpdateNodeData = useCallback(
    (id: string, data: FlowCanvasNode["data"]) => {
      pushHistory();
      setNodes((nds) => (nds ?? []).map((n) => (n.id === id ? { ...n, data } : n)));
      setDirty(true);
    },
    [pushHistory]
  );
  const onDeleteNode = useCallback(
    (id: string) => {
      pushHistory();
      setNodes((nds) => (nds ?? []).filter((n) => n.id !== id));
      setEdges((eds) => (eds ?? []).filter((e) => e.source !== id && e.target !== id));
      setSelectedNodeIds([]);
      setDirty(true);
    },
    [pushHistory]
  );
  const onUpdateEdgeCondition = useCallback(
    (id: string, condition: string | null) => {
      pushHistory();
      setEdges((eds) => (eds ?? []).map((e) => (e.id === id ? { ...e, data: { condition } } : e)));
      setDirty(true);
    },
    [pushHistory]
  );
  const onDeleteEdge = useCallback(
    (id: string) => {
      pushHistory();
      setEdges((eds) => (eds ?? []).filter((e) => e.id !== id));
      setSelectedEdgeIds([]);
      setDirty(true);
    },
    [pushHistory]
  );
  // Bulk delete from the inspector's multi-select panel (§10) -- `nodeIds`
  // never includes the trigger (NodeInspector filters it out before calling
  // this), but an edge touching a node being removed here still needs to go
  // too, same as the single-node onDeleteNode above.
  const onDeleteSelected = useCallback(
    (nodeIds: string[], edgeIds: string[]) => {
      if (nodeIds.length === 0 && edgeIds.length === 0) return;
      pushHistory();
      const nodeIdSet = new Set(nodeIds);
      const edgeIdSet = new Set(edgeIds);
      setNodes((nds) => (nds ?? []).filter((n) => !nodeIdSet.has(n.id)));
      setEdges((eds) =>
        (eds ?? []).filter((e) => !edgeIdSet.has(e.id) && !nodeIdSet.has(e.source) && !nodeIdSet.has(e.target))
      );
      setSelectedNodeIds([]);
      setSelectedEdgeIds([]);
      setDirty(true);
    },
    [pushHistory]
  );
  const handleAutoLayout = useCallback(() => {
    if (nodes === null || edges === null) return;
    pushHistory();
    const spec = specFromCanvas(nodes, edges);
    const positions = autoLayoutPositions(spec);
    setNodes((nds) => (nds ?? []).map((n) => ({ ...n, position: positions.get(n.id) ?? n.position })));
    setDirty(true);
  }, [nodes, edges, pushHistory]);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [triggersOpen, setTriggersOpen] = useState(false);

  async function handleSave() {
    if (!nodes || !edges) return;
    setSaving(true);
    setSaveError(null);
    try {
      const spec = specFromCanvas(nodes, edges);
      await unwrap(
        api.POST("/workspaces/{workspace_id}/flows/{flow_id}/versions", {
          params: { path: { workspace_id: workspaceId!, flow_id: flowId } },
          body: { spec },
        })
      );
      setDirty(false);
      // Reloads the version just saved, which re-seeds the canvas via the
      // effect above -- the cheapest way to confirm the round trip actually
      // took (same mechanism docs/04-frontend-builder.md §8 verified by hand
      // against the raw API; this is that same check, now live in the UI).
      await mutateVersions();
    } catch (e) {
      setSaveError(e instanceof ApiError ? e.message : "Failed to save version");
    } finally {
      setSaving(false);
    }
  }

  const lookup = useMemo(
    () => ({
      agentNameById: Object.fromEntries((agents ?? []).map((a: Agent) => [a.id, a.name])),
      toolKeyById: Object.fromEntries((tools ?? []).map((t: Tool) => [t.id, t.key])),
    }),
    [agents, tools]
  );

  if (!workspaceId) {
    return <p className="p-6 text-sm text-gray-500">Pick or create a workspace first.</p>;
  }

  const selectedNodes = (nodes ?? []).filter((n) => selectedNodeIds.includes(n.id));
  const selectedEdges = (edges ?? []).filter((e) => selectedEdgeIds.includes(e.id));

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-shrink-0 items-center justify-between border-b border-gray-200 bg-white px-4 py-2">
        <div className="flex items-center gap-3">
          <Link href="/flows" className="text-sm text-gray-500 hover:text-gray-700">
            ← Flows
          </Link>
          <h1 className="text-sm font-semibold">{flow?.name ?? "…"}</h1>
        </div>
        <div className="flex items-center gap-3">
          {versions && versions.length > 0 && (
            <select
              value={selectedVersionId ?? ""}
              onChange={(e) => handleVersionPick(e.target.value)}
              className="rounded-md border border-gray-300 px-2 py-1 text-xs focus:border-gray-500 focus:outline-none"
              title="Load a version into the editor -- saving always creates a new one, never overwrites"
            >
              {versions.map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.version} — {new Date(v.created_at).toLocaleString()}
                </option>
              ))}
            </select>
          )}
          <button
            onClick={() => setTriggersOpen(true)}
            className="rounded-md border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
          >
            Triggers
          </button>
          <div className="flex items-center gap-1">
            <button
              onClick={handleUndo}
              disabled={undoStack.length === 0}
              title="Undo (Cmd/Ctrl+Z)"
              className="rounded-md border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Undo
            </button>
            <button
              onClick={handleRedo}
              disabled={redoStack.length === 0}
              title="Redo (Cmd/Ctrl+Shift+Z)"
              className="rounded-md border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Redo
            </button>
            <button
              onClick={handleAutoLayout}
              disabled={!nodes || !edges}
              title="Re-lay-out every node by BFS depth from the trigger"
              className="rounded-md border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Auto-layout
            </button>
          </div>
          <p className="text-xs text-gray-400">
            {saveError ? (
              <span className="text-red-600">{saveError}</span>
            ) : saving ? (
              "Saving…"
            ) : dirty ? (
              "Unsaved changes"
            ) : (
              "All changes saved"
            )}
          </p>
          <button
            onClick={handleSave}
            disabled={saving || !nodes || !edges}
            className="rounded-md bg-gray-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-gray-800 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save version"}
          </button>
        </div>
      </div>
      <div className="flex min-h-0 flex-1">
        <NodePalette />
        <div className="min-w-0 flex-1">
          {nodes === null || edges === null ? (
            <p className="p-6 text-sm text-gray-500">Loading…</p>
          ) : (
            <FlowLookupProvider value={lookup}>
              <FlowCanvas
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onConnect={onConnect}
                onSelectionChange={onSelectionChange}
                onAddNode={onAddNode}
              />
            </FlowLookupProvider>
          )}
        </div>
        <div className="w-72 flex-shrink-0 overflow-y-auto border-l border-gray-200 bg-white p-3">
          <h2 className="text-xs font-medium uppercase tracking-wide text-gray-500">Inspector</h2>
          <div className="mt-2">
            <FlowLookupProvider value={lookup}>
              <NodeInspector
                selectedNodes={selectedNodes}
                selectedEdges={selectedEdges}
                nodes={nodes ?? []}
                edges={edges ?? []}
                agents={agents ?? []}
                tools={tools ?? []}
                onUpdateNodeData={onUpdateNodeData}
                onDeleteNode={onDeleteNode}
                onUpdateEdgeCondition={onUpdateEdgeCondition}
                onDeleteEdge={onDeleteEdge}
                onDeleteSelected={onDeleteSelected}
              />
            </FlowLookupProvider>
          </div>
        </div>
      </div>
      <TriggersDrawer
        open={triggersOpen}
        onClose={() => setTriggersOpen(false)}
        workspaceId={workspaceId}
        flowId={flowId}
      />
    </div>
  );
}
