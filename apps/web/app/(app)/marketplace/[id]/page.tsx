"use client";

/** Listing detail + install-with-consent (docs/05-frontend-marketplace.md §3): every
 * required tool category is its own checkbox, unchecked by default -- "Install" stays
 * disabled until all are checked. This is a client-side nicety only; the API's 403 (missing
 * categories) is the real enforcement, this just saves the round trip a partial approval
 * would otherwise need to fail first. A listing with no required categories skips the
 * checklist and is installable immediately.
 *
 * A priced listing (price_cents > 0) gates this whole panel behind a completed Stripe
 * Checkout purchase (routers/payments.py) -- see PurchaseGate below. A free listing (the
 * overwhelming majority, and the only kind that existed before this) skips it entirely. */
import { useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import useSWR from "swr";

import { api, unwrap, ApiError } from "@/lib/api";
import { useWorkspace } from "@/lib/useWorkspace";
import { Badge } from "@/components/Badge";
import { ReviewsSection } from "@/components/marketplace/ReviewsSection";

function formatPrice(priceCents: number, currency: string) {
  if (priceCents === 0) return "Free";
  return (priceCents / 100).toLocaleString(undefined, { style: "currency", currency: currency.toUpperCase() });
}

export default function ListingDetailPage() {
  const { id: listingId } = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const { currentWorkspace } = useWorkspace();
  const workspaceId = currentWorkspace?.id;

  const { data: listing, error: listingError, mutate: mutateListing } = useSWR(
    `/marketplace/listings/${listingId}`,
    () => unwrap(api.GET("/marketplace/listings/{listing_id}", { params: { path: { listing_id: listingId } } }))
  );

  const [approved, setApproved] = useState<Set<string>>(new Set());
  const [installing, setInstalling] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);
  const [installResult, setInstallResult] = useState<{ installed_agent_id: string | null; installed_flow_id: string | null } | null>(
    null
  );

  const isPaidListing = (listing?.price_cents ?? 0) > 0;
  // Right after a Checkout redirect-back, the webhook that actually flips the
  // purchase to 'paid' races the browser's own redirect -- so while
  // ?purchase=success is in the URL and the purchase isn't confirmed paid
  // yet, poll every 1.5s instead of trusting the query param alone.
  const justReturnedFromCheckout = searchParams.get("purchase") === "success";
  const { data: purchase } = useSWR(
    workspaceId && listingId && isPaidListing
      ? `/workspaces/${workspaceId}/marketplace/listings/${listingId}/purchase`
      : null,
    () =>
      unwrap(
        api.GET("/workspaces/{workspace_id}/marketplace/listings/{listing_id}/purchase", {
          params: { path: { workspace_id: workspaceId!, listing_id: listingId } },
        })
      ),
    { refreshInterval: (latest) => (justReturnedFromCheckout && !latest?.paid ? 1500 : 0) }
  );

  const [checkingOut, setCheckingOut] = useState(false);
  const [checkoutError, setCheckoutError] = useState<string | null>(null);

  async function handleBuy() {
    if (!workspaceId || !listing) return;
    setCheckingOut(true);
    setCheckoutError(null);
    try {
      const { checkout_url } = await unwrap(
        api.POST("/workspaces/{workspace_id}/marketplace/listings/{listing_id}/checkout", {
          params: { path: { workspace_id: workspaceId, listing_id: listing.id } },
        })
      );
      window.location.href = checkout_url;
    } catch (e) {
      setCheckoutError(e instanceof ApiError ? e.message : "Failed to start checkout");
      setCheckingOut(false);
    }
  }

  function toggleCategory(category: string) {
    setApproved((prev) => {
      const next = new Set(prev);
      if (next.has(category)) {
        next.delete(category);
      } else {
        next.add(category);
      }
      return next;
    });
  }

  async function handleInstall() {
    if (!workspaceId || !listing) return;
    setInstalling(true);
    setInstallError(null);
    try {
      const result = await unwrap(
        api.POST("/workspaces/{workspace_id}/marketplace/installs", {
          params: { path: { workspace_id: workspaceId } },
          body: { listing_id: listing.id, approved_tool_categories: Array.from(approved) },
        })
      );
      setInstallResult(result);
    } catch (e) {
      setInstallError(e instanceof ApiError ? e.message : "Failed to install");
    } finally {
      setInstalling(false);
    }
  }

  if (listingError) {
    return <p className="text-sm text-gray-500">Listing not found, or not published.</p>;
  }
  if (!listing) {
    return <p className="text-sm text-gray-500">Loading…</p>;
  }

  const required = listing.required_tool_categories;
  const allApproved = required.every((c) => approved.has(c));

  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center gap-2">
          <h1 className="text-2xl font-semibold">{listing.name}</h1>
          <Badge tone={listing.kind === "flow" ? "blue" : "gray"}>{listing.kind}</Badge>
        </div>
        {listing.description && <p className="mt-1 text-gray-600">{listing.description}</p>}
        <dl className="mt-3 flex gap-6 text-sm text-gray-500">
          {listing.category && (
            <div>
              <dt className="inline">Category: </dt>
              <dd className="inline">{listing.category}</dd>
            </div>
          )}
          <div>
            <dt className="inline">Price: </dt>
            <dd className="inline font-medium text-gray-700">{formatPrice(listing.price_cents, listing.currency)}</dd>
          </div>
          <div>
            <dt className="inline">Installs: </dt>
            <dd className="inline">{listing.install_count}</dd>
          </div>
          <div>
            <dt className="inline">Rating: </dt>
            <dd className="inline">
              {listing.rating_avg != null
                ? `${listing.rating_avg.toFixed(1)} (${listing.rating_count} review${listing.rating_count === 1 ? "" : "s"})`
                : "No reviews yet"}
            </dd>
          </div>
        </dl>
      </div>

      {!workspaceId ? (
        <p className="text-sm text-gray-500">Pick or create a workspace first.</p>
      ) : installResult ? (
        <div className="rounded-md border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
          Installed into <span className="font-medium">{currentWorkspace?.name}</span> —{" "}
          <Link
            href={installResult.installed_agent_id ? `/agents/${installResult.installed_agent_id}` : `/flows/${installResult.installed_flow_id}`}
            className="font-medium text-blue-700 hover:underline"
          >
            open it
          </Link>
          .
        </div>
      ) : isPaidListing && !purchase?.paid ? (
        <div className="space-y-3 rounded-md border border-gray-200 p-4">
          {searchParams.get("purchase") === "cancelled" && (
            <p className="text-sm text-gray-500">Checkout cancelled — no charge was made.</p>
          )}
          {justReturnedFromCheckout ? (
            <p className="text-sm text-gray-500">Confirming your payment…</p>
          ) : (
            <>
              <h2 className="text-sm font-medium text-gray-700">
                Buy this listing to install it into {currentWorkspace?.name ?? "this workspace"}
              </h2>
              {checkoutError && <p className="text-sm text-red-600">{checkoutError}</p>}
              <button
                onClick={handleBuy}
                disabled={checkingOut}
                className="rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
              >
                {checkingOut ? "Redirecting to checkout…" : `Buy for ${formatPrice(listing.price_cents, listing.currency)}`}
              </button>
            </>
          )}
        </div>
      ) : (
        <div className="space-y-3 rounded-md border border-gray-200 p-4">
          <h2 className="text-sm font-medium text-gray-700">
            {required.length > 0 ? "Approve what this needs, then install" : "Nothing to approve — ready to install"}
          </h2>
          {required.length > 0 && (
            <ul className="space-y-2">
              {required.map((category) => (
                <li key={category} className="flex items-center gap-2 text-sm">
                  <input
                    id={`cat-${category}`}
                    type="checkbox"
                    checked={approved.has(category)}
                    onChange={() => toggleCategory(category)}
                  />
                  <label htmlFor={`cat-${category}`} className="font-mono text-xs text-gray-700">
                    {category}
                  </label>
                </li>
              ))}
            </ul>
          )}
          {installError && <p className="text-sm text-red-600">{installError}</p>}
          <button
            onClick={handleInstall}
            disabled={installing || !allApproved}
            className="rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
          >
            {installing ? "Installing…" : `Install into ${currentWorkspace?.name ?? "this workspace"}`}
          </button>
        </div>
      )}

      <ReviewsSection listingId={listing.id} workspaceId={workspaceId} onReviewed={() => mutateListing()} />
    </div>
  );
}
