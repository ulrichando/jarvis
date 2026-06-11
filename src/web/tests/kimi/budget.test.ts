import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

const redisMock = {
  incrbyfloat: vi.fn(),
  expireat: vi.fn(),
  get: vi.fn(),
};
vi.mock("ioredis", () => ({
  // A real class: vitest 4 invokes the default export with `new`, and a
  // vi.fn whose implementation is an arrow is no longer constructible.
  // The constructor returns the shared redisMock so every instance is
  // the same object the assertions reset/inspect.
  default: class {
    constructor() {
      return redisMock;
    }
  },
}));

import { reserveSwarmBudget, recordSwarmSpend } from "@/lib/ai/kimi/budget";

describe("budget guard", () => {
  beforeEach(() => {
    redisMock.incrbyfloat.mockReset();
    redisMock.expireat.mockReset();
    redisMock.get.mockReset();
    process.env.KIMI_SWARM_DAILY_BUDGET_USD = "5";
  });
  afterEach(() => {
    delete process.env.KIMI_SWARM_DAILY_BUDGET_USD;
  });

  it("allows when current spend below budget", async () => {
    redisMock.get.mockResolvedValueOnce("2.50");
    const r = await reserveSwarmBudget(0.06);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.remaining).toBeCloseTo(2.5);
  });

  it("denies when current + estimated would exceed budget", async () => {
    redisMock.get.mockResolvedValueOnce("4.99");
    const r = await reserveSwarmBudget(0.06);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toMatch(/budget/i);
  });

  it("denies when current already at/over budget", async () => {
    redisMock.get.mockResolvedValueOnce("5.00");
    const r = await reserveSwarmBudget(0.01);
    expect(r.ok).toBe(false);
  });

  it("treats null current as 0", async () => {
    redisMock.get.mockResolvedValueOnce(null);
    const r = await reserveSwarmBudget(0.06);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.remaining).toBeCloseTo(5);
  });

  it("recordSwarmSpend INCRs by actual cost and sets expireat to end-of-day", async () => {
    redisMock.incrbyfloat.mockResolvedValueOnce("1.06");
    await recordSwarmSpend(0.06);
    expect(redisMock.incrbyfloat).toHaveBeenCalledTimes(1);
    expect(redisMock.expireat).toHaveBeenCalledTimes(1);
    // expireat should be a Unix ts at end of UTC day
    const expireTs = redisMock.expireat.mock.calls[0][1] as number;
    const now = Math.floor(Date.now() / 1000);
    expect(expireTs).toBeGreaterThan(now);
    expect(expireTs).toBeLessThan(now + 24 * 60 * 60 + 60);
  });

  it("respects custom KIMI_SWARM_DAILY_BUDGET_USD", async () => {
    process.env.KIMI_SWARM_DAILY_BUDGET_USD = "1.00";
    redisMock.get.mockResolvedValueOnce("0.95");
    const r = await reserveSwarmBudget(0.06);
    expect(r.ok).toBe(false);
  });

  it("falls back to default $5 when env var unset", async () => {
    delete process.env.KIMI_SWARM_DAILY_BUDGET_USD;
    redisMock.get.mockResolvedValueOnce("4.95");
    const r = await reserveSwarmBudget(0.06);
    expect(r.ok).toBe(false);
  });
});
