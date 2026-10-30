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

type TriggerKind = "manual" | "webhook" | "schedule" | "agent_event" | "telegram";

type Trigger = {
  id: string;
  kind: TriggerKind;
  config: Record<string, unknown>;
  enabled: boolean;
  last_fired_at: string | null;
  created_at: string;
};

type Agent = { id: string; name: string };
type Flow = { id: string; name: string };

const KIND_TONE: Record<TriggerKind, "gray" | "blue" | "green" | "yellow" | "red"> = {
  manual: "gray",
  webhook: "blue",
  schedule: "green",
  agent_event: "yellow",
  telegram: "red",
};

const ON_STATUS_OPTIONS = ["succeeded", "failed", "budget_exceeded"] as const;

const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function TriggersDrawer({
  open,
  onClose,
  workspaceId,
  flowId,
  agents,
  flows,
}: {
  open: boolean;
  onClose: () => void;
  workspaceId: string;
  flowId: string;
  // For the agent_event source picker -- other agents/flows in this same
  // workspace that can be named as the upstream thing whose run starts this
  // one. Optional so callers that don't already have these lists loaded
  // (none today, but nothing requires it) just don't get that trigger kind
  // offered, rather than this component fetching its own duplicate copies.
  agents?: Agent[];
  flows?: Flow[];
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
  const [addKind, setAddKind] = useState<"webhook" | "schedule" | "agent_event" | "telegram">("webhook");
  const [cron, setCron] = useState("0 * * * *");
  // agent_event fields -- sourceType picks which half of the union
  // (source_agent_id vs. source_flow_id) the form is filling in.
  const [sourceType, setSourceType] = useState<"agent" | "flow">("agent");
  const [sourceId, setSourceId] = useState("");
  const [onStatus, setOnStatus] = useState<(typeof ON_STATUS_OPTIONS)[number]>("succeeded");
  const [botToken, setBotToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Other flows this flow could depend on -- excludes itself, since the API
  // rejects a flow naming its own completion as its trigger (a 1-step
  // infinite loop) and there's no point letting the form offer a choice
  // the backend will just reject.
  const otherFlows = (flows ?? []).filter((f) => f.id !== flowId);

  async function handleAdd(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      // on_status is only meaningful for agent_event, but the generated
      // client type marks it required (it has a server-side default, which
      // openapi-typescript treats as "always present," not "optional") --
      // sending the UI's current value for every kind is harmless, since
      // the backend only ever reads it on the agent_event branch.
      const body =
        addKind === "schedule"
          ? { kind: "schedule" as const, cron, on_status: onStatus }
          : addKind === "agent_event"
            ? {
                kind: "agent_event" as const,
                on_status: onStatus,
                ...(sourceType === "agent" ? { source_agent_id: sourceId } : { source_flow_id: sourceId }),
              }
            : addKind === "telegram"
              ? { kind: "telegram" as const, on_status: onStatus, bot_token: botToken }
              : { kind: "webhook" as const, on_status: onStatus };
      await unwrap(
        api.POST("/workspaces/{workspace_id}/flows/{flow_id}/triggers", {
          params: { path: { workspace_id: workspaceId, flow_id: flowId } },
          body,
        })
      );
      setAdding(false);
      setSourceId("");
      setBotToken("");
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
          Starts this flow without opening the app — a webhook POST, a cron schedule, another agent/flow's run
          finishing, or a Telegram message, on top of starting it manually from the Runs page.
        </p>
        {actionError && <p className="text-sm text-red-600">{actionError}</p>}

        {!triggers ? (
          <p className="text-sm text-gray-500">Loading…</p>
        ) : triggers.length === 0 ? (
          <p className="text-sm text-gray-500">No triggers yet.</p>
        ) : (
          <ul className="space-y-3">
            {triggers.map((t) => (
              <TriggerRow
                key={t.id}
                trigger={t}
                agents={agents}
                flows={flows}
                onToggle={() => handleToggle(t)}
                onDelete={() => handleDelete(t.id)}
              />
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
              {(agents || otherFlows.length > 0) && (
                <button
                  type="button"
                  onClick={() => setAddKind("agent_event")}
                  className={`flex-1 rounded-md border px-2 py-1 ${
                    addKind === "agent_event"
                      ? "border-gray-900 bg-gray-900 text-white"
                      : "border-gray-300 text-gray-700"
                  }`}
                >
                  Agent/flow event
                </button>
              )}
              <button
                type="button"
                onClick={() => setAddKind("telegram")}
                className={`flex-1 rounded-md border px-2 py-1 ${
                  addKind === "telegram" ? "border-gray-900 bg-gray-900 text-white" : "border-gray-300 text-gray-700"
                }`}
              >
                Telegram
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
            {addKind === "agent_event" && (
              <div className="space-y-3">
                <p className="text-xs text-gray-500">
                  Starts this flow automatically when another agent's or flow's run finishes, in this same
                  workspace.
                </p>
                <div>
                  <span className="block text-sm font-medium text-gray-700">Source</span>
                  <div className="mt-1 flex gap-2 text-sm">
                    <button
                      type="button"
                      onClick={() => {
                        setSourceType("agent");
                        setSourceId("");
                      }}
                      className={`flex-1 rounded-md border px-2 py-1 ${
                        sourceType === "agent" ? "border-gray-900 bg-gray-900 text-white" : "border-gray-300 text-gray-700"
                      }`}
                    >
                      An agent
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setSourceType("flow");
                        setSourceId("");
                      }}
                      className={`flex-1 rounded-md border px-2 py-1 ${
                        sourceType === "flow" ? "border-gray-900 bg-gray-900 text-white" : "border-gray-300 text-gray-700"
                      }`}
                    >
                      Another flow
                    </button>
                  </div>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700" htmlFor="trigger-source">
                    {sourceType === "agent" ? "Agent" : "Flow"}
                  </label>
                  <select
                    id="trigger-source"
                    required
                    value={sourceId}
                    onChange={(e) => setSourceId(e.target.value)}
                    className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
                  >
                    <option value="" disabled>
                      Choose {sourceType === "agent" ? "an agent" : "a flow"}…
                    </option>
                    {(sourceType === "agent" ? agents ?? [] : otherFlows).map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700" htmlFor="trigger-on-status">
                    When it
                  </label>
                  <select
                    id="trigger-on-status"
                    value={onStatus}
                    onChange={(e) => setOnStatus(e.target.value as (typeof ON_STATUS_OPTIONS)[number])}
                    className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
                  >
                    {ON_STATUS_OPTIONS.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            )}
            {addKind === "telegram" && (
              <div className="space-y-3">
                <p className="text-xs text-gray-500">
                  Starts this flow when a message is sent to a Telegram bot — the run's result is sent back to
                  that chat. Create a bot with{" "}
                  <a
                    href="https://t.me/BotFather"
                    target="_blank"
                    rel="noreferrer"
                    className="text-blue-600 hover:underline"
                  >
                    @BotFather
                  </a>{" "}
                  and paste the token it gives you.
                </p>
                <div>
                  <label className="block text-sm font-medium text-gray-700" htmlFor="trigger-bot-token">
                    Bot token
                  </label>
                  <input
                    id="trigger-bot-token"
                    type="password"
                    required
                    value={botToken}
                    onChange={(e) => setBotToken(e.target.value)}
                    placeholder="123456:ABC-DEF…"
                    className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-sm focus:border-gray-500 focus:outline-none"
                  />
                  <p className="mt-1 text-xs text-gray-400">
                    Verified against Telegram and encrypted immediately — never shown again.
                  </p>
                </div>
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

function TriggerRow({
  trigger,
  agents,
  flows,
  onToggle,
  onDelete,
}: {
  trigger: Trigger;
  agents?: Agent[];
  flows?: Flow[];
  onToggle: () => void;
  onDelete: () => void;
}) {
  const [revealSecret, setRevealSecret] = useState(false);
  const webhookUrl = `${apiBaseUrl}/webhooks/${trigger.id}`;
  const secret = typeof trigger.config.secret === "string" ? trigger.config.secret : null;
  const cron = typeof trigger.config.cron === "string" ? trigger.config.cron : null;
  const sourceAgentId = typeof trigger.config.source_agent_id === "string" ? trigger.config.source_agent_id : null;
  const sourceFlowId = typeof trigger.config.source_flow_id === "string" ? trigger.config.source_flow_id : null;
  const onStatus = typeof trigger.config.on_status === "string" ? trigger.config.on_status : null;
  const botUsername = typeof trigger.config.bot_username === "string" ? trigger.config.bot_username : null;
  const sourceName = sourceAgentId
    ? agents?.find((a) => a.id === sourceAgentId)?.name ?? sourceAgentId.slice(0, 8)
    : sourceFlowId
      ? flows?.find((f) => f.id === sourceFlowId)?.name ?? sourceFlowId.slice(0, 8)
      : null;

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
      {trigger.kind === "agent_event" && (
        <div className="text-xs text-gray-500">
          When <span className="font-medium text-gray-700">{sourceName ?? "—"}</span>{" "}
          {sourceAgentId ? "(agent)" : "(flow)"} runs and <code className="rounded bg-gray-100 px-1 py-0.5">{onStatus}</code>
          {" — "}
          {trigger.last_fired_at ? `last fired ${new Date(trigger.last_fired_at).toLocaleString()}` : "never fired yet"}
        </div>
      )}
      {trigger.kind === "telegram" && (
        <div className="text-xs text-gray-500">
          Connected to <span className="font-medium text-gray-700">@{botUsername ?? "unknown"}</span> — message the
          bot to start this flow
          {" — "}
          {trigger.last_fired_at ? `last fired ${new Date(trigger.last_fired_at).toLocaleString()}` : "never fired yet"}
        </div>
      )}
    </li>
  );
}
