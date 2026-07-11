import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GET } from "@/app/api/chrome/status/route";

type Status = { bridgeReachable: boolean; extensionConnected: boolean | null };

// A fetch stub that answers /health and /api/ext_status independently.
function stubFetch(opts: {
  health?: { ok: boolean };
  extStatus?: { ok: boolean; body?: unknown; jsonThrows?: boolean };
  healthThrows?: boolean;
}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/health")) {
        if (opts.healthThrows) throw new Error("ECONNREFUSED");
        return { ok: opts.health?.ok ?? false };
      }
      // /api/ext_status
      const e = opts.extStatus ?? { ok: false };
      return {
        ok: e.ok,
        json: async () => {
          if (e.jsonThrows) throw new Error("bad json");
          return e.body ?? {};
        },
      };
    }),
  );
}

const run = async (): Promise<Status> => (await GET()).json();

beforeEach(() => {
  // Token present in env → route skips the ~/.jarvis token file read.
  process.env.JARVIS_LOCAL_API_TOKEN = "tok";
});
afterEach(() => {
  vi.unstubAllGlobals();
  delete process.env.JARVIS_LOCAL_API_TOKEN;
});

describe("GET /api/chrome/status", () => {
  it("bridge unreachable → not reachable, extension unknown, no throw", async () => {
    stubFetch({ healthThrows: true });
    expect(await run()).toEqual({
      bridgeReachable: false,
      extensionConnected: null,
    });
  });

  it("bridge up + extension connected", async () => {
    stubFetch({
      health: { ok: true },
      extStatus: { ok: true, body: { connected: true } },
    });
    expect(await run()).toEqual({
      bridgeReachable: true,
      extensionConnected: true,
    });
  });

  it("bridge up + extension reported disconnected", async () => {
    stubFetch({
      health: { ok: true },
      extStatus: { ok: true, body: { connected: false } },
    });
    expect(await run()).toEqual({
      bridgeReachable: true,
      extensionConnected: false,
    });
  });

  it("bridge up but ext_status errors → connected stays unknown (null)", async () => {
    stubFetch({ health: { ok: true }, extStatus: { ok: false } });
    expect(await run()).toEqual({
      bridgeReachable: true,
      extensionConnected: null,
    });
  });

  it("bridge up, ext_status ok but body is unparseable → null", async () => {
    stubFetch({
      health: { ok: true },
      extStatus: { ok: true, jsonThrows: true },
    });
    expect(await run()).toEqual({
      bridgeReachable: true,
      extensionConnected: null,
    });
  });
});
