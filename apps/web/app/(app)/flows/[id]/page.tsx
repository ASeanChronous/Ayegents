"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
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
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);

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

  const loadSpec = useCallback((spec: FlowSpec) => {
    const { nodes: seededNodes, edges: seededEdges } = canvasFromSpec(spec);
    setNodes(seededNodes);
    setEdges(seededEdges);
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
    setDirty(false);
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

  const onNodesChange: OnNodesChange<FlowCanvasNode> = useCallback((changes) => {
    setNodes((nds) => applyNodeChanges(changes, nds ?? []));
    if (changes.some((c) => c.type === "remove" || (c.type === "position" && c.dragging === false))) {
      setDirty(true);
    }
  }, []);
  const onEdgesChange: OnEdgesChange<FlowCanvasEdge> = useCallback((changes) => {
    setEdges((eds) => applyEdgeChanges(changes, eds ?? []));
    if (changes.some((c) => c.type === "remove")) setDirty(true);
  }, []);
  const onConnect: OnConnect = useCallback((connection) => {
    setEdges((eds) => addEdge({ ...connection, data: { condition: null } }, eds ?? []));
    setDirty(true);
  }, []);
  const onSelectionChange: OnSelectionChangeFunc = useCallback(({ nodes: selNodes, edges: selEdges }) => {
    setSelectedNodeId(selNodes[0]?.id ?? null);
    setSelectedEdgeId(selEdges[0]?.id ?? null);
  }, []);
  const onAddNode = useCallback((type: FlowNodeType, position: { x: number; y: number }) => {
    setNodes((nds) => [...(nds ?? []), createCanvasNode(type, position)]);
    setDirty(true);
  }, []);
  const onUpdateNodeData = useCallback((id: string, data: FlowCanvasNode["data"]) => {
    setNodes((nds) => (nds ?? []).map((n) => (n.id === id ? { ...n, data } : n)));
    setDirty(true);
  }, []);
  const onDeleteNode = useCallback((id: string) => {
    setNodes((nds) => (nds ?? []).filter((n) => n.id !== id));
    setEdges((eds) => (eds ?? []).filter((e) => e.source !== id && e.target !== id));
    setSelectedNodeId(null);
    setDirty(true);
  }, []);
  const onUpdateEdgeCondition = useCallback((id: string, condition: string | null) => {
    setEdges((eds) => (eds ?? []).map((e) => (e.id === id ? { ...e, data: { condition } } : e)));
    setDirty(true);
  }, []);
  const onDeleteEdge = useCallback((id: string) => {
    setEdges((eds) => (eds ?? []).filter((e) => e.id !== id));
    setSelectedEdgeId(null);
    setDirty(true);
  }, []);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

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

  const selectedNode = nodes?.find((n) => n.id === selectedNodeId) ?? null;
  const selectedEdge = edges?.find((e) => e.id === selectedEdgeId) ?? null;

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
            {" — "}
            Start a run arrives in the next step (docs/04-frontend-builder.md §9).
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
                selectedNode={selectedNode}
                selectedEdge={selectedEdge}
                nodes={nodes ?? []}
                edges={edges ?? []}
                agents={agents ?? []}
                tools={tools ?? []}
                onUpdateNodeData={onUpdateNodeData}
                onDeleteNode={onDeleteNode}
                onUpdateEdgeCondition={onUpdateEdgeCondition}
                onDeleteEdge={onDeleteEdge}
              />
            </FlowLookupProvider>
          </div>
        </div>
      </div>
    </div>
  );
}
