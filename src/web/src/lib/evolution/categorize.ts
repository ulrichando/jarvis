// Categorize an evolution proposal / queued intent into one user-facing bucket,
// from the files it touches + its intent text. Heuristic; order matters
// (most-specific first). The voice-agent is the only tree auto-mod edits, so
// paths are relative to src/voice-agent/ (e.g. "desktop-tauri/...", "prompts/soul.md").

export type Category =
  | "Functionality"
  | "Reliability"
  | "Performance"
  | "Voice / Persona"
  | "UI"
  | "Safety"
  | "Maintenance";

export const CATEGORIES: Category[] = [
  "Functionality",
  "Reliability",
  "Performance",
  "Voice / Persona",
  "UI",
  "Safety",
  "Maintenance",
];

// Tailwind tone per category (dot + chip), within the existing palette.
export const CATEGORY_TONE: Record<Category, string> = {
  Functionality: "text-sky-500",
  Reliability: "text-emerald-500",
  Performance: "text-violet-500",
  "Voice / Persona": "text-amber-500",
  UI: "text-pink-500",
  Safety: "text-rose-500",
  Maintenance: "text-muted-foreground",
};

const strip = (f: string) => f.replace(/^src\/voice-agent\//, "");

export function categorize(files: string[] | undefined, intent: string | undefined): Category {
  const f = (files ?? []).map(strip);
  const t = (intent ?? "").toLowerCase();
  const inFiles = (re: RegExp) => f.some((x) => re.test(x));

  // UI — the desktop app / tray / kiosk visuals (how he LOOKS).
  if (
    inFiles(/^desktop-tauri\/|kiosk|talking[-_]?face|tray/i) ||
    /\b(desktop|tauri|tray|kiosk|on-?screen|visual|window|button|icon|menu)\b/.test(t)
  )
    return "UI";
  // Voice / Persona — how he TALKS.
  if (
    inFiles(/^prompts\/|soul\.md/i) ||
    /\b(persona|soul|register|tone|how he (talks|sounds)|catchphrase|wording|reply text)\b/.test(t)
  )
    return "Voice / Persona";
  // Safety — guards, confab, security, sanitizers.
  if (
    inFiles(/sanitizer|confab|skill_review|blocklist/i) ||
    /\b(security|injection|guard|safety|confab|gaslight|blocklist|leak|sanitiz)\b/.test(t)
  )
    return "Safety";
  // Performance — latency / streaming / speed.
  if (/\b(latency|streaming|stream|speed|fast(er)?|throughput|ttfw|time[-_ ]?to[-_ ]?first|performance|warm|prefetch)\b/.test(t))
    return "Performance";
  // Reliability — bugfixes / stability / error handling.
  if (
    inFiles(/^resilience\//i) ||
    /\b(fix|bug|crash|error|stale|wedge|hang|silent|recover|stabil|reconnect|fallback|retry|regression|deadlock|leak)\b/.test(t)
  )
    return "Reliability";
  // Maintenance — tests / docs / refactor.
  if (
    inFiles(/(^|\/)tests?\/|\.md$/i) ||
    /\b(refactor|cleanup|tests?|docstring|comment|rename|typo|lint|dead code|docs?)\b/.test(t)
  )
    return "Maintenance";
  // Default — a new capability.
  return "Functionality";
}
