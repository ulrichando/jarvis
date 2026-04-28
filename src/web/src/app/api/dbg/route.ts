import { NextResponse } from "next/server";
import { appendFileSync } from "node:fs";

// Diagnostic sink. Two output channels (stdout + file) so we capture
// events even if one path is blocked.
export const runtime = "nodejs";

const DBG_PATH = "/tmp/jarvis-web-chat-dbg.log";

export async function POST(req: Request) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    body = { _malformed: true };
  }
  const line = `[${new Date().toISOString()}] ${JSON.stringify(body)}\n`;
  // Channel 1: stdout (always visible in dev log)
  console.log("[DBG]", line.trim());
  // Channel 2: file (for grep / tail)
  try {
    appendFileSync(DBG_PATH, line);
  } catch (e) {
    console.error("[DBG] file-write failed:", (e as Error)?.message);
  }
  return NextResponse.json({ ok: true });
}
