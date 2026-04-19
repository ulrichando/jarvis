import { test, expect } from "bun:test";
import { createGroqClient } from "../providers/groqClient.ts";

test("createGroqClient returns an object with name='groq' and a complete fn", () => {
  const client = createGroqClient({ apiKey: "test-key" });
  expect(client.name).toBe("groq");
  expect(typeof client.complete).toBe("function");
});
