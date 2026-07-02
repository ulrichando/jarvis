import { describe, it, expect, vi, beforeEach } from "vitest";

// The route returns SECRET values, so its gate must be the strict
// resolveBridgeToken lookup — not the v1-permissive "any bearer" pattern the
// worker routes use. These tests pin that: no bearer → 401, unknown token →
// 401, known token → the effective keys with settings.json winning over env.

const resolveBridgeToken = vi.fn();
const loadSettings = vi.fn();
const providerEnvKey = vi.fn();

vi.mock("@/lib/bridge/db", () => ({ getStore: () => ({}) }));
vi.mock("@/lib/bridge/store", () => ({
  resolveBridgeToken: (...args: unknown[]) => resolveBridgeToken(...args),
}));
vi.mock("@/lib/settings/store", () => ({
  loadSettings: (...args: unknown[]) => loadSettings(...args),
}));
vi.mock("@/lib/ai/provider-keys", () => ({
  providerEnvKey: (...args: unknown[]) => providerEnvKey(...args),
}));

import { GET } from "@/app/api/bridge/v1/keys/route";

function req(auth?: string): Request {
  return new Request("http://127.0.0.1/api/bridge/v1/keys", {
    headers: auth ? { authorization: auth } : {},
  });
}

beforeEach(() => {
  resolveBridgeToken.mockReset();
  loadSettings.mockReset();
  providerEnvKey.mockReset();
  loadSettings.mockResolvedValue({ providers: {} });
  providerEnvKey.mockReturnValue(undefined);
});

describe("GET /api/bridge/v1/keys", () => {
  it("401s without a bearer", async () => {
    const res = await GET(req());
    expect(res.status).toBe(401);
    expect(resolveBridgeToken).not.toHaveBeenCalled();
  });

  it("401s when the token does not resolve to a user", async () => {
    resolveBridgeToken.mockReturnValue(null);
    const res = await GET(req("Bearer nope"));
    expect(res.status).toBe(401);
    expect(loadSettings).not.toHaveBeenCalled();
  });

  it("returns effective keys — settings.json wins over env, empties omitted", async () => {
    resolveBridgeToken.mockReturnValue("user-1");
    loadSettings.mockResolvedValue({
      providers: {
        anthropic: { apiKey: "  sk-from-settings  " },
        google: {},
      },
    });
    providerEnvKey.mockImplementation((provider: unknown) =>
      provider === "google"
        ? "g-from-env"
        : provider === "anthropic"
          ? "env-should-lose"
          : undefined,
    );

    const res = await GET(req("Bearer good"));
    expect(res.status).toBe(200);
    const body = (await res.json()) as { keys: Record<string, string> };
    expect(body.keys).toEqual({
      ANTHROPIC_API_KEY: "sk-from-settings",
      GOOGLE_API_KEY: "g-from-env",
    });
  });
});
