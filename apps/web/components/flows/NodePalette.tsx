"use client";

/** docs/04-frontend-builder.md §7: four draggable entries (agent/tool/conditional/merge --
 * not trigger, which is auto-added and exactly-one-per-flow, §4). Drag-to-add onto the
 * canvas is step 5 of the build order (§9); this is the static list on its own, so the
 * canvas's three-pane layout (§6) is in place before that interaction is wired up. */
const PALETTE_ENTRIES: { type: string; label: string; dot: string }[] = [
  { type: "agent", label: "Agent", dot: "bg-blue-500" },
  { type: "tool", label: "Tool", dot: "bg-emerald-500" },
  { type: "conditional", label: "Conditional", dot: "bg-amber-500" },
  { type: "merge", label: "Merge", dot: "bg-purple-500" },
];

export function NodePalette() {
  return (
    <div className="flex h-full w-48 flex-shrink-0 flex-col gap-2 border-r border-gray-200 bg-white p-3">
      <h2 className="text-xs font-medium uppercase tracking-wide text-gray-500">Add a node</h2>
      {PALETTE_ENTRIES.map((entry) => (
        <div
          key={entry.type}
          className="flex cursor-not-allowed items-center gap-2 rounded-md border border-gray-200 px-3 py-2 text-sm text-gray-400"
          title="Dragging a node onto the canvas isn't wired up yet"
        >
          <span className={`h-1.5 w-1.5 rounded-full ${entry.dot} opacity-50`} />
          {entry.label}
        </div>
      ))}
    </div>
  );
}
