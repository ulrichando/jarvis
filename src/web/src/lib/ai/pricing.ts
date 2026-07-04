/**
 * Best-effort list pricing (USD per 1M tokens) shared by the per-message
 * usage chip and the Settings → Usage aggregates. Providers shift these
 * often — treat every derived dollar figure as an estimate. Models absent
 * from the table cost `null` (UI shows tokens without a dollar figure);
 * local Ollama models are $0 by construction.
 *
 * Verified against published list prices 2026-07-04.
 */

export const PRICING: Record<string, { inputPer1M: number; outputPer1M: number }> = {
  // Anthropic
  "claude-fable-5": { inputPer1M: 10, outputPer1M: 50 },
  "claude-opus-4-8": { inputPer1M: 5, outputPer1M: 25 },
  "claude-opus-4-7": { inputPer1M: 5, outputPer1M: 25 },
  "claude-sonnet-4-6": { inputPer1M: 3, outputPer1M: 15 },
  "claude-haiku-4-5": { inputPer1M: 1, outputPer1M: 5 },
  // OpenAI (rough)
  "gpt-5": { inputPer1M: 5, outputPer1M: 20 },
  "gpt-5-mini": { inputPer1M: 0.5, outputPer1M: 2 },
  o3: { inputPer1M: 60, outputPer1M: 240 },
  // Google
  "gemini-2.5-pro": { inputPer1M: 1.25, outputPer1M: 5 },
  "gemini-2.5-flash": { inputPer1M: 0.1, outputPer1M: 0.4 },
  // DeepSeek
  "deepseek-chat": { inputPer1M: 0.14, outputPer1M: 0.28 },
  "deepseek-reasoner": { inputPer1M: 0.55, outputPer1M: 2.19 },
  "deepseek-v4-pro": { inputPer1M: 0.55, outputPer1M: 2.19 },
  "deepseek-v4-flash": { inputPer1M: 0.14, outputPer1M: 0.28 },
  // Kimi — same list rate the swarm cost meter uses (lib/ai/kimi/swarm.ts)
  "kimi-k2-instant": { inputPer1M: 0.5, outputPer1M: 5 },
  "kimi-k2-thinking": { inputPer1M: 0.5, outputPer1M: 5 },
  "kimi-k2-agent": { inputPer1M: 0.5, outputPer1M: 5 },
  "kimi-k2-swarm": { inputPer1M: 0.5, outputPer1M: 5 },
  "kimi-vision-8k": { inputPer1M: 0.5, outputPer1M: 5 },
  "kimi-vision-32k": { inputPer1M: 0.5, outputPer1M: 5 },
  "kimi-vision-128k": { inputPer1M: 0.5, outputPer1M: 5 },
};

/**
 * Estimated USD cost for a turn, or null when the model has no list price.
 * Local models (ollama-*) are free.
 */
export function estimateCostUsd(
  model: string,
  tokensIn: number,
  tokensOut: number,
): number | null {
  if (model.startsWith("ollama-")) return 0;
  const p = PRICING[model];
  if (!p) return null;
  return (tokensIn * p.inputPer1M + tokensOut * p.outputPer1M) / 1_000_000;
}
