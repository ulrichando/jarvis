import { test, expect } from "bun:test";
import { createServer } from "node:net";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createHyprIpc, resolveSocketPath } from "../hyprland/ipc.ts";

test("resolveSocketPath throws when XDG_RUNTIME_DIR is unset", () => {
  expect(() => resolveSocketPath({ HYPRLAND_INSTANCE_SIGNATURE: "sig" })).toThrow(/XDG_RUNTIME_DIR/);
});

test("resolveSocketPath throws when HYPRLAND_INSTANCE_SIGNATURE is unset", () => {
  expect(() => resolveSocketPath({ XDG_RUNTIME_DIR: "/run/user/1000" })).toThrow(/HYPRLAND_INSTANCE_SIGNATURE/);
});

test("resolveSocketPath builds the expected path", () => {
  const p = resolveSocketPath({ XDG_RUNTIME_DIR: "/run/user/1000", HYPRLAND_INSTANCE_SIGNATURE: "abc" });
  expect(p).toBe("/run/user/1000/hypr/abc/.socket.sock");
});

test("sendCommand writes to socket and returns the response", async () => {
  const dir = await mkdtemp(join(tmpdir(), "misty-hypr-"));
  const socketPath = join(dir, "fake.sock");
  const server = createServer((conn) => {
    conn.on("data", (data) => {
      conn.write(`received: ${data.toString()}`);
      conn.end();
    });
  });
  await new Promise<void>((resolve) => server.listen(socketPath, () => resolve()));

  try {
    const ipc = createHyprIpc({ socketPath });
    const response = await ipc.sendCommand("dispatch exec firefox");
    expect(response).toBe("received: dispatch exec firefox");
  } finally {
    server.close();
    await rm(dir, { recursive: true, force: true });
  }
});

test("sendCommand rejects when server refuses connection", async () => {
  const dir = await mkdtemp(join(tmpdir(), "misty-hypr-"));
  const socketPath = join(dir, "silent.sock");
  const server = createServer(() => { /* noop */ });
  await new Promise<void>((resolve) => server.listen(socketPath, () => resolve()));

  try {
    const ipc = createHyprIpc({ socketPath });
    server.close();
    await expect(ipc.sendCommand("nop")).rejects.toThrow();
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
