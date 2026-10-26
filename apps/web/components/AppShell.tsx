"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { supabase } from "@/lib/supabaseClient";
import { useSession } from "@/lib/useSession";

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
    <div className="min-h-screen">
      <nav className="flex items-center justify-between border-b border-gray-200 bg-white px-6 py-3">
        <span className="font-semibold">Agent Ecosystem</span>
        <div className="flex items-center gap-4 text-sm text-gray-600">
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
  );
}
