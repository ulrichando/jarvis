/**
 * Stage 2 offline parity — web local-failover tests.
 *
 * Pins the load-bearing properties of @/lib/ai/local-failover:
 *   1. Healthy cloud → the wrapped model returns the CLOUD result and the
 *      local probe/model are never touched (online path unchanged).
 *   2. Connection-level cloud failure → retried once on local ollama.
 *   3. 401 / 429 (auth/quota — the provider ANSWERED) → NEVER rerouted.
 *   4. Ollama absent (deployed VPS) → the original cloud error surfaces.
 *   5. MissingApiKeyError → local serve when ollama is up, rethrow when not.
 *   6. Flag off → getModel result passes through unwrapped.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import type { LanguageModelV3 } from "@ai-sdk/provider";
import { MissingApiKeyError } from "@/lib/ai/models";
import {
  getModelWithLocalFailover,
  isOutageError,
  withLocalFailover,
} from "@/lib/ai/local-failover";

// getModel pulls settings/store (fs) — mock the module boundary so these
// tests exercise ONLY the failover layer.
vi.mock("@/lib/ai/models", async () => {
  const actual = await vi.importActual<Record<string, unknown>>(
    "@/lib/ai/models",
  );
  return { ...actual, getModel: vi.fn(), buildProvider: actual.buildProvider };
});
import { getModel } from "@/lib/ai/models";
const getModelMock = vi.mocked(getModel);

afterEach(() => {
  vi.clearAllMocks();
});

function statusError(statusCode: number, message = `HTTP ${statusCode}`) {
  const err = new Error(message) as Error & { statusCode: number };
  err.statusCode = statusCode;
  return err;
}

const connError = () =>
  new Error("Failed to fetch: ECONNREFUSED api.example.com:443");

/** Minimal fake LanguageModelV3 whose doStream/doGenerate we control. */
function fakeModel(opts: {
  streamImpl: () => Promise<unknown>;
  label: string;
}): LanguageModelV3 & { calls: number } {
  const model = {
    specificationVersion: "v3",
    provider: "fake",
    modelId: opts.label,
    supportedUrls: {},
    calls: 0,
    async doGenerate() {
      model.calls += 1;
      return opts.streamImpl();
    },
    async doStream() {
      model.calls += 1;
      return opts.streamImpl();
    },
  } as unknown as LanguageModelV3 & { calls: number };
  return model;
}

const CLOUD_OK = { marker: "cloud-stream" };
const LOCAL_OK = { marker: "local-stream" };

const params = { prompt: [] } as never;

describe("isOutageError — which failures may reroute to local", () => {
  it("network-shaped errors and 5xx/408 are outages", () => {
    expect(isOutageError(connError())).toBe(true);
    expect(isOutageError(new Error("fetch failed"))).toBe(true);
    expect(isOutageError(statusError(500))).toBe(true);
    expect(isOutageError(statusError(503))).toBe(true);
    expect(isOutageError(statusError(529))).toBe(true);
    expect(isOutageError(statusError(408))).toBe(true);
    // Wrapped one level down in .cause (AI SDK APICallError shape).
    const wrapped = new Error("Cannot connect to API", {
      cause: new Error("connect ECONNREFUSED 1.2.3.4:443"),
    });
    expect(isOutageError(wrapped)).toBe(true);
    expect(isOutageError(new MissingApiKeyError("deepseek"))).toBe(true);
  });

  it("401/403/429/400 and non-network errors are NOT outages", () => {
    expect(isOutageError(statusError(401, "invalid api key"))).toBe(false);
    expect(isOutageError(statusError(403))).toBe(false);
    expect(isOutageError(statusError(429, "quota exceeded"))).toBe(false);
    expect(isOutageError(statusError(400, "bad request"))).toBe(false);
    expect(isOutageError(new Error("invalid prompt shape"))).toBe(false);
    expect(isOutageError(undefined)).toBe(false);
  });
});

