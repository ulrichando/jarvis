import { test, expect } from "bun:test";
import { transcribe } from "../voice/stt.ts";

test("transcribe posts multipart form-data and returns text", async () => {
  let capturedUrl = "";
  const fetchFn = (async (url: string) => {
    capturedUrl = String(url);
    return new Response(JSON.stringify({ text: "hello from audio" }), {
      status: 200, headers: { "content-type": "application/json" },
    });
  }) as unknown as typeof fetch;

  const audio = new Uint8Array([0x52, 0x49, 0x46, 0x46]);
  const result = await transcribe({ apiKey: "k", audio, fetchFn });
  expect(capturedUrl).toContain("audio/transcriptions");
  expect(result).toBe("hello from audio");
});

test("transcribe throws on non-2xx", async () => {
  const fetchFn = (async () => new Response("bad audio", { status: 400 })) as unknown as typeof fetch;
  await expect(transcribe({
    apiKey: "k", audio: new Uint8Array(), fetchFn,
  })).rejects.toThrow(/STT failed \(400\)/);
});

test("transcribe throws if response body missing text", async () => {
  const fetchFn = (async () => new Response(JSON.stringify({}), {
    status: 200, headers: { "content-type": "application/json" },
  })) as unknown as typeof fetch;
  await expect(transcribe({
    apiKey: "k", audio: new Uint8Array(), fetchFn,
  })).rejects.toThrow(/missing 'text' field/);
});
