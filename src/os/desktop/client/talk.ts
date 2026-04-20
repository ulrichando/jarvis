// One-turn voice interaction: record mic → STT → think → TTS → play.
// Pure composition of audio + HTTP calls; no hotkey/daemon concerns.

import { startRecording, playAudio } from "./audio.ts";

export type TalkOpts = {
  baseUrl: string;                 // e.g. "http://127.0.0.1:8765"
  messages?: unknown[];            // prior conversation turns to include
  interactive?: boolean;           // pass ?interactive=1 to /api/think
  maxSeconds?: number;             // record cap
  /**
   * Record a fixed duration instead of prompting. When set, skips the
   * press-Enter flow and records for autoRecordMs milliseconds.
   */
  autoRecordMs?: number;
  /** Read a line from stdin. Default uses Bun's console reader; tests inject a stub. */
  prompt?: () => Promise<string>;
  /** For tests: swap out the recorder + fetch + player. */
  recorder?: typeof startRecording;
  fetchFn?: typeof fetch;
  player?: (bytes: Uint8Array) => Promise<void>;
};

export type TalkResult = {
  transcript: string;
  reply: string;
  messages: unknown[];             // full updated transcript
  audioPlayed: boolean;
};

const DEFAULT_MAX_SECONDS = 30;

export async function talkOnce(opts: TalkOpts): Promise<TalkResult> {
  const f = opts.fetchFn ?? fetch;
  const recorder = opts.recorder ?? startRecording;
  const player = opts.player ?? playAudio;
  const prompt = opts.prompt ?? defaultPrompt;

  // 1) Record — either prompt-driven (press Enter to start/stop) or timed.
  let audio: Uint8Array;
  if (opts.autoRecordMs && opts.autoRecordMs > 0) {
    const rec = recorder({ maxSeconds: Math.ceil(opts.autoRecordMs / 1000) });
    await new Promise<void>((r) => setTimeout(r, opts.autoRecordMs));
    rec.stop();
    audio = await rec.done;
  } else {
    await prompt();  // "press Enter to start recording"
    const rec = recorder({ maxSeconds: opts.maxSeconds ?? DEFAULT_MAX_SECONDS });
    await prompt();  // "press Enter to stop"
    rec.stop();
    audio = await rec.done;
  }
  if (audio.length === 0) throw new Error("no audio captured");

  // 2) STT.
  const form = new FormData();
  form.append("audio", new Blob([audio], { type: "audio/wav" }), "mic.wav");
  const sttResp = await f(`${opts.baseUrl}/api/transcribe`, { method: "POST", body: form });
  if (!sttResp.ok) throw new Error(`STT failed ${sttResp.status}: ${await sttResp.text()}`);
  const { text: transcript } = (await sttResp.json()) as { text: string };
  if (!transcript || !transcript.trim()) throw new Error("STT returned empty transcript");

  // 3) Think.
  const messages = [...(opts.messages ?? []), { role: "user", content: transcript }];
  const think = await f(
    `${opts.baseUrl}/api/think${opts.interactive ? "?interactive=1" : ""}`,
    { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ messages }) },
  );
  if (!think.ok) throw new Error(`think failed ${think.status}: ${await think.text()}`);
  const thinkBody = (await think.json()) as { messages: Array<{ role: string; content: unknown }> };
  const reply = extractAssistantText(thinkBody.messages);

  // 4) TTS.
  const tts = await f(`${opts.baseUrl}/api/speak`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text: reply }),
  });
  if (!tts.ok) throw new Error(`TTS failed ${tts.status}: ${await tts.text()}`);
  const ttsBytes = new Uint8Array(await tts.arrayBuffer());

  // 5) Play.
  let audioPlayed = false;
  try {
    await player(ttsBytes);
    audioPlayed = true;
  } catch (err) {
    console.error("[misty-talk] playback failed (reply still returned):", err);
  }

  return { transcript, reply, messages: thinkBody.messages, audioPlayed };
}

function extractAssistantText(messages: Array<{ role: string; content: unknown }>): string {
  // Walk the transcript backwards, take the last assistant turn's text content.
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]!;
    if (m.role !== "assistant") continue;
    if (typeof m.content === "string") return m.content;
    if (Array.isArray(m.content)) {
      const texts: string[] = [];
      for (const b of m.content) {
        if (b && typeof b === "object" && (b as { type?: string }).type === "text") {
          texts.push((b as { text: string }).text);
        }
      }
      if (texts.length > 0) return texts.join("\n");
    }
  }
  return "(no text reply)";
}

async function defaultPrompt(): Promise<string> {
  process.stdout.write("press Enter: ");
  const reader = Bun.stdin.stream().getReader();
  try {
    const { value } = await reader.read();
    const text = value ? new TextDecoder().decode(value) : "";
    return text.trim();
  } finally {
    reader.releaseLock();
  }
}
