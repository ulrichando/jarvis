import { test, expect } from "bun:test";
import { bashTool } from "../agent/tools/bash.ts";

test("bash executes ls and returns output", async () => {
  const result = await bashTool.run({ command: "echo hello" });
  expect(result.is_error).toBeFalsy();
  expect(result.output.trim()).toBe("hello");
});

test("bash returns is_error on non-zero exit", async () => {
  const result = await bashTool.run({ command: "exit 17" });
  expect(result.is_error).toBe(true);
});

test("bash captures stderr", async () => {
  const result = await bashTool.run({ command: "echo oops >&2" });
  expect(result.output).toContain("[stderr]");
  expect(result.output).toContain("oops");
});

test("bash rejects empty command", async () => {
  const result = await bashTool.run({ command: "" });
  expect(result.is_error).toBe(true);
  expect(result.output).toContain("empty command");
});

test("bash truncates output over 16KB", async () => {
  const result = await bashTool.run({ command: "yes x | head -c 20480" });
  expect(result.output).toContain("[truncated");
});

test("bash respects timeout", async () => {
  const start = Date.now();
  const result = await bashTool.run({ command: "sleep 5", timeout_ms: 200 });
  const elapsed = Date.now() - start;
  expect(elapsed).toBeLessThan(2000);
  expect(result.is_error).toBe(true);
});
