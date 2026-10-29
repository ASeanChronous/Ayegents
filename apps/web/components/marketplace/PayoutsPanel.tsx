"use client";

/** Stripe Connect status + onboarding entry point (docs/05-frontend-marketplace.md §11) --
 * shown above "My listings" since that's exactly where payouts status bites: a listing
 * can't be priced (create/update 400s) until this shows payouts_enabled. Three states:
 * not connected ("Connect with Stripe"), connected but not yet finished onboarding
 * ("Finish Stripe onboarding" -- same button, a fresh Account Link either way), and
 * payouts enabled (a small confirmation, no button). */
import { useState } from "react";
import useSWR from "swr";

import { api, unwrap, ApiError } from "@/lib/api";

export function PayoutsPanel({ workspaceId }: { workspaceId: string }) {
  // No special handling for a ?payouts=return redirect-back beyond this:
  // SWR revalidates on mount by default, and the status endpoint itself
  // self-heals with a live Stripe lookup when not yet enabled (payments.py's
  // own docstring) -- no extra polling or query-param plumbing needed here.
  const { data: status } = useSWR(
    `/workspaces/${workspaceId}/payouts/status`,
    () => unwrap(api.GET("/workspaces/{workspace_id}/payouts/status", { params: { path: { workspace_id: workspaceId } } }))
  );

  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleConnect() {
    setConnecting(true);
    setError(null);
    try {
      const { onboarding_url } = await unwrap(
        api.POST("/workspaces/{workspace_id}/payouts/connect-onboarding", {
          params: { path: { workspace_id: workspaceId } },
        })
      );
      window.location.href = onboarding_url;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to start payout onboarding");
      setConnecting(false);
    }
  }

  if (!status) return null;

  if (status.payouts_enabled) {
    return (
      <p className="text-xs text-green-700">
        ✓ Payouts connected — your paid listings' share of each sale goes to your Stripe account automatically.
      </p>
    );
  }

  return (
    <div className="space-y-2 rounded-md border border-gray-200 bg-gray-50 p-3 text-sm">
      <p className="text-gray-700">
        {status.connected
          ? "Finish connecting Stripe to enable payouts before pricing a listing."
          : "Connect a Stripe payout account before you can price a listing — free listings don't need this."}
      </p>
      {error && <p className="text-xs text-red-600">{error}</p>}
      <button
        onClick={handleConnect}
        disabled={connecting}
        className="rounded-md bg-gray-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-gray-800 disabled:opacity-50"
      >
        {connecting ? "Redirecting to Stripe…" : status.connected ? "Finish Stripe onboarding" : "Connect with Stripe"}
      </button>
    </div>
  );
}
