"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import useSWR from "swr";

import { api, unwrap } from "@/lib/api";
import { useWorkspace } from "@/lib/useWorkspace";
import { Badge } from "@/components/Badge";

const STATUS_TONES = {
  queued: "gray",
  running: "blue",
  succeeded: "green",
  failed: "red",
  budget_exceeded: "yellow",
} as const;

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "budget_exceeded"]);

function statusTone(status: string) {
  return STATUS_TONES[status as keyof typeof STATUS_TONES] ?? "gray";
}

type RunStep = {
  id: string;
  step_index: number;
  kind: string;
  node_id: string | null;
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
  tokens_input: number;
  tokens_output: number;
  latency_ms: number | null;
  status: string;
  error: string | null;
};

export default function RunDetailPage() {
  const { id: runId } = useParams<{ id: string }>();
  const { currentWorkspace } = useWorkspace();
  const workspaceId = currentWorkspace?.id;

  const { data: run } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/runs/${runId}` : null,
    () =>
      unwrap(
        api.GET("/workspaces/{workspace_id}/runs/{run_id}", {
          params: { path: { workspace_id: workspaceId!, run_id: runId } },
        })
      ),
    {
      // Poll while the run is in flight; stop once it reaches a terminal
      // state (docs/03-frontend.md §6 -- Realtime is the better long-term
      // answer, but polling avoids needing that setup right now).
      refreshInterval: (latest) => (latest && TERMINAL_STATUSES.has(latest.status) ? 0 : 2000),
    }
  );
  const { data: steps } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/runs/${runId}/steps` : null,
    () =>
      unwrap(
        api.GET("/workspaces/{workspace_id}/runs/{run_id}/steps", {
          params: { path: { workspace_id: workspaceId!, run_id: runId } },
        })
      ),
    { refreshInterval: run && !TERMINAL_STATUSES.has(run.status) ? 2000 : 0 }
  );

  if (!workspaceId) {
    return <p className="text-sm text-gray-500">Pick or create a workspace first.</p>;
  }
  if (!run) {
    return <p className="text-sm text-gray-500">Loading…</p>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-mono text-lg font-semibold">{run.id}</h1>
          <p className="text-sm text-gray-500">Started {new Date(run.created_at).toLocaleString()}</p>
        </div>
        <div className="flex items-center gap-2">
          {run.is_draft && <Badge tone="gray">test run — unsaved spec</Badge>}
          <Badge tone={statusTone(run.status)}>{run.status}</Badge>
        </div>
      </div>

      {run.error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{run.error}</div>
      )}

      <div className="grid grid-cols-2 gap-6">
        <JsonPanel title="Input" value={run.input} />
        <JsonPanel title="Output" value={run.output} />
      </div>

      <dl className="grid grid-cols-3 gap-4 text-sm">
        <div>
          <dt className="text-gray-500">Total tokens</dt>
          <dd className="font-medium">{run.total_tokens}</dd>
        </div>
        <div>
          <dt className="text-gray-500">Finished</dt>
          <dd className="font-medium">{run.finished_at ? new Date(run.finished_at).toLocaleString() : "—"}</dd>
        </div>
      </dl>

      <section className="space-y-2">
        <h2 className="text-sm font-medium text-gray-700">Steps</h2>
        {!steps ? (
          <p className="text-sm text-gray-500">Loading…</p>
        ) : steps.length === 0 ? (
          <p className="rounded-md border border-dashed border-gray-300 p-6 text-center text-sm text-gray-500">
            No steps yet — they appear here as the orchestrator executes this run.
          </p>
        ) : (
          <RunStepTimeline steps={steps} />
        )}
      </section>
    </div>
  );
}

function JsonPanel({ title, value }: { title: string; value: unknown }) {
  return (
    <div>
      <h2 className="mb-1 text-sm font-medium text-gray-700">{title}</h2>
      <pre className="max-h-64 overflow-auto rounded-md border border-gray-200 bg-gray-50 p-3 text-xs">
        {value == null ? "—" : JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

function RunStepTimeline({ steps }: { steps: RunStep[] }) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  return (
    <ul className="divide-y divide-gray-100 rounded-md border border-gray-200">
      {steps.map((step) => {
        const expanded = expandedId === step.id;
        return (
          <li key={step.id}>
            <button
              onClick={() => setExpandedId(expanded ? null : step.id)}
              className="flex w-full items-center justify-between px-4 py-2 text-left text-sm hover:bg-gray-50"
            >
              <span className="flex items-center gap-3">
                <span className="font-mono text-xs text-gray-400">#{step.step_index}</span>
                <span className="font-medium">{step.node_id ?? step.kind}</span>
                <span className="text-xs text-gray-500">{step.kind}</span>
              </span>
              <span className="flex items-center gap-3 text-xs text-gray-500">
                <span>
                  {step.tokens_input}+{step.tokens_output} tok
                </span>
                <span>{step.latency_ms != null ? `${step.latency_ms}ms` : "—"}</span>
                <Badge tone={statusTone(step.status)}>{step.status}</Badge>
              </span>
            </button>
            {expanded && (
              <div className="grid grid-cols-2 gap-4 border-t border-gray-100 bg-gray-50 px-4 py-3">
                {step.error && <p className="col-span-2 text-sm text-red-600">{step.error}</p>}
                <JsonPanel title="Step input" value={step.input} />
                <JsonPanel title="Step output" value={step.output} />
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
