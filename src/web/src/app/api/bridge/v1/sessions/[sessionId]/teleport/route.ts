import { NextResponse } from "next/server";
import { getStore } from "@/lib/bridge/db";
import { findSession, listSessionEvents, resolveBridgeToken } from "@/lib/bridge/store";
import { getContainerDiff } from "@/lib/bridge/containers";
import { extractBearer } from "@/lib/bridge/auth";
import { bridgeError } from "@/lib/bridge/errors";

// GET /api/bridge/v1/sessions/{id}/teleport — pull a cloud session down to the
// local terminal (the `jarvis teleport` CLI command, inverse of --remote).
// Returns the repo, the branch the work lives on, and a markdown transcript so
// the local CLI can continue with context. Bearer-authed (the CLI presents its
// JARVIS_BRIDGE_TOKEN).
function renderTranscript(events: { type: string; payload_json: string }[]): string {
  const out: string[] = [];
  for (const e of events) {
    let p: Record<string, unknown>;
    try {
      p = JSON.parse(e.payload_json) as Record<string, unknown>;
    } catch {
      continue;
    }
    const msg = p.message as { content?: unknown } | undefined;
    let text = "";
    if (typeof msg?.content === "string") text = msg.content;
    else if (Array.isArray(msg?.content)) {
      for (const b of msg.content as Array<Record<string, unknown>>) {
        if (b?.type === "text" && typeof b.text === "string") text += b.text + "\n";
      }
    } else if (typeof p.prompt === "string") text = p.prompt;
    else if (typeof p.text === "string") text = p.text;
    text = text.trim();
    if (!text) continue;
    if (e.type === "user_prompt" || e.type === "user") out.push(`## You\n\n${text}`);
    else if (e.type === "assistant" || e.type === "result") out.push(`## Jarvis\n\n${text}`);
  }
  return out.join("\n\n");
}

export async function GET(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params;
  const store = getStore();
  const token = extractBearer(req.headers.get("authorization"));
  if (!token || !resolveBridgeToken(store, token)) {
    return bridgeError(401, "unauthorized", "A valid bridge token is required");
  }
  try {
    const session = findSession(store, sessionId);
    const meta = session?.container_json
      ? (JSON.parse(session.container_json) as { repo?: string })
      : null;
    if (!meta?.repo) {
      return bridgeError(400, "invalid_request", "Session has no repository to teleport");
    }
    // Branch the work is on (falls back to the session-branch convention if the
    // container is already gone).
    const diff = await getContainerDiff(store, sessionId);
    const branch =
      "error" in diff || !diff.branch || diff.branch === "HEAD"
        ? `jarvis/session-${sessionId.slice(0, 8)}`
        : diff.branch;
    const transcript = renderTranscript(listSessionEvents(store, sessionId, 0));
    return NextResponse.json({ repo: meta.repo, branch, transcript });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return bridgeError(500, "internal_error", `teleport failed: ${msg}`);
  }
}
