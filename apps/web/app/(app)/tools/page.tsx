"use client";

import { FormEvent, useState } from "react";
import useSWR from "swr";

import { api, unwrap, ApiError } from "@/lib/api";
import { useWorkspace } from "@/lib/useWorkspace";
import { DataTable } from "@/components/DataTable";
import { FormDrawer } from "@/components/FormDrawer";
import { Badge } from "@/components/Badge";

type Tool = {
  id: string;
  key: string;
  name: string;
  kind: string;
  requires_credential: boolean;
  allowed_hosts: string[] | null;
};

// 'builtin' deliberately excluded -- a builtin tool's key must match an
// entry in the orchestrator's own BUILTIN_TOOLS registry
// (orchestrator_app/tool_gateway.py), so creating one through this generic
// form would just produce a tool that fails every dispatch with "no builtin
// tool registered for key ...". Builtins are seeded by migration, not
// created here.
const CREATABLE_KINDS = ["http", "code_exec", "agent"] as const;

const KIND_TONE: Record<string, "blue" | "yellow" | "gray"> = {
  http: "blue",
  code_exec: "yellow",
  agent: "gray",
  builtin: "gray",
};

export default function ToolsPage() {
  const { currentWorkspace } = useWorkspace();
  const workspaceId = currentWorkspace?.id;

  const { data: tools, isLoading, mutate } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/tools` : null,
    () => unwrap(api.GET("/workspaces/{workspace_id}/tools", { params: { path: { workspace_id: workspaceId! } } }))
  );

  const [createOpen, setCreateOpen] = useState(false);
  const [credentialTool, setCredentialTool] = useState<Tool | null>(null);

  if (!workspaceId) {
    return <p className="text-sm text-gray-500">Pick or create a workspace first.</p>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Tools</h1>
        <button
          onClick={() => setCreateOpen(true)}
          className="rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-gray-800"
        >
          New tool
        </button>
      </div>

      {isLoading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : (
        <DataTable<Tool>
          rows={tools ?? []}
          keyFn={(t) => t.id}
          emptyMessage="No tools yet — create one to attach to an agent."
          columns={[
            { header: "Key", render: (t) => <code className="text-xs">{t.key}</code> },
            { header: "Name", render: (t) => t.name },
            { header: "Kind", render: (t) => <Badge tone={KIND_TONE[t.kind] ?? "gray"}>{t.kind}</Badge> },
            {
              header: "Egress",
              render: (t) =>
                t.allowed_hosts && t.allowed_hosts.length > 0 ? (
                  <span className="text-xs text-gray-600">{t.allowed_hosts.join(", ")}</span>
                ) : (
                  <span className="text-xs text-gray-400">unrestricted</span>
                ),
            },
            {
              header: "",
              render: (t) =>
                t.requires_credential ? (
                  <button onClick={() => setCredentialTool(t)} className="text-xs font-medium text-blue-600 hover:underline">
                    Add credential
                  </button>
                ) : null,
            },
          ]}
        />
      )}

      <CreateToolDrawer
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        workspaceId={workspaceId}
        onCreated={() => mutate()}
      />
      {credentialTool && (
        <CredentialDrawer
          tool={credentialTool}
          workspaceId={workspaceId}
          onClose={() => setCredentialTool(null)}
        />
      )}
    </div>
  );
}

function CreateToolDrawer({
  open,
  onClose,
  workspaceId,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  workspaceId: string;
  onCreated: () => void;
}) {
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [kind, setKind] = useState<(typeof CREATABLE_KINDS)[number]>("http");
  const [requiresCredential, setRequiresCredential] = useState(false);
  const [allowedHosts, setAllowedHosts] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await unwrap(
        api.POST("/workspaces/{workspace_id}/tools", {
          params: { path: { workspace_id: workspaceId } },
          body: {
            key,
            name,
            kind,
            config_schema: {},
            requires_credential: requiresCredential,
            allowed_hosts: allowedHosts.trim()
              ? allowedHosts.split(",").map((h) => h.trim()).filter(Boolean)
              : null,
          },
        })
      );
      onCreated();
      setKey("");
      setName("");
      setKind("http");
      setRequiresCredential(false);
      setAllowedHosts("");
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to create tool");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <FormDrawer open={open} onClose={onClose} title="New tool">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700" htmlFor="tool-key">
            Key
          </label>
          <input
            id="tool-key"
            required
            placeholder="e.g. weather_api"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-mono focus:border-gray-500 focus:outline-none"
          />
          <p className="mt-1 text-xs text-gray-500">The name the model calls this tool by.</p>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700" htmlFor="tool-name">
            Display name
          </label>
          <input
            id="tool-name"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700" htmlFor="tool-kind">
            Kind
          </label>
          <select
            id="tool-kind"
            value={kind}
            onChange={(e) => setKind(e.target.value as (typeof CREATABLE_KINDS)[number])}
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
          >
            {CREATABLE_KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700" htmlFor="tool-hosts">
            Allowed hosts (egress)
          </label>
          <input
            id="tool-hosts"
            placeholder="api.example.com, *.partner.com"
            value={allowedHosts}
            onChange={(e) => setAllowedHosts(e.target.value)}
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
          />
          <p className="mt-1 text-xs text-gray-500">
            Comma-separated. Leave blank for unrestricted — not recommended for{" "}
            <code>http</code>/<code>code_exec</code> tools that will handle a credential.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm text-gray-700">
          <input
            type="checkbox"
            checked={requiresCredential}
            onChange={(e) => setRequiresCredential(e.target.checked)}
          />
          Requires a credential
        </label>

        {error && <p className="text-sm text-red-600">{error}</p>}

        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
        >
          {submitting ? "Creating…" : "Create tool"}
        </button>
      </form>
    </FormDrawer>
  );
}

function CredentialDrawer({
  tool,
  workspaceId,
  onClose,
}: {
  tool: Tool;
  workspaceId: string;
  onClose: () => void;
}) {
  const [label, setLabel] = useState("");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await unwrap(
        api.POST("/workspaces/{workspace_id}/tools/{tool_id}/credentials", {
          params: { path: { workspace_id: workspaceId, tool_id: tool.id } },
          body: { label, secret },
        })
      );
      setSuccess(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to add credential");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <FormDrawer open onClose={onClose} title={`Add credential — ${tool.name}`}>
      {success ? (
        <p className="text-sm text-green-700">
          Credential added. It&apos;s encrypted at rest and only the orchestrator can read it.
        </p>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700" htmlFor="cred-label">
              Label
            </label>
            <input
              id="cred-label"
              required
              placeholder="e.g. production key"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700" htmlFor="cred-secret">
              Secret
            </label>
            <input
              id="cred-secret"
              type="password"
              required
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-mono focus:border-gray-500 focus:outline-none"
            />
            <p className="mt-1 text-xs text-gray-500">
              Sent once, encrypted immediately, never shown again.
            </p>
          </div>
          {error && <p className="text-sm text-red-600">{error}</p>}
          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
          >
            {submitting ? "Saving…" : "Save credential"}
          </button>
        </form>
      )}
    </FormDrawer>
  );
}
