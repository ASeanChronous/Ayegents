"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import useSWR from "swr";

import { api, unwrap } from "@/lib/api";
import { useRunEvents } from "@/lib/useRunEvents";
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
  cost_usd: number;
  latency_ms: number | null;
  status: string;
  error: string | null;
  started_at: string | null;
};

// List-price estimate, not a billing-accurate figure — see
// orchestrator_app/model_router.py's MODEL_PRICING_PER_TOKEN.
function formatCost(usd: number) {
  if (usd === 0) return "$0";
  return usd < 0.01 ? "<$0.01" : `$${usd.toFixed(2)}`;
}

export default function RunDetailPage() {
  const { id: runId } = useParams<{ id: string }>();
  const { currentWorkspace } = useWorkspace();
  const workspaceId = currentWorkspace?.id;

  const { data: run, mutate: mutateRun } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/runs/${runId}` : null,
    () =>
      unwrap(
        api.GET("/workspaces/{workspace_id}/runs/{run_id}", {
          params: { path: { workspace_id: workspaceId!, run_id: runId } },
        })
      ),
    {
      // The SSE subscription below (useRunEvents) is the real update
      // mechanism now (docs/03-frontend.md §6); this interval is just a
      // slow safety net in case that stream never connects or silently
      // drops (a proxy that blocks streaming entirely, say) -- 20s is cheap
      // enough to not matter and nowhere near the old 2s polling this
      // replaced.
      refreshInterval: (latest) => (latest && TERMINAL_STATUSES.has(latest.status) ? 0 : 20000),
    }
  );
  const { data: steps, mutate: mutateSteps } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/runs/${runId}/steps` : null,
    () =>
      unwrap(
        api.GET("/workspaces/{workspace_id}/runs/{run_id}/steps", {
          params: { path: { workspace_id: workspaceId!, run_id: runId } },
        })
      ),
    { refreshInterval: run && !TERMINAL_STATUSES.has(run.status) ? 20000 : 0 }
  );

  useRunEvents(workspaceId, runId, () => {
    mutateRun();
    mutateSteps();
  });

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
          <dt className="text-gray-500">Estimated cost</dt>
          <dd className="font-medium" title="List-price estimate, not a billing-accurate figure">
            {formatCost(run.total_cost_usd)}
          </dd>
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
                {step.cost_usd > 0 && <span>{formatCost(step.cost_usd)}</span>}
                <span>{step.latency_ms != null ? `${step.latency_ms}ms` : "—"}</span>
                <Badge tone={statusTone(step.status)}>{step.status}</Badge>
              </span>
            </button>
            {expanded && (
              <div className="grid grid-cols-2 gap-4 border-t border-gray-100 bg-gray-50 px-4 py-3">
                {step.error && <p className="col-span-2 text-sm text-red-600">{step.error}</p>}
                {step.started_at && (
                  <p className="col-span-2 text-xs text-gray-500">
                    Started {new Date(step.started_at).toLocaleString()}
                  </p>
                )}
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
