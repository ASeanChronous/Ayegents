"use client";

import type { DragEvent } from "react";

import type { FlowNodeType } from "@/lib/flowSpec";

/** docs/04-frontend-builder.md §7: four draggable entries (agent/tool/conditional/merge --
 * not trigger, which is auto-added and exactly-one-per-flow, §4). Dragging one onto the
 * canvas fires FlowCanvas's onDrop (step 5 of the build order, §9). */
const PALETTE_ENTRIES: { type: FlowNodeType; label: string; dot: string }[] = [
  { type: "agent", label: "Agent", dot: "bg-blue-500" },
  { type: "tool", label: "Tool", dot: "bg-emerald-500" },
  { type: "conditional", label: "Conditional", dot: "bg-amber-500" },
  { type: "merge", label: "Merge", dot: "bg-purple-500" },
];

const PALETTE_DRAG_TYPE = "application/flow-node-type";

export function NodePalette() {
  function handleDragStart(event: DragEvent<HTMLDivElement>, type: FlowNodeType) {
    event.dataTransfer.setData(PALETTE_DRAG_TYPE, type);
    event.dataTransfer.effectAllowed = "move";
  }

  return (
    <div className="flex h-full w-48 flex-shrink-0 flex-col gap-2 border-r border-gray-200 bg-white p-3">
      <h2 className="text-xs font-medium uppercase tracking-wide text-gray-500">Add a node</h2>
      <p className="text-xs text-gray-400">Drag onto the canvas</p>
      {PALETTE_ENTRIES.map((entry) => (
        <div
          key={entry.type}
          draggable
          onDragStart={(e) => handleDragStart(e, entry.type)}
          className="flex cursor-grab items-center gap-2 rounded-md border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:border-gray-400 active:cursor-grabbing"
        >
          <span className={`h-1.5 w-1.5 rounded-full ${entry.dot}`} />
          {entry.label}
        </div>
      ))}
    </div>
  );
}
