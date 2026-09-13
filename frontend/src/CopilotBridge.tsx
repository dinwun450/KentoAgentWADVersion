import { CopilotKit, CopilotPopup, useFrontendTool } from "@copilotkit/react-core/v2";
import "@copilotkit/react-core/v2/styles.css";
import { z } from "zod";
import type { Snapshot } from "./types";

type Props = {
  snapshot: Snapshot;
  refresh: () => Promise<void>;
  selectAgent: (agentId: string) => void;
};

async function post(path: string, body?: unknown) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return response.json();
}

function RegisteredTools({ snapshot, refresh, selectAgent }: Props) {
  useFrontendTool({
    name: "control_simulation",
    description: "Play, pause, step, or reset the deterministic disaster simulation.",
    parameters: z.object({ action: z.enum(["play", "pause", "step", "reset"]) }),
    handler: async ({ action }) => {
      await post(`/api/simulation/${action}`);
      await refresh();
      return { status: "success", action };
    },
  });
  useFrontendTool({
    name: "generate_disaster_scenario",
    description: "Generate an exactly replayable earthquake scenario from a seed.",
    parameters: z.object({ seed: z.number().int() }),
    handler: async ({ seed }) => {
      await post("/api/scenarios", { seed });
      await refresh();
      return { status: "success", seed };
    },
  });
  useFrontendTool({
    name: "inspect_agent",
    description: "Select an agent and return its structured decision summary.",
    parameters: z.object({ agentId: z.string() }),
    handler: async ({ agentId }) => {
      const agent = snapshot.world.agents[agentId];
      if (!agent) return { status: "not_found", agentId };
      selectAgent(agentId);
      return { status: "success", agent };
    },
  });
  return <CopilotPopup agentId="kento-operator" labels={{ modalHeaderTitle: "Kento Copilot" }} />;
}

export function CopilotBridge(props: Props) {
  const runtimeUrl = import.meta.env.VITE_COPILOT_RUNTIME_URL as string | undefined;
  if (!runtimeUrl) return null;
  return (
    <CopilotKit runtimeUrl={runtimeUrl}>
      <RegisteredTools {...props} />
    </CopilotKit>
  );
}

