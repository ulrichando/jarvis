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

test("screen tool surfaces capture failure when vision is configured but grim is missing", async () => {
  const vision: VisionClient = {
    name: "fake",
    async describe() { return "a terminal"; },
  };
  const tool = createScreenTool(vision);
  const result = await tool.run({});
  // On the dev host, grim won't exist → spawn fails → capture throws → tool returns is_error.
  expect(result.is_error).toBe(true);
  expect(result.output).toContain("screen capture/describe failed");
});
