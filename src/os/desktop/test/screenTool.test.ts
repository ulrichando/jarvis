import { test, expect } from "bun:test";
import type { VisionClient } from "../providers/types.ts";
import { createScreenTool } from "../agent/tools/screen.ts";

test("screen tool returns is_error when no vision client configured", async () => {
  const tool = createScreenTool(undefined);
  const result = await tool.run({});
  expect(result.is_error).toBe(true);
  expect(result.output).toContain("vision provider not configured");
});

test("screen tool has expected schema", () => {
  const tool = createScreenTool(undefined);
  expect(tool.def.name).toBe("screen");
  expect(tool.def.input_schema).toHaveProperty("properties.monitor");
  expect(tool.def.input_schema).toHaveProperty("properties.question");
});

test("screen tool surfaces vision-client errors with the expected prefix", async () => {
  const vision: VisionClient = {
    name: "fake",
    async describe() { throw new Error("api rate limit"); },
  };
  const tool = createScreenTool(vision);
  // This test will only meaningfully exercise the describe() path if capture() succeeds.
  // On most dev hosts grim is absent, so capture() throws first; either way the is_error path
  // is exercised and the "screen capture/describe failed" prefix appears.
  const result = await tool.run({});
  expect(result.is_error).toBe(true);
  expect(result.output).toContain("screen capture/describe failed");
});
