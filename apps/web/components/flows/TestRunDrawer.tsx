"use client";

/** A true draft dry-run (docs/04-frontend-builder.md §10): runs the canvas's
 * current nodes/edges exactly as they are right now, without saving a version
 * first. Sends the spec as `draft_spec` on the ordinary POST .../runs call
 * (services/api/app/routers/runs.py) rather than a separate endpoint -- it's
 * still "start a run," just one that skips flow_versions entirely. The
 * resulting run is a real run (real tokens, real tool calls, shows up in the
 * Runs list and this flow's own history) -- the only thing unsaved is the
 * spec it executed, not the run itself. */
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { api, unwrap, ApiError } from "@/lib/api";
import { FormDrawer } from "@/components/FormDrawer";
import type { FlowSpec } from "@/lib/flowSpec";

export function TestRunDrawer({
  open,
  onClose,
  workspaceId,
  flowId,
  spec,
}: {
  open: boolean;
  onClose: () => void;
  workspaceId: string;
  flowId: string;
  spec: FlowSpec | null;
}) {
  const router = useRouter();
  const [input, setInput] = useState("{}");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!spec) return;
    setError(null);

    let parsedInput: Record<string, unknown>;
    try {
      parsedInput = JSON.parse(input || "{}");
    } catch {
      setError("Input must be valid JSON");
      return;
    }

    setSubmitting(true);
    try {
      const run = await unwrap(
        api.POST("/workspaces/{workspace_id}/runs", {
          params: { path: { workspace_id: workspaceId } },
          body: { flow_id: flowId, draft_spec: spec, input: parsedInput },
        })
      );
      onClose();
      router.push(`/runs/${run.id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to start test run");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <FormDrawer open={open} onClose={onClose} title="Test run">
      <form onSubmit={handleSubmit} className="space-y-4">
        <p className="text-sm text-gray-500">
          Runs the canvas exactly as it is right now — including anything not saved as a version yet. Costs real
          tokens and makes real tool calls just like any other run; it just isn&apos;t tied to this flow&apos;s
          saved version history.
        </p>
        <div>
          <label className="block text-sm font-medium text-gray-700" htmlFor="test-run-input">
            Input (JSON)
          </label>
          <textarea
            id="test-run-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            rows={4}
            className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-sm focus:border-gray-500 focus:outline-none"
          />
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button
          type="submit"
          disabled={submitting || !spec}
          className="w-full rounded-md bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
        >
          {submitting ? "Starting…" : "Run now"}
        </button>
      </form>
    </FormDrawer>
  );
}