describe("withLocalFailover (middleware)", () => {
  it("ONLINE UNCHANGED: healthy cloud → cloud result; probe + local never touched", async () => {
    const cloud = fakeModel({ streamImpl: async () => CLOUD_OK, label: "cloud" });
    const local = fakeModel({ streamImpl: async () => LOCAL_OK, label: "local" });
    const probe = vi.fn(async () => true);

    const wrapped = withLocalFailover(cloud, {
      enabled: true,
      probe,
      localModel: () => local,
    }) as LanguageModelV3;

    const res = await wrapped.doStream(params);
    expect(res).toBe(CLOUD_OK);
    expect(probe).not.toHaveBeenCalled();
    expect(local.calls).toBe(0);
  });

  it("cloud CONNECTION failure + ollama up → served by the local model", async () => {
    const cloud = fakeModel({
      streamImpl: async () => {
        throw connError();
      },
      label: "cloud",
    });
    const local = fakeModel({ streamImpl: async () => LOCAL_OK, label: "local" });

    const wrapped = withLocalFailover(cloud, {
      enabled: true,
      probe: async () => true,
      localModel: () => local,
    }) as LanguageModelV3;

    const res = await wrapped.doStream(params);
    expect(res).toBe(LOCAL_OK);
    expect(local.calls).toBe(1);
  });

  it("cloud 5xx → served by the local model (generate path too)", async () => {
    const cloud = fakeModel({
      streamImpl: async () => {
        throw statusError(502, "bad gateway");
      },
      label: "cloud",
    });
    const local = fakeModel({ streamImpl: async () => LOCAL_OK, label: "local" });

    const wrapped = withLocalFailover(cloud, {
      enabled: true,
      probe: async () => true,
      localModel: () => local,
    }) as LanguageModelV3;

    await expect(wrapped.doGenerate(params)).resolves.toBe(LOCAL_OK);
  });

  it("401 auth error → surfaces; local never consulted", async () => {
    const authErr = statusError(401, "invalid x-api-key");
    const cloud = fakeModel({
      streamImpl: async () => {
        throw authErr;
      },
      label: "cloud",
    });
    const local = fakeModel({ streamImpl: async () => LOCAL_OK, label: "local" });
    const probe = vi.fn(async () => true);

    const wrapped = withLocalFailover(cloud, {
      enabled: true,
      probe,
      localModel: () => local,
    }) as LanguageModelV3;

    await expect(wrapped.doStream(params)).rejects.toBe(authErr);
    expect(probe).not.toHaveBeenCalled();
    expect(local.calls).toBe(0);
  });

  it("429 quota → surfaces; local never consulted", async () => {
    const quotaErr = statusError(429, "rate limit exceeded");
    const cloud = fakeModel({
      streamImpl: async () => {
        throw quotaErr;
      },
      label: "cloud",
    });
    const local = fakeModel({ streamImpl: async () => LOCAL_OK, label: "local" });

    const wrapped = withLocalFailover(cloud, {
      enabled: true,
      probe: async () => true,
      localModel: () => local,
    }) as LanguageModelV3;

    await expect(wrapped.doStream(params)).rejects.toBe(quotaErr);
    expect(local.calls).toBe(0);
  });

  it("ollama absent (deployed VPS) → the ORIGINAL cloud error surfaces", async () => {
    const outage = connError();
    const cloud = fakeModel({
      streamImpl: async () => {
        throw outage;
      },
      label: "cloud",
    });
    const local = fakeModel({ streamImpl: async () => LOCAL_OK, label: "local" });

    const wrapped = withLocalFailover(cloud, {
      enabled: true,
      probe: async () => false, // no daemon / model not pulled
      localModel: () => local,
    }) as LanguageModelV3;

    await expect(wrapped.doStream(params)).rejects.toBe(outage);
    expect(local.calls).toBe(0);
  });

  it("flag off → returns the model UNWRAPPED (byte-identical path)", () => {
    const cloud = fakeModel({ streamImpl: async () => CLOUD_OK, label: "cloud" });
    const same = withLocalFailover(cloud, { enabled: false });
    expect(same).toBe(cloud);
  });
});

describe("getModelWithLocalFailover", () => {
  it("cloud model resolves → same meta, model wrapped (not the original ref)", async () => {
    const cloud = fakeModel({ streamImpl: async () => CLOUD_OK, label: "cloud" });
    const meta = { id: "deepseek-chat", provider: "deepseek" } as never;
    getModelMock.mockResolvedValue({ meta, model: cloud } as never);

    const selected = await getModelWithLocalFailover("deepseek-chat", {
      enabled: true,
      probe: async () => true,
    });
    expect(selected.meta).toBe(meta);
    // Wrapped pass-through still serves the cloud result.
    const res = await (selected.model as LanguageModelV3).doStream(params);
    expect(res).toBe(CLOUD_OK);
  });

  it("already-local (ollama) model → returned untouched, no self-wrap", async () => {
    const localMeta = { id: "ollama:qwen2.5:7b", provider: "ollama" } as never;
    const local = fakeModel({ streamImpl: async () => LOCAL_OK, label: "local" });
    getModelMock.mockResolvedValue({ meta: localMeta, model: local } as never);

    const selected = await getModelWithLocalFailover("ollama:qwen2.5:7b", {
      enabled: true,
    });
    expect(selected.model).toBe(local);
  });

  it("MissingApiKeyError + ollama up → serves local qwen2.5:7b", async () => {
    getModelMock.mockRejectedValue(new MissingApiKeyError("anthropic"));
    const local = fakeModel({ streamImpl: async () => LOCAL_OK, label: "local" });

    const selected = await getModelWithLocalFailover("claude-fable-5", {
      enabled: true,
      probe: async () => true,
      localModel: () => local,
    });
    expect(selected.meta.provider).toBe("ollama");
    expect(selected.meta.id).toBe("ollama:qwen2.5:7b");
    await expect(
      (selected.model as LanguageModelV3).doStream(params),
    ).resolves.toBe(LOCAL_OK);
  });

  it("MissingApiKeyError + no ollama → rethrows (route 400s as before)", async () => {
    const missing = new MissingApiKeyError("anthropic");
    getModelMock.mockRejectedValue(missing);

    await expect(
      getModelWithLocalFailover("claude-fable-5", {
        enabled: true,
        probe: async () => false,
      }),
    ).rejects.toBe(missing);
  });

  it("flag off → getModel result returned identically", async () => {
    const cloud = fakeModel({ streamImpl: async () => CLOUD_OK, label: "cloud" });
    const meta = { id: "deepseek-chat", provider: "deepseek" } as never;
    getModelMock.mockResolvedValue({ meta, model: cloud } as never);

    const selected = await getModelWithLocalFailover("deepseek-chat", {
      enabled: false,
    });
    expect(selected.model).toBe(cloud);
    expect(selected.meta).toBe(meta);
  });
});
