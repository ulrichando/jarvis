import { describe, expect, it } from "vitest";
import { estimateCostUsd } from "@/lib/ai/pricing";
import { summarizeUsage, type UsageRow } from "@/lib/usage";

function row(partial: Partial<UsageRow> & { model: string }): UsageRow {
  return {
    requests: 0,
    tokensIn: 0,
    tokensOut: 0,
    requestsToday: 0,
    tokensInToday: 0,
    tokensOutToday: 0,
    ...partial,
  };
}

describe("estimateCostUsd", () => {
  it("prices a known model", () => {
    // sonnet: $3/M in, $15/M out
    expect(estimateCostUsd("claude-sonnet-4-6", 1_000_000, 1_000_000)).toBe(18);
    expect(estimateCostUsd("deepseek-chat", 500_000, 0)).toBeCloseTo(0.07);
  });

  it("returns null for unknown models and 0 for local ollama", () => {
    expect(estimateCostUsd("llama-3.3-70b", 1000, 1000)).toBeNull();
    expect(estimateCostUsd("ollama-qwen3-30b-a3b", 1_000_000, 1_000_000)).toBe(0);
  });
});

describe("summarizeUsage", () => {
  it("aggregates totals, providers, and shares", () => {
    const summary = summarizeUsage([
      row({
        model: "claude-sonnet-4-6",
        requests: 2,
        tokensIn: 1_000_000, // $3
        tokensOut: 200_000, // $3
        requestsToday: 1,
        tokensInToday: 500_000,
        tokensOutToday: 100_000,
      }),
      row({
        model: "claude-haiku-4-5",
        requests: 1,
        tokensIn: 1_000_000, // $1
        tokensOut: 0,
      }),
      row({
        model: "deepseek-chat",
        requests: 3,
        tokensIn: 0,
        tokensOut: 5_000_000, // $1.40
      }),
    ]);

    expect(summary.month.requests).toBe(6);
    expect(summary.month.tokensIn).toBe(2_000_000);
    expect(summary.month.costUsd).toBeCloseTo(6 + 1 + 1.4);
    expect(summary.today.requests).toBe(1);
    expect(summary.today.costUsd).toBeCloseTo(1.5 + 1.5);

    // Two Anthropic models merge into one provider bucket, sorted by cost.
    expect(summary.providers.map((p) => p.provider)).toEqual([
      "anthropic",
      "deepseek",
    ]);
    const anthropic = summary.providers[0];
    expect(anthropic.requests).toBe(3);
    expect(anthropic.costUsd).toBeCloseTo(7);
    expect(anthropic.share).toBeCloseTo(7 / 8.4);
    expect(summary.unpricedModels).toEqual([]);
  });

  it("counts tokens for unpriced models but excludes their cost", () => {
    const summary = summarizeUsage([
      row({ model: "llama-3.3-70b", requests: 1, tokensIn: 1000, tokensOut: 1000 }),
    ]);
    expect(summary.month.tokensIn).toBe(1000);
    expect(summary.month.costUsd).toBe(0);
    expect(summary.unpricedModels).toEqual(["llama-3.3-70b"]);
    expect(summary.providers[0].provider).toBe("unknown");
    expect(summary.providers[0].share).toBe(0);
  });
});
