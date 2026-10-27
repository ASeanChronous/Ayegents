import { describe, expect, it } from "vitest";

import { canvasFromSpec, specFromCanvas, type FlowSpec } from "./flowSpec";

// The architecture doc's own spec sketch (docs/01-architecture.md §4) --
// exercising the real shape this project committed to before any canvas
// code existed, not just a shape convenient for this test.
const ARCHITECTURE_DOC_SKETCH: FlowSpec = {
  nodes: [
    { id: "n1", type: "trigger", agent_id: null, tool_id: null, config: {} },
    { id: "n2", type: "agent", agent_id: "agent_123", tool_id: null, config: {} },
    { id: "n3", type: "tool", agent_id: null, tool_id: "tool_slack_post", config: {} },
  ],
  edges: [
    { from: "n1", to: "n2", condition: null },
    { from: "n2", to: "n3", condition: "n2.output.success" },
  ],
};

describe("canvasFromSpec / specFromCanvas round trip", () => {
  it("round-trips the architecture doc's own spec sketch", () => {
    const { nodes, edges } = canvasFromSpec(ARCHITECTURE_DOC_SKETCH);
    const spec = specFromCanvas(nodes, edges);

    // Positions are new (auto-layout, §7) -- strip ui_position before
    // comparing the rest, which must come back byte-for-byte.
    const stripPositions = (s: FlowSpec) => ({
      ...s,
      nodes: s.nodes.map(({ config, ...rest }) => rest),
    });
    expect(stripPositions(spec)).toEqual(stripPositions(ARCHITECTURE_DOC_SKETCH));
  });

  it("round-trips a saved ui_position exactly", () => {
    const spec: FlowSpec = {
      nodes: [
        { id: "t", type: "trigger", agent_id: null, tool_id: null, config: { ui_position: { x: 40, y: 80 } } },
      ],
      edges: [],
    };
    const { nodes } = canvasFromSpec(spec);
    expect(nodes[0].position).toEqual({ x: 40, y: 80 });

    const resaved = specFromCanvas(nodes, []);
    expect(resaved.nodes[0].config.ui_position).toEqual({ x: 40, y: 80 });
  });

  it("never leaks ui_position into the node's real config", () => {
    const spec: FlowSpec = {
      nodes: [
        {
          id: "t",
          type: "tool",
          agent_id: null,
          tool_id: "tool_1",
          config: { ui_position: { x: 10, y: 10 }, url: "https://example.com" },
        },
      ],
      edges: [],
    };
    const { nodes } = canvasFromSpec(spec);
    expect(nodes[0].data.config).toEqual({ url: "https://example.com" });
    expect(nodes[0].data.config).not.toHaveProperty("ui_position");
  });

  it("auto-layouts nodes with no saved position by BFS depth from the trigger", () => {
    const spec: FlowSpec = {
      nodes: [
        { id: "trigger", type: "trigger", agent_id: null, tool_id: null, config: {} },
        { id: "a", type: "agent", agent_id: "a1", tool_id: null, config: {} },
        { id: "b", type: "agent", agent_id: "a2", tool_id: null, config: {} },
      ],
      edges: [
        { from: "trigger", to: "a", condition: null },
        { from: "a", to: "b", condition: null },
      ],
    };
    const { nodes } = canvasFromSpec(spec);
    const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
    // Depth increases left to right; exact y doesn't matter here, only that
    // depth-0/1/2 land at strictly increasing x.
    expect(byId.trigger.position.x).toBeLessThan(byId.a.position.x);
    expect(byId.a.position.x).toBeLessThan(byId.b.position.x);
  });

  it("gives two nodes at the same depth different y so they don't stack", () => {
    const spec: FlowSpec = {
      nodes: [
        { id: "trigger", type: "trigger", agent_id: null, tool_id: null, config: {} },
        { id: "a", type: "agent", agent_id: "a1", tool_id: null, config: {} },
        { id: "b", type: "agent", agent_id: "a2", tool_id: null, config: {} },
      ],
      // Fan-out: both a and b are depth 1.
      edges: [
        { from: "trigger", to: "a", condition: null },
        { from: "trigger", to: "b", condition: null },
      ],
    };
    const { nodes } = canvasFromSpec(spec);
    const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
    expect(byId.a.position.x).toEqual(byId.b.position.x);
    expect(byId.a.position.y).not.toEqual(byId.b.position.y);
  });

  it("preserves agent_id/tool_id and null-vs-set distinctly", () => {
    const spec: FlowSpec = {
      nodes: [
        { id: "a", type: "agent", agent_id: "agent_1", tool_id: null, config: {} },
        { id: "t", type: "tool", agent_id: null, tool_id: "tool_1", config: {} },
        { id: "c", type: "conditional", agent_id: null, tool_id: null, config: {} },
      ],
      edges: [],
    };
    const { nodes } = canvasFromSpec(spec);
    const resaved = specFromCanvas(nodes, []);
    const byId = Object.fromEntries(resaved.nodes.map((n) => [n.id, n]));
    expect(byId.a.agent_id).toBe("agent_1");
    expect(byId.a.tool_id).toBeNull();
    expect(byId.t.tool_id).toBe("tool_1");
    expect(byId.c.agent_id).toBeNull();
    expect(byId.c.tool_id).toBeNull();
  });

  it("preserves edge conditions, including null (unconditional/fan-out)", () => {
    const spec: FlowSpec = {
      nodes: [
        { id: "n1", type: "trigger", agent_id: null, tool_id: null, config: {} },
        { id: "n2", type: "agent", agent_id: "a1", tool_id: null, config: {} },
        { id: "n3", type: "agent", agent_id: "a2", tool_id: null, config: {} },
      ],
      edges: [
        { from: "n1", to: "n2", condition: null },
        { from: "n1", to: "n3", condition: "n2.output.decision" },
      ],
    };
    const { nodes, edges } = canvasFromSpec(spec);
    const resaved = specFromCanvas(nodes, edges);
    expect(resaved.edges).toEqual(spec.edges);
  });

  it("gives duplicate from/to edge pairs distinct ids (two conditional branches)", () => {
    const spec: FlowSpec = {
      nodes: [
        { id: "a", type: "conditional", agent_id: null, tool_id: null, config: {} },
        { id: "b", type: "agent", agent_id: "a1", tool_id: null, config: {} },
      ],
      edges: [
        { from: "a", to: "b", condition: "a.output.x" },
        { from: "a", to: "b", condition: "not a.output.x" },
      ],
    };
    const { edges } = canvasFromSpec(spec);
    expect(new Set(edges.map((e) => e.id)).size).toBe(2);
  });

  it("round-trips a dragged position that differs from the saved one", () => {
    const spec: FlowSpec = {
      nodes: [{ id: "t", type: "trigger", agent_id: null, tool_id: null, config: { ui_position: { x: 0, y: 0 } } }],
      edges: [],
    };
    const { nodes } = canvasFromSpec(spec);
    nodes[0].position = { x: 999, y: 123 };
    const resaved = specFromCanvas(nodes, []);
    expect(resaved.nodes[0].config.ui_position).toEqual({ x: 999, y: 123 });
  });
});
