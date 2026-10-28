"use client";

/** The right-hand inspector panel (docs/04-frontend-builder.md §4/§5, step 5 of the build
 * order §9). Switches on whatever's selected: a single node gets that type's form, a single
 * edge gets the three-control condition editor, more than one of either (React Flow's own
 * multi-select, §10) gets a bulk-delete panel, nothing selected gets a placeholder. */
import { useEffect, useState } from "react";

import type { FlowCanvasEdge, FlowCanvasNode, FlowNodeConfig } from "@/lib/flowSpec";
import { useFlowLookup } from "./FlowLookupContext";

type Agent = { id: string; name: string };
type Tool = { id: string; key: string };

const INPUT_MAP_KEY = "input_map";

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function withoutInputMap(config: FlowNodeConfig): FlowNodeConfig {
  const { [INPUT_MAP_KEY]: _inputMap, ...rest } = config;
  return rest;
}

function inputMapSource(config: FlowNodeConfig): string {
  const value = config[INPUT_MAP_KEY];
  if (value && typeof value === "object" && typeof (value as { from?: unknown }).from === "string") {
    return (value as { from: string }).from;
  }
  return "";
}

export function NodeInspector({
  selectedNodes,
  selectedEdges,
  nodes,
  edges,
  agents,
  tools,
  onUpdateNodeData,
  onDeleteNode,
  onUpdateEdgeCondition,
  onDeleteEdge,
  onDeleteSelected,
}: {
  selectedNodes: FlowCanvasNode[];
  selectedEdges: FlowCanvasEdge[];
  nodes: FlowCanvasNode[];
  edges: FlowCanvasEdge[];
  agents: Agent[];
  tools: Tool[];
  onUpdateNodeData: (id: string, data: FlowCanvasNode["data"]) => void;
  onDeleteNode: (id: string) => void;
  onUpdateEdgeCondition: (id: string, condition: string | null) => void;
  onDeleteEdge: (id: string) => void;
  onDeleteSelected: (nodeIds: string[], edgeIds: string[]) => void;
}) {
  const total = selectedNodes.length + selectedEdges.length;

  if (total > 1) {
    return (
      <MultiSelectPanel selectedNodes={selectedNodes} selectedEdges={selectedEdges} onDeleteSelected={onDeleteSelected} />
    );
  }
  if (selectedNodes.length === 1) {
    const node = selectedNodes[0];
    return (
      <NodePanel
        key={node.id}
        node={node}
        nodes={nodes}
        edges={edges}
        agents={agents}
        tools={tools}
        onUpdateNodeData={onUpdateNodeData}
        onDeleteNode={onDeleteNode}
      />
    );
  }
  if (selectedEdges.length === 1) {
    const edge = selectedEdges[0];
    return (
      <EdgePanel
        key={edge.id}
        edge={edge}
        nodes={nodes}
        onUpdateEdgeCondition={onUpdateEdgeCondition}
        onDeleteEdge={onDeleteEdge}
      />
    );
  }
  return <p className="mt-2 text-sm text-gray-400">Select a node or edge to edit it here.</p>;
}

function MultiSelectPanel({
  selectedNodes,
  selectedEdges,
  onDeleteSelected,
}: {
  selectedNodes: FlowCanvasNode[];
  selectedEdges: FlowCanvasEdge[];
  onDeleteSelected: (nodeIds: string[], edgeIds: string[]) => void;
}) {
  // Same rule as the single-node panel (no Delete button for the trigger) --
  // applied across the whole selection instead of just one node.
  const deletableNodes = selectedNodes.filter((n) => n.type !== "trigger");
  const skippedTrigger = selectedNodes.length !== deletableNodes.length;
  const count = deletableNodes.length + selectedEdges.length;

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-gray-900">
        {selectedNodes.length} node{selectedNodes.length === 1 ? "" : "s"}, {selectedEdges.length} edge
        {selectedEdges.length === 1 ? "" : "s"} selected
      </h3>
      <p className="text-sm text-gray-500">Multi-select with Shift-drag or Cmd/Ctrl-click.</p>
      {skippedTrigger && <p className="text-xs text-gray-400">The trigger node can&apos;t be deleted.</p>}
      <button
        onClick={() => onDeleteSelected(deletableNodes.map((n) => n.id), selectedEdges.map((e) => e.id))}
        disabled={count === 0}
        className="w-full rounded-md border border-red-200 px-3 py-2 text-sm font-medium text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
      >
        Delete {count} selected
      </button>
    </div>
  );
}

function PanelShell({
  title,
  onDelete,
  children,
}: {
  title: string;
  onDelete?: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">{title}</h3>
        {onDelete && (
          <button onClick={onDelete} className="text-xs font-medium text-red-600 hover:text-red-700">
            Delete
          </button>
        )}
      </div>
      {children}
    </div>
  );
}

