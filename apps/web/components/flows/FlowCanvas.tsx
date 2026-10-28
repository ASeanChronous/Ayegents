"use client";

/** Wraps @xyflow/react's <ReactFlow> (docs/04-frontend-builder.md §7). Read-only for now
 * (step 4 of the build order, §9) -- nodes can't be dragged, connected, or deleted; that
 * interaction lands in step 5 alongside <NodeInspector>. */
import "@xyflow/react/dist/style.css";

import { Background, Controls, MiniMap, ReactFlow, ReactFlowProvider } from "@xyflow/react";

import type { FlowCanvasEdge, FlowCanvasNode } from "@/lib/flowSpec";
import { nodeTypes } from "./nodeTypes";

export function FlowCanvas({ nodes, edges }: { nodes: FlowCanvasNode[]; edges: FlowCanvasEdge[] }) {
  return (
    <ReactFlowProvider>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        nodesDraggable={false}
        nodesConnectable={false}
        edgesReconnectable={false}
        elementsSelectable={true}
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Background />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable />
      </ReactFlow>
    </ReactFlowProvider>
  );
}
