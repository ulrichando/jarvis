import { test, expect } from "bun:test";
import { synthesize } from "../voice/tts.ts";

function stubFetch(response: Response): typeof fetch {
  return (async () => response) as unknown as typeof fetch;
}

test("synthesize posts JSON to Groq and returns audio bytes", async () => {
  let capturedUrl = "";
  let capturedBody = "";
  let capturedAuth = "";
  const bytes = new Uint8Array([0x52, 0x49, 0x46, 0x46]); // RIFF header
  const fetchFn = (async (url: string, init?: RequestInit) => {
    capturedUrl = String(url);
    capturedBody = String(init?.body);
    capturedAuth = String((init?.headers as Record<string, string>)?.authorization ?? "");
    return new Response(bytes, { status: 200 });
  }) as unknown as typeof fetch;

  const result = await synthesize({ apiKey: "k", text: "hello", fetchFn });
  expect(capturedUrl).toContain("groq.com");
  expect(capturedBody).toContain('"input":"hello"');
  expect(capturedAuth).toBe("Bearer k");
  expect(result[0]).toBe(0x52);
});

test("synthesize uses default voice when none provided", async () => {
  let capturedBody = "";
  const fetchFn = (async (_url: string, init?: RequestInit) => {
    capturedBody = String(init?.body);
    return new Response(new Uint8Array(), { status: 200 });
  }) as unknown as typeof fetch;
  await synthesize({ apiKey: "k", text: "t", fetchFn });
  expect(capturedBody).toContain('"voice":"daniel"');
});

test("synthesize throws on non-2xx", async () => {
  const fetchFn = stubFetch(new Response("bad request body", { status: 400 }));
  await expect(synthesize({ apiKey: "k", text: "t", fetchFn })).rejects.toThrow(/TTS failed \(400\)/);
});