function InputMapControl({
  node,
  nodes,
  edges,
  onChange,
}: {
  node: FlowCanvasNode;
  nodes: FlowCanvasNode[];
  edges: FlowCanvasEdge[];
  onChange: (source: string) => void;
}) {
  const { agentNameById, toolKeyById } = useFlowLookup();
  const predecessorIds = edges.filter((e) => e.target === node.id).map((e) => e.source);

  function labelFor(id: string): string {
    const predecessor = nodes.find((n) => n.id === id);
    if (!predecessor) return id;
    if (predecessor.type === "agent" && predecessor.data.agentId) {
      return agentNameById[predecessor.data.agentId] ?? id;
    }
    if (predecessor.type === "tool" && predecessor.data.toolId) {
      return toolKeyById[predecessor.data.toolId] ?? id;
    }
    return `${predecessor.type} node`;
  }

  return (
    <div>
      <label className="block text-sm font-medium text-gray-700">Input</label>
      <select
        value={inputMapSource(node.data.config)}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
      >
        <option value="">Flow input</option>
        {predecessorIds.map((id) => (
          <option key={id} value={id}>
            ← {labelFor(id)}
          </option>
        ))}
      </select>
      {predecessorIds.length === 0 && (
        <p className="mt-1 text-xs text-gray-400">No incoming edges yet — this node will receive the flow's input.</p>
      )}
    </div>
  );
}

function NodePanel({
  node,
  nodes,
  edges,
  agents,
  tools,
  onUpdateNodeData,
  onDeleteNode,
}: {
  node: FlowCanvasNode;
  nodes: FlowCanvasNode[];
  edges: FlowCanvasEdge[];
  agents: Agent[];
  tools: Tool[];
  onUpdateNodeData: (id: string, data: FlowCanvasNode["data"]) => void;
  onDeleteNode: (id: string) => void;
}) {
  function setInputMap(source: string) {
    const rest = withoutInputMap(node.data.config);
    onUpdateNodeData(node.id, {
      ...node.data,
      config: source ? { ...rest, [INPUT_MAP_KEY]: { from: source } } : rest,
    });
  }

  if (node.type === "trigger") {
    return (
      <PanelShell title="Trigger">
        <p className="text-sm text-gray-500">
          This flow starts here, seeded with the run's input — the same way whether the run was started by hand,
          by a webhook, or on a schedule. Nothing to configure on this node itself; set up webhook/schedule triggers
          from the "Triggers" button in the top bar.
        </p>
      </PanelShell>
    );
  }

  if (node.type === "agent") {
    return (
      <PanelShell title="Agent" onDelete={() => onDeleteNode(node.id)}>
        <div>
          <label className="block text-sm font-medium text-gray-700">Agent</label>
          <select
            value={node.data.agentId ?? ""}
            onChange={(e) => onUpdateNodeData(node.id, { ...node.data, agentId: e.target.value || null })}
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
          >
            <option value="">Select an agent…</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </div>
        <InputMapControl node={node} nodes={nodes} edges={edges} onChange={setInputMap} />
      </PanelShell>
    );
  }

  if (node.type === "tool") {
    return (
      <ToolPanel
        node={node}
        nodes={nodes}
        edges={edges}
        tools={tools}
        onUpdateNodeData={onUpdateNodeData}
        onDeleteNode={onDeleteNode}
        setInputMap={setInputMap}
      />
    );
  }

  // conditional / merge: passthrough in the executor, label-only panel (§4) -- the
  // conditional's actual branching logic lives on its *outgoing edges*, not here.
  const note =
    node.type === "conditional"
      ? "A branch point. The condition that decides which way a run goes lives on each outgoing edge — select one to edit it."
      : "Waits for every predecessor before continuing (LangGraph's default join). Nothing to configure here.";
  return (
    <PanelShell title={node.type === "conditional" ? "Conditional" : "Merge"} onDelete={() => onDeleteNode(node.id)}>
      <p className="text-sm text-gray-500">{note}</p>
    </PanelShell>
  );
}

