"use client";

/** Edit a listing's name/description/category (docs/05-frontend-marketplace.md §1's
 * last deferred item) -- deliberately not the same form as PublishListingDrawer: this
 * only ever touches listings.name/description/category via PATCH, never the listing's
 * source agent/flow or its spec, which stay fixed once a listing exists. */
import { FormEvent, useState } from "react";

import { api, unwrap, ApiError } from "@/lib/api";
import { FormDrawer } from "@/components/FormDrawer";

type EditableListing = {
  id: string;
  name: string;
  description: string | null;
  category: string | null;
};

export function EditListingDrawer({
  open,
  onClose,
  workspaceId,
  listing,
  onUpdated,
}: {
  open: boolean;
  onClose: () => void;
  workspaceId: string;
  listing: EditableListing | null;
  onUpdated: () => void;
}) {
  return (
    <FormDrawer open={open} onClose={onClose} title="Edit listing">
      {listing && (
        // Keyed by listing.id so switching which row is being edited remounts
        // this form with fresh state, instead of a second edit in the same
        // session inheriting the first listing's leftover field values.
        <EditForm key={listing.id} listing={listing} workspaceId={workspaceId} onUpdated={onUpdated} onClose={onClose} />
      )}
    </FormDrawer>
  );
}

function EditForm({
  listing,
  workspaceId,
  onUpdated,
  onClose,
}: {
  listing: EditableListing;
  workspaceId: string;
  onUpdated: () => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(listing.name);
  const [description, setDescription] = useState(listing.description ?? "");
  const [category, setCategory] = useState(listing.category ?? "");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await unwrap(
        api.PATCH("/workspaces/{workspace_id}/listings/{listing_id}", {
          params: { path: { workspace_id: workspaceId, listing_id: listing.id } },
          body: { name, description: description || null, category: category || null },
        })
      );
      onUpdated();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to update listing");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-gray-700" htmlFor="edit-listing-name">
          Name
        </label>
        <input
          id="edit-listing-name"
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
        />
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700" htmlFor="edit-listing-description">
          Description
        </label>
        <textarea
          id="edit-listing-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={3}
          className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
        />
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700" htmlFor="edit-listing-category">
          Category
        </label>
        <input
          id="edit-listing-category"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          placeholder="optional"
          className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-gray-500 focus:outline-none"
        />
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <button
        type="submit"
        disabled={submitting || !name}
        className="w-full rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
      >
        {submitting ? "Saving…" : "Save changes"}
      </button>
    </form>
  );
}
