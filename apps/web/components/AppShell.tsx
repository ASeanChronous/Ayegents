"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { supabase } from "@/lib/supabaseClient";
import { useSession } from "@/lib/useSession";
import { WorkspaceProvider } from "@/lib/useWorkspace";
import WorkspaceSwitcher from "./WorkspaceSwitcher";

export default function AppShell({ children }: { children: React.ReactNode }) {
  const { session, loading } = useSession();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !session) {
      router.replace("/login");
    }
  }, [loading, session, router]);

  if (loading) {
    return <div className="flex min-h-screen items-center justify-center text-sm text-gray-500">Loading…</div>;
  }

  if (!session) {
    // Redirect above is in flight; render nothing rather than a flash of
    // gated content.
    return null;
  }

  return (
    // WorkspaceProvider only mounts once a session exists -- its /workspaces
    // fetch would otherwise fire before there's a token to send.
    <WorkspaceProvider>
      <div className="min-h-screen">
        <nav className="flex items-center justify-between border-b border-gray-200 bg-white px-6 py-3">
          <div className="flex items-center gap-6">
            <Link href="/" className="font-semibold">
              Agent Ecosystem
            </Link>
            <div className="flex items-center gap-4 text-sm text-gray-600">
              <Link href="/agents" className="hover:text-gray-900">
                Agents
              </Link>
              <Link href="/tools" className="hover:text-gray-900">
                Tools
              </Link>
              <Link href="/runs" className="hover:text-gray-900">
                Runs
              </Link>
              <Link href="/workspaces" className="hover:text-gray-900">
                Workspaces
              </Link>
            </div>
          </div>
          <div className="flex items-center gap-4 text-sm text-gray-600">
            <WorkspaceSwitcher />
            <span>{session.user.email}</span>
            <button
              onClick={async () => {
                await supabase.auth.signOut();
                router.replace("/login");
              }}
              className="rounded-md border border-gray-300 px-3 py-1 hover:bg-gray-50"
            >
              Sign out
            </button>
          </div>
        </nav>
        <main className="mx-auto max-w-5xl p-6">{children}</main>
      </div>
    </WorkspaceProvider>
  );
}
