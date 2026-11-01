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
// 'agent' also excluded: an agent-as-tool's key/config_schema/config
// (config.sub_agent_id) all have one specific, interdependent shape that the
// publish-version form's new "Sub-agents" picker (agents/[id]/page.tsx)
// generates automatically -- hand-creating one here would just produce a
// tool that fails dispatch with a malformed or missing sub_agent_id.
const CREATABLE_KINDS = ["http", "code_exec"] as const;

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
                    Manage credentials
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
  const [submitting, setSubmitting] = useState(false);

  const { data: credentials, mutate: refreshCredentials } = useSWR(
    `/workspaces/${workspaceId}/tools/${tool.id}/credentials`,
    () =>
      unwrap(
        api.GET("/workspaces/{workspace_id}/tools/{tool_id}/credentials", {
          params: { path: { workspace_id: workspaceId, tool_id: tool.id } },
        })
      )
  );

  // _load_credential_secret (tool_gateway.py) always uses the most recently
  // created row for this tool -- the first item here, since the list comes
  // back newest-first -- so every call this agent makes right now is using
  // exactly this one, and every other row below it is dead weight nobody
  // can see is dead without this label.
  const liveCredentialId = credentials?.[0]?.id;

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
      setLabel("");
      setSecret("");
      await refreshCredentials();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to add credential");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(credentialId: string) {
    if (!confirm("Delete this credential? Any tool call still relying on it will start failing.")) return;
    try {
      await unwrap(
        api.DELETE("/workspaces/{workspace_id}/tools/{tool_id}/credentials/{credential_id}", {
          params: { path: { workspace_id: workspaceId, tool_id: tool.id, credential_id: credentialId } },
        })
      );
      await refreshCredentials();
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "Failed to delete credential");
    }
  }

  return (
    <FormDrawer open onClose={onClose} title={`Credentials — ${tool.name}`}>
      <div className="space-y-6">
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-gray-700">Existing credentials</h3>
          {credentials && credentials.length === 0 && (
            <p className="text-xs text-gray-500">None yet — add one below.</p>
          )}
          {credentials?.map((c) => (
            <div key={c.id} className="flex items-center justify-between rounded-md border border-gray-200 px-3 py-2 text-sm">
              <div>
                <span className="font-medium">{c.label}</span>
                {c.id === liveCredentialId && (
                  <span className="ml-2 text-xs text-green-700">● in use</span>
                )}
                <p className="text-xs text-gray-400">{new Date(c.created_at).toLocaleString()}</p>
              </div>
              <button onClick={() => handleDelete(c.id)} className="text-xs font-medium text-red-600 hover:underline">
                Delete
              </button>
            </div>
          ))}
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 border-t border-gray-200 pt-4">
          <h3 className="text-sm font-medium text-gray-700">Add a new credential</h3>
          <p className="text-xs text-gray-500">
            The newest credential is always the one tool calls use — adding one here effectively rotates it.
          </p>
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
      </div>
    </FormDrawer>
  );
}
