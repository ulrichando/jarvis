import { test, expect } from "bun:test";
import { currentTimeTool } from "../agent/tools/time.ts";

test("current_time returns formatted time for a valid IANA timezone", async () => {
  const r = await currentTimeTool.run({ timezone: "Asia/Kolkata" });
  expect(r.is_error).toBeFalsy();
  expect(r.output).toMatch(/tz: Asia\/Kolkata/);
  expect(r.output).toMatch(/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);
});

test("current_time works for 'local'", async () => {
  const r = await currentTimeTool.run({ timezone: "local" });
  expect(r.is_error).toBeFalsy();
  expect(r.output).toContain("tz: local");
});

test("current_time works for UTC", async () => {
  const r = await currentTimeTool.run({ timezone: "UTC" });
  expect(r.is_error).toBeFalsy();
  expect(r.output).toMatch(/tz: UTC/);
});

test("current_time errors on invalid timezone", async () => {
  const r = await currentTimeTool.run({ timezone: "Not/A/Real/Zone" });
  expect(r.is_error).toBe(true);
});

test("current_time errors on missing timezone", async () => {
  const r = await currentTimeTool.run({});
  expect(r.is_error).toBe(true);
});
