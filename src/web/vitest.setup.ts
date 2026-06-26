import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// In-memory ioredis mock so NO test hits a real Redis (127.0.0.1:6379).
// budget.ts (kimi swarm) does `new Redis(url)` then get/incrbyfloat/expireat;
// without this, CI's no-Redis sandbox floods ECONNREFUSED and the swarm tests
// hang/time out. Map-backed so the budget gate reads coherently.
vi.mock("ioredis", () => {
  class MockRedis {
    private store = new Map<string, string>();
    on() {
      return this;
    }
    async get(k: string) {
      return this.store.get(k) ?? null;
    }
    async set(k: string, v: string | number) {
      this.store.set(k, String(v));
      return "OK";
    }
    async incrbyfloat(k: string, n: number) {
      const next = parseFloat(this.store.get(k) ?? "0") + Number(n);
      this.store.set(k, String(next));
      return String(next);
    }
    async incrby(k: string, n: number) {
      const next = parseInt(this.store.get(k) ?? "0", 10) + Number(n);
      this.store.set(k, String(next));
      return next;
    }
    async expireat() {
      return 1;
    }
    async expire() {
      return 1;
    }
    async del(k: string) {
      return this.store.delete(k) ? 1 : 0;
    }
    async quit() {
      return "OK";
    }
    disconnect() {}
  }
  return { default: MockRedis };
});

// Ensure DATABASE_URL is set for better-auth initialization in auth.ts.
// Most tests mock the DB or don't use it; setting a dummy URL prevents
// the module-load error for auth-helpers imports.
if (!process.env.DATABASE_URL) {
  process.env.DATABASE_URL =
    "postgresql://test:test@localhost:5432/test_jarvis";
}

afterEach(() => cleanup());

// Tests that need MSW import { server } from "./tests/_msw/server" and
// call server.listen() in their own beforeAll. We don't start it
// globally because most unit tests mock the SDK directly and don't
// need a network interceptor.
