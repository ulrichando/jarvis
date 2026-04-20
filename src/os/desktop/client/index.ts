#!/usr/bin/env bun
// misty-talk — one-shot voice client. Run: bun run client/index.ts [--loop] [--url=http://...]
//
// Flow per turn:
//   press Enter → record mic → press Enter → transcribe → think → TTS → play reply.

import { talkOnce } from "./talk.ts";

type Args = { url: string; loop: boolean; interactive: boolean };

function parseArgs(argv: string[]): Args {
  const out: Args = { url: "http://127.0.0.1:8765", loop: false, interactive: false };
  for (const a of argv) {
    if (a === "--loop") out.loop = true;
    else if (a === "--interactive") out.interactive = true;
    else if (a.startsWith("--url=")) out.url = a.slice("--url=".length);
  }
  return out;
}

async function main(): Promise<void> {
  const args = parseArgs(process.argv.slice(2));
  console.log(`[misty-talk] target ${args.url}${args.interactive ? " (interactive confirmations)" : ""}`);
  let messages: unknown[] = [];

  do {
    try {
      const res = await talkOnce({
        baseUrl: args.url,
        messages,
        interactive: args.interactive,
      });
      console.log(`\n[you]     ${res.transcript.trim()}`);
      console.log(`[misty]   ${res.reply.trim()}`);
      if (!res.audioPlayed) console.log(`[warn]    audio playback failed; reply text only`);
      messages = res.messages;
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
