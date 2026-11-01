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
  const [showArchived, setShowArchived] = useState(false);

  const { data: flows, isLoading, mutate } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/flows?include_archived=${showArchived}` : null,
    () =>
      unwrap(
        api.GET("/workspaces/{workspace_id}/flows", {
          params: { path: { workspace_id: workspaceId! }, query: { include_archived: showArchived } },
        })
      )
  );

  const [createOpen, setCreateOpen] = useState(false);
  const [renaming, setRenaming] = useState<Flow | null>(null);

  async function toggleArchived(flow: Flow) {
    try {
      await unwrap(
        api.PATCH("/workspaces/{workspace_id}/flows/{flow_id}", {
          params: { path: { workspace_id: workspaceId!, flow_id: flow.id } },
          body: { status: flow.status === "archived" ? "draft" : "archived" },
        })
      );
      await mutate();
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "Failed to update flow");
    }
  }

  if (!workspaceId) {
    return <p className="text-sm text-gray-500">Pick or create a workspace first.</p>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Flows</h1>
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-1.5 text-sm text-gray-600">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
            />
            Show archived
          </label>
          <button
            onClick={() => setCreateOpen(true)}
            className="rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-gray-800"
          >
            New flow
          </button>
        </div>
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
              render: (f) => <Badge tone={f.status === "archived" ? "gray" : f.status === "published" ? "green" : "gray"}>{f.status}</Badge>,
            },
            {
              header: "",
              render: (f) => (
                <div className="flex gap-3">
                  <button onClick={() => setRenaming(f)} className="text-xs font-medium text-blue-600 hover:underline">
                    Rename
                  </button>
                  <button onClick={() => toggleArchived(f)} className="text-xs font-medium text-gray-600 hover:underline">
                    {f.status === "archived" ? "Unarchive" : "Archive"}
                  </button>
                </div>
              ),
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
      <RenameFlowDrawer
        key={renaming?.id ?? "none"}
        flow={renaming}
        onClose={() => setRenaming(null)}
        workspaceId={workspaceId}
        onRenamed={() => mutate()}
      />
    </div>
  );
}

function RenameFlowDrawer({
  flow,
  onClose,
  workspaceId,
  onRenamed,
}: {
  flow: Flow | null;
  onClose: () => void;
  workspaceId: string;
  onRenamed: () => void;
}) {
  const [name, setName] = useState(flow?.name ?? "");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!flow) return;
    setError(null);
    setSubmitting(true);
    try {
      await unwrap(
        api.PATCH("/workspaces/{workspace_id}/flows/{flow_id}", {
          params: { path: { workspace_id: workspaceId, flow_id: flow.id } },
          body: { name },
        })
      );
      onRenamed();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to rename flow");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <FormDrawer open={flow !== null} onClose={onClose} title="Rename flow">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-gray-700" htmlFor="rename-flow-name">
            Name
          </label>
          <input
            id="rename-flow-name"
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
          {submitting ? "Saving…" : "Save"}
        </button>
      </form>
    </FormDrawer>
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
