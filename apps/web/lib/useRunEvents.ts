"use client";

import { useEffect, useRef } from "react";

import { supabase } from "@/lib/supabaseClient";

const baseUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * Subscribes to a run's SSE stream (GET .../runs/{id}/events,
 * services/api/app/routers/runs.py) and calls onUpdate on every "update"
 * event, replacing the run inspector's old fixed-interval SWR polling
 * (docs/03-frontend.md §6). Native EventSource can't attach an Authorization
 * header, and this API has no cookie-based session (every other call site
 * attaches the Supabase access token as a bearer header, lib/api.ts) -- so
 * this reads the stream by hand via fetch's ReadableStream instead of
 * EventSource.
 *
 * Reconnects on any drop (network blip, proxy timeout) rather than leaving
 * the inspector stuck showing stale data with nothing retrying; stops for
 * good once the stream sends its final update for a terminal run status, or
 * once the component unmounts.
 */
export function useRunEvents(
  workspaceId: string | undefined,
  runId: string | undefined,
  onUpdate: () => void
) {
  const onUpdateRef = useRef(onUpdate);
  onUpdateRef.current = onUpdate;

  useEffect(() => {
    if (!workspaceId || !runId) return;

    const controller = new AbortController();
    let stopped = false;

    async function connectOnce() {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      const res = await fetch(`${baseUrl}/workspaces/${workspaceId}/runs/${runId}/events`, {
        headers: session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {},
        signal: controller.signal,
      });
      if (!res.ok || !res.body) throw new Error(`events stream failed: ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let sawTerminal = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() ?? "";
        for (const chunk of chunks) {
          if (!chunk.startsWith("event: update")) continue;
          onUpdateRef.current();
          const dataLine = chunk.split("\n").find((line) => line.startsWith("data: "));
          const payload = dataLine ? JSON.parse(dataLine.slice("data: ".length)) : null;
          if (payload?.status && ["succeeded", "failed", "budget_exceeded"].includes(payload.status)) {
            sawTerminal = true;
          }
        }
      }
      return sawTerminal;
    }

    async function run() {
      while (!stopped) {
        try {
          const sawTerminal = await connectOnce();
          if (sawTerminal || stopped) return;
        } catch {
          if (controller.signal.aborted) return;
        }
        await new Promise((resolve) => setTimeout(resolve, 2000));
      }
    }

    run();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [workspaceId, runId]);
}
