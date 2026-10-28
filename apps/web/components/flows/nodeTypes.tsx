"use client";

/** The five node components, one per `FlowNode.type` (docs/04-frontend-builder.md §4).
 * Read-only for now (step 4 of the build order, §9): each renders compactly from the
 * `FlowCanvasNodeData` `canvasFromSpec` produces, with no editing. Per-type side panels
 * (the agent/tool pickers, the `input_map` control) are step 5. */
import { Handle, Position, type NodeProps } from "@xyflow/react";

import type { FlowCanvasNode } from "@/lib/flowSpec";
import { useFlowLookup } from "./FlowLookupContext";

const TYPE_STYLES: Record<FlowCanvasNode["type"] & string, { label: string; border: string; dot: string }> = {
  trigger: { label: "Trigger", border: "border-gray-900", dot: "bg-gray-900" },
  agent: { label: "Agent", border: "border-blue-500", dot: "bg-blue-500" },
  tool: { label: "Tool", border: "border-emerald-500", dot: "bg-emerald-500" },
  conditional: { label: "Conditional", border: "border-amber-500", dot: "bg-amber-500" },
  merge: { label: "Merge", border: "border-purple-500", dot: "bg-purple-500" },
};

function NodeShell({
  type,
  title,
  subtitle,
  selected,
  showTarget = true,
  showSource = true,
}: {
  type: FlowCanvasNode["type"] & string;
  title: string;
  subtitle?: string;
  selected: boolean;
  showTarget?: boolean;
  showSource?: boolean;
}) {
  const style = TYPE_STYLES[type];
  return (
    <div
      className={`w-48 rounded-md border-2 bg-white px-3 py-2 shadow-sm ${style.border} ${
        selected ? "ring-2 ring-offset-1 ring-gray-400" : ""
      }`}
    >
      {showTarget && <Handle type="target" position={Position.Left} className="!bg-gray-400" />}
      <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-gray-500">
        <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
        {style.label}
      </div>
      <div className="mt-0.5 truncate text-sm font-medium text-gray-900" title={title}>
        {title}
      </div>
      {subtitle && (
        <div className="mt-0.5 truncate text-xs text-gray-500" title={subtitle}>
          {subtitle}
        </div>
      )}
      {showSource && <Handle type="source" position={Position.Right} className="!bg-gray-400" />}
    </div>
  );
}

function inputMapSubtitle(data: FlowCanvasNode["data"]): string {
  const from = data.config.input_map;
  if (from && typeof from === "object" && "from" in from && typeof (from as { from: unknown }).from === "string") {
    return `Input: ← ${(from as { from: string }).from}`;
  }
  return "Input: flow input";
}

function TriggerNodeComponent({ selected }: NodeProps<FlowCanvasNode>) {
  return <NodeShell type="trigger" title="Flow starts here" subtitle="Seeded with the run's input" selected={selected} showTarget={false} />;
}

function AgentNodeComponent({ data, selected }: NodeProps<FlowCanvasNode>) {
  const { agentNameById } = useFlowLookup();
  const name = data.agentId ? agentNameById[data.agentId] ?? data.agentId : "No agent selected";
  return <NodeShell type="agent" title={name} subtitle={inputMapSubtitle(data)} selected={selected} />;
}

function ToolNodeComponent({ data, selected }: NodeProps<FlowCanvasNode>) {
  const { toolKeyById } = useFlowLookup();
  const name = data.toolId ? toolKeyById[data.toolId] ?? data.toolId : "No tool selected";
  return <NodeShell type="tool" title={name} subtitle={inputMapSubtitle(data)} selected={selected} />;
}

function ConditionalNodeComponent({ selected }: NodeProps<FlowCanvasNode>) {
  return <NodeShell type="conditional" title="Conditional" subtitle="Branches on an outgoing edge's condition" selected={selected} />;
}

function MergeNodeComponent({ selected }: NodeProps<FlowCanvasNode>) {
  return <NodeShell type="merge" title="Merge" subtitle="Waits for every predecessor" selected={selected} />;
}

export const nodeTypes = {
  trigger: TriggerNodeComponent,
  agent: AgentNodeComponent,
  tool: ToolNodeComponent,
  conditional: ConditionalNodeComponent,
  merge: MergeNodeComponent,
};
