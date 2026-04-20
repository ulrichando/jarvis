#!/usr/bin/env bun
// misty-talk — voice client. Three modes:
//   bun run client/index.ts [--loop] [--url=...]                    Manual Enter-to-record
//   bun run client/index.ts --wake-listen [--duration-ms=6000]      Listen on /ws, auto-record on wake
//   bun run client/index.ts --url=...                                Single turn, then exit

import { talkOnce } from "./talk.ts";
import { startRecording, playAudio } from "./audio.ts";

type Args = {
  url: string;
  loop: boolean;
  interactive: boolean;
  wakeListen: boolean;
  autoListen: boolean;
  durationMs: number;
  chunkSec: number;
  vadThreshold: number;
};

function parseArgs(argv: string[]): Args {
  const out: Args = {
    url: "http://127.0.0.1:8765",
    loop: false,
    interactive: false,
    wakeListen: false,
    autoListen: false,
    durationMs: 6000,
    chunkSec: 5,
    vadThreshold: 0.15,
  };
  for (const a of argv) {
    if (a === "--loop") out.loop = true;
    else if (a === "--interactive") out.interactive = true;
    else if (a === "--wake-listen") out.wakeListen = true;
    else if (a === "--auto-listen") out.autoListen = true;
    else if (a.startsWith("--url=")) out.url = a.slice("--url=".length);
    else if (a.startsWith("--duration-ms=")) out.durationMs = Number(a.slice("--duration-ms=".length));
    else if (a.startsWith("--chunk-sec=")) out.chunkSec = Number(a.slice("--chunk-sec=".length));
    else if (a.startsWith("--vad=")) out.vadThreshold = Number(a.slice("--vad=".length));
  }
  return out;
}


async function runManualTurn(args: Args, messages: unknown[]): Promise<unknown[]> {
  const res = await talkOnce({
    baseUrl: args.url,
    messages,
    interactive: args.interactive,
  });
  console.log(`\n[you]     ${res.transcript.trim()}`);
  console.log(`[misty]   ${res.reply.trim()}`);
  if (!res.audioPlayed) console.log(`[warn]    audio playback failed; reply text only`);
  return res.messages;
}

async function runAutoTurn(args: Args, messages: unknown[]): Promise<unknown[]> {
  console.log(`[misty-talk] recording ${args.durationMs}ms...`);
  const res = await talkOnce({
    baseUrl: args.url,
    messages,
    interactive: args.interactive,
    autoRecordMs: args.durationMs,
  });
  console.log(`[you]     ${res.transcript.trim()}`);
  console.log(`[misty]   ${res.reply.trim()}`);
  return res.messages;
}

async function postPhase(baseUrl: string, phase: "idle" | "listening" | "thinking" | "speaking", audioLevel?: number): Promise<void> {
  try {
    await fetch(`${baseUrl}/api/turn/state`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ phase, audioLevel }),
    });
  } catch { /* HUD is optional — don't crash the turn */ }
}

async function setSourceMute(mute: boolean): Promise<void> {
  // Prevents the echo loop where misty's own TTS comes back through the mic
  // and triggers another turn. @DEFAULT_SOURCE@ expands to the active input.
  try {
    const proc = Bun.spawn(["pactl", "set-source-mute", "@DEFAULT_SOURCE@", mute ? "1" : "0"], {
      stdout: "ignore",
      stderr: "ignore",
    });
    await proc.exited;
  } catch { /* pactl missing or pipewire not up — caller continues */ }
}

// Whisper hallucinates these on silence / music / echo tails. They're also
// what Whisper outputs when fed a fragment of misty's own TTS bleeding back
// through host speaker → host mic → VM mic. Drop them unconditionally —
// a legit "thanks" from the user is rarely the sole input and doesn't need
// a full agent turn anyway.
const SILENCE_HALLUCINATIONS = new Set([
  "thank you.", "thank you", "thanks.", "thanks",
  "thank you for watching.", "thank you for watching",
  "thanks for watching.", "thanks for watching",
  "thank you for watching!", "thanks for watching!",
  "bye.", "bye", "goodbye.", "goodbye",
  ".", "..", "...", "you", "you.",
  "ご視聴ありがとうございました", // JP: "thanks for watching" — Whisper emits this on noise
  "[music]", "[applause]", "[laughter]",
]);

