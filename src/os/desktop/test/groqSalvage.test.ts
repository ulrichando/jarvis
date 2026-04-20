import { test, expect } from "bun:test";
import { salvageToolCall, extractInlineToolCalls } from "../providers/groqClient.ts";

test("salvages bash tool call from closing-tag form", () => {
  const errText = JSON.stringify({
    error: {
      code: "tool_use_failed",
      failed_generation: `<function=bash {"command": "echo hi"}</function>`,
    },
  });
  const block = salvageToolCall(errText);
  expect(block).toBeTruthy();
  expect(block?.type).toBe("tool_use");
  if (block?.type === "tool_use") {
    expect(block.name).toBe("bash");
    expect(block.input).toEqual({ command: "echo hi" });
  }
});

test("salvages glob tool call from self-closing angle-bracket form", () => {
  const errText = JSON.stringify({
    error: {
      code: "tool_use_failed",
      failed_generation: `<function=glob {"pattern": "**/*.ts", "cwd": "/"}>`,
    },
  });
  const block = salvageToolCall(errText);
  expect(block?.type).toBe("tool_use");
  if (block?.type === "tool_use") {
    expect(block.name).toBe("glob");
    expect(block.input).toEqual({ pattern: "**/*.ts", cwd: "/" });
  }
});

test("returns null when code is not tool_use_failed", () => {
  const errText = JSON.stringify({ error: { code: "something_else", message: "nope" } });
  expect(salvageToolCall(errText)).toBeNull();
});

test("returns null for garbage input", () => {
  expect(salvageToolCall("not json")).toBeNull();
  expect(salvageToolCall("")).toBeNull();
});

test("salvages NAME-then-angle-bracket form: <function=web_search>{...}</function>", () => {
  const errText = JSON.stringify({
    error: {
      code: "tool_use_failed",
      failed_generation: `<function=web_search>{"query":"time in india","limit":"1"}</function>`,
    },
  });
  const block = salvageToolCall(errText);
  expect(block?.type).toBe("tool_use");
  if (block?.type === "tool_use") {
    expect(block.name).toBe("web_search");
    expect(block.input).toEqual({ query: "time in india", limit: "1" });
  }
});

test("returns null when failed_generation has no <function=...> pattern", () => {
  const errText = JSON.stringify({
    error: { code: "tool_use_failed", failed_generation: "just some text" },
  });
  expect(salvageToolCall(errText)).toBeNull();
});

test("extracts tool call from inline text (<function=name {args}</function>)", () => {
  const text = `Sure, let me search. <function=web_search {"query": "time in india"}</function>`;
  const calls = extractInlineToolCalls(text);
  expect(calls).toHaveLength(1);
  expect(calls[0]?.name).toBe("web_search");
  expect(calls[0]?.input).toEqual({ query: "time in india" });
});

test("extracts backslash variant (<function\\name {args})", () => {
  const text = `<function\\web_search {"query":"x"}</function>`;
  const calls = extractInlineToolCalls(text);
  expect(calls[0]?.name).toBe("web_search");
});

test("extracts multiple inline tool calls", () => {
  const text = `<function=bash {"command":"ls"}</function> then <function=glob {"pattern":"*.ts"}</function>`;
  const calls = extractInlineToolCalls(text);
  expect(calls).toHaveLength(2);
  expect(calls[0]?.name).toBe("bash");
  expect(calls[1]?.name).toBe("glob");
});

test("extractInlineToolCalls returns empty on plain text", () => {
  expect(extractInlineToolCalls("Hello, how can I help?")).toHaveLength(0);
});
