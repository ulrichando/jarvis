export type LoopEvent =
  | { type: "start"; task?: string }
  | { type: "text"; text?: string }
  | { type: "action"; summary?: string }
  | { type: "permission_request"; id?: string; action?: string; kind?: string; label?: string; summary?: string }
  | { type: "blocked"; summary?: string }
  | { type: "denied"; summary?: string }
  | { type: "ping" }
  | { type: "done" }
  | { type: "error"; error?: string };

export type PartKind = "text" | "action" | "error" | "done" | "blocked" | "permission";
export type Part = {
  kind: PartKind;
  text: string;
  reqId?: string;
  label?: string;
  resolved?: "once" | "session" | "deny";
  ts?: number;
  thumb?: string;
};
export type ChatMsg = { role: "user" | "assistant"; parts: Part[] };

/** Map a sidecar SSE frame to a timeline Part, or null for frames that add no row. */
export function eventToPart(evt: LoopEvent, now: number): Part | null {
  switch (evt.type) {
    case "text":   return evt.text ? { kind: "text", text: evt.text, ts: now } : null;
    case "action": return evt.summary ? { kind: "action", text: evt.summary, ts: now } : null;
    case "permission_request":
      return evt.id
        ? { kind: "permission", reqId: evt.id, label: evt.label ?? "this action", text: evt.summary ?? "", ts: now }
        : null;
    case "blocked": return evt.summary ? { kind: "blocked", text: evt.summary, ts: now } : null;
    case "error":   return evt.error ? { kind: "error", text: evt.error, ts: now } : null;
    case "done":    return { kind: "done", text: "Done", ts: now };
    default:        return null; // start / ping / denied -> no new row
  }
}

/** "14:32:05" local wall-clock for a step row. */
export function formatStepTime(ts: number): string {
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** "0:38" elapsed-since-run for the Activity counter. */
export function formatElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Downscale w×h to fit maxW, preserving aspect (for canvas thumbnails). */
export function computeThumbSize(w: number, h: number, maxW: number): { w: number; h: number } {
  if (w <= 0 || h <= 0) return { w: 0, h: 0 };
  if (w <= maxW) return { w, h };
  return { w: maxW, h: Math.round(h * (maxW / w)) };
}
