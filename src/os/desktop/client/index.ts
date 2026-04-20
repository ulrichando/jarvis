#!/usr/bin/env bun
// misty-talk — voice client. Three modes:
//   bun run client/index.ts [--loop] [--url=...]                    Manual Enter-to-record
//   bun run client/index.ts --wake-listen [--duration-ms=6000]      Listen on /ws, auto-record on wake
//   bun run client/index.ts --url=...                                Single turn, then exit

import { talkOnce } from "./talk.ts";

type Args = {
  url: string;
  loop: boolean;
  interactive: boolean;
  wakeListen: boolean;
  durationMs: number;
};

function parseArgs(argv: string[]): Args {
  const out: Args = {
    url: "http://127.0.0.1:8765",
    loop: false,
    interactive: false,
    wakeListen: false,
    durationMs: 6000,
  };
  for (const a of argv) {
    if (a === "--loop") out.loop = true;
    else if (a === "--interactive") out.interactive = true;
    else if (a === "--wake-listen") out.wakeListen = true;
    else if (a.startsWith("--url=")) out.url = a.slice("--url=".length);
    else if (a.startsWith("--duration-ms=")) out.durationMs = Number(a.slice("--duration-ms=".length));
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
