// Smoke test: exercise the full talk pipeline against a real misty-core daemon,
// using a pre-recorded WAV instead of real mic capture.
// Usage: bun run client/smoke.ts <base-url> <wav-path>

import { talkOnce } from "./talk.ts";

const [, , baseUrl, wavPath] = process.argv;
if (!baseUrl || !wavPath) {
  console.error("usage: bun run client/smoke.ts <base-url> <wav-path>");
  process.exit(2);
}

const wav = new Uint8Array(await Bun.file(wavPath).arrayBuffer());

const result = await talkOnce({
  baseUrl,
  recorder: () => ({
    done: Promise.resolve(wav),
    stop() { /* noop */ },
  }),
  prompt: async () => "",
  player: async () => { /* no playback; just print the reply */ },
});

console.log("transcript:", result.transcript);
console.log("reply:     ", result.reply);
console.log("audioPlayed:", result.audioPlayed);
console.log("final messages:", result.messages.length);
