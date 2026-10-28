"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { supabase } from "@/lib/supabaseClient";
import { useSession } from "@/lib/useSession";
import { WorkspaceProvider } from "@/lib/useWorkspace";
import WorkspaceSwitcher from "./WorkspaceSwitcher";

// The canvas (/flows/[id]) is the one page that needs real screen space --
// every other page gets the standard centered, padded column
// (docs/04-frontend-builder.md §6). Matched by path rather than a prop so
// every other page stays untouched.
const FULL_BLEED_PATTERN = /^\/flows\/[^/]+$/;

export default function AppShell({ children }: { children: React.ReactNode }) {
  const { session, loading } = useSession();
  const router = useRouter();
  const pathname = usePathname();
  const fullBleed = FULL_BLEED_PATTERN.test(pathname);

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
      <div className="flex h-screen flex-col">
        <nav className="flex flex-shrink-0 items-center justify-between border-b border-gray-200 bg-white px-6 py-3">
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
              <Link href="/flows" className="hover:text-gray-900">
                Flows
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
        <main className={fullBleed ? "min-h-0 flex-1" : "mx-auto w-full max-w-5xl flex-1 overflow-y-auto p-6"}>
          {children}
        </main>
      </div>
    </WorkspaceProvider>
  );
}
