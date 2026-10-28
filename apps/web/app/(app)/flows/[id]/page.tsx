"use client";

import { useMemo } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import useSWR from "swr";

import { api, unwrap } from "@/lib/api";
import { useWorkspace } from "@/lib/useWorkspace";
import { canvasFromSpec, type FlowSpec } from "@/lib/flowSpec";
import { FlowCanvas } from "@/components/flows/FlowCanvas";
import { NodePalette } from "@/components/flows/NodePalette";
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
  const { data: versions } = useSWR(
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

  // versions is ordered newest-first (services/api/app/routers/flows.py), so
  // [0] is the latest -- the thing this read-only preview loads (§1: "load
  // the latest version's spec").
  const latestSpec: FlowSpec | undefined =
    versions === undefined ? undefined : versions.length > 0 ? versions[0].spec : BLANK_SPEC;

  const { nodes, edges } = useMemo(() => canvasFromSpec(latestSpec ?? BLANK_SPEC), [latestSpec]);

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

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-shrink-0 items-center justify-between border-b border-gray-200 bg-white px-4 py-2">
        <div className="flex items-center gap-3">
          <Link href="/flows" className="text-sm text-gray-500 hover:text-gray-700">
            ← Flows
          </Link>
          <h1 className="text-sm font-semibold">{flow?.name ?? "…"}</h1>
        </div>
        <p className="text-xs text-gray-400">
          Read-only preview — editing, saving, and running arrive in the next steps
          (docs/04-frontend-builder.md §9).
        </p>
      </div>
      <div className="flex min-h-0 flex-1">
        <NodePalette />
        <div className="min-w-0 flex-1">
          {latestSpec === undefined ? (
            <p className="p-6 text-sm text-gray-500">Loading…</p>
          ) : (
            <FlowLookupProvider value={lookup}>
              <FlowCanvas nodes={nodes} edges={edges} />
            </FlowLookupProvider>
          )}
        </div>
        <div className="w-64 flex-shrink-0 border-l border-gray-200 bg-white p-3">
          <h2 className="text-xs font-medium uppercase tracking-wide text-gray-500">Inspector</h2>
          <p className="mt-2 text-sm text-gray-400">
            Selecting a node or edge will show its settings here — arriving in the next step.
          </p>
        </div>
      </div>
    </div>
  );
}
