import { test, expect } from "bun:test";
import type { LLMClient, LLMResponse } from "../providers/types.ts";
import type { ToolRegistry, ToolRunner } from "../agent/types.ts";
import { runAgent } from "../agent/loop.ts";

function stubClient(scripted: LLMResponse[]): LLMClient {
  let i = 0;
  return {
    name: "stub",
    async complete() {
      const r = scripted[i++];
      if (!r) throw new Error("stub client out of scripted responses");
      return r;
    },
  };
}

function countingTool(name: string): { runner: ToolRunner; count: () => number } {
  let c = 0;
  const runner: ToolRunner = {
    def: { name, description: "", input_schema: { type: "object", properties: {} } },
    async run(input) {
      c++;
      return { output: `ran ${name} with ${JSON.stringify(input)}` };
    },
  };
  return { runner, count: () => c };
}

test("runAgent returns text response when model stops without tool_use", async () => {
  const client = stubClient([
    { content: [{ type: "text", text: "hi there" }], stop_reason: "end_turn" },
  ]);
  const result = await runAgent({
    client,
    model: "m",
    messages: [{ role: "user", content: "hi" }],
    tools: {},
  });
  expect(result.stop_reason).toBe("end_turn");
  expect(result.messages.at(-1)?.role).toBe("assistant");
  expect(result.blocked).toHaveLength(0);
});

test("runAgent executes a safe tool call and returns final text", async () => {
  const { runner, count } = countingTool("ls");
  const tools: ToolRegistry = { ls: runner };
  const client = stubClient([
    { content: [{ type: "tool_use", id: "t1", name: "ls", input: { path: "." } }], stop_reason: "tool_use" },
    { content: [{ type: "text", text: "done" }], stop_reason: "end_turn" },
  ]);
  const result = await runAgent({
    client,
    model: "m",
    messages: [{ role: "user", content: "list" }],
    tools,
  });
  expect(count()).toBe(1);
  expect(result.stop_reason).toBe("end_turn");
});

test("runAgent blocks high-risk bash via gate", async () => {
  const { bashTool } = await import("../agent/tools/bash.ts");
  const tools: ToolRegistry = { bash: bashTool };
  const client = stubClient([
    { content: [{ type: "tool_use", id: "t1", name: "bash", input: { command: "sudo rm -rf /" } }], stop_reason: "tool_use" },
    { content: [{ type: "text", text: "I couldn't run that." }], stop_reason: "end_turn" },
  ]);
  const result = await runAgent({
    client,
    model: "m",
    messages: [{ role: "user", content: "nuke it" }],
    tools,
  });
  expect(result.blocked).toHaveLength(1);
  expect(result.blocked[0]!.tool).toBe("bash");
});

test("runAgent stops at MAX_ITERATIONS when model keeps asking for tools", async () => {
  const { runner } = countingTool("dummy");
  const tools: ToolRegistry = { dummy: runner };
  const scripted: LLMResponse[] = Array.from({ length: 15 }, (_, i) => ({
    content: [{ type: "tool_use" as const, id: `t${i}`, name: "dummy", input: {} }],
    stop_reason: "tool_use" as const,
  }));
  const client = stubClient(scripted);
  const result = await runAgent({
    client,
    model: "m",
    messages: [{ role: "user", content: "loop" }],
    tools,
  });
  expect(result.stop_reason).toBe("max_iterations");
});

test("runAgent allows high-risk when confirm callback returns allow", async () => {
  // Use a stubbed bash tool that doesn't actually exec — we're testing the gate/confirm flow,
  // not bash execution. The command string "sudo true" is classified high-risk by the
  // regex; the stubbed tool captures the call so we can verify it was actually invoked.
  let captured: unknown = null;
  const stubBash: ToolRunner = {
    def: { name: "bash", description: "", input_schema: { type: "object", properties: {} } },
    async run(input) {
      captured = input;
      return { output: "stubbed — ok" };
    },
  };
  const tools: ToolRegistry = { bash: stubBash };
  const client = stubClient([
    { content: [{ type: "tool_use", id: "t1", name: "bash", input: { command: "sudo true" } }], stop_reason: "tool_use" },
    { content: [{ type: "text", text: "Done." }], stop_reason: "end_turn" },
  ]);
  const result = await runAgent({
    client,
    model: "m",
    messages: [{ role: "user", content: "run sudo true" }],
    tools,
    confirm: async () => "allow",
  });
  expect(result.blocked).toHaveLength(0);
  expect(result.stop_reason).toBe("end_turn");
  // Verify the gate actually called through to the tool after approval.
  expect(captured).toEqual({ command: "sudo true" });
});
