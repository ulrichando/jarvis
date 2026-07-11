import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

// Mock the store so the route's validation/coercion/status-code logic is
// tested without touching the real ~/.jarvis/knowledge/ directory.
vi.mock("@/lib/knowledge/store", () => ({
  listKnowledge: vi.fn(),
  addKnowledge: vi.fn(),
  removeKnowledge: vi.fn(),
  setKnowledgeEnabled: vi.fn(),
}));

import {
  addKnowledge,
  listKnowledge,
  removeKnowledge,
  setKnowledgeEnabled,
} from "@/lib/knowledge/store";
import { DELETE, GET, PATCH, POST } from "@/app/api/knowledge/route";

const json = (body: unknown) =>
  new Request("http://localhost/api/knowledge", {
    method: "POST",
    body: JSON.stringify(body),
  });

beforeEach(() => {
  (listKnowledge as Mock).mockResolvedValue([{ name: "a", enabled: true }]);
  (addKnowledge as Mock).mockResolvedValue({ ok: true, doc: { name: "a" } });
  (removeKnowledge as Mock).mockResolvedValue(true);
  (setKnowledgeEnabled as Mock).mockResolvedValue(true);
});
afterEach(() => vi.clearAllMocks());

describe("GET /api/knowledge", () => {
  it("returns the doc list", async () => {
    const res = await GET();
    expect(await res.json()).toEqual({ docs: [{ name: "a", enabled: true }] });
  });
});

describe("POST /api/knowledge", () => {
  it("adds and returns the doc on success", async () => {
    const res = await POST(json({ name: "notes", content: "hi" }));
    expect(addKnowledge).toHaveBeenCalledWith("notes", "hi");
    expect(await res.json()).toEqual({ doc: { name: "a" } });
  });

  it("coerces non-string name/content to empty strings", async () => {
    await POST(json({ name: 123, content: null }));
    expect(addKnowledge).toHaveBeenCalledWith("", "");
  });

  it("400s with the store's error when add is rejected", async () => {
    (addKnowledge as Mock).mockResolvedValue({ ok: false, error: "too big" });
    const res = await POST(json({ name: "x", content: "y" }));
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "too big" });
  });

  it("treats an unparseable body as empty (no throw)", async () => {
    const bad = new Request("http://localhost/api/knowledge", {
      method: "POST",
      body: "not json",
    });
    await POST(bad);
    expect(addKnowledge).toHaveBeenCalledWith("", "");
  });
});

describe("PATCH /api/knowledge", () => {
  it("enables and returns ok", async () => {
    const res = await PATCH(json({ name: "a", enabled: true }));
    expect(setKnowledgeEnabled).toHaveBeenCalledWith("a", true);
    expect(await res.json()).toEqual({ ok: true });
  });

  it("coerces enabled to a strict boolean (only true is true)", async () => {
    await PATCH(json({ name: "a", enabled: "yes" }));
    expect(setKnowledgeEnabled).toHaveBeenCalledWith("a", false);
  });

  it("400s when the store rejects the name", async () => {
    (setKnowledgeEnabled as Mock).mockResolvedValue(false);
    const res = await PATCH(json({ name: "ghost", enabled: true }));
    expect(res.status).toBe(400);
  });
});

describe("DELETE /api/knowledge", () => {
  it("removes by ?name= and returns ok", async () => {
    const res = await DELETE(
      new Request("http://localhost/api/knowledge?name=a", { method: "DELETE" }),
    );
    expect(removeKnowledge).toHaveBeenCalledWith("a");
    expect(await res.json()).toEqual({ ok: true });
  });

  it("404s when the doc is not found", async () => {
    (removeKnowledge as Mock).mockResolvedValue(false);
    const res = await DELETE(
      new Request("http://localhost/api/knowledge?name=ghost", {
        method: "DELETE",
      }),
    );
    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ error: "not_found" });
  });
});
