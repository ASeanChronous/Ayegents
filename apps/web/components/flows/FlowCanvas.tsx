"use client";

/** Wraps @xyflow/react's <ReactFlow> (docs/04-frontend-builder.md §7). Step 5 of the build
 * order: fully interactive now -- drag to move, drag-to-connect edges, drop a palette entry
 * to add a node. The page owns `nodes`/`edges` as controlled state (it needs them for
 * `specFromCanvas` once Save lands in step 6), so this component only forwards React Flow's
 * change/connect/selection events upward rather than holding any canvas state itself. */
import "@xyflow/react/dist/style.css";

import { useCallback, type DragEvent } from "react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type OnConnect,
  type OnEdgesChange,
  type OnNodesChange,
  type OnSelectionChangeFunc,
} from "@xyflow/react";

import type { FlowCanvasEdge, FlowCanvasNode, FlowNodeType } from "@/lib/flowSpec";
import { nodeTypes } from "./nodeTypes";

const PALETTE_DRAG_TYPE = "application/flow-node-type";

type FlowCanvasProps = {
  nodes: FlowCanvasNode[];
  edges: FlowCanvasEdge[];
  onNodesChange: OnNodesChange<FlowCanvasNode>;
  onEdgesChange: OnEdgesChange<FlowCanvasEdge>;
  onConnect: OnConnect;
  onSelectionChange: OnSelectionChangeFunc;
  onAddNode: (type: FlowNodeType, position: { x: number; y: number }) => void;
};

function FlowCanvasInner({ nodes, edges, onNodesChange, onEdgesChange, onConnect, onSelectionChange, onAddNode }: FlowCanvasProps) {
  const { screenToFlowPosition } = useReactFlow();

  const handleDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      const type = event.dataTransfer.getData(PALETTE_DRAG_TYPE) as FlowNodeType | "";
      if (!type) return;
      onAddNode(type, screenToFlowPosition({ x: event.clientX, y: event.clientY }));
    },
    [screenToFlowPosition, onAddNode]
  );

  const handleDragOver = useCallback((event: DragEvent<HTMLDivElement>) => {
    if (event.dataTransfer.types.includes(PALETTE_DRAG_TYPE)) {
      event.preventDefault();
    }
  }, []);

  return (
    <div className="h-full w-full" onDrop={handleDrop} onDragOver={handleDragOver}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onSelectionChange={onSelectionChange}
        nodesDraggable
        nodesConnectable
        edgesReconnectable={false}
        elementsSelectable
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Background />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable />
      </ReactFlow>
    </div>
  );
}

export function FlowCanvas(props: FlowCanvasProps) {
  return (
    <ReactFlowProvider>
      <FlowCanvasInner {...props} />
    </ReactFlowProvider>
  );
}
