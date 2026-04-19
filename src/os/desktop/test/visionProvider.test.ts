import { test, expect } from "bun:test";
import { createGeminiVisionClient } from "../providers/geminiClient.ts";
import { createVisionClient } from "../providers/vision.ts";

test("createGeminiVisionClient returns client with name='gemini'", () => {
  const client = createGeminiVisionClient({ apiKey: "test" });
  expect(client.name).toBe("gemini");
  expect(typeof client.describe).toBe("function");
});

test("createVisionClient throws if GEMINI_API_KEY missing", () => {
  expect(() => createVisionClient({
    host: "h", port: 1, provider: "groq", model: "m", apiKey: "k",
    visionProvider: "gemini", visionApiKey: undefined, visionModel: "v",
    ttsVoice: "daniel",
  })).toThrow(/GEMINI_API_KEY/);
});

test("createVisionClient returns gemini client when key is set", () => {
  const client = createVisionClient({
    host: "h", port: 1, provider: "groq", model: "m", apiKey: "k",
    visionProvider: "gemini", visionApiKey: "vk", visionModel: "v",
    ttsVoice: "daniel",
  });
  expect(client.name).toBe("gemini");
});
