// @vitest-environment node
import { describe, expect, test } from "vitest";
import { fetchConversationsForExport } from "../src/lib/export-conversations";

const okResp = (body: unknown) => ({ ok: true, json: async () => body }) as unknown as Response;
const errResp = () => ({ ok: false, json: async () => ({}) }) as unknown as Response;

describe("fetchConversationsForExport", () => {
  test("counts all successes", async () => {
    const fetchFn = (async (url: string) => okResp({ id: url })) as unknown as typeof fetch;
    const r = await fetchConversationsForExport(["a", "b", "c"], fetchFn);
    expect(r.requested).toBe(3);
    expect(r.failed).toBe(0);
    expect(r.data.length).toBe(3);
  });

  test("non-2xx responses count as failures, not silent nulls", async () => {
    const fetchFn = (async (url: string) =>
      url.endsWith("/b") ? errResp() : okResp({ url })) as unknown as typeof fetch;
    const r = await fetchConversationsForExport(["a", "b", "c"], fetchFn);
    expect(r.failed).toBe(1);
    expect(r.data.length).toBe(2);
    expect(r.data).not.toContain(null); // the old bug pushed null for failures
  });

  test("network errors count as failures", async () => {
    const fetchFn = (async () => {
      throw new Error("network down");
    }) as unknown as typeof fetch;
    const r = await fetchConversationsForExport(["a", "b"], fetchFn);
    expect(r.failed).toBe(2);
    expect(r.data.length).toBe(0);
  });
});
