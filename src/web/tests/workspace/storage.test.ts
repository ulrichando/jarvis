import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";

// storage.ts reads JARVIS_WORKSPACES_ROOT at import time, so set it to a
// throwaway temp dir BEFORE the dynamic import below.
let storage: typeof import("@/lib/workspace/storage");
let root: string;
let prevRoot: string | undefined;

beforeAll(async () => {
  prevRoot = process.env.JARVIS_WORKSPACES_ROOT;
  root = await fs.mkdtemp(path.join(os.tmpdir(), "jarvis-ws-test-"));
  process.env.JARVIS_WORKSPACES_ROOT = root;
  storage = await import("@/lib/workspace/storage");
});

afterAll(async () => {
  // Restore the env var so this temp root doesn't leak into other test
  // files that share the worker process.
  if (prevRoot === undefined) delete process.env.JARVIS_WORKSPACES_ROOT;
  else process.env.JARVIS_WORKSPACES_ROOT = prevRoot;
  await fs.rm(root, { recursive: true, force: true });
});

describe("workspace env-var round-trip", () => {
  it("MERGES env vars so unsent (masked) keys are never dropped", async () => {
    const ws = await storage.createWorkspace("envtest", "workbench");
    await storage.updateWorkspaceMeta(ws.id, {
      envVars: { A: "1", SECRET_KEY: "xyz" },
    });
    // The editor can't round-trip masked secrets, so it sends only the new
    // key. The merge must preserve A + SECRET_KEY (the old wholesale-replace
    // wiped them — the bug this guards against).
    const updated = await storage.updateWorkspaceMeta(ws.id, {
      envVars: { B: "2" },
    });
    expect(updated?.envVars).toEqual({ A: "1", SECRET_KEY: "xyz", B: "2" });
  });

  it("removes env vars via removeEnvKeys", async () => {
    const ws = await storage.createWorkspace("rmtest", "workbench");
    await storage.updateWorkspaceMeta(ws.id, { envVars: { A: "1", B: "2" } });
    const updated = await storage.updateWorkspaceMeta(ws.id, {
      removeEnvKeys: ["A"],
    });
    expect(updated?.envVars).toEqual({ B: "2" });
  });
});

describe("workspace share tokens", () => {
  it("mints, resolves, and revokes a share token", async () => {
    const ws = await storage.createWorkspace("sharetest", "workbench");
    const share = await storage.setShareToken(ws.id);
    expect(share?.token).toBeTruthy();

    const found = await storage.getWorkspaceByShareToken(share!.token);
    expect(found?.id).toBe(ws.id);

    await storage.clearShareToken(ws.id);
    expect(await storage.getWorkspaceByShareToken(share!.token)).toBeNull();
  });

  it("treats an expired share token as invalid", async () => {
    const ws = await storage.createWorkspace("expiretest", "workbench");
    const share = await storage.setShareToken(ws.id, -1000); // already expired
    expect(await storage.getWorkspaceByShareToken(share!.token)).toBeNull();
  });
});

describe("workspace _meta.json durability", () => {
  it("serializes concurrent meta writes without losing updates", async () => {
    const ws = await storage.createWorkspace("conc", "workbench");
    // Without the write-lock these two read-modify-write cycles race and one
    // field is lost (last writer wins). The mutex must let both land.
    await Promise.all([
      storage.updateWorkspaceMeta(ws.id, { customInstructions: "hello" }),
      storage.updateWorkspaceMeta(ws.id, { devCommand: "npm run dev" }),
    ]);
    const got = await storage.getWorkspace(ws.id);
    expect(got?.customInstructions).toBe("hello");
    expect(got?.devCommand).toBe("npm run dev");
  });

  it("recovers from .bak when the primary _meta.json is corrupt", async () => {
    // Two writes guarantee a .bak exists (saveMeta copies the prior good file
    // before the atomic rename).
    const a = await storage.createWorkspace("bak-a", "workbench");
    await storage.createWorkspace("bak-b", "workbench");
    await fs.writeFile(path.join(root, "_meta.json"), "{ not valid json");
    const list = await storage.listWorkspaces();
    expect(list.some((w) => w.id === a.id)).toBe(true);
  });
});
