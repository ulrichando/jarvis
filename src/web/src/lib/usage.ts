import { MODELS_META, PROVIDER_LABEL, type Provider } from "@/lib/ai/models-meta";
import { estimateCostUsd } from "@/lib/ai/pricing";

/** One month-window row per model, as returned by the usage_events GROUP BY. */
export type UsageRow = {
  model: string;
  requests: number;
  tokensIn: number;
  tokensOut: number;
  requestsToday: number;
  tokensInToday: number;
  tokensOutToday: number;
};

export type UsageTotals = {
  requests: number;
  tokensIn: number;
  tokensOut: number;
  /** Estimated from list pricing at read time; excludes unpriced models. */
  costUsd: number;
};

export type ProviderUsage = UsageTotals & {
  provider: Provider | "unknown";
  label: string;
  /** Fraction of this month's estimated cost (0..1); 0 when total cost is 0. */
  share: number;
};

export type UsageSummary = {
  today: UsageTotals;
  month: UsageTotals;
  providers: ProviderUsage[];
  /** Model ids seen this month with no list price (their cost is excluded). */
  unpricedModels: string[];
};

/** Wire shape of GET /api/usage. */
export type UsageResponse = UsageSummary & {
  budget: {
    monthlyUsd: number | null;
    /** month.costUsd / monthlyUsd; null when no budget is set. */
    percentUsed: number | null;
    alertsEnabled: boolean;
  };
  generatedAt: string;
};

const EMPTY: UsageTotals = { requests: 0, tokensIn: 0, tokensOut: 0, costUsd: 0 };

function providerOf(model: string): { provider: Provider | "unknown"; label: string } {
  const provider = MODELS_META[model]?.provider;
  if (provider) return { provider, label: PROVIDER_LABEL[provider] };
  if (model.startsWith("ollama-")) return { provider: "ollama", label: PROVIDER_LABEL.ollama };
  return { provider: "unknown", label: "Other" };
}

/** Costs are estimates: read-time list pricing, so historical rows re-price
 *  when the table is corrected. Unpriced models contribute tokens but no $. */
export function summarizeUsage(rows: UsageRow[]): UsageSummary {
  const today = { ...EMPTY };
  const month = { ...EMPTY };
  const byProvider = new Map<string, ProviderUsage>();
  const unpriced = new Set<string>();

  for (const r of rows) {
    const monthCost = estimateCostUsd(r.model, r.tokensIn, r.tokensOut);
    const todayCost = estimateCostUsd(r.model, r.tokensInToday, r.tokensOutToday);
    if (monthCost === null && (r.tokensIn > 0 || r.tokensOut > 0)) unpriced.add(r.model);

    month.requests += r.requests;
    month.tokensIn += r.tokensIn;
    month.tokensOut += r.tokensOut;
    month.costUsd += monthCost ?? 0;

    today.requests += r.requestsToday;
    today.tokensIn += r.tokensInToday;
    today.tokensOut += r.tokensOutToday;
    today.costUsd += todayCost ?? 0;

    const { provider, label } = providerOf(r.model);
    const agg =
      byProvider.get(provider) ??
      ({ provider, label, ...EMPTY, share: 0 } as ProviderUsage);
    agg.requests += r.requests;
    agg.tokensIn += r.tokensIn;
    agg.tokensOut += r.tokensOut;
    agg.costUsd += monthCost ?? 0;
    byProvider.set(provider, agg);
  }

  const providers = [...byProvider.values()].sort((a, b) => b.costUsd - a.costUsd);
  for (const p of providers) {
    p.share = month.costUsd > 0 ? p.costUsd / month.costUsd : 0;
  }

  return { today, month, providers, unpricedModels: [...unpriced].sort() };
}
