import { test, expect } from "bun:test";
import { sshExecTool, dockerExecTool, envListTool } from "../agent/tools/env.ts";

test("ssh_exec errors when host is missing", async () => {
  const r = await sshExecTool.run({ command: "ls" });
  expect(r.is_error).toBe(true);
});

test("ssh_exec errors when command is missing", async () => {
  const r = await sshExecTool.run({ host: "user@host" });
  expect(r.is_error).toBe(true);
});

test("docker_exec errors when container is missing", async () => {
  const r = await dockerExecTool.run({ command: "ls" });
  expect(r.is_error).toBe(true);
});

test("env_list always returns at least local", async () => {
  const r = await envListTool.run({});
  expect(r.is_error).toBeFalsy();
  expect(r.output).toContain("local");
});
