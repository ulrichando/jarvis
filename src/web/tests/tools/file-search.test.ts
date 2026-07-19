import { afterEach, describe, expect, it, vi } from "vitest";

// Stub the knowledge store so tests don't touch ~/.jarvis/knowledge.
const readAllKnowledgeDocs = vi.fn();
vi.mock("@/lib/knowledge/store", () => ({
  readAllKnowledgeDocs: (...a: unknown[]) => readAllKnowledgeDocs(...a),
}));

import { fileSearchTool } from "../../src/lib/tools/file-search";

type Match = { file: string; lineStart: number; excerpt: string };
type Out = {
  query: string;
  results: Match[];
  note?: string;
  error?: string;
};

const run = (query: string, fileName?: string): Promise<Out> =>
  (
    fileSearchTool.execute as (
      i: { query: string; fileName?: string },
      o: unknown,
    ) => Promise<Out>
  )({ query, fileName }, {});

afterEach(() => {
  readAllKnowledgeDocs.mockReset();
});

describe("fileSearchTool", () => {
  it("returns a note when no documents are uploaded", async () => {
    readAllKnowledgeDocs.mockResolvedValue([]);
    const out = await run("anything");
    expect(out.results).toEqual([]);
    expect(out.note).toMatch(/no documents/i);
  });

  it("finds the matching passage and reports file + line", async () => {
    readAllKnowledgeDocs.mockResolvedValue([
      {
        name: "notes.md",
        content:
          "intro line\n" +
          "nothing relevant here\n" +
          "The deployment uses Kubernetes on Hetzner\n" +
          "more filler text",
      },
    ]);
    const out = await run("kubernetes deployment");
    expect(out.results.length).toBeGreaterThan(0);
    expect(out.results[0].file).toBe("notes.md");
    expect(out.results[0].excerpt).toMatch(/Kubernetes/);
    expect(out.results[0].lineStart).toBe(1);
    expect(out.error).toBeUndefined();
  });

  it("ranks the more relevant document first", async () => {
    readAllKnowledgeDocs.mockResolvedValue([
      { name: "unrelated.txt", content: "cats and dogs and weather" },
      {
        name: "billing.txt",
        content: "The invoice total and payment invoice due date invoice",
      },
    ]);
    const out = await run("invoice payment");
    expect(out.results[0].file).toBe("billing.txt");
  });

  it("restricts to a document when fileName is given", async () => {
    readAllKnowledgeDocs.mockResolvedValue([
      { name: "a-spec.md", content: "widget spec details" },
      { name: "b-notes.md", content: "widget notes details" },
    ]);
    const out = await run("widget", "spec");
    expect(out.results.every((r) => r.file === "a-spec.md")).toBe(true);
  });

  it("notes when fileName matches nothing", async () => {
    readAllKnowledgeDocs.mockResolvedValue([
      { name: "a.md", content: "content" },
    ]);
    const out = await run("content", "does-not-exist");
    expect(out.results).toEqual([]);
    expect(out.note).toMatch(/no uploaded document matches/i);
  });

  it("returns a no-match note when nothing scores", async () => {
    readAllKnowledgeDocs.mockResolvedValue([
      { name: "a.md", content: "completely different subject matter" },
    ]);
    const out = await run("xylophone quasar");
    expect(out.results).toEqual([]);
    expect(out.note).toMatch(/no matching passages/i);
  });

  it("degrades to an error result (never throws) when the store fails", async () => {
    readAllKnowledgeDocs.mockRejectedValue(new Error("disk gone"));
    const out = await run("x");
    expect(out.results).toEqual([]);
    expect(out.error).toMatch(/unavailable/i);
  });
});
