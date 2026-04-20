import { test, expect } from "bun:test";
import { webFetchTool } from "../agent/tools/web.ts";

test("web_fetch rejects non-http URLs", async () => {
  const r = await webFetchTool.run({ url: "file:///etc/passwd" });
  expect(r.is_error).toBe(true);
});

test("web_fetch rejects empty url", async () => {
  const r = await webFetchTool.run({ url: "" });
  expect(r.is_error).toBe(true);
});
