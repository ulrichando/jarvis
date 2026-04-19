import { classify } from "./tiers.ts";

export type GateDecision = { allow: true } | { allow: false; reason: string };

export type ConfirmCallback = (req: {
  tool: string;
  input: unknown;
  reason: string;
  promptText: string;
}) => Promise<"allow" | "deny">;

export type GateOpts = {
  /** If provided, high-risk tool calls ask this callback instead of auto-denying. */
  confirm?: ConfirmCallback;
};

export async function gate(
  toolName: string,
  input: unknown,
  opts: GateOpts = {},
): Promise<GateDecision> {
  const tier = classify(toolName, input);
  if (tier === "low") return { allow: true };

  const summary = summarize(input);
  const reason = `high-risk ${toolName} call (${summary})`;
  const promptText = buildPrompt(toolName, input);

  if (opts.confirm) {
    const decision = await opts.confirm({ tool: toolName, input, reason, promptText });
    if (decision === "allow") return { allow: true };
    return { allow: false, reason: `user denied: ${reason}` };
  }

  return { allow: false, reason: `${reason}; no approval UI attached (pass confirm callback to allow)` };
}

function summarize(input: unknown): string {
  try {
    const s = JSON.stringify(input);
    return s.length > 200 ? s.slice(0, 200) + "…" : s;
  } catch {
    return "<unserializable input>";
  }
}

function buildPrompt(tool: string, input: unknown): string {
  if (tool === "bash") {
    const cmd = (input as { command?: string })?.command ?? "";
    return `Run \`${cmd.slice(0, 200)}\`? This was flagged as high-risk.`;
  }
  return `Proceed with ${tool} (${summarize(input)})? This was flagged as high-risk.`;
}
