-- Ratings/reviews (docs/01-architecture.md: "V1 marketplace: ... ratings + install
-- counts"; docs/05-frontend-marketplace.md §1's last deferred item). listings.rating_avg/
-- rating_count (0001_init_schema.sql) have existed since the beginning as aggregate
-- columns with nothing to aggregate -- this is the table of individual reviews that
-- actually feeds them.
--
-- One review per (listing, workspace), not per user: the same unit every other
-- marketplace interaction (install, "my listings") is scoped to, and it avoids a
-- listing being review-bombed by one workspace's several members voting the same
-- opinion five times. Editing a review is an upsert on that same unique pair, not a
-- new row.
create table listing_reviews (
  id uuid primary key default gen_random_uuid(),
  listing_id uuid not null references listings(id) on delete cascade,
  reviewing_workspace_id uuid not null references workspaces(id) on delete cascade,
  rating int not null check (rating between 1 and 5),
  comment text,
  created_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (listing_id, reviewing_workspace_id)
);

alter table listing_reviews enable row level security;

create policy "anyone can read reviews on a published listing" on listing_reviews for select
  using (exists (select 1 from listings l where l.id = listing_id and l.status = 'published'));

-- Reviewing is gated on having actually installed the listing (an `installs` row for
-- this exact (listing, workspace) pair) -- same "tried it first" bar a real review
-- should clear, not just anyone with an opinion and a workspace.
create policy "an installing workspace can review what it installed" on listing_reviews for insert
  with check (
    is_workspace_member(reviewing_workspace_id)
    and exists (
      select 1 from installs i
      where i.listing_id = listing_reviews.listing_id and i.installing_workspace_id = reviewing_workspace_id
    )
  );

create policy "a workspace can update its own review" on listing_reviews for update
  using (is_workspace_member(reviewing_workspace_id));
