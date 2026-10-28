"use client";

/** Lists/manages a flow's triggers (docs/04-frontend-builder.md §10) -- separate from the
 * canvas entirely, since a trigger is "how does a run of this flow get started," not
 * something the flow spec's own trigger node configures (that node only ever seeds a
 * run's input, regardless of what started it -- see services/api/app/routers/triggers.py's
 * own docstring). Opened from a "Triggers" button in the flow page's top bar, same
 * FormDrawer pattern the Runs start-form and the workspace create form already use. */
import { FormEvent, useState } from "react";
import useSWR from "swr";

import { api, unwrap, ApiError } from "@/lib/api";
import { FormDrawer } from "@/components/FormDrawer";
import { Badge } from "@/components/Badge";

type TriggerKind = "manual" | "webhook" | "schedule";

type Trigger = {
  id: string;
  kind: TriggerKind;
  config: Record<string, unknown>;
  enabled: boolean;
  last_fired_at: string | null;
  created_at: string;
};

const KIND_TONE: Record<TriggerKind, "gray" | "blue" | "green"> = {
  manual: "gray",
  webhook: "blue",
  schedule: "green",
};

const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function TriggersDrawer({
  open,
  onClose,
  workspaceId,
  flowId,
}: {
  open: boolean;
  onClose: () => void;
  workspaceId: string;
  flowId: string;
}) {
  const { data: triggers, mutate } = useSWR(
    open ? `/workspaces/${workspaceId}/flows/${flowId}/triggers` : null,
    () =>
      unwrap(
        api.GET("/workspaces/{workspace_id}/flows/{flow_id}/triggers", {
          params: { path: { workspace_id: workspaceId, flow_id: flowId } },
        })
      ) as Promise<Trigger[]>
  );

  const [adding, setAdding] = useState(false);
  const [addKind, setAddKind] = useState<"webhook" | "schedule">("webhook");
  const [cron, setCron] = useState("0 * * * *");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleAdd(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await unwrap(
        api.POST("/workspaces/{workspace_id}/flows/{flow_id}/triggers", {
          params: { path: { workspace_id: workspaceId, flow_id: flowId } },
          body: addKind === "schedule" ? { kind: "schedule", cron } : { kind: "webhook" },
        })
      );
      setAdding(false);
      await mutate();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to create trigger");
    } finally {
      setSubmitting(false);
    }
  }

  // Separate from `error` (the add-trigger form's own inline error) -- a
  // toggle or delete can fail after the form has long since closed, so it
  // needs a place to show up that isn't inside that form.
  const [actionError, setActionError] = useState<string | null>(null);

  async function handleToggle(trigger: Trigger) {
    setActionError(null);
    try {
      await unwrap(
        api.PATCH("/workspaces/{workspace_id}/flows/{flow_id}/triggers/{trigger_id}", {
          params: { path: { workspace_id: workspaceId, flow_id: flowId, trigger_id: trigger.id } },
          body: { enabled: !trigger.enabled },
        })
      );
      await mutate();
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : "Failed to update trigger");
    }
  }

  async function handleDelete(triggerId: string) {
    setActionError(null);
    try {
      await unwrap(
        api.DELETE("/workspaces/{workspace_id}/flows/{flow_id}/triggers/{trigger_id}", {
          params: { path: { workspace_id: workspaceId, flow_id: flowId, trigger_id: triggerId } },
        })
      );
      await mutate();
    } catch (e) {
      // A trigger that already started a run can't be deleted (foreign key
      // from runs.trigger_id, services/api/app/routers/triggers.py) --
      // the API's 409 message already says to disable it instead, surfaced
      // here rather than left as a silent no-op or a console-only exception.
      setActionError(e instanceof ApiError ? e.message : "Failed to delete trigger");
    }
  }

  return (
    <FormDrawer open={open} onClose={onClose} title="Triggers">
      <div className="space-y-4">
        <p className="text-sm text-gray-500">
          Starts this flow without opening the app — a webhook POST or a cron schedule, on top of starting it
          manually from the Runs page.
        </p>
        {actionError && <p className="text-sm text-red-600">{actionError}</p>}

        {!triggers ? (
          <p className="text-sm text-gray-500">Loading…</p>
        ) : triggers.length === 0 ? (
          <p className="text-sm text-gray-500">No webhook or schedule triggers yet.</p>
        ) : (
          <ul className="space-y-3">
            {triggers.map((t) => (
              <TriggerRow key={t.id} trigger={t} onToggle={() => handleToggle(t)} onDelete={() => handleDelete(t.id)} />
            ))}
          </ul>
        )}

        {adding ? (
          <form onSubmit={handleAdd} className="space-y-3 rounded-md border border-gray-200 p-3">
            <div className="flex gap-2 text-sm">
              <button
                type="button"
                onClick={() => setAddKind("webhook")}
                className={`flex-1 rounded-md border px-2 py-1 ${
                  addKind === "webhook" ? "border-gray-900 bg-gray-900 text-white" : "border-gray-300 text-gray-700"
                }`}
              >
                Webhook
              </button>
              <button
                type="button"
                onClick={() => setAddKind("schedule")}
                className={`flex-1 rounded-md border px-2 py-1 ${
                  addKind === "schedule" ? "border-gray-900 bg-gray-900 text-white" : "border-gray-300 text-gray-700"
                }`}
              >
                Schedule
              </button>
            </div>
            {addKind === "schedule" && (
              <div>
                <label className="block text-sm font-medium text-gray-700" htmlFor="trigger-cron">
                  Cron expression
                </label>
                <input
                  id="trigger-cron"
                  value={cron}
                  onChange={(e) => setCron(e.target.value)}
                  placeholder="0 * * * *"
                  className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-sm focus:border-gray-500 focus:outline-none"
                />
                <p className="mt-1 text-xs text-gray-400">Standard 5-field cron — minute hour day-of-month month day-of-week.</p>
              </div>
            )}
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={submitting}
                className="flex-1 rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
              >
                {submitting ? "Adding…" : "Add trigger"}
              </button>
              <button
                type="button"
                onClick={() => setAdding(false)}
                className="rounded-md border border-gray-300 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
            </div>
          </form>
        ) : (
          <button
            onClick={() => setAdding(true)}
            className="w-full rounded-md border border-dashed border-gray-300 px-3 py-2 text-sm text-gray-600 hover:bg-gray-50"
          >
            + Add trigger
          </button>
        )}
      </div>
    </FormDrawer>
  );
}

