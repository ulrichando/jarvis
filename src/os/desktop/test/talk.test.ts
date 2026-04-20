// Tests for client/talk.ts with mocked recorder + fetch + player.

import { test, expect } from "bun:test";
import { talkOnce } from "../client/talk.ts";

const WAV = new Uint8Array([0x52, 0x49, 0x46, 0x46, 0x00, 0x00, 0x00, 0x00]); // minimal RIFF header

type FetchCall = { url: string; init?: RequestInit };

function scriptedFetch(map: Record<string, { body: string | Uint8Array; status?: number; contentType?: string }>): {
  fn: typeof fetch;
  calls: FetchCall[];
} {
  const calls: FetchCall[] = [];
  const fn = (async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    const key = Object.keys(map).find((k) => String(url).includes(k));
    if (!key) return new Response("no route", { status: 404 });
    const entry = map[key]!;
    return new Response(entry.body, {
      status: entry.status ?? 200,
      headers: { "content-type": entry.contentType ?? "application/json" },
    });
  }) as unknown as typeof fetch;
  return { fn, calls };
}

function stubRecorder(bytes: Uint8Array): typeof import("../client/audio.ts").startRecording {
  return (() => ({
    done: Promise.resolve(bytes),
    stop() { /* noop */ },
  })) as unknown as typeof import("../client/audio.ts").startRecording;
}

test("talkOnce hits /api/transcribe, /api/think, /api/speak in order and returns reply text", async () => {
  const ttsBytes = new Uint8Array([0x52, 0x49, 0x46, 0x46]);
  const { fn, calls } = scriptedFetch({
    "/api/transcribe": { body: JSON.stringify({ text: "hello there" }) },
    "/api/think": { body: JSON.stringify({
      messages: [
        { role: "user", content: "hello there" },
        { role: "assistant", content: [{ type: "text", text: "hi, nice to meet you" }] },
      ],
      stop_reason: "end_turn",
      blocked: [],
    }) },
    "/api/speak": { body: ttsBytes, contentType: "audio/wav" },
  });

  let played: Uint8Array | undefined;
  const result = await talkOnce({
    baseUrl: "http://fake",
    fetchFn: fn,
    recorder: stubRecorder(WAV),
    prompt: async () => "",
    player: async (b) => { played = b; },
  });

  expect(result.transcript).toBe("hello there");
  expect(result.reply).toContain("hi, nice to meet you");
  expect(result.audioPlayed).toBe(true);
  expect(played).toBeDefined();
  expect(played![0]).toBe(0x52);

  // Order: transcribe, think, speak
  expect(calls[0]!.url).toContain("/api/transcribe");
  expect(calls[1]!.url).toContain("/api/think");
  expect(calls[2]!.url).toContain("/api/speak");
});

test("talkOnce passes ?interactive=1 when flag is set", async () => {
  const { fn, calls } = scriptedFetch({
    "/api/transcribe": { body: JSON.stringify({ text: "x" }) },
    "/api/think": { body: JSON.stringify({ messages: [{ role: "assistant", content: "y" }] }) },
    "/api/speak": { body: new Uint8Array(), contentType: "audio/wav" },
  });

  await talkOnce({
    baseUrl: "http://fake",
    fetchFn: fn,
    recorder: stubRecorder(WAV),
    prompt: async () => "",
    player: async () => {},
    interactive: true,
  });

  const thinkCall = calls.find((c) => c.url.includes("/api/think"))!;
  expect(thinkCall.url).toContain("interactive=1");
});

test("talkOnce throws on empty audio capture", async () => {
  await expect(
    talkOnce({
      baseUrl: "http://fake",
      fetchFn: ((async () => new Response("{}")) as unknown) as typeof fetch,
      recorder: stubRecorder(new Uint8Array()),
      prompt: async () => "",
      player: async () => {},
    }),
  ).rejects.toThrow(/no audio captured/);
});

test("talkOnce throws on empty STT response", async () => {
  const { fn } = scriptedFetch({
    "/api/transcribe": { body: JSON.stringify({ text: "" }) },
  });
  await expect(
    talkOnce({
      baseUrl: "http://fake",
      fetchFn: fn,
      recorder: stubRecorder(WAV),
      prompt: async () => "",
      player: async () => {},
    }),
  ).rejects.toThrow(/empty transcript/);
});

test("talkOnce surfaces audioPlayed=false when player throws, but still returns reply", async () => {
  const { fn } = scriptedFetch({
    "/api/transcribe": { body: JSON.stringify({ text: "q" }) },
    "/api/think": { body: JSON.stringify({ messages: [{ role: "assistant", content: "a" }] }) },
    "/api/speak": { body: new Uint8Array(), contentType: "audio/wav" },
  });
  const result = await talkOnce({
    baseUrl: "http://fake",
    fetchFn: fn,
    recorder: stubRecorder(WAV),
    prompt: async () => "",
    player: async () => { throw new Error("no audio sink"); },
  });
  expect(result.audioPlayed).toBe(false);
  expect(result.reply).toBe("a");
});

test("talkOnce extracts text from a mixed-block assistant message", async () => {
  const { fn } = scriptedFetch({
    "/api/transcribe": { body: JSON.stringify({ text: "do x" }) },
    "/api/think": { body: JSON.stringify({
      messages: [
        { role: "user", content: "do x" },
        { role: "assistant", content: [
          { type: "tool_use", id: "t1", name: "bash", input: {} },
        ] },
        { role: "user", content: [{ type: "tool_result", tool_use_id: "t1", content: "out" }] },
        { role: "assistant", content: [
          { type: "text", text: "Part one." },
          { type: "text", text: "Part two." },
        ] },
      ],
    }) },
    "/api/speak": { body: new Uint8Array(), contentType: "audio/wav" },
  });
  const result = await talkOnce({
    baseUrl: "http://fake",
    fetchFn: fn,
    recorder: stubRecorder(WAV),
    prompt: async () => "",
    player: async () => {},
  });
  expect(result.reply).toContain("Part one.");
  expect(result.reply).toContain("Part two.");
});