function isLikelyHallucination(transcript: string, _peak: number): boolean {
  const t = transcript.trim().toLowerCase();
  if (t.length < 3) return true;
  if (/^\[[^\]]+\]$/.test(t)) return true;
  if (SILENCE_HALLUCINATIONS.has(t)) return true;
  return false;
}

async function autoListen(args: Args): Promise<never> {
  // Always unmute on exit so a crash/restart doesn't leave the mic dead.
  const cleanupMute = async () => { await setSourceMute(false); };
  process.on("SIGINT", () => { cleanupMute().finally(() => process.exit(0)); });
  process.on("SIGTERM", () => { cleanupMute().finally(() => process.exit(0)); });

  // Hysteresis: start requires a clear peak, continuing allows softer syllables.
  // Silence window of 1500ms matches natural speaker pauses between phrases
  // without cutting off mid-sentence ("Jarvis... what time..." ).
  const START_THRESHOLD = args.vadThreshold;
  const CONTINUE_THRESHOLD = args.vadThreshold * 0.3;
  const MAX_UTTERANCE_SEC = 15;
  const SILENCE_MS = 1500;
  const NO_VOICE_TIMEOUT_MS = 3000;
  const MIN_VOICE_MS = 300; // require at least this much voice before treating as real utterance

  console.log(`[misty-talk] auto-listen — start/continue ${START_THRESHOLD.toFixed(2)}/${CONTINUE_THRESHOLD.toFixed(3)}, silence ${SILENCE_MS}ms, min-voice ${MIN_VOICE_MS}ms`);
  let messages: unknown[] = [];
  await postPhase(args.url, "listening", 0);
  while (true) {
    let lastLevelPost = 0;
    let voiceStartedAt = 0; // first tick where we crossed START_THRESHOLD
    let lastVoiceAt = 0;    // most recent tick above CONTINUE_THRESHOLD
    let voiceMs = 0;        // cumulative voice duration (rough)
    const recordStartAt = Date.now();
    let peak = 0;

    const rec = startRecording({
      maxSeconds: MAX_UTTERANCE_SEC,
      onLevel: (p) => {
        if (p > peak) peak = p;
        const now = Date.now();
        if (now - lastLevelPost >= 80) {
          lastLevelPost = now;
          fetch(`${args.url}/api/turn/state`, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ phase: "listening", audioLevel: p }),
          }).catch(() => {});
        }
        if (!voiceStartedAt && p > START_THRESHOLD) voiceStartedAt = now;
        if (voiceStartedAt && p > CONTINUE_THRESHOLD) {
          lastVoiceAt = now;
          voiceMs += 80; // approximate: onLevel fires every ~100ms
        }
        // Once we've started, stop on extended silence.
        if (voiceStartedAt && lastVoiceAt && now - lastVoiceAt > SILENCE_MS) rec.stop();
        // Haven't heard anything real yet — re-arm after a few seconds.
        if (!voiceStartedAt && now - recordStartAt > NO_VOICE_TIMEOUT_MS) rec.stop();
      },
    });
    const audio = await rec.done;
    if (!voiceStartedAt || audio.length === 0) continue;
    if (voiceMs < MIN_VOICE_MS) {
      // Brief blip (mouse click, door slam) — not a real utterance.
      continue;
    }
    console.log(`[misty-talk] utterance ended (peak ${(peak * 100).toFixed(1)}%, voice ~${voiceMs}ms, ${audio.length} bytes) — transcribing`);
    // Stage 1: STT only — so we can drop hallucinations BEFORE think+TTS.
    let transcript = "";
    try {
      const form = new FormData();
      form.append("audio", new Blob([audio], { type: "audio/wav" }), "mic.wav");
      const r = await fetch(`${args.url}/api/transcribe`, { method: "POST", body: form });
      if (r.ok) transcript = ((await r.json()) as { text: string }).text ?? "";
    } catch (err) {
      console.error(`[misty-talk] STT failed: ${err instanceof Error ? err.message : String(err)}`);
    }
    if (!transcript.trim() || isLikelyHallucination(transcript, peak)) {
      console.log(`[misty-talk] ignoring "${transcript.trim()}" (silence/hallucination)`);
      await postPhase(args.url, "listening", 0);
      continue;
    }
    console.log(`[you]     ${transcript.trim()}`);
    await postPhase(args.url, "thinking");
    try {
      const res = await talkOnce({
        baseUrl: args.url,
        messages,
        interactive: args.interactive,
        audio,
        player: async (bytes) => {
          await postPhase(args.url, "speaking");
          await setSourceMute(true);
          try { await playAudio(bytes); }
          finally {
            // Keep mic muted 2s after TTS ends. The VM's audio goes through
            // the host speaker and the host mic's reverb tail re-enters the
            // VM mic — 400ms wasn't enough. Two seconds of hard silence
            // reliably breaks the loop without being too sluggish.
            await new Promise((r) => setTimeout(r, 2000));
            await setSourceMute(false);
          }
        },
      });
      console.log(`[misty]   ${res.reply.trim()}`);
      messages = res.messages;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`[misty-talk] turn failed: ${msg}`);
    }
    // Post-speak cooldown: drop one chunk of any residual echo from our own TTS.
    const cooldown = startRecording({ maxSeconds: 1 });
    await cooldown.done;
    await postPhase(args.url, "listening", 0);
  }
}


