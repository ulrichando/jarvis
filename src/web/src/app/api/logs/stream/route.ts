import { spawn, type ChildProcessByStdio } from "node:child_process";
import type { Readable } from "node:stream";
import { type NextRequest } from "next/server";

export const runtime = "nodejs";
// SSE connections are long-lived; let them run for the whole dev session.
export const maxDuration = 3600;

// Allowlist of log files we expose. Prevents path traversal and
// accidental exposure of unrelated files. The list mirrors what
// bin/jarvis-logs aggregates — keep them in sync if either changes.
const ALLOWED: Record<string, string> = {
  "jarvis-desktop.log": "/tmp/jarvis-desktop.log",
  "jarvis-web.log": "/tmp/jarvis-web.log",
  "jarvis-bridge.log": "/tmp/jarvis-bridge.log",
  "jarvis-proxy.log": "/tmp/jarvis-proxy.log",
  "jarvis-hub.log": "/tmp/jarvis-hub.log",
  "jarvis-voice-agent.log": "/tmp/jarvis-voice-agent.log",
  "jarvis-voice-client.log": "/tmp/jarvis-voice-client.log",
  "jarvis-launch.log": "/tmp/jarvis-launch.log",
  "jarvis-web-chat-dbg.log": "/tmp/jarvis-web-chat-dbg.log",
};

/**
 * GET /api/logs/stream?file=jarvis-web.log&tail=200
 *
 * Server-Sent Events stream of the requested log file. Each event
 * payload is a JSON object: { line: string, ts: number }.
 *
 * Spawns `tail -F` so the stream survives log rotation and includes
 * the most recent N lines (default 200) before continuing live. The
 * child process is killed when the client disconnects (req.signal
 * abort listener) so closed browser tabs don't leak tail processes.
 */
export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const fileParam = searchParams.get("file") ?? "jarvis-web.log";
  const tailParam = Number.parseInt(searchParams.get("tail") ?? "200", 10);
  const tailLines = Number.isFinite(tailParam)
    ? Math.min(Math.max(tailParam, 0), 5000)
    : 200;

  const path = ALLOWED[fileParam];
  if (!path) {
    return new Response(
      JSON.stringify({
        error: "unknown_file",
        message: `${fileParam} is not in the allowlist`,
        allowed: Object.keys(ALLOWED),
      }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }

  const encoder = new TextEncoder();
  // stdio: ["ignore", "pipe", "pipe"] gives us a child whose stdin
  // is null but stdout/stderr are real Readable streams — that's
  // ChildProcessByStdio<null, Readable, Readable>.
  let child: ChildProcessByStdio<null, Readable, Readable> | null = null;

  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      // Once the client disconnects, tail's stdout/close callbacks can
      // still fire — enqueue() on a closed controller throws, and since
      // those callbacks run as Node event handlers the throw escaped as
      // an uncaughtException (live crash 2026-06-09: ERR_INVALID_STATE
      // from the socket-close and child-exit paths). Every write goes
      // through this guard; every teardown path flips `closed` first.
      let closed = false;
      const send = (line: string) => {
        if (closed) return;
        try {
          const payload = JSON.stringify({ line, ts: Date.now() });
          controller.enqueue(encoder.encode(`data: ${payload}\n\n`));
        } catch {
          closed = true; // racing teardown — stop writing
        }
      };

      // Heartbeat every 15s so proxies / browsers don't time out idle SSE.
      const heartbeat = setInterval(() => {
        if (closed) return;
        try {
          controller.enqueue(encoder.encode(`: ping\n\n`));
        } catch {
          closed = true;
        }
      }, 15_000);

      const teardown = () => {
        closed = true;
        clearInterval(heartbeat);
        try {
          child?.kill("SIGTERM");
        } catch {
          /* already dead */
        }
        try {
          controller.close();
        } catch {
          /* already closed */
        }
      };

      let spawnedChild: ChildProcessByStdio<null, Readable, Readable>;
      try {
        spawnedChild = spawn("tail", ["-F", "-n", String(tailLines), path], {
          stdio: ["ignore", "pipe", "pipe"],
        });
        child = spawnedChild;
      } catch (err) {
        send(`[stream] spawn tail failed: ${String(err)}`);
        teardown();
        return;
      }

      let buf = "";
      spawnedChild.stdout.on("data", (chunk: Buffer) => {
        buf += chunk.toString("utf8");
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) send(line);
      });
      spawnedChild.stderr.on("data", (chunk: Buffer) => {
        send(`[stream stderr] ${chunk.toString("utf8").trimEnd()}`);
      });
      spawnedChild.on("error", (err) => {
        send(`[stream] tail error: ${String(err)}`);
      });
      spawnedChild.on("close", (code) => {
        send(`[stream] tail exited with code ${code}`);
        teardown();
      });

      // Client disconnect: kill the tail process so it doesn't leak.
      req.signal.addEventListener("abort", teardown);
    },
    cancel() {
      // Stream-side teardown (e.g. response GC'd without abort firing).
      try {
        child?.kill("SIGTERM"); // close handler does the rest
      } catch {
        /* */
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
