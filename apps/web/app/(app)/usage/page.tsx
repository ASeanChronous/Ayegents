"use client";

import useSWR from "swr";

import { api, unwrap } from "@/lib/api";
import { useWorkspace } from "@/lib/useWorkspace";
import { Badge } from "@/components/Badge";

// List-price estimate, not a billing-accurate figure — see
// orchestrator_app/model_router.py's MODEL_PRICING_PER_TOKEN (same convention
// the runs pages already use).
function formatCost(usd: number) {
  if (usd === 0) return "$0";
  return usd < 0.01 ? "<$0.01" : `$${usd.toFixed(2)}`;
}

function Meter({ label, used, max }: { label: string; used: number; max: number }) {
  const pct = max > 0 ? Math.min(100, (used / max) * 100) : 0;
  const near = pct >= 80;
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between text-sm">
        <span className="text-gray-700">{label}</span>
        <span className={near ? "font-medium text-red-600" : "font-medium text-gray-900"}>
          {used} / {max}
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-gray-100">
        <div
          className={`h-full rounded-full ${near ? "bg-red-500" : "bg-gray-900"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export default function UsagePage() {
  const { currentWorkspace, isLoading: workspaceLoading } = useWorkspace();
  const workspaceId = currentWorkspace?.id;

  const { data: usage } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/usage` : null,
    () => unwrap(api.GET("/workspaces/{workspace_id}/usage", { params: { path: { workspace_id: workspaceId! } } })),
    // Runs today / concurrent running change as runs land — a plain page
    // load would otherwise show a stale count right after hitting a cap.
    { refreshInterval: 10_000 }
  );

  if (workspaceLoading || !currentWorkspace || !usage) {
    return <p className="text-sm text-gray-500">Loading…</p>;
  }

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Usage — {currentWorkspace.name}</h1>
        <Badge tone="blue">{usage.plan_tier} plan</Badge>
      </div>

      {usage.budget_exceeded_today > 0 && (
        <p className="rounded-md border border-yellow-200 bg-yellow-50 px-3 py-2 text-sm text-yellow-800">
          {usage.budget_exceeded_today} run{usage.budget_exceeded_today === 1 ? "" : "s"} hit a budget limit
          (steps, tokens, or wall-clock time) in the last 24 hours — see the Runs page for which ones.
        </p>
      )}

      <section className="grid grid-cols-1 gap-6 sm:grid-cols-2">
        <Meter label="Runs started (last 24h)" used={usage.runs_today} max={usage.max_runs_per_day} />
        <Meter label="Concurrent runs" used={usage.concurrent_running} max={usage.max_concurrent_runs} />
      </section>

      <section className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div className="rounded-md border border-gray-200 p-4">
          <p className="text-xs text-gray-500">Cost, last 24h</p>
          <p className="mt-1 text-lg font-semibold">{formatCost(usage.cost_today_usd)}</p>
        </div>
        <div className="rounded-md border border-gray-200 p-4">
          <p className="text-xs text-gray-500">Cost, this month</p>
          <p className="mt-1 text-lg font-semibold">{formatCost(usage.cost_this_month_usd)}</p>
        </div>
        <div className="rounded-md border border-gray-200 p-4">
          <p className="text-xs text-gray-500">Max steps / run</p>
          <p className="mt-1 text-lg font-semibold">{usage.max_steps_per_run}</p>
        </div>
        <div className="rounded-md border border-gray-200 p-4">
          <p className="text-xs text-gray-500">Max wall-clock / run</p>
          <p className="mt-1 text-lg font-semibold">{usage.max_wall_clock_seconds}s</p>
        </div>
      </section>

      <p className="text-xs text-gray-500">
        Max tokens per run: {usage.max_tokens_per_run.toLocaleString()} — a run that crosses any of these limits
        lands with status <Badge tone="yellow">budget_exceeded</Badge> rather than being silently cut off.
      </p>
    </div>
  );
}