async function wakeListen(args: Args): Promise<never> {
  const wsUrl = args.url.replace(/^http/, "ws") + "/ws";
  console.log(`[misty-talk] wake-listen mode — connecting to ${wsUrl}`);
  let messages: unknown[] = [];
  let busy = false;

  // Reconnect with a small backoff if the socket drops.
  while (true) {
    await new Promise<void>((resolve) => {
      const ws = new WebSocket(wsUrl);
      ws.addEventListener("open", () => console.log(`[misty-talk] connected, waiting for voice.wake_triggered`));
      ws.addEventListener("message", async (msg) => {
        let event: { type?: string };
        try { event = JSON.parse(msg.data as string); } catch { return; }
        if (event.type !== "voice.wake_triggered") return;
        if (busy) { console.log(`[misty-talk] wake ignored — turn in progress`); return; }
        busy = true;
        try {
          messages = (await runAutoTurn(args, messages)) as unknown[];
        } catch (err) {
          console.error(`[misty-talk] turn failed:`, err instanceof Error ? err.message : err);
        } finally {
          busy = false;
        }
      });
      ws.addEventListener("close", () => { console.log(`[misty-talk] ws closed; reconnecting in 2s`); resolve(); });
      ws.addEventListener("error", () => { /* handled by close */ });
    });
    await new Promise((r) => setTimeout(r, 2000));
  }
}

async function main(): Promise<void> {
  const args = parseArgs(process.argv.slice(2));
  console.log(`[misty-talk] target ${args.url}${args.interactive ? " (interactive confirmations)" : ""}${args.wakeListen ? " (wake-listen)" : ""}`);

  if (args.autoListen) {
    await autoListen(args);
    return;
  }
  if (args.wakeListen) {
    await wakeListen(args);
    return;
  }

  let messages: unknown[] = [];
  do {
    try {
      messages = await runManualTurn(args, messages);
    } catch (err) {
      console.error(`[misty-talk] turn failed:`, err instanceof Error ? err.message : err);
      if (!args.loop) process.exit(1);
    }
  } while (args.loop);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
