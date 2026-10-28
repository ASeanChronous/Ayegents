"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import useSWR from "swr";

import { api, unwrap, ApiError } from "@/lib/api";
import { useWorkspace } from "@/lib/useWorkspace";
import { DataTable } from "@/components/DataTable";
import { FormDrawer } from "@/components/FormDrawer";
import { Badge } from "@/components/Badge";

const STATUS_TONES = {
  queued: "gray",
  running: "blue",
  succeeded: "green",
  failed: "red",
  budget_exceeded: "yellow",
} as const;

type Agent = { id: string; name: string };
type Flow = { id: string; name: string };
type Run = {
  id: string;
  status: string;
  agent_id: string | null;
  flow_id: string | null;
  total_tokens: number;
  created_at: string;
  finished_at: string | null;
};

function statusTone(status: string) {
  return STATUS_TONES[status as keyof typeof STATUS_TONES] ?? "gray";
}

export default function RunsPage() {
  const { currentWorkspace } = useWorkspace();
  const workspaceId = currentWorkspace?.id;

  const { data: runs, isLoading, mutate } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/runs` : null,
    () => unwrap(api.GET("/workspaces/{workspace_id}/runs", { params: { path: { workspace_id: workspaceId! } } })),
    // Runs move through queued/running fairly quickly, and this list has no
    // per-row polling of its own (that's the inspector's job) -- a light
    // interval here is enough to see new runs land and statuses settle.
    { refreshInterval: 4000 }
  );
  const { data: agents } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/agents` : null,
    () => unwrap(api.GET("/workspaces/{workspace_id}/agents", { params: { path: { workspace_id: workspaceId! } } }))
  );
  const { data: flows } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/flows` : null,
    () => unwrap(api.GET("/workspaces/{workspace_id}/flows", { params: { path: { workspace_id: workspaceId! } } }))
  );

  const [startOpen, setStartOpen] = useState(false);
  const agentsById = new Map((agents ?? []).map((a) => [a.id, a]));
  const flowsById = new Map((flows ?? []).map((f) => [f.id, f]));

  if (!workspaceId) {
    return <p className="text-sm text-gray-500">Pick or create a workspace first.</p>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Runs</h1>
        <button
          onClick={() => setStartOpen(true)}
          className="rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-gray-800"
        >
          Start a run
        </button>
      </div>

      {isLoading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : (
        <DataTable<Run>
          rows={runs ?? []}
          keyFn={(r) => r.id}
          emptyMessage="No runs yet — start one against a published agent."
          columns={[
            {
              header: "Run",
              render: (r) => (
                <Link href={`/runs/${r.id}`} className="font-mono text-xs text-blue-600 hover:underline">
                  {r.id.slice(0, 8)}
                </Link>
              ),
            },
            {
              header: "Agent / Flow",
              render: (r) =>
                r.agent_id ? (
                  agentsById.get(r.agent_id)?.name ?? r.agent_id.slice(0, 8)
                ) : (
                  <span className="text-gray-600">
                    {r.flow_id ? flowsById.get(r.flow_id)?.name ?? r.flow_id.slice(0, 8) : "—"}
                    <span className="ml-1 text-xs text-gray-400">(flow)</span>
                  </span>
                ),
            },
            { header: "Status", render: (r) => <Badge tone={statusTone(r.status)}>{r.status}</Badge> },
            { header: "Tokens", render: (r) => r.total_tokens },
            { header: "Started", render: (r) => new Date(r.created_at).toLocaleString() },
          ]}
        />
      )}

      {agents && flows && (
        <StartRunDrawer
          open={startOpen}
          onClose={() => setStartOpen(false)}
          workspaceId={workspaceId}
          agents={agents}
          flows={flows}
          onStarted={() => mutate()}
        />
      )}
    </div>
  );
}

function StartRunDrawer({
  open,
  onClose,
  workspaceId,
  agents,
  flows,
  onStarted,
}: {
  open: boolean;
  onClose: () => void;
  workspaceId: string;
  agents: Agent[];
  flows: Flow[];
  onStarted: () => void;
}) {
  // Agent is the default target whenever there's at least one agent to pick
  // -- flows are newer and less likely to be the common case yet, same as
  // every other page in this app defaulting to the thing that's existed
  // longer (docs/04-frontend-builder.md §9 step 7: "flow option in the Runs
  // start-form," additive to the existing agent flow, not replacing it).
  const [targetType, setTargetType] = useState<"agent" | "flow">(agents.length > 0 ? "agent" : "flow");
  const [agentId, setAgentId] = useState("");
  const [flowId, setFlowId] = useState("");
  const [input, setInput] = useState("{}");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    let parsedInput: Record<string, unknown>;
    try {
      parsedInput = JSON.parse(input || "{}");
    } catch {
      setError("Input must be valid JSON");
      return;
    }

    setSubmitting(true);
    try {
      await unwrap(
        api.POST("/workspaces/{workspace_id}/runs", {
          params: { path: { workspace_id: workspaceId } },
          body:
            targetType === "agent" ? { agent_id: agentId, input: parsedInput } : { flow_id: flowId, input: parsedInput },
        })
      );
      onStarted();
      setInput("{}");
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to start run");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <FormDrawer open={open} onClose={onClose} title="Start a run">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <span className="block text-sm font-medium text-gray-700">Run</span>
          <div className="mt-1 flex gap-2 text-sm">
            <button
              type="button"
              onClick={() => setTargetType("agent")}
              className={`flex-1 rounded-md border px-2 py-1 ${
                targetType === "agent" ? "border-gray-900 bg-gray-900 text-white" : "border-gray-300 text-gray-700"
              }`}
            >
              An agent
            </button>
            <button
              type="button"
              onClick={() => setTargetType("flow")}
              className={`flex-1 rounded-md border px-2 py-1 ${
                targetType === "flow" ? "border-gray-900 bg-gray-900 text-white" : "border-gray-300 text-gray-700"
              }`}
            >
              A flow
            </button>
          </div>
        </div>
        {targetType === "agent" ? (
          <div>
            <label className="block text-sm font-medium text-gray-700" htmlFor="run-agent">
              Agent
            </label>
            {agents.length === 0 ? (
              <p className="mt-1 text-sm text-gray-500">No agents yet — create one on the Agents page first.</p>
            ) : (
              <select
                id="run-agent"
                required
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
              >
                <option value="" disabled>
                  Choose an agent…
                </option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            )}
            <p className="mt-1 text-xs text-gray-500">
              Running against an agent with no published version will fail — publish one first.
            </p>
          </div>
        ) : (
          <div>
            <label className="block text-sm font-medium text-gray-700" htmlFor="run-flow">
              Flow
            </label>
            {flows.length === 0 ? (
              <p className="mt-1 text-sm text-gray-500">No flows yet — create one on the Flows page first.</p>
            ) : (
              <select
                id="run-flow"
                required
                value={flowId}
                onChange={(e) => setFlowId(e.target.value)}
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
              >
                <option value="" disabled>
                  Choose a flow…
                </option>
                {flows.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name}
                  </option>
                ))}
              </select>
            )}
            <p className="mt-1 text-xs text-gray-500">
              Runs the flow's latest saved version — a flow with no saved version will fail, same as an unpublished
              agent.
            </p>
          </div>
        )}
        <div>
          <label className="block text-sm font-medium text-gray-700" htmlFor="run-input">
            Input (JSON)
          </label>
          <textarea
            id="run-input"
            rows={5}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs focus:border-gray-500 focus:outline-none"
          />
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button
          type="submit"
          disabled={submitting || agents.length === 0}
          className="w-full rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
        >
          {submitting ? "Starting…" : "Start run"}
        </button>
      </form>
    </FormDrawer>
  );
}