function ToolPanel({
  node,
  nodes,
  edges,
  tools,
  onUpdateNodeData,
  onDeleteNode,
  setInputMap,
}: {
  node: FlowCanvasNode;
  nodes: FlowCanvasNode[];
  edges: FlowCanvasEdge[];
  tools: Tool[];
  onUpdateNodeData: (id: string, data: FlowCanvasNode["data"]) => void;
  onDeleteNode: (id: string) => void;
  setInputMap: (source: string) => void;
}) {
  const [configText, setConfigText] = useState("{}");
  const [configError, setConfigError] = useState<string | null>(null);

  // Reset the textarea buffer whenever the selected node changes (the panel is
  // re-keyed by node.id already, so this only ever runs for the node it's showing).
  useEffect(() => {
    setConfigText(JSON.stringify(withoutInputMap(node.data.config), null, 2));
    setConfigError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node.id]);

  function handleConfigChange(text: string) {
    setConfigText(text);
    try {
      const parsed = JSON.parse(text || "{}");
      setConfigError(null);
      const inputMap = node.data.config[INPUT_MAP_KEY];
      onUpdateNodeData(node.id, {
        ...node.data,
        config: inputMap !== undefined ? { ...parsed, [INPUT_MAP_KEY]: inputMap } : parsed,
      });
    } catch {
      setConfigError("Invalid JSON — not saved until this is fixed");
    }
  }

  return (
    <PanelShell title="Tool" onDelete={() => onDeleteNode(node.id)}>
      <div>
        <label className="block text-sm font-medium text-gray-700">Tool</label>
        <select
          value={node.data.toolId ?? ""}
          onChange={(e) => onUpdateNodeData(node.id, { ...node.data, toolId: e.target.value || null })}
          className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
        >
          <option value="">Select a tool…</option>
          {tools.map((t) => (
            <option key={t.id} value={t.id}>
              {t.key}
            </option>
          ))}
        </select>
      </div>
      <InputMapControl node={node} nodes={nodes} edges={edges} onChange={setInputMap} />
      <div>
        <label className="block text-sm font-medium text-gray-700">Config (JSON)</label>
        <textarea
          rows={6}
          value={configText}
          onChange={(e) => handleConfigChange(e.target.value)}
          className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1 font-mono text-xs focus:border-gray-500 focus:outline-none"
        />
        {configError && <p className="mt-1 text-xs text-red-600">{configError}</p>}
      </div>
    </PanelShell>
  );
}

function EdgePanel({
  edge,
  nodes,
  onUpdateEdgeCondition,
  onDeleteEdge,
}: {
  edge: FlowCanvasEdge;
  nodes: FlowCanvasNode[];
  onUpdateEdgeCondition: (id: string, condition: string | null) => void;
  onDeleteEdge: (id: string) => void;
}) {
  const { agentNameById, toolKeyById } = useFlowLookup();
  const sourceNode = nodes.find((n) => n.id === edge.source);
  const sourceLabel =
    sourceNode?.type === "agent" && sourceNode.data.agentId
      ? agentNameById[sourceNode.data.agentId] ?? edge.source
      : sourceNode?.type === "tool" && sourceNode.data.toolId
      ? toolKeyById[sourceNode.data.toolId] ?? edge.source
      : sourceNode
      ? `${sourceNode.type} node`
      : edge.source;

  const [negate, setNegate] = useState(false);
  const [keyPath, setKeyPath] = useState("");

  useEffect(() => {
    const condition = edge.data?.condition ?? null;
    if (!condition) {
      setNegate(false);
      setKeyPath("");
      return;
    }
    const pattern = new RegExp(`^(not )?${escapeRegExp(edge.source)}\\.output\\.(.+)$`);
    const match = condition.match(pattern);
    if (match) {
      setNegate(Boolean(match[1]));
      setKeyPath(match[2]);
    } else {
      setNegate(false);
      setKeyPath("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [edge.id]);

  function apply(nextNegate: boolean, nextKeyPath: string) {
    setNegate(nextNegate);
    setKeyPath(nextKeyPath);
    const trimmed = nextKeyPath.trim();
    onUpdateEdgeCondition(edge.id, trimmed ? `${nextNegate ? "not " : ""}${edge.source}.output.${trimmed}` : null);
  }

  return (
    <PanelShell title="Edge" onDelete={() => onDeleteEdge(edge.id)}>
      <p className="text-xs text-gray-500">
        From <span className="font-medium text-gray-700">{sourceLabel}</span> — a condition can only reference the
        node this edge comes from (docs/04-frontend-builder.md §5).
      </p>
      <div>
        <label className="block text-sm font-medium text-gray-700">Branch</label>
        <div className="mt-1 flex gap-2 text-sm">
          <button
            onClick={() => apply(false, keyPath)}
            className={`flex-1 rounded-md border px-2 py-1 ${!negate ? "border-gray-900 bg-gray-900 text-white" : "border-gray-300 text-gray-700"}`}
          >
            when
          </button>
          <button
            onClick={() => apply(true, keyPath)}
            className={`flex-1 rounded-md border px-2 py-1 ${negate ? "border-gray-900 bg-gray-900 text-white" : "border-gray-300 text-gray-700"}`}
          >
            unless
          </button>
        </div>
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700" htmlFor="condition-key-path">
          {sourceLabel}.output.
        </label>
        <input
          id="condition-key-path"
          value={keyPath}
          onChange={(e) => apply(negate, e.target.value)}
          placeholder="decision (leave blank for unconditional)"
          className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-sm focus:border-gray-500 focus:outline-none"
        />
        <p className="mt-1 text-xs text-gray-400">
          {keyPath.trim()
            ? `Taken when ${edge.source}.output.${keyPath.trim()} is ${negate ? "falsy" : "truthy"}.`
            : "Unconditional — always taken (and if this node has other outgoing edges with no condition either, all of them run in parallel)."}
        </p>
      </div>
    </PanelShell>
  );
}
