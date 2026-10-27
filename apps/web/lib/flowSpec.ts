/** Conversion between React Flow's own `nodes`/`edges` canvas state and the
 * `FlowSpec` shape `flow_versions.spec` stores and `flow_executor.py`
 * actually executes — the thing the canvas is a view over, not the source
 * of truth (docs/04-frontend-builder.md §2). The two functions here
 * (`specFromCanvas`/`canvasFromSpec`) are the entire boundary between them;
 * nothing elsewhere in the app should reach into a node's `config` to find
 * or set layout.
 */
import type { Edge, Node, XYPosition } from "@xyflow/react";

import type { components } from "./api-types";

export type FlowSpec = components["schemas"]["FlowSpec"];
export type FlowNode = components["schemas"]["FlowNode"];
export type FlowEdge = components["schemas"]["FlowEdge"];
export type FlowNodeType = FlowNode["type"];

/** A node's real config, as the orchestrator will see it — never includes
 * `ui_position` (§3: that key is this module's own layout-storage
 * mechanism, stripped out before anything else touches `config`). */
export type FlowNodeConfig = Record<string, unknown>;

export type FlowCanvasNodeData = {
  agentId: string | null;
  toolId: string | null;
  config: FlowNodeConfig;
};

export type FlowCanvasEdgeData = {
  condition: string | null;
};

export type FlowCanvasNode = Node<FlowCanvasNodeData, FlowNodeType>;
export type FlowCanvasEdge = Edge<FlowCanvasEdgeData>;

const UI_POSITION_KEY = "ui_position";

function isXYPosition(value: unknown): value is XYPosition {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as Record<string, unknown>).x === "number" &&
    typeof (value as Record<string, unknown>).y === "number"
  );
}

/** Simple left-to-right layout by BFS depth from the trigger node, for any
 * node a loaded spec doesn't carry a `ui_position` for yet -- a spec
 * written by hand, by a test fixture, or by an older version of this app
 * from before `ui_position` existed (docs/04-frontend-builder.md §7). Pure
 * fallback, not meant to be a good layout: it only has to put every node
 * somewhere sane, not somewhere final -- dragging a node on the canvas and
 * saving overwrites it with a real position from then on. */
function autoLayoutPositions(spec: FlowSpec): Map<string, XYPosition> {
  const outgoing = new Map<string, string[]>();
  for (const edge of spec.edges) {
    const targets = outgoing.get(edge.from) ?? [];
    targets.push(edge.to);
    outgoing.set(edge.from, targets);
  }

  const trigger = spec.nodes.find((n) => n.type === "trigger");
  const depth = new Map<string, number>();
  if (trigger) {
    const queue: string[] = [trigger.id];
    depth.set(trigger.id, 0);
    while (queue.length > 0) {
      const id = queue.shift()!;
      for (const nextId of outgoing.get(id) ?? []) {
        if (!depth.has(nextId)) {
          depth.set(nextId, depth.get(id)! + 1);
          queue.push(nextId);
        }
      }
    }
  }

  const countAtDepth = new Map<number, number>();
  const positions = new Map<string, XYPosition>();
  for (const node of spec.nodes) {
    const d = depth.get(node.id) ?? 0;
    const row = countAtDepth.get(d) ?? 0;
    countAtDepth.set(d, row + 1);
    positions.set(node.id, { x: d * 220, y: row * 120 });
  }
  return positions;
}

/** Deterministic id for an edge that has none in the backend's model
 * (`FlowEdge` is just `{from, to, condition}`) -- React Flow needs a stable
 * `id` per edge, and two edges can share a `from`/`to` pair (e.g. a
 * conditional edge and its fallback), so the spec's array index is folded
 * in rather than relying on `from`/`to` alone. */
function edgeId(edge: FlowEdge, index: number): string {
  return `${edge.from}->${edge.to}#${index}`;
}

export function canvasFromSpec(spec: FlowSpec): { nodes: FlowCanvasNode[]; edges: FlowCanvasEdge[] } {
  const fallbackPositions = autoLayoutPositions(spec);

  const nodes: FlowCanvasNode[] = spec.nodes.map((node) => {
    const { [UI_POSITION_KEY]: uiPosition, ...config } = node.config ?? {};
    const position = isXYPosition(uiPosition) ? uiPosition : fallbackPositions.get(node.id) ?? { x: 0, y: 0 };
    return {
      id: node.id,
      type: node.type,
      position,
      data: {
        agentId: node.agent_id ?? null,
        toolId: node.tool_id ?? null,
        config,
      },
    };
  });

  const edges: FlowCanvasEdge[] = spec.edges.map((edge, index) => ({
    id: edgeId(edge, index),
    source: edge.from,
    target: edge.to,
    data: { condition: edge.condition ?? null },
  }));

  return { nodes, edges };
}

export function specFromCanvas(nodes: FlowCanvasNode[], edges: FlowCanvasEdge[]): FlowSpec {
  return {
    nodes: nodes.map((node) => ({
      id: node.id,
      type: node.type as FlowNodeType,
      agent_id: node.data.agentId,
      tool_id: node.data.toolId,
      config: {
        ...node.data.config,
        [UI_POSITION_KEY]: { x: node.position.x, y: node.position.y },
      },
    })),
    edges: edges.map((edge) => ({
      from: edge.source,
      to: edge.target,
      condition: edge.data?.condition ?? null,
    })),
  };
}
