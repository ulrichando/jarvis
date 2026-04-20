import { test, expect } from "bun:test";
import { VoiceModeState, isVoiceMode, VOICE_MODES } from "../voice/mode.ts";

test("VOICE_MODES is the expected set", () => {
  expect(VOICE_MODES).toEqual(["off", "ptt", "wake"]);
});

test("isVoiceMode accepts valid, rejects invalid", () => {
  expect(isVoiceMode("off")).toBe(true);
  expect(isVoiceMode("ptt")).toBe(true);
  expect(isVoiceMode("wake")).toBe(true);
  expect(isVoiceMode("invalid")).toBe(false);
  expect(isVoiceMode(null)).toBe(false);
  expect(isVoiceMode(undefined)).toBe(false);
  expect(isVoiceMode(1)).toBe(false);
});

test("VoiceModeState defaults to off", () => {
  const s = new VoiceModeState();
  expect(s.get().mode).toBe("off");
});

test("set updates mode and timestamp when changing", async () => {
  const s = new VoiceModeState();
  const t0 = s.get().changedAt;
  await Bun.sleep(5);
  const next = s.set("ptt");
  expect(next.mode).toBe("ptt");
  expect(next.changedAt).toBeGreaterThan(t0);
});

test("set does not bump timestamp for a no-op set to same value", async () => {
  const s = new VoiceModeState();
  s.set("ptt");
  const t1 = s.get().changedAt;
  await Bun.sleep(5);
  const same = s.set("ptt");
  expect(same.changedAt).toBe(t1);
});

test("set throws on invalid mode", () => {
  const s = new VoiceModeState();
  expect(() => s.set("bogus" as never)).toThrow(/invalid voice mode/);
});

test("cycle walks off → ptt → wake → off", () => {
  const s = new VoiceModeState();
  expect(s.get().mode).toBe("off");
  expect(s.cycle().mode).toBe("ptt");
  expect(s.cycle().mode).toBe("wake");
  expect(s.cycle().mode).toBe("off");
});
