"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";

import { api, unwrap, ApiError } from "@/lib/api";
import { useWorkspace } from "@/lib/useWorkspace";
import { DataTable } from "@/components/DataTable";
import { Badge } from "@/components/Badge";
import { PublishListingDrawer } from "@/components/marketplace/PublishListingDrawer";

type Listing = {
  id: string;
  kind: string;
  name: string;
  description: string | null;
  category: string | null;
  install_count: number;
  rating_avg: number | null;
  required_tool_categories: string[];
};

type MyListing = Listing & { status: string };

type InstallHistoryItem = {
  id: string;
  listing_id: string;
  listing_name: string;
  listing_kind: string;
  installed_agent_id: string | null;
  installed_flow_id: string | null;
  created_at: string;
};

export default function MarketplacePage() {
  const { currentWorkspace } = useWorkspace();
  const workspaceId = currentWorkspace?.id;

  const [view, setView] = useState<"browse" | "mine" | "installs">("browse");
  const [publishOpen, setPublishOpen] = useState(false);
  const [publishError, setPublishError] = useState<string | null>(null);

  const { data: listings, isLoading: browseLoading, mutate: mutateBrowse } = useSWR("/marketplace/listings", () =>
    unwrap(api.GET("/marketplace/listings"))
  );
  const { data: myListings, isLoading: mineLoading, mutate: mutateMine } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/listings` : null,
    () => unwrap(api.GET("/workspaces/{workspace_id}/listings", { params: { path: { workspace_id: workspaceId! } } }))
  );
  const { data: agents } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/agents` : null,
    () => unwrap(api.GET("/workspaces/{workspace_id}/agents", { params: { path: { workspace_id: workspaceId! } } }))
  );
  const { data: flows } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/flows` : null,
    () => unwrap(api.GET("/workspaces/{workspace_id}/flows", { params: { path: { workspace_id: workspaceId! } } }))
  );
  const { data: installs, isLoading: installsLoading } = useSWR(
    workspaceId ? `/workspaces/${workspaceId}/marketplace/installs` : null,
    () =>
      unwrap(
        api.GET("/workspaces/{workspace_id}/marketplace/installs", {
          params: { path: { workspace_id: workspaceId! } },
        })
      )
  );

  // Publishing (first time or a republish) changes what Browse should show
  // too -- a draft becoming published, or a listing's required_tool_categories
  // changing -- so every publish path revalidates both SWR caches, not just
  // My listings' own.
  async function refreshListings() {
    await Promise.all([mutateMine(), mutateBrowse()]);
  }

  async function handleRepublish(listingId: string) {
    if (!workspaceId) return;
    setPublishError(null);
    try {
      await unwrap(
        api.POST("/workspaces/{workspace_id}/listings/{listing_id}/versions", {
          params: { path: { workspace_id: workspaceId, listing_id: listingId } },
        })
      );
      await refreshListings();
    } catch (e) {
      setPublishError(e instanceof ApiError ? e.message : "Failed to publish a new version");
    }
  }

  const listingColumns = [
    {
      header: "Name",
      render: (l: Listing) => (
        <Link href={`/marketplace/${l.id}`} className="font-medium text-blue-600 hover:underline">
          {l.name}
        </Link>
      ),
    },
    { header: "Kind", render: (l: Listing) => <Badge tone={l.kind === "flow" ? "blue" : "gray"}>{l.kind}</Badge> },
    { header: "Category", render: (l: Listing) => l.category ?? <span className="text-gray-400">—</span> },
    { header: "Installs", render: (l: Listing) => l.install_count },
    {
      header: "Rating",
      render: (l: Listing) => (l.rating_avg != null ? l.rating_avg.toFixed(1) : <span className="text-gray-400">—</span>),
    },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Marketplace</h1>
        {workspaceId && (
          <button
            onClick={() => setPublishOpen(true)}
            className="rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-gray-800"
          >
            New listing
          </button>
        )}
      </div>

      <div className="flex gap-2 text-sm">
        <button
          onClick={() => setView("browse")}
          className={`rounded-md border px-3 py-1 ${
            view === "browse" ? "border-gray-900 bg-gray-900 text-white" : "border-gray-300 text-gray-700"
          }`}
        >
          Browse
        </button>
        <button
          onClick={() => setView("mine")}
          className={`rounded-md border px-3 py-1 ${
            view === "mine" ? "border-gray-900 bg-gray-900 text-white" : "border-gray-300 text-gray-700"
          }`}
        >
          My listings
        </button>
        <button
          onClick={() => setView("installs")}
          className={`rounded-md border px-3 py-1 ${
            view === "installs" ? "border-gray-900 bg-gray-900 text-white" : "border-gray-300 text-gray-700"
          }`}
        >
          My installs
        </button>
      </div>

      {publishError && <p className="text-sm text-red-600">{publishError}</p>}

      {view === "browse" ? (
        browseLoading ? (
          <p className="text-sm text-gray-500">Loading…</p>
        ) : (
          <DataTable<Listing>
            rows={listings ?? []}
            keyFn={(l) => l.id}
            emptyMessage="No published listings yet — be the first to publish one from My listings."
            columns={listingColumns}
          />
        )
      ) : !workspaceId ? (
        <p className="text-sm text-gray-500">Pick or create a workspace first.</p>
      ) : view === "installs" ? (
        installsLoading ? (
          <p className="text-sm text-gray-500">Loading…</p>
        ) : (
          <DataTable<InstallHistoryItem>
            rows={installs ?? []}
            keyFn={(i) => i.id}
            emptyMessage="Nothing installed into this workspace yet — install something from Browse to get started."
            columns={[
              {
                header: "Listing",
                render: (i) => (
                  <Link href={`/marketplace/${i.listing_id}`} className="font-medium text-blue-600 hover:underline">
                    {i.listing_name}
                  </Link>
                ),
              },
              {
                header: "Kind",
                render: (i) => <Badge tone={i.listing_kind === "flow" ? "blue" : "gray"}>{i.listing_kind}</Badge>,
              },
              {
                header: "Installed as",
                render: (i) => (
                  <Link
                    href={i.installed_agent_id ? `/agents/${i.installed_agent_id}` : `/flows/${i.installed_flow_id}`}
                    className="font-medium text-blue-600 hover:underline"
                  >
                    open it
                  </Link>
                ),
              },
              { header: "Installed", render: (i) => new Date(i.created_at).toLocaleString() },
            ]}
          />
        )
      ) : mineLoading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : (
        <DataTable<MyListing>
          rows={myListings ?? []}
          keyFn={(l) => l.id}
          emptyMessage="You haven't published anything yet — publish an agent or flow to get started."
          columns={[
            {
              header: "Name",
              render: (l) =>
                // A draft has no published version to show -- the detail
                // page is driven by the public GET, which only ever returns
                // status = 'published' rows (listings.py), so linking to it
                // here would just 404.
                l.status === "published" ? (
                  <Link href={`/marketplace/${l.id}`} className="font-medium text-blue-600 hover:underline">
                    {l.name}
                  </Link>
                ) : (
                  <span className="font-medium text-gray-700">{l.name}</span>
                ),
            },
            ...listingColumns.slice(1),
            { header: "Status", render: (l) => <Badge tone={l.status === "published" ? "green" : "gray"}>{l.status}</Badge> },
            {
              header: "",
              render: (l) =>
                l.status === "published" ? (
                  <button onClick={() => handleRepublish(l.id)} className="text-xs font-medium text-blue-600 hover:underline">
                    Publish new version
                  </button>
                ) : (
                  <button onClick={() => handleRepublish(l.id)} className="text-xs font-medium text-blue-600 hover:underline">
                    Publish
                  </button>
                ),
            },
          ]}
        />
      )}

      {workspaceId && agents && flows && (
        <PublishListingDrawer
          open={publishOpen}
          onClose={() => setPublishOpen(false)}
          workspaceId={workspaceId}
          agents={agents}
          flows={flows}
          onPublished={() => refreshListings()}
        />
      )}
    </div>
  );
}
