import "server-only";

import { execFile, spawn } from "node:child_process";
import { appendFileSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

// Bridge a fired scheduled task to the JARVIS voice agent so it reminds the
// user out loud "when the time comes". Two rails, both belonging to the voice
// stack already (no voice-agent code change):
//   1. LIVE  — POST the voice-client HTTP API's /user-input; the supervisor
//      voices a one-sentence "it's ready, want me to read it?" and holds the
//      result in context, so a spoken "yes" reads it.
//   2. OFFLINE — append a short reminder to ~/.jarvis/cron/pending.jsonl (the
//      voice agent's existing pending queue); it's spoken on the next session
//      connect as "While you were away: …".
// Plus a best-effort desktop notification as a backstop.

const VOICE_PORT = process.env.JARVIS_VOICE_CLIENT_PORT || "8767";
const CRON_DIR = join(homedir(), ".jarvis", "cron");
const PENDING_FILE = join(CRON_DIR, "pending.jsonl");
const PENDING_LOCK = join(CRON_DIR, ".pending.lock");

// The synthetic user turn we feed the live session. Bracketed so the model
// treats it as an automated directive, not the user speaking. The result is
// embedded (capped) so answering "yes" reads it without another round-trip.
function liveDirective(name: string, result: string): string {
  const capped = result.length > 2500 ? result.slice(0, 2500) + "…" : result;
  return (
    `(Automated — your scheduled task "${name}" just ran. In ONE short sentence, ` +
    `tell me it's ready and ask if I'd like you to read it. Don't read the content ` +
    `unless I say yes. Here is the result to read only if I ask:)\n\n${capped}`
  );
}

async function tryLiveAnnounce(name: string, result: string): Promise<boolean> {
  try {
    const res = await fetch(`http://127.0.0.1:${VOICE_PORT}/user-input`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: liveDirective(name, result) }),
      signal: AbortSignal.timeout(3000),
    });
    // 200 → a session is live and JARVIS will voice it. 503 → no session.
    return res.ok;
  } catch {
    // Voice client down / not running → fall through to the offline queue.
    return false;
  }
}

// Append a reminder to the voice agent's pending queue under the SAME advisory
// lock it uses (fcntl.flock on .pending.lock — flock(1) and fcntl.flock share
// flock(2), so this is mutually exclusive with the Python drain). Falls back to
// a plain append if the `flock` binary is missing (tiny lost-line race — the
// drain read→truncate window is microseconds; acceptable for a reminder).
// ponytail: flock CLI over a native lock dep; plain-append fallback if absent.
function queuePending(name: string, text: string): void {
  mkdirSync(CRON_DIR, { recursive: true });
  const line = JSON.stringify({ job: name, text }) + "\n";
  const fallback = () => {
    try {
      appendFileSync(PENDING_FILE, line);
    } catch {
      /* best-effort */
    }
  };
  try {
    // `cat >> file` under the shared flock, line via stdin (no shell escaping
    // of the JSON). spawn (not execFile) so we can write+close stdin — cat
    // would otherwise block on an unclosed pipe and hold the lock.
    const child = spawn(
      "flock",
      ["-x", PENDING_LOCK, "sh", "-c", `cat >> '${PENDING_FILE}'`],
      { stdio: ["pipe", "ignore", "ignore"] },
    );
    child.on("error", fallback); // flock binary missing → plain append
    child.stdin.on("error", () => {});
    child.stdin.write(line);
    child.stdin.end();
  } catch {
    fallback();
  }
}

function notifyDesktop(name: string): void {
  // notify-send is absent in headless/container deploys — no-op there.
  execFile("notify-send", [`${name} is ready`, "Your scheduled task just ran."], () => {
    /* best-effort; ignore failure */
  });
}

/** Announce a fired scheduled task by voice. Never throws — a reminder failure
 *  must not break the task run. Off via JARVIS_SCHEDULED_VOICE=0. */
export async function announceScheduledResult(
  name: string,
  result: string,
): Promise<void> {
  if (process.env.JARVIS_SCHEDULED_VOICE === "0") return;
  try {
    const spokenLive = await tryLiveAnnounce(name, result);
    if (!spokenLive) {
      queuePending(
        name,
        "just ran — it's ready in your chats. Ask me to read it any time.",
      );
    }
    notifyDesktop(name);
  } catch {
    /* best-effort — never break the run */
  }
}
