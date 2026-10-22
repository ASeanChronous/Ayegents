-- LOCAL DEV ONLY. Plain Postgres (e.g. this Docker container) doesn't have
-- Supabase's `auth` schema. A real Supabase project already provides
-- auth.users and auth.uid() — never run this against one.
--
-- auth.uid() here mirrors real Supabase's own implementation (reads the
-- 'request.jwt.claim.sub' session setting) rather than inventing a different
-- convention, so code written against this shim behaves the same way
-- against a real Supabase project. Impersonate a user with:
--   select set_config('request.jwt.claim.sub', '<uuid>', true);

create extension if not exists pgcrypto;

create schema if not exists auth;

create table if not exists auth.users (
  id uuid primary key default gen_random_uuid(),
  email text
);

create or replace function auth.uid() returns uuid
language sql stable as $$
  select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
$$;
