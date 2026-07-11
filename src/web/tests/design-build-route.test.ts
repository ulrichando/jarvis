import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import os from "node:os";
import nodePath from "node:path";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";

// Mock every heavy dep so the route's request-validation + response-contract
// logic is tested without a real workspace store, docker runtime, or codegen.
// node:fs is NOT mocked (next/server needs its real default export) — instead
// resolveSafe points at a real temp file so the route's fs.readFile succeeds.
vi.mock("@/lib/workspace/storage", () => ({
  createWorkspace: vi.fn(),
  getWorkspace: vi.fn(),
  listAllFiles: vi.fn(),
  resolveSafe: vi.fn((_id: string, rel: string) => `/abs/${rel}`),
  workspaceRoot: vi.fn(() => "/root"),
  writeFile: vi.fn(),
}));
vi.mock("@/lib/design/brand", () => ({ getBrand: vi.fn(async () => null) }));
vi.mock("@/lib/design/codegen", () => ({ generateScaffold: vi.fn() }));
vi.mock("@/lib/workspace/docker", () => ({
  execInRuntime: vi.fn(async () => ({ exitCode: 0, durationMs: 1, stdout: "", stderr: "" })),
  spawnDetached: vi.fn(async () => {}),
}));

import {
  createWorkspace,
  getWorkspace,
  listAllFiles,
  resolveSafe,
} from "@/lib/workspace/storage";
import { generateScaffold } from "@/lib/design/codegen";
import { POST } from "@/app/api/design/build/route";

let tmp: string;

const post = (body: unknown) =>
  new Request("http://localhost/api/design/build", {
    method: "POST",
    body: JSON.stringify(body),
  });

beforeEach(() => {
  tmp = mkdtempSync(nodePath.join(os.tmpdir(), "designbuild-"));
  writeFileSync(nodePath.join(tmp, "landing.html"), "<html><body></body></html>");
  (resolveSafe as Mock).mockImplementation((_id: string, rel: string) =>
    nodePath.join(tmp, rel),
  );
  (getWorkspace as Mock).mockResolvedValue({ id: "src1", name: "My Site" });
  (listAllFiles as Mock).mockResolvedValue(["landing.html"]);
  (createWorkspace as Mock).mockResolvedValue({ id: "new1", name: "Build: My Site" });
  (generateScaffold as Mock).mockReturnValue({
    files: [{ path: "app/page.tsx", content: "x" }],
    sections: ["Hero"],
    forms: [],
    hasAuth: false,
    hasUploads: false,
  });
});
afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
  vi.clearAllMocks();
});

describe("POST /api/design/build", () => {
  it("400s when sourceWorkspaceId is missing", async () => {
    const res = await POST(post({}));
    expect(res.status).toBe(400);
    expect((await res.json()).error).toContain("sourceWorkspaceId");
  });

  it("404s when the source workspace does not exist", async () => {
    (getWorkspace as Mock).mockResolvedValue(null);
    const res = await POST(post({ sourceWorkspaceId: "ghost" }));
    expect(res.status).toBe(404);
  });

  it("400s when the source has no usable design files", async () => {
    // Only clarify/reference artifacts → filtered to zero design files.
    (listAllFiles as Mock).mockResolvedValue(["questions.html", "references/logo.png"]);
    const res = await POST(post({ sourceWorkspaceId: "src1" }));
    expect(res.status).toBe(400);
    expect((await res.json()).error).toContain("no design files");
  });

  it("builds a workbench workspace and returns the seed contract", async () => {
    const res = await POST(post({ sourceWorkspaceId: "src1" }));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.workspaceId).toBe("new1");
    expect(body.copiedFiles).toBe(1);
    expect(typeof body.seed).toBe("string");
    expect(body.seed.length).toBeGreaterThan(0);
    // codegen path taken → the "build is COMPLETE" iteration seed, not the
    // fallback translation seed.
    expect(body.seed).toContain("COMPLETE");
    expect(createWorkspace).toHaveBeenCalledWith("Build: My Site", "workbench");
  });
});
