"use client";

import { FormEvent, useState } from "react";

import { api, unwrap, ApiError } from "@/lib/api";
import { useWorkspace } from "@/lib/useWorkspace";
import { DataTable } from "@/components/DataTable";
import { FormDrawer } from "@/components/FormDrawer";

export default function WorkspacesPage() {
  const { workspaces, currentWorkspace, setCurrentWorkspaceId, isLoading, refresh } = useWorkspace();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const created = await unwrap(api.POST("/workspaces", { body: { name } }));
      await refresh();
      setCurrentWorkspaceId(created.id);
      setName("");
      setDrawerOpen(false);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to create workspace");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Workspaces</h1>
        <button
          onClick={() => setDrawerOpen(true)}
          className="rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-gray-800"
        >
          New workspace
        </button>
      </div>

      {isLoading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : (
        <DataTable
          rows={workspaces}
          keyFn={(w) => w.id}
          emptyMessage="No workspaces yet — create one to get started."
          columns={[
            {
              header: "Name",
              render: (w) => (
                <span className={w.id === currentWorkspace?.id ? "font-medium" : ""}>{w.name}</span>
              ),
            },
            { header: "Plan", render: (w) => w.plan_tier },
            {
              header: "",
              render: (w) =>
                w.id === currentWorkspace?.id ? (
                  <span className="text-xs font-medium text-green-700">Current</span>
                ) : (
                  <button
                    onClick={() => setCurrentWorkspaceId(w.id)}
                    className="text-xs font-medium text-blue-600 hover:underline"
                  >
                    Switch to this workspace
                  </button>
                ),
            },
          ]}
        />
      )}

      <FormDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} title="New workspace">
        <form onSubmit={handleCreate} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700" htmlFor="ws-name">
              Name
            </label>
            <input
              id="ws-name"
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
            {submitting ? "Creating…" : "Create workspace"}
          </button>
        </form>
      </FormDrawer>
    </div>
  );
}
