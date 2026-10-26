"use client";

import { createContext, useContext, useEffect, useState } from "react";
import useSWR from "swr";

import { api, unwrap } from "./api";

type Workspace = { id: string; name: string; plan_tier: string };

type WorkspaceContextValue = {
  workspaces: Workspace[];
  currentWorkspace: Workspace | null;
  setCurrentWorkspaceId: (id: string) => void;
  isLoading: boolean;
  refresh: () => void;
};

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

// Current workspace is client-side state, not a route segment (see
// docs/03-frontend.md §4) — avoids /workspaces/{id}/agents/... everywhere
// for a UI that only ever looks at one workspace at a time.
const STORAGE_KEY = "agent-ecosystem:current-workspace-id";

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const { data, isLoading, mutate } = useSWR("/workspaces", () => unwrap(api.GET("/workspaces")));
  const [currentId, setCurrentIdState] = useState<string | null>(null);

  useEffect(() => {
    setCurrentIdState(localStorage.getItem(STORAGE_KEY));
  }, []);

  useEffect(() => {
    if (!data || data.length === 0) return;
    // Nothing selected yet, or the selected id no longer exists (e.g. this
    // browser's localStorage points at a workspace from a different
    // account) — fall back to the first workspace rather than showing none.
    if (!currentId || !data.some((w) => w.id === currentId)) {
      setCurrentWorkspaceId(data[0].id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, currentId]);

  function setCurrentWorkspaceId(id: string) {
    setCurrentIdState(id);
    localStorage.setItem(STORAGE_KEY, id);
  }

  const currentWorkspace = data?.find((w) => w.id === currentId) ?? null;

  return (
    <WorkspaceContext.Provider
      value={{
        workspaces: data ?? [],
        currentWorkspace,
        setCurrentWorkspaceId,
        isLoading,
        refresh: () => mutate(),
      }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error("useWorkspace must be used within <WorkspaceProvider>");
  return ctx;
}
