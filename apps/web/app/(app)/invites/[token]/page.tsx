"use client";

import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";

import { api, unwrap, ApiError } from "@/lib/api";
import { useWorkspace } from "@/lib/useWorkspace";

export default function AcceptInvitePage() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const { refresh: refreshWorkspaces, setCurrentWorkspaceId } = useWorkspace();

  const { data: invite, error: lookupError } = useSWR(`/invites/${token}`, () =>
    unwrap(api.GET("/invites/{token}", { params: { path: { token } } }))
  );

  const [accepting, setAccepting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleAccept() {
    setAccepting(true);
    setError(null);
    try {
      await unwrap(api.POST("/invites/{token}/accept", { params: { path: { token } } }));
      await refreshWorkspaces();
      if (invite) setCurrentWorkspaceId(invite.workspace_id);
      router.replace("/team");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to accept invite");
      setAccepting(false);
    }
  }

  if (lookupError) {
    return <p className="text-sm text-red-600">This invite link is invalid or has been revoked.</p>;
  }
  if (!invite) {
    return <p className="text-sm text-gray-500">Loading…</p>;
  }

  if (invite.accepted) {
    return <p className="text-sm text-gray-700">This invite has already been accepted.</p>;
  }
  if (invite.expired) {
    return <p className="text-sm text-red-600">This invite has expired — ask {`"`}{invite.workspace_name}{`"`} to send a new one.</p>;
  }
  if (!invite.matches_current_user) {
    return (
      <p className="text-sm text-red-600">
        This invite was sent to <span className="font-medium">{invite.email}</span> — sign out and sign in as that
        account to accept it.
      </p>
    );
  }

  return (
    <div className="space-y-4 text-sm">
      <h1 className="text-xl font-semibold">Join {invite.workspace_name}</h1>
      <p className="text-gray-700">
        You{"'"}ve been invited to join <span className="font-medium">{invite.workspace_name}</span> as{" "}
        <span className="font-medium">{invite.role}</span>.
      </p>
      {error && <p className="text-red-600">{error}</p>}
      <button
        onClick={handleAccept}
        disabled={accepting}
        className="rounded-md bg-gray-900 px-4 py-2 font-medium text-white hover:bg-gray-800 disabled:opacity-50"
      >
        {accepting ? "Joining…" : "Accept invite"}
      </button>
    </div>
  );
}
