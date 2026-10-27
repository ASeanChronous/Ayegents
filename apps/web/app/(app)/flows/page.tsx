"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import useSWR from "swr";

import { api, unwrap, ApiError } from "@/lib/api";
import { useWorkspace } from "@/lib/useWorkspace";
import { DataTable } from "@/components/DataTable";
import { FormDrawer } from "@/components/FormDrawer";
import { Badge } from "@/components/Badge";

type Flow = { id: string; name: string; status: string };

export default function FlowsPage() {
  const { currentWorkspace } = useWorkspace();
  const workspaceId = currentWorkspace?.id;

  const { data: flows, isLoading, mutate } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/flows` : null,
    () => unwrap(api.GET("/workspaces/{workspace_id}/flows", { params: { path: { workspace_id: workspaceId! } } }))
  );

  const [createOpen, setCreateOpen] = useState(false);

  if (!workspaceId) {
    return <p className="text-sm text-gray-500">Pick or create a workspace first.</p>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Flows</h1>
        <button
          onClick={() => setCreateOpen(true)}
          className="rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-gray-800"
        >
          New flow
        </button>
      </div>

      {isLoading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : (
        <DataTable<Flow>
          rows={flows ?? []}
          keyFn={(f) => f.id}
          emptyMessage="No flows yet — create one to get started."
          columns={[
            {
              header: "Name",
              render: (f) => (
                <Link href={`/flows/${f.id}`} className="font-medium text-blue-600 hover:underline">
                  {f.name}
                </Link>
              ),
            },
            {
              header: "Status",
              render: (f) => <Badge tone={f.status === "published" ? "green" : "gray"}>{f.status}</Badge>,
            },
          ]}
        />
      )}

      <CreateFlowDrawer
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        workspaceId={workspaceId}
        onCreated={() => mutate()}
      />
    </div>
  );
}

function CreateFlowDrawer({
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
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await unwrap(
        api.POST("/workspaces/{workspace_id}/flows", {
          params: { path: { workspace_id: workspaceId } },
          body: { name },
        })
      );
      onCreated();
      setName("");
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to create flow");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <FormDrawer open={open} onClose={onClose} title="New flow">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700" htmlFor="flow-name">
            Name
          </label>
          <input
            id="flow-name"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
          />
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
        >
          {submitting ? "Creating…" : "Create flow"}
        </button>
      </form>
    </FormDrawer>
  );
}
