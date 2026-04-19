import { test, expect } from "bun:test";
import { loadConfig } from "../config/load.ts";

test("loadConfig defaults to groq + llama-3.3-70b-versatile", () => {
  const cfg = loadConfig({ GROQ_API_KEY: "x" });
  expect(cfg.provider).toBe("groq");
  expect(cfg.model).toBe("llama-3.3-70b-versatile");
  expect(cfg.host).toBe("127.0.0.1");
  expect(cfg.port).toBe(8765);
  expect(cfg.apiKey).toBe("x");
});

test("loadConfig throws on missing api key", () => {
  expect(() => loadConfig({ JARVIS_PROVIDER: "groq" })).toThrow(/missing GROQ_API_KEY/);
});

test("loadConfig throws on unknown provider", () => {
  expect(() => loadConfig({ JARVIS_PROVIDER: "madeup" })).toThrow(/unknown JARVIS_PROVIDER/);
});

test("loadConfig respects JARVIS_MODEL override", () => {
  const cfg = loadConfig({ GROQ_API_KEY: "x", JARVIS_MODEL: "qwen/qwen3-32b" });
  expect(cfg.model).toBe("qwen/qwen3-32b");
});

test("loadConfig rejects invalid MISTY_PORT", () => {
  expect(() => loadConfig({ GROQ_API_KEY: "x", MISTY_PORT: "notanumber" })).toThrow(/invalid MISTY_PORT/);
  expect(() => loadConfig({ GROQ_API_KEY: "x", MISTY_PORT: "99999" })).toThrow(/invalid MISTY_PORT/);
});
