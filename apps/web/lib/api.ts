import createClient from "openapi-fetch";

import type { paths } from "./api-types";
import { supabase } from "./supabaseClient";

const baseUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const client = createClient<paths>({ baseUrl });

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// openapi-fetch's `use()` middleware runs on every request/response —
// attaching the current Supabase session's access token here means every
// call site (lib/api.ts consumers) gets auth for free, instead of each one
// re-deriving it from useSession().
client.use({
  async onRequest({ request }) {
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (session?.access_token) {
      request.headers.set("Authorization", `Bearer ${session.access_token}`);
    }
    return request;
  },
});

/** Unwraps an openapi-fetch call, throwing ApiError on failure instead of
 * making every call site check `error` separately. Takes the call's Promise
 * directly (`unwrap(api.GET(...))`) rather than requiring `await` at every
 * call site first. */
export async function unwrap<T>(
  resultPromise: Promise<{ data?: T; error?: unknown; response: Response }>
): Promise<T> {
  const result = await resultPromise;
  if (result.error !== undefined) {
    const detail =
      typeof result.error === "object" && result.error !== null && "detail" in result.error
        ? String((result.error as { detail: unknown }).detail)
        : result.response.statusText;
    throw new ApiError(result.response.status, detail);
  }
  if (result.data === undefined) {
    throw new ApiError(result.response.status, "empty response body");
  }
  return result.data;
}

export const api = client;
