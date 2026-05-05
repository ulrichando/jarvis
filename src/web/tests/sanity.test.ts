import { describe, it, expect } from "vitest";

describe("vitest infrastructure", () => {
  it("runs", () => {
    expect(1 + 1).toBe(2);
  });

  it("resolves @/ alias", async () => {
    const mod = await import("@/lib/ai/models-meta");
    expect(mod).toBeDefined();
    expect(mod.MODELS_META).toBeTypeOf("object");
  });
});
