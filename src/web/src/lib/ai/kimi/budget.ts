import "server-only";
import Redis from "ioredis";

let _client: Redis | null = null;
function client(): Redis {
  if (_client) return _client;
  const url = process.env.REDIS_URL ?? "redis://127.0.0.1:6379";
  _client = new Redis(url);
  return _client;
}

const DEFAULT_BUDGET_USD = 5.0;

function dailyKey(): string {
  const d = new Date();
  const yyyy = d.getUTCFullYear();
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(d.getUTCDate()).padStart(2, "0");
  return `kimi:swarm:spend:${yyyy}-${mm}-${dd}`;
}

function endOfUtcDayTs(): number {
  const d = new Date();
  d.setUTCHours(23, 59, 59, 999);
  return Math.floor(d.getTime() / 1000);
}

function dailyBudget(): number {
  const env = process.env.KIMI_SWARM_DAILY_BUDGET_USD;
  if (!env) return DEFAULT_BUDGET_USD;
  const n = Number(env);
  return Number.isFinite(n) && n > 0 ? n : DEFAULT_BUDGET_USD;
}

export async function reserveSwarmBudget(
  estimatedCostUsd: number,
): Promise<
  | { ok: true; remaining: number }
  | { ok: false; reason: string; remaining: number }
> {
  const c = client();
  const budget = dailyBudget();
  const raw = await c.get(dailyKey());
  const current = raw ? Number(raw) : 0;
  const remaining = Math.max(0, budget - current);
  if (current + estimatedCostUsd > budget) {
    return {
      ok: false,
      reason: `Per-day Swarm budget ($${budget.toFixed(2)}) reached. Current spend: $${current.toFixed(2)}.`,
      remaining,
    };
  }
  return { ok: true, remaining };
}

export async function recordSwarmSpend(actualCostUsd: number): Promise<void> {
  const c = client();
  const key = dailyKey();
  await c.incrbyfloat(key, actualCostUsd);
  await c.expireat(key, endOfUtcDayTs());
}