function TriggerRow({ trigger, onToggle, onDelete }: { trigger: Trigger; onToggle: () => void; onDelete: () => void }) {
  const [revealSecret, setRevealSecret] = useState(false);
  const webhookUrl = `${apiBaseUrl}/webhooks/${trigger.id}`;
  const secret = typeof trigger.config.secret === "string" ? trigger.config.secret : null;
  const cron = typeof trigger.config.cron === "string" ? trigger.config.cron : null;

  return (
    <li className="space-y-2 rounded-md border border-gray-200 p-3">
      <div className="flex items-center justify-between">
        <Badge tone={KIND_TONE[trigger.kind]}>{trigger.kind}</Badge>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-gray-600">
            <input type="checkbox" checked={trigger.enabled} onChange={onToggle} />
            Enabled
          </label>
          <button onClick={onDelete} className="text-xs font-medium text-red-600 hover:text-red-700">
            Delete
          </button>
        </div>
      </div>

      {trigger.kind === "webhook" && (
        <div className="space-y-1 text-xs">
          <div>
            <span className="text-gray-500">POST </span>
            <code className="break-all rounded bg-gray-100 px-1 py-0.5">{webhookUrl}</code>
          </div>
          <div className="flex items-center gap-1 text-gray-500">
            <span>Header </span>
            <code className="rounded bg-gray-100 px-1 py-0.5">X-Webhook-Secret:</code>
            <code className="rounded bg-gray-100 px-1 py-0.5">{revealSecret ? secret : "••••••••••••"}</code>
            <button onClick={() => setRevealSecret((v) => !v)} className="text-blue-600 hover:underline">
              {revealSecret ? "hide" : "reveal"}
            </button>
          </div>
        </div>
      )}
      {trigger.kind === "schedule" && (
        <div className="text-xs text-gray-500">
          <code className="rounded bg-gray-100 px-1 py-0.5">{cron}</code>
          {" — "}
          {trigger.last_fired_at ? `last fired ${new Date(trigger.last_fired_at).toLocaleString()}` : "never fired yet"}
        </div>
      )}
      {trigger.kind === "manual" && <p className="text-xs text-gray-500">Start it from the Runs page.</p>}
    </li>
  );
}
