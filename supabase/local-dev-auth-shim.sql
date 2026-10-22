-- LOCAL DEV ONLY. Plain Postgres (e.g. this Docker container) doesn't have
-- Supabase's `auth` schema. A real Supabase project already provides
-- auth.users and auth.uid() — never run this against one.
--
-- auth.uid() here reads a session-local setting instead of a JWT claim, so
-- tests can impersonate a user with `select set_config('app.current_user_id', '<uuid>', false);`

create extension if not exists pgcrypto;

create schema if not exists auth;

create table if not exists auth.users (
  id uuid primary key default gen_random_uuid(),
  email text
);

create or replace function auth.uid() returns uuid
language sql stable as $$
  select nullif(current_setting('app.current_user_id', true), '')::uuid
$$;
