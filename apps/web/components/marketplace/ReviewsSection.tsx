"use client";

/** Ratings/reviews (docs/05-frontend-marketplace.md §1's last deferred item): the public
 * list of every review left on a listing, plus, for a workspace that has installed it, a
 * form to leave or edit its own one-per-workspace review. The 403 a workspace that hasn't
 * installed yet gets back from the API is the real "install it first" enforcement -- this
 * component still shows the form to everyone with a workspace selected rather than trying
 * to pre-derive install status itself, and just surfaces that error if it comes back. */
import { useEffect, useState } from "react";
import useSWR from "swr";

import { api, unwrap, ApiError } from "@/lib/api";

type Review = {
  id: string;
  reviewing_workspace_id: string;
  reviewing_workspace_name: string;
  rating: number;
  comment: string | null;
  created_at: string;
  updated_at: string;
};

function Stars({ rating }: { rating: number }) {
  return (
    <span className="text-amber-500" aria-label={`${rating} out of 5 stars`}>
      {"★".repeat(rating)}
      <span className="text-gray-300">{"★".repeat(5 - rating)}</span>
    </span>
  );
}

export function ReviewsSection({
  listingId,
  workspaceId,
  onReviewed,
}: {
  listingId: string;
  workspaceId: string | undefined;
  onReviewed: () => void;
}) {
  const { data: reviews, mutate: mutateReviews } = useSWR(`/marketplace/listings/${listingId}/reviews`, () =>
    unwrap(api.GET("/marketplace/listings/{listing_id}/reviews", { params: { path: { listing_id: listingId } } }))
  );
  const { data: myReview, mutate: mutateMyReview } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/marketplace/listings/${listingId}/review` : null,
    () =>
      unwrap(
        api.GET("/workspaces/{workspace_id}/marketplace/listings/{listing_id}/review", {
          params: { path: { workspace_id: workspaceId!, listing_id: listingId } },
        })
      )
  );

  const [rating, setRating] = useState(5);
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Prefill from the workspace's existing review once it loads, so re-opening
  // this page to edit a review doesn't mean retyping it from scratch.
  useEffect(() => {
    if (myReview) {
      setRating(myReview.rating);
      setComment(myReview.comment ?? "");
    }
  }, [myReview]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!workspaceId) return;
    setSubmitting(true);
    setError(null);
    try {
      await unwrap(
        api.PUT("/workspaces/{workspace_id}/marketplace/listings/{listing_id}/review", {
          params: { path: { workspace_id: workspaceId, listing_id: listingId } },
          body: { rating, comment: comment.trim() || null },
        })
      );
      await Promise.all([mutateMyReview(), mutateReviews()]);
      onReviewed();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to submit your review");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="space-y-4 border-t border-gray-200 pt-6">
      <h2 className="text-sm font-medium text-gray-700">Reviews</h2>

      {!reviews ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : reviews.length === 0 ? (
        <p className="text-sm text-gray-500">No reviews yet.</p>
      ) : (
        <ul className="space-y-3">
          {reviews.map((r: Review) => (
            <li key={r.id} className="rounded-md border border-gray-200 p-3 text-sm">
              <div className="flex items-center justify-between">
                <span className="font-medium text-gray-700">{r.reviewing_workspace_name}</span>
                <Stars rating={r.rating} />
              </div>
              {r.comment && <p className="mt-1 text-gray-600">{r.comment}</p>}
              <p className="mt-1 text-xs text-gray-400">
                {new Date(r.updated_at).toLocaleDateString()}
                {r.updated_at !== r.created_at ? " (edited)" : ""}
              </p>
            </li>
          ))}
        </ul>
      )}

      {workspaceId && (
        <form onSubmit={handleSubmit} className="space-y-2 rounded-md border border-gray-200 p-4">
          <h3 className="text-sm font-medium text-gray-700">
            {myReview ? "Edit your review" : "Leave a review"}
          </h3>
          <div className="flex items-center gap-2">
            <label htmlFor="review-rating" className="text-sm text-gray-600">
              Rating
            </label>
            <select
              id="review-rating"
              value={rating}
              onChange={(e) => setRating(Number(e.target.value))}
              className="rounded-md border border-gray-300 px-2 py-1 text-sm"
            >
              {[5, 4, 3, 2, 1].map((n) => (
                <option key={n} value={n}>
                  {n} star{n === 1 ? "" : "s"}
                </option>
              ))}
            </select>
          </div>
          <textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Optional comment"
            className="w-full rounded-md border border-gray-300 p-2 text-sm"
            rows={2}
          />
          {error && <p className="text-sm text-red-600">{error}</p>}
          <button
            type="submit"
            disabled={submitting}
            className="rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
          >
            {submitting ? "Saving…" : myReview ? "Update review" : "Submit review"}
          </button>
        </form>
      )}
    </section>
  );
}
