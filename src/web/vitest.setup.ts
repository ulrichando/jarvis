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

// Vitest reuses worker threads across test files, and process.env survives the
// per-file module-registry reset — so module-level assignments like
// `process.env.JARVIS_REQUIRE_LOCAL_AUTH = '1'` in proxy-*.test.ts leak into
// whichever file the worker runs next. Routes read these gates at REQUEST time
// (e.g. environments/bridge registration 401s under REQUIRE_LOCAL_AUTH), which
// made tests/bridge/integration.test.ts fail only in full-suite runs. This
// setup file runs per test file BEFORE the file's own module code, so deleting
// the hazards here cleans inherited state while each file's own assignments
// still apply.
delete process.env.JARVIS_REQUIRE_LOCAL_AUTH;
delete process.env.JARVIS_LOCAL_API_TOKEN;
delete process.env.JARVIS_WEB_ALLOWED_HOSTS;
delete process.env.JARVIS_AUTH_DISABLED;

afterEach(() => cleanup());

// Tests that need MSW import { server } from "./tests/_msw/server" and
// call server.listen() in their own beforeAll. We don't start it
// globally because most unit tests mock the SDK directly and don't
// need a network interceptor.
