import { NextResponse } from "next/server";
import { randomUUID } from "node:crypto";
import { getStore } from "@/lib/bridge/db";
import { findEnvironment, findSession, listSessionEvents, resolveBridgeToken } from "@/lib/bridge/store";
import { getContainerDiff, readCliTranscript } from "@/lib/bridge/containers";
import { extractBearer } from "@/lib/bridge/auth";
import { bridgeError } from "@/lib/bridge/errors";

// GET /api/bridge/v1/sessions/{id}/teleport — pull a cloud session down to the
// local terminal (the `jarvis teleport` CLI command, inverse of --remote).
// Returns the repo, the branch the work lives on, and a markdown transcript so
// the local CLI can continue with context. Bearer-authed (the CLI presents its
// JARVIS_BRIDGE_TOKEN).
// Rebuild a RESUMABLE native jsonl from persisted session events, for sessions
// whose container was reclaimed (so readCliTranscript is empty). claude.ai keeps
// conversations server-side, not container-bound — this gives us the same: an
// old session still resumes. Each user/assistant event becomes a transcript line
// with a fresh uuid + parentUuid chain; sessionId/cwd are placeholders the CLI
// rewrites to the local session on teleport. Best-effort: skips unparseable /
// content-less events. Returns "" when there's nothing to resume.
export function reconstructNativeJsonl(
  events: { type: string; payload_json: string; created_at: number }[],
  sessionId: string,
): string {
  const lines: string[] = [];
  let parentUuid: string | null = null;
  for (const e of events) {
    let p: Record<string, unknown>;
    try {
      p = JSON.parse(e.payload_json) as Record<string, unknown>;
    } catch {
      continue;
    }
    let type: "user" | "assistant";
    let message: unknown;
    if (e.type === "assistant" && p.message) {
      type = "assistant";
      message = p.message;
    } else if (e.type === "user" && p.message) {
      type = "user";
      message = p.message;
    } else if (e.type === "user_prompt" && typeof p.prompt === "string" && p.prompt.trim()) {
      type = "user";
      message = { role: "user", content: [{ type: "text", text: p.prompt }] };
    } else {
      continue; // status / result / content-less → not part of the conversation
    }
    const uuid = randomUUID();
    lines.push(
      JSON.stringify({
        type,
        uuid,
        parentUuid,
        sessionId, // rewritten to a fresh local UUID by the CLI on teleport
        cwd: "/workspace", // rewritten to the local checkout by the CLI
        timestamp: new Date(e.created_at).toISOString(),
        userType: "external",
        isSidechain: false,
        message,
      }),
    );
    parentUuid = uuid;
  }
  return lines.length ? lines.join("\n") + "\n" : "";
}

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
  const tokenUserId = token ? resolveBridgeToken(store, token) : null;
  if (!tokenUserId) {
    return bridgeError(401, "unauthorized", "A valid bridge token is required");
  }
  try {
    const session = findSession(store, sessionId);
    if (!session) return bridgeError(404, "not_found", "Session not found");
    // Ownership: a valid bridge token authenticates WHO, but teleport returns a
    // full transcript — verify the token's user owns the session's environment.
    const env = session.environment_id
      ? findEnvironment(store, session.environment_id)
      : null;
    if (env?.user_id && env.user_id !== tokenUserId) {
      return bridgeError(403, "forbidden", "Not your session");
    }
    const meta = session.container_json
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
    const events = listSessionEvents(store, sessionId, 0);
    const transcript = renderTranscript(events);
    // Full-fidelity resume: the CLI's own jsonl from the container (worker runs
    // with --session-id <id>, so the file IS this session). When the container
    // is gone, rebuild a resumable jsonl from the persisted events so the
    // conversation still teleports (claude.ai keeps sessions server-side).
    const nativeJsonl =
      (await readCliTranscript(store, sessionId)) || reconstructNativeJsonl(events, sessionId);
    return NextResponse.json({
      repo: meta.repo,
      branch,
      transcript,
      ...(nativeJsonl ? { native_jsonl: nativeJsonl } : {}),
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return bridgeError(500, "internal_error", `teleport failed: ${msg}`);
  }
}
