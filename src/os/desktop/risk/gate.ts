import { classify } from "./tiers.ts";

export type GateDecision = { allow: true } | { allow: false; reason: string };

// Plan 2: high-risk auto-denies. Plans 3+ inject an approval callback for voice/HUD confirmation.
export function gate(toolName: string, input: unknown): GateDecision {
  const tier = classify(toolName, input);
  if (tier === "low") return { allow: true };
  return {
    allow: false,
    reason: `high-risk ${toolName} call (${summarize(input)}); denied — approval UI lands in Plan 3+`,
  };
}

function summarize(input: unknown): string {
  try {
    const s = JSON.stringify(input);
    return s.length > 200 ? s.slice(0, 200) + "…" : s;
  } catch {
    return "<unserializable input>";
  }
}
