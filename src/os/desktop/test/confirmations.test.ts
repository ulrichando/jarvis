import { test, expect } from "bun:test";
import { ConfirmationQueue } from "../voice/confirmations.ts";

test("open returns a unique id and a pending promise", () => {
  const q = new ConfirmationQueue();
  const a = q.open({ tool: "bash", input: {}, reason: "r", promptText: "p" });
  const b = q.open({ tool: "bash", input: {}, reason: "r", promptText: "p" });
  expect(a.id).not.toBe(b.id);
  // Suppress unhandled rejections before shutdown rejects the open promises.
  a.wait.catch(() => {});
  b.wait.catch(() => {});
  q.shutdown();
});

test("resolve with 'allow' settles the promise with 'allow'", async () => {
  const q = new ConfirmationQueue();
  const { id, wait } = q.open({ tool: "bash", input: {}, reason: "r", promptText: "p" });
  expect(q.resolve(id, "allow")).toBe(true);
  expect(await wait).toBe("allow");
});

test("resolve with 'deny' settles with 'deny'", async () => {
  const q = new ConfirmationQueue();
  const { id, wait } = q.open({ tool: "bash", input: {}, reason: "r", promptText: "p" });
  q.resolve(id, "deny");
  expect(await wait).toBe("deny");
});

test("resolve returns false for unknown id", () => {
  const q = new ConfirmationQueue();
  expect(q.resolve("bogus", "allow")).toBe(false);
});

test("resolve returns false for already-resolved id", () => {
  const q = new ConfirmationQueue();
  const { id } = q.open({ tool: "bash", input: {}, reason: "r", promptText: "p" });
  q.resolve(id, "allow");
  expect(q.resolve(id, "allow")).toBe(false);
});

test("list returns all pending requests", () => {
  const q = new ConfirmationQueue();
  const a = q.open({ tool: "bash", input: { cmd: "ls" }, reason: "r1", promptText: "p1" });
  const b = q.open({ tool: "bash", input: { cmd: "pwd" }, reason: "r2", promptText: "p2" });
  expect(q.list()).toHaveLength(2);
  q.resolve(a.id, "allow");
  expect(q.list()).toHaveLength(1);
  q.resolve(b.id, "deny");
  expect(q.list()).toHaveLength(0);
});

test("timeout rejects the pending promise", async () => {
  const q = new ConfirmationQueue(50);
  const { wait } = q.open({ tool: "bash", input: {}, reason: "r", promptText: "p" });
  await expect(wait).rejects.toThrow(/timed out after 50ms/);
});

test("shutdown rejects all pending", async () => {
  const q = new ConfirmationQueue();
  const { wait } = q.open({ tool: "bash", input: {}, reason: "r", promptText: "p" });
  q.shutdown();
  await expect(wait).rejects.toThrow(/shutting down/);
});
