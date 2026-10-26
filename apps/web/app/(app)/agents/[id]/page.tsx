"use client";

import { FormEvent, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import useSWR from "swr";

import { api, unwrap, ApiError } from "@/lib/api";
import { useWorkspace } from "@/lib/useWorkspace";
import { DataTable } from "@/components/DataTable";
import { FormDrawer } from "@/components/FormDrawer";
import { Badge } from "@/components/Badge";

const MODEL_TIERS = ["haiku", "sonnet", "opus"] as const;

type Tool = { id: string; key: string; name: string; kind: string };
type AgentVersion = {
  id: string;
  version: number;
  system_prompt: string;
  model_tier: string;
  max_steps: number | null;
  max_tokens: number | null;
  created_at: string;
  tools: { tool_id: string; key: string; name: string; config: Record<string, unknown> }[];
  sub_agent_ids: string[];
};

export default function AgentDetailPage() {
  const { id: agentId } = useParams<{ id: string }>();
  const { currentWorkspace } = useWorkspace();
  const workspaceId = currentWorkspace?.id;

  const { data: agent } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/agents/${agentId}` : null,
    () =>
      unwrap(
        api.GET("/workspaces/{workspace_id}/agents/{agent_id}", {
          params: { path: { workspace_id: workspaceId!, agent_id: agentId } },
        })
      )
  );
  const { data: versions, isLoading: versionsLoading, mutate: mutateVersions } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/agents/${agentId}/versions` : null,
    () =>
      unwrap(
        api.GET("/workspaces/{workspace_id}/agents/{agent_id}/versions", {
          params: { path: { workspace_id: workspaceId!, agent_id: agentId } },
        })
      )
  );
  const { data: tools } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/tools` : null,
    () => unwrap(api.GET("/workspaces/{workspace_id}/tools", { params: { path: { workspace_id: workspaceId! } } }))
  );

  const [publishOpen, setPublishOpen] = useState(false);
  const latestVersion = versions?.[0] as AgentVersion | undefined;

  if (!workspaceId) {
    return <p className="text-sm text-gray-500">Pick or create a workspace first.</p>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">{agent?.name ?? "…"}</h1>
          {agent?.description && <p className="text-gray-600">{agent.description}</p>}
        </div>
        <button
          onClick={() => setPublishOpen(true)}
          className="rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-gray-800"
        >
          Publish new version
        </button>
      </div>

      <section className="space-y-2">
        <h2 className="text-sm font-medium text-gray-700">Versions</h2>
        {versionsLoading ? (
          <p className="text-sm text-gray-500">Loading…</p>
        ) : (
          <DataTable<AgentVersion>
            rows={versions ?? []}
            keyFn={(v) => v.id}
            emptyMessage="No versions published yet — publish one to make this agent runnable."
            columns={[
              { header: "Version", render: (v) => `v${v.version}` },
              { header: "Model", render: (v) => <Badge tone="blue">{v.model_tier}</Badge> },
              {
                header: "Tools",
                render: (v) =>
                  v.tools.length > 0 ? (
                    <span className="text-xs text-gray-600">{v.tools.map((t) => t.key).join(", ")}</span>
                  ) : (
                    <span className="text-xs text-gray-400">none</span>
                  ),
              },
              { header: "Published", render: (v) => new Date(v.created_at).toLocaleString() },
            ]}
          />
        )}
      </section>

      {tools && (
        <PublishVersionDrawer
          open={publishOpen}
          onClose={() => setPublishOpen(false)}
          workspaceId={workspaceId}
          agentId={agentId}
          availableTools={tools}
          latestVersion={latestVersion}
          onPublished={() => mutateVersions()}
        />
      )}
    </div>
  );
}

function PublishVersionDrawer({
  open,
  onClose,
  workspaceId,
  agentId,
  availableTools,
  latestVersion,
  onPublished,
}: {
  open: boolean;
  onClose: () => void;
  workspaceId: string;
  agentId: string;
  availableTools: Tool[];
  latestVersion: AgentVersion | undefined;
  onPublished: () => void;
}) {
  const [systemPrompt, setSystemPrompt] = useState("");
  const [modelTier, setModelTier] = useState<(typeof MODEL_TIERS)[number]>("sonnet");
  const [maxSteps, setMaxSteps] = useState("");
  const [selectedToolIds, setSelectedToolIds] = useState<Set<string>>(new Set());
  const [toolConfigs, setToolConfigs] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Prefill from the latest version when the drawer opens, so publishing a
  // tweak doesn't mean retyping the whole prompt and re-picking every tool.
  useEffect(() => {
    if (!open) return;
    setSystemPrompt(latestVersion?.system_prompt ?? "");
    setModelTier((latestVersion?.model_tier as (typeof MODEL_TIERS)[number]) ?? "sonnet");
    setMaxSteps(latestVersion?.max_steps != null ? String(latestVersion.max_steps) : "");
    setSelectedToolIds(new Set(latestVersion?.tools.map((t) => t.tool_id) ?? []));
    setToolConfigs(
      Object.fromEntries((latestVersion?.tools ?? []).map((t) => [t.tool_id, JSON.stringify(t.config, null, 2)]))
    );
    setError(null);
  }, [open, latestVersion]);

  function toggleTool(toolId: string) {
    setSelectedToolIds((prev) => {
      const next = new Set(prev);
      if (next.has(toolId)) {
        next.delete(toolId);
      } else {
        next.add(toolId);
        if (!(toolId in toolConfigs)) {
          setToolConfigs((c) => ({ ...c, [toolId]: "{}" }));
        }
      }
      return next;
    });
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    const toolPayload: { tool_id: string; config: Record<string, unknown> }[] = [];
    for (const toolId of selectedToolIds) {
      try {
        toolPayload.push({ tool_id: toolId, config: JSON.parse(toolConfigs[toolId] || "{}") });
      } catch {
        const tool = availableTools.find((t) => t.id === toolId);
        setError(`Invalid JSON in config for "${tool?.key ?? toolId}"`);
        return;
      }
    }

    setSubmitting(true);
    try {
      await unwrap(
        api.POST("/workspaces/{workspace_id}/agents/{agent_id}/versions", {
          params: { path: { workspace_id: workspaceId, agent_id: agentId } },
          body: {
            system_prompt: systemPrompt,
            model_tier: modelTier,
            max_steps: maxSteps ? Number(maxSteps) : null,
            // Agent-as-tool (sub_agent_ids) isn't exposed in this form: wiring
            // it up needs an 'agent' kind tool with matching config.sub_agent_id
            // too (tool_gateway.py), a two-step setup left as API-only for now.
            sub_agent_ids: [],
            tools: toolPayload,
          },
        })
      );
      onPublished();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to publish version");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <FormDrawer open={open} onClose={onClose} title="Publish new version">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700" htmlFor="system-prompt">
            System prompt
          </label>
          <textarea
            id="system-prompt"
            required
            rows={6}
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700" htmlFor="model-tier">
            Model
          </label>
          <select
            id="model-tier"
            value={modelTier}
            onChange={(e) => setModelTier(e.target.value as (typeof MODEL_TIERS)[number])}
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
          >
            {MODEL_TIERS.map((tier) => (
              <option key={tier} value={tier}>
                {tier}
              </option>
            ))}
          </select>
          <p className="mt-1 text-xs text-gray-500">
            Cost/capability tradeoff — haiku cheapest, opus most capable (architecture doc §9a).
          </p>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700" htmlFor="max-steps">
            Max steps (optional)
          </label>
          <input
            id="max-steps"
            type="number"
            min={1}
            value={maxSteps}
            onChange={(e) => setMaxSteps(e.target.value)}
            placeholder="Uses the workspace plan's default"
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
          />
        </div>

        <div>
          <span className="block text-sm font-medium text-gray-700">Tools</span>
          {availableTools.length === 0 ? (
            <p className="mt-1 text-sm text-gray-500">No tools yet — create one on the Tools page first.</p>
          ) : (
            <div className="mt-1 space-y-2">
              {availableTools.map((tool) => (
                <div key={tool.id} className="rounded-md border border-gray-200 p-2">
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={selectedToolIds.has(tool.id)}
                      onChange={() => toggleTool(tool.id)}
                    />
                    <code className="text-xs">{tool.key}</code>
                    <span className="text-gray-500">({tool.kind})</span>
                  </label>
                  {selectedToolIds.has(tool.id) && (
                    <textarea
                      rows={2}
                      value={toolConfigs[tool.id] ?? "{}"}
                      onChange={(e) => setToolConfigs((c) => ({ ...c, [tool.id]: e.target.value }))}
                      placeholder='Static config this tool needs, e.g. {"url": "https://..."}'
                      className="mt-2 w-full rounded-md border border-gray-200 px-2 py-1 font-mono text-xs focus:border-gray-500 focus:outline-none"
                    />
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {error && <p className="text-sm text-red-600">{error}</p>}

        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
        >
          {submitting ? "Publishing…" : "Publish version"}
        </button>
      </form>
    </FormDrawer>
  );
}
