import { createClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

if (!url || !anonKey) {
  throw new Error(
    "NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY must be set (see .env.example)"
  );
}

// Single browser client for the whole app — Supabase's own client already
// handles session persistence (localStorage) and token refresh; no extra
// session/cookie layer needed on top of it for this client-side-only phase
// (see docs/03-frontend.md §2).
export const supabase = createClient(url, anonKey);
