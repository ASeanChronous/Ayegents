"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import useSWR from "swr";

import { api, unwrap, ApiError } from "@/lib/api";
import { useWorkspace } from "@/lib/useWorkspace";
import { DataTable } from "@/components/DataTable";
import { FormDrawer } from "@/components/FormDrawer";
import { Badge } from "@/components/Badge";

type Agent = { id: string; name: string; description: string | null; status: string };

export default function AgentsPage() {
  const { currentWorkspace } = useWorkspace();
  const workspaceId = currentWorkspace?.id;

  const { data: agents, isLoading, mutate } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/agents` : null,
    () => unwrap(api.GET("/workspaces/{workspace_id}/agents", { params: { path: { workspace_id: workspaceId! } } }))
  );

  const [createOpen, setCreateOpen] = useState(false);

  if (!workspaceId) {
    return <p className="text-sm text-gray-500">Pick or create a workspace first.</p>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Agents</h1>
        <button
          onClick={() => setCreateOpen(true)}
          className="rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-gray-800"
        >
          New agent
        </button>
      </div>

      {isLoading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : (
        <DataTable<Agent>
          rows={agents ?? []}
          keyFn={(a) => a.id}
          emptyMessage="No agents yet — create one to get started."
          columns={[
            {
              header: "Name",
              render: (a) => (
                <Link href={`/agents/${a.id}`} className="font-medium text-blue-600 hover:underline">
                  {a.name}
                </Link>
              ),
            },
            { header: "Description", render: (a) => a.description ?? <span className="text-gray-400">—</span> },
            {
              header: "Status",
              render: (a) => <Badge tone={a.status === "published" ? "green" : "gray"}>{a.status}</Badge>,
            },
          ]}
        />
      )}

      <CreateAgentDrawer
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        workspaceId={workspaceId}
        onCreated={() => mutate()}
      />
    </div>
  );
}

function CreateAgentDrawer({
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
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await unwrap(
        api.POST("/workspaces/{workspace_id}/agents", {
          params: { path: { workspace_id: workspaceId } },
          body: { name, description: description || null },
        })
      );
      onCreated();
      setName("");
      setDescription("");
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to create agent");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <FormDrawer open={open} onClose={onClose} title="New agent">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700" htmlFor="agent-name">
            Name
          </label>
          <input
            id="agent-name"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700" htmlFor="agent-description">
            Description
          </label>
          <input
            id="agent-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
          />
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
        >
          {submitting ? "Creating…" : "Create agent"}
        </button>
      </form>
    </FormDrawer>
  );
}
