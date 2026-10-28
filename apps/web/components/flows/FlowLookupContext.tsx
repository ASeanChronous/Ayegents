"use client";

/** Lets the node components (nodeTypes.tsx) resolve an `agent_id`/`tool_id`
 * to a human name without threading those lookup maps through every node's
 * `data` -- `data` stays exactly what `canvasFromSpec` produces (lib/flowSpec.ts),
 * not something the canvas page has to re-shape per node. */
import { createContext, useContext } from "react";

export type FlowLookup = {
  agentNameById: Record<string, string>;
  toolKeyById: Record<string, string>;
};

const FlowLookupContext = createContext<FlowLookup>({ agentNameById: {}, toolKeyById: {} });

export const FlowLookupProvider = FlowLookupContext.Provider;

export function useFlowLookup(): FlowLookup {
  return useContext(FlowLookupContext);
}
