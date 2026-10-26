"use client";

import Link from "next/link";

import { useWorkspace } from "@/lib/useWorkspace";

export default function WorkspaceSwitcher() {
  const { workspaces, currentWorkspace, setCurrentWorkspaceId, isLoading } = useWorkspace();

  if (isLoading) {
    return <span className="text-sm text-gray-400">Loading workspaces…</span>;
  }

  if (workspaces.length === 0) {
    return (
      <Link href="/workspaces" className="text-sm font-medium text-blue-600 hover:underline">
        Create a workspace
      </Link>
    );
  }

  return (
    <select
      value={currentWorkspace?.id ?? ""}
      onChange={(e) => setCurrentWorkspaceId(e.target.value)}
      className="rounded-md border border-gray-300 bg-white px-2 py-1 text-sm"
    >
      {workspaces.map((w) => (
        <option key={w.id} value={w.id}>
          {w.name}
        </option>
      ))}
    </select>
  );
}
