"use client";

/** Create-and-publish a listing in one step (docs/05-frontend-marketplace.md §5): a listing
 * with no published version is useless to browse, so this never exposes "create" and
 * "publish" as two separate user-facing steps the way marketplace.py's own two endpoints
 * might suggest -- it calls POST .../listings immediately followed by POST
 * .../listings/{id}/versions in the same submit. Publish-time validation (no agent-as-tool
 * sub-agents, no workspace-private tools -- marketplace.py's own docstring) only happens on
 * the second call, so a listing can be validly created and still fail to publish; both
 * calls' errors need to reach this form, not just the first. */
import { FormEvent, useState } from "react";

import { api, unwrap, ApiError } from "@/lib/api";
import { FormDrawer } from "@/components/FormDrawer";

type Agent = { id: string; name: string };
type Flow = { id: string; name: string };

export function PublishListingDrawer({
  open,
  onClose,
  workspaceId,
  agents,
  flows,
  onPublished,
}: {
  open: boolean;
  onClose: () => void;
  workspaceId: string;
  agents: Agent[];
  flows: Flow[];
  onPublished: () => void;
}) {
  const [sourceType, setSourceType] = useState<"agent" | "flow">(agents.length > 0 ? "agent" : "flow");
  const [agentId, setAgentId] = useState("");
  const [flowId, setFlowId] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("");
  // Dollars, as the user types them -- converted to price_cents only at
  // submit time, so "4.9" mid-type isn't forced through a cents conversion
  // on every keystroke. Blank (the default) means free.
  const [priceDollars, setPriceDollars] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const listing = await unwrap(
        api.POST("/workspaces/{workspace_id}/listings", {
          params: { path: { workspace_id: workspaceId } },
          body: {
            ...(sourceType === "agent" ? { source_agent_id: agentId } : { source_flow_id: flowId }),
            name,
            description: description || null,
            category: category || null,
            price_cents: priceDollars.trim() ? Math.round(parseFloat(priceDollars) * 100) : 0,
          },
        })
      );
      try {
        await unwrap(
          api.POST("/workspaces/{workspace_id}/listings/{listing_id}/versions", {
            params: { path: { workspace_id: workspaceId, listing_id: listing.id } },
          })
        );
      } catch (e) {
        // The listing itself was created (it'll show up in My listings as a
        // draft) -- only publishing failed, so say that specifically rather
        // than a generic "failed to publish listing" that reads like nothing
        // happened at all.
        setError(
          `Listing created, but publishing failed: ${e instanceof ApiError ? e.message : "unknown error"}. Fix the issue and publish again from My listings.`
        );
        onPublished();
        return;
      }
      onPublished();
      setName("");
      setDescription("");
      setCategory("");
      setPriceDollars("");
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to create listing");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <FormDrawer open={open} onClose={onClose} title="New listing">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <span className="block text-sm font-medium text-gray-700">Publish</span>
          <div className="mt-1 flex gap-2 text-sm">
            <button
              type="button"
              onClick={() => setSourceType("agent")}
              className={`flex-1 rounded-md border px-2 py-1 ${
                sourceType === "agent" ? "border-gray-900 bg-gray-900 text-white" : "border-gray-300 text-gray-700"
              }`}
            >
              An agent
            </button>
            <button
              type="button"
              onClick={() => setSourceType("flow")}
              className={`flex-1 rounded-md border px-2 py-1 ${
                sourceType === "flow" ? "border-gray-900 bg-gray-900 text-white" : "border-gray-300 text-gray-700"
              }`}
            >
              A flow
            </button>
          </div>
        </div>

        {sourceType === "agent" ? (
          <div>
            <label className="block text-sm font-medium text-gray-700" htmlFor="listing-agent">
              Agent
            </label>
            {agents.length === 0 ? (
              <p className="mt-1 text-sm text-gray-500">No agents yet — create one on the Agents page first.</p>
            ) : (
              <select
                id="listing-agent"
                required
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
              >
                <option value="" disabled>
                  Select an agent…
                </option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            )}
          </div>
        ) : (
          <div>
            <label className="block text-sm font-medium text-gray-700" htmlFor="listing-flow">
              Flow
            </label>
            {flows.length === 0 ? (
              <p className="mt-1 text-sm text-gray-500">No flows yet — create one on the Flows page first.</p>
            ) : (
              <select
                id="listing-flow"
                required
                value={flowId}
                onChange={(e) => setFlowId(e.target.value)}
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
              >
                <option value="" disabled>
                  Select a flow…
                </option>
                {flows.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name}
                  </option>
                ))}
              </select>
            )}
          </div>
        )}

        <div>
          <label className="block text-sm font-medium text-gray-700" htmlFor="listing-name">
            Name
          </label>
          <input
            id="listing-name"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700" htmlFor="listing-description">
            Description
          </label>
          <textarea
            id="listing-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700" htmlFor="listing-category">
            Category
          </label>
          <input
            id="listing-category"
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="optional"
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700" htmlFor="listing-price">
            Price (USD)
          </label>
          <input
            id="listing-price"
            type="number"
            min="0"
            step="0.01"
            value={priceDollars}
            onChange={(e) => setPriceDollars(e.target.value)}
            placeholder="0.00 — leave blank for free"
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
          />
          <p className="mt-1 text-xs text-gray-400">
            Paid via Stripe Checkout at install time. No payouts yet — proceeds go to the platform account.
          </p>
        </div>

        {error && <p className="text-sm text-red-600">{error}</p>}
        <button
          type="submit"
          disabled={submitting || (sourceType === "agent" ? !agentId : !flowId)}
          className="w-full rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
        >
          {submitting ? "Publishing…" : "Publish"}
        </button>
      </form>
    </FormDrawer>
  );
}
