"use client";

import { FormEvent, useState } from "react";
import useSWR from "swr";

import { api, unwrap, ApiError } from "@/lib/api";
import { useWorkspace } from "@/lib/useWorkspace";
import { DataTable } from "@/components/DataTable";
import { FormDrawer } from "@/components/FormDrawer";
import { Badge } from "@/components/Badge";

const ROLE_TONE = { owner: "purple", admin: "blue", member: "gray" } as const;

export default function TeamPage() {
  const { currentWorkspace, isLoading: workspaceLoading } = useWorkspace();
  const workspaceId = currentWorkspace?.id;

  const { data: members, mutate: refreshMembers } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/members` : null,
    () => unwrap(api.GET("/workspaces/{workspace_id}/members", { params: { path: { workspace_id: workspaceId! } } }))
  );
  const { data: invites, mutate: refreshInvites } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/invites` : null,
    () => unwrap(api.GET("/workspaces/{workspace_id}/invites", { params: { path: { workspace_id: workspaceId! } } }))
  );

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // Shown once, right after creating an invite -- never persisted or refetched, the
  // same "shown once, never echoed back again" posture a trigger's webhook secret uses.
  const [justCreatedUrl, setJustCreatedUrl] = useState<string | null>(null);

  async function handleInvite(e: FormEvent) {
    e.preventDefault();
    if (!workspaceId) return;
    setError(null);
    setSubmitting(true);
    try {
      const invite = await unwrap(
        api.POST("/workspaces/{workspace_id}/invites", {
          params: { path: { workspace_id: workspaceId } },
          body: { email, role },
        })
      );
      await refreshInvites();
      setJustCreatedUrl(invite.invite_url ?? null);
      setEmail("");
      setRole("member");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to create invite");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRoleChange(userId: string, newRole: string) {
    if (!workspaceId) return;
    try {
      await unwrap(
        api.PATCH("/workspaces/{workspace_id}/members/{user_id}", {
          params: { path: { workspace_id: workspaceId, user_id: userId } },
          body: { role: newRole },
        })
      );
      await refreshMembers();
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "Failed to change role");
    }
  }

  async function handleRemove(userId: string) {
    if (!workspaceId) return;
    if (!confirm("Remove this member from the workspace?")) return;
    try {
      await unwrap(
        api.DELETE("/workspaces/{workspace_id}/members/{user_id}", {
          params: { path: { workspace_id: workspaceId, user_id: userId } },
        })
      );
      await refreshMembers();
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "Failed to remove member");
    }
  }

  async function handleRevoke(inviteId: string) {
    if (!workspaceId) return;
    try {
      await unwrap(
        api.DELETE("/workspaces/{workspace_id}/invites/{invite_id}", {
          params: { path: { workspace_id: workspaceId, invite_id: inviteId } },
        })
      );
      await refreshInvites();
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "Failed to revoke invite");
    }
  }

  if (workspaceLoading || !currentWorkspace) {
    return <p className="text-sm text-gray-500">Loading…</p>;
  }

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Team — {currentWorkspace.name}</h1>
        <button
          onClick={() => {
            setJustCreatedUrl(null);
            setDrawerOpen(true);
          }}
          className="rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-gray-800"
        >
          Invite someone
        </button>
      </div>

      <section className="space-y-2">
        <h2 className="text-sm font-medium text-gray-700">Members</h2>
        <DataTable
          rows={members ?? []}
          keyFn={(m) => m.user_id}
          emptyMessage="No members yet."
          columns={[
            { header: "Email", render: (m) => m.email },
            {
              header: "Role",
              render: (m) => (
                <select
                  value={m.role}
                  onChange={(e) => handleRoleChange(m.user_id, e.target.value)}
                  className="rounded-md border border-gray-300 px-2 py-1 text-xs"
                >
                  <option value="owner">Owner</option>
                  <option value="admin">Admin</option>
                  <option value="member">Member</option>
                </select>
              ),
            },
            {
              header: "",
              render: (m) => (
                <button onClick={() => handleRemove(m.user_id)} className="text-xs font-medium text-red-600 hover:underline">
                  Remove
                </button>
              ),
            },
          ]}
        />
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-medium text-gray-700">Pending invites</h2>
        <DataTable
          rows={invites ?? []}
          keyFn={(i) => i.id}
          emptyMessage="No pending invites."
          columns={[
            { header: "Email", render: (i) => i.email },
            { header: "Role", render: (i) => <Badge tone={ROLE_TONE[i.role as keyof typeof ROLE_TONE]}>{i.role}</Badge> },
            { header: "Expires", render: (i) => new Date(i.expires_at).toLocaleDateString() },
            {
              header: "",
              render: (i) => (
                <button onClick={() => handleRevoke(i.id)} className="text-xs font-medium text-red-600 hover:underline">
                  Revoke
                </button>
              ),
            },
          ]}
        />
      </section>

      <FormDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} title="Invite someone">
        {justCreatedUrl ? (
          <div className="space-y-3">
            <p className="text-sm text-gray-700">
              Invite created. Copy this link and send it to them yourself — there's no email sent automatically.
            </p>
            <input
              readOnly
              value={justCreatedUrl}
              onClick={(e) => e.currentTarget.select()}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-xs"
            />
            <button
              onClick={() => setDrawerOpen(false)}
              className="w-full rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800"
            >
              Done
            </button>
          </div>
        ) : (
          <form onSubmit={handleInvite} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700" htmlFor="invite-email">
                Email
              </label>
              <input
                id="invite-email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700" htmlFor="invite-role">
                Role
              </label>
              <select
                id="invite-role"
                value={role}
                onChange={(e) => setRole(e.target.value)}
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
              >
                <option value="member">Member</option>
                <option value="admin">Admin</option>
                <option value="owner">Owner</option>
              </select>
            </div>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <button
              type="submit"
              disabled={submitting}
              className="w-full rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
            >
              {submitting ? "Creating…" : "Create invite link"}
            </button>
          </form>
        )}
      </FormDrawer>
    </div>
  );
}
