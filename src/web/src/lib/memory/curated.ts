// Curated memory contract — the cloud port of the local voice agent's
// file-backed memory (src/voice-agent/pipeline/file_memory.py + the tool
// layer in src/voice-agent/tools/memory.py). Three stores per user:
//
//   - "user"      → who the user is: role, preferences, communication style.
//   - "memory"    → the assistant's own notes: environment facts, project
//     conventions, tool quirks, lessons learned.
//   - "procedure" → named multi-step processes, invoked by name and replayed
//     step-by-step (8000 char cap, larger than MEMORY/USER).
//
// Where the local implementation persists §-delimited entries in files under
// ~/.jarvis/memories/ guarded by an fcntl lock, the cloud keeps one
// web.curated_memories row per (user_id, kind) with a jsonb entries array,
// mutated inside a SELECT ... FOR UPDATE transaction (see
// src/app/api/memory/route.ts). THIS module is the pure contract — budgets,
// overflow rules, injection/exfil scan, meta-paraphrase filter, dedup, the
// tool-call dispatch, and the ════-header snapshot rendering — so it is
// directly testable and byte-compatible with the local behavior.
//
// All regexes, budgets, and message strings are ported VERBATIM from
// file_memory.py / tools/memory.py — they are load-bearing: accepted content
// lands verbatim in the voice agent's system prompt.

export const ENTRY_DELIMITER = "\n§\n";

// Char budgets per store (file_memory.py). MEMORY is a ROLLING store (oldest
// entries evicted on overflow); USER + PROCEDURE hard-reject on overflow so a
// durable identity fact / curated procedure is never silently auto-dropped.
export const MEMORY_CHAR_LIMIT = 3000;
export const USER_CHAR_LIMIT = 2000;
export const PROCEDURE_CHAR_LIMIT = 8000;

// Canonical store targets the tool accepts (file_memory.py::VALID_TARGETS).
export const VALID_TARGETS = ["memory", "user", "procedure"] as const;
export type MemoryTarget = (typeof VALID_TARGETS)[number];

// ---------------------------------------------------------------------------
// Meta-paraphrase reject filter — verbatim port of
// file_memory.py::_META_PARAPHRASE_RE (Python (?ix) verbose regex, flattened;
// alternatives and word lists are character-for-character identical).
// "This string is LLM narration of the conversation, not a durable fact."
// Anchored at start-of-content; case-insensitive. A hedged-but-real project
// fact like "Coding Kiddos appears to involve teaching" intentionally passes.
// ---------------------------------------------------------------------------
const META_PARAPHRASE_RE =
  /(?:^\s*the\s+(?:user|conversation|discussion|topic|exchange)\s+(?:is|was|has|appears|seems|seemed|inquires|expresses|expressed|mentions|describes|asks|asked|seeks|sought|wants|wanted)|^\s*(?:it|this|that)\s+(?:seems|appears|looks|sounds)\s+(?:to|like)\b|^\s*user\s+(?:appears|seems|seemed|inquires|inquired|expresses|expressed|mentions|mentioned|describes|described|asks|asked|seeks|sought|wants|wanted)\b|^\s*the\s+user\s+seeks)/i;

/** True for LLM-meta-narration outputs that must not be stored as a durable
 * fact. See META_PARAPHRASE_RE. */
export function isMetaParaphrase(content: string): boolean {
  return META_PARAPHRASE_RE.test(content || "");
}

// ---------------------------------------------------------------------------
// Content scanning — lightweight injection / exfiltration check for content
// that gets injected verbatim into the system prompt. Patterns are a verbatim
// port of file_memory.py::_MEMORY_THREAT_PATTERNS (case-insensitive there via
// re.IGNORECASE, here via the `i` flag).
// ---------------------------------------------------------------------------

const MEMORY_THREAT_PATTERNS: ReadonlyArray<readonly [RegExp, string]> = [
  // Prompt injection / role hijack
  [/ignore\s+(previous|all|above|prior)\s+instructions/i, "prompt_injection"],
  [/you\s+are\s+now\s+/i, "role_hijack"],
  [/do\s+not\s+tell\s+the\s+user/i, "deception_hide"],
  [/system\s+prompt\s+override/i, "sys_prompt_override"],
  [/disregard\s+(your|all|any)\s+(instructions|rules|guidelines)/i, "disregard_rules"],
  [/act\s+as\s+(if|though)\s+you\s+(have\s+no|don't\s+have)\s+(restrictions|limits|rules)/i, "bypass_restrictions"],
  // Exfiltration via curl/wget carrying a secret-shaped var
  [/curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)/i, "exfil_curl"],
  [/wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)/i, "exfil_wget"],
  [/cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)/i, "read_secrets"],
  // Persistence / key access
  [/authorized_keys/i, "ssh_backdoor"],
  [/\$HOME\/\.ssh|~\/\.ssh/i, "ssh_access"],
  [/\$HOME\/\.jarvis\/\.env|~\/\.jarvis\/\.env/i, "jarvis_env"],
];

// Invisible / bidi-control unicode used to smuggle injection payloads past a
// human reviewer skimming the store. Verbatim codepoint set from
// file_memory.py::_INVISIBLE_CHARS (written as escapes so they stay visible):
// ZWSP, ZWNJ, ZWJ, WORD JOINER, BOM/ZWNBSP, LRE, RLE, PDF, LRO, RLO.
const INVISIBLE_CHARS = [
  "\u200B",
  "\u200C",
  "\u200D",
  "\u2060",
  "\uFEFF",
  "\u202A",
  "\u202B",
  "\u202C",
  "\u202D",
  "\u202E",
] as const;

// C0/C1 control characters, except tab (U+0009), newline (U+000A), and
// carriage return (U+000D). The threat regexes above are ported VERBATIM from
// Python, but Python's \s matches the C0 separators U+001C–U+001F (FS/GS/RS/
// US) and U+0085 (NEL) while JavaScript's \s does not — so a payload like
// "ignore\x1Call\x1Cinstructions" would pass the cloud scan yet be blocked by
// the local store. Rather than patching each regex (breaking verbatim
// fidelity), reject ALL bare control chars: a strict superset of the gap that
// can't be bypassed with another control-char separator. Legit memory text
// never contains bare control characters.
const CONTROL_CHAR_RE = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F]/;

/** Scan content for injection / exfil patterns. Return an error string if
 * the content must be blocked, else null. Port of
 * file_memory.py::scan_memory_content. */
export function scanMemoryContent(content: string): string | null {
  for (const char of INVISIBLE_CHARS) {
    if (content.includes(char)) {
      const hex = char
        .codePointAt(0)!
        .toString(16)
        .toUpperCase()
        .padStart(4, "0");
      return (
        `Blocked: content contains invisible unicode character ` +
        `U+${hex} (possible injection).`
      );
    }
  }
  const control = CONTROL_CHAR_RE.exec(content);
  if (control) {
    const hex = control[0]
      .codePointAt(0)!
      .toString(16)
      .toUpperCase()
      .padStart(4, "0");
    return (
      `Blocked: content contains control character ` +
      `U+${hex} (possible injection).`
    );
  }
  for (const [pattern, pid] of MEMORY_THREAT_PATTERNS) {
    if (pattern.test(content)) {
      return (
        `Blocked: content matches threat pattern '${pid}'. Memory ` +
        `entries are injected into the system prompt and must not ` +
        `contain injection or exfiltration payloads.`
      );
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Tool response shape — matches tools/memory.py exactly:
//   success → {success:true, target, entries, usage, entry_count, message?}
//   error   → {success:false, error, ...extra (current_entries/usage/matches)}
// ---------------------------------------------------------------------------

export type MemoryToolSuccess = {
  success: true;
  target: MemoryTarget;
  entries: string[];
  usage: string;
  entry_count: number;
  message?: string;
};

export type MemoryToolError = {
  success: false;
  error: string;
  current_entries?: string[];
  usage?: string;
  matches?: string[];
};

export type MemoryToolResponse = MemoryToolSuccess | MemoryToolError;

function charLimit(target: MemoryTarget): number {
  if (target === "user") return USER_CHAR_LIMIT;
  if (target === "procedure") return PROCEDURE_CHAR_LIMIT;
  return MEMORY_CHAR_LIMIT;
}

function charCount(entries: string[]): number {
  return entries.length ? entries.join(ENTRY_DELIMITER).length : 0;
}

/** Python's `{n:,}` thousands-separator formatting. */
function fmt(n: number): string {
  return n.toLocaleString("en-US");
}

function successResponse(
  target: MemoryTarget,
  entries: string[],
  message?: string,
): MemoryToolSuccess {
  const current = charCount(entries);
  const limit = charLimit(target);
  const pct = limit > 0 ? Math.min(100, Math.trunc((current / limit) * 100)) : 0;
  const resp: MemoryToolSuccess = {
    success: true,
    target,
    entries,
    usage: `${pct}% — ${fmt(current)}/${fmt(limit)} chars`,
    entry_count: entries.length,
  };
  if (message) resp.message = message;
  return resp;
}

/** Order-preserving dedup + trim of a stored jsonb entries array — the cloud
 * analog of file_memory's `list(dict.fromkeys(self._read_file(...)))` (which
 * strips each entry and drops empties on load). */
export function dedupEntries(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of raw) {
    if (typeof item !== "string") continue;
    const trimmed = item.trim();
    if (!trimmed || seen.has(trimmed)) continue;
    seen.add(trimmed);
    out.push(trimmed);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Store mutations — pure ports of MemoryStore.add / replace / remove / read.
// Each takes the current entries and returns the tool response, the next
// entries array, and whether anything changed (i.e. whether to persist).
// ---------------------------------------------------------------------------

export type ApplyResult = {
  response: MemoryToolResponse;
  entries: string[];
  changed: boolean;
};

/** Append a new entry. Error if it would exceed the char limit — except
 * MEMORY, which is a ROLLING window: evict the OLDEST entries until the new
 * one fits, then append (never a silent reject). */
export function applyAdd(
  target: MemoryTarget,
  existing: string[],
  rawContent: string,
): ApplyResult {
  const entries = [...existing];
  const content = (rawContent || "").trim();
  if (!content) {
    return {
      response: { success: false, error: "Content cannot be empty." },
      entries,
      changed: false,
    };
  }

  const scanError = scanMemoryContent(content);
  if (scanError) {
    return {
      response: { success: false, error: scanError },
      entries,
      changed: false,
    };
  }
  if (isMetaParaphrase(content)) {
    return {
      response: {
        success: false,
        error:
          "Blocked: that reads as narration of the conversation " +
          "('The user is…' / 'It seems…'), not a durable fact. " +
          "Store a plain assertion instead.",
      },
      entries,
      changed: false,
    };
  }

  const limit = charLimit(target);

  if (entries.includes(content)) {
    return {
      response: successResponse(
        target,
        entries,
        "Entry already exists (no duplicate added).",
      ),
      entries,
      changed: false,
    };
  }

  const newTotal = [...entries, content].join(ENTRY_DELIMITER).length;
  if (newTotal > limit) {
    // MEMORY is a ROLLING window: rather than silently rejecting new captures
    // once full, evict the OLDEST entries until the new one fits, then
    // append. USER (identity) + PROCEDURE (curated) still hard-reject so a
    // durable fact is never auto-dropped — put identity facts in USER.
    if (target === "memory" && content.length <= limit) {
      while (
        entries.length &&
        [...entries, content].join(ENTRY_DELIMITER).length > limit
      ) {
        entries.shift();
      }
    } else {
      const current = charCount(entries);
      return {
        response: {
          success: false,
          error:
            `Memory at ${fmt(current)}/${fmt(limit)} chars. Adding this ` +
            `entry (${content.length} chars) would exceed the limit. ` +
            `Replace or remove existing entries first.`,
          current_entries: entries,
          usage: `${fmt(current)}/${fmt(limit)}`,
        },
        entries,
        changed: false,
      };
    }
  }

  entries.push(content);
  return {
    response: successResponse(target, entries, "Entry added."),
    entries,
    changed: true,
  };
}

/** Find matching entries by substring; mirrors the local matching rule:
 * multiple matches are only ambiguous when they span >1 DISTINCT entry
 * text. */
function matchEntries(
  entries: string[],
  oldText: string,
): { indices: number[]; texts: string[] } {
  const indices: number[] = [];
  const texts: string[] = [];
  entries.forEach((e, i) => {
    if (e.includes(oldText)) {
      indices.push(i);
      texts.push(e);
    }
  });
  return { indices, texts };
}

function previews(texts: string[]): string[] {
  return texts.map((e) => e.slice(0, 80) + (e.length > 80 ? "..." : ""));
}

/** Find the entry containing `old_text` and replace it wholesale. */
export function applyReplace(
  target: MemoryTarget,
  existing: string[],
  rawOldText: string,
  rawNewContent: string,
): ApplyResult {
  const entries = [...existing];
  const oldText = (rawOldText || "").trim();
  const newContent = (rawNewContent || "").trim();
  if (!oldText) {
    return {
      response: { success: false, error: "old_text cannot be empty." },
      entries,
      changed: false,
    };
  }
  if (!newContent) {
    return {
      response: {
        success: false,
        error: "content cannot be empty. Use 'remove' to delete entries.",
      },
      entries,
      changed: false,
    };
  }

  const scanError = scanMemoryContent(newContent);
  if (scanError) {
    return {
      response: { success: false, error: scanError },
      entries,
      changed: false,
    };
  }
  if (isMetaParaphrase(newContent)) {
    return {
      response: {
        success: false,
        error:
          "Blocked: that reads as narration, not a durable fact. " +
          "Store a plain assertion instead.",
      },
      entries,
      changed: false,
    };
  }

  const { indices, texts } = matchEntries(entries, oldText);
  if (indices.length === 0) {
    return {
      response: { success: false, error: `No entry matched '${oldText}'.` },
      entries,
      changed: false,
    };
  }
  if (indices.length > 1 && new Set(texts).size > 1) {
    return {
      response: {
        success: false,
        error: `Multiple entries matched '${oldText}'. Be more specific.`,
        matches: previews(texts),
      },
      entries,
      changed: false,
    };
  }

  const idx = indices[0]!;
  const limit = charLimit(target);
  const test = [...entries];
  test[idx] = newContent;
  const testTotal = test.join(ENTRY_DELIMITER).length;
  if (testTotal > limit) {
    return {
      response: {
        success: false,
        error:
          `Replacement would put memory at ` +
          `${fmt(testTotal)}/${fmt(limit)} chars. ` +
          `Shorten the new content or remove other entries first.`,
      },
      entries,
      changed: false,
    };
  }

  entries[idx] = newContent;
  return {
    response: successResponse(target, entries, "Entry replaced."),
    entries,
    changed: true,
  };
}

/** Remove the entry containing `old_text`. */
export function applyRemove(
  target: MemoryTarget,
  existing: string[],
  rawOldText: string,
): ApplyResult {
  const entries = [...existing];
  const oldText = (rawOldText || "").trim();
  if (!oldText) {
    return {
      response: { success: false, error: "old_text cannot be empty." },
      entries,
      changed: false,
    };
  }

  const { indices, texts } = matchEntries(entries, oldText);
  if (indices.length === 0) {
    return {
      response: { success: false, error: `No entry matched '${oldText}'.` },
      entries,
      changed: false,
    };
  }
  if (indices.length > 1 && new Set(texts).size > 1) {
    return {
      response: {
        success: false,
        error: `Multiple entries matched '${oldText}'. Be more specific.`,
        matches: previews(texts),
      },
      entries,
      changed: false,
    };
  }

  entries.splice(indices[0]!, 1);
  return {
    response: successResponse(target, entries, "Entry removed."),
    entries,
    changed: true,
  };
}

/** Return the live entries for a store. */
export function applyRead(target: MemoryTarget, existing: string[]): ApplyResult {
  const entries = [...existing];
  return {
    response: successResponse(target, entries),
    entries,
    changed: false,
  };
}

// ---------------------------------------------------------------------------
// Tool-call dispatch — port of tools/memory.py::_handle_memory's validation
// layer (action/target coercion, required params, procedure kebab-name +
// "## {name}\n{content}" prepend). Returns either a ready-to-return error
// response or a prepared op for the route to run inside its transaction.
// ---------------------------------------------------------------------------

// tools/memory.py::_PROCEDURE_NAME_RE
export const PROCEDURE_NAME_RE = /^[a-z0-9]+(-[a-z0-9]+)*$/;

export type PreparedOp =
  | { op: "add"; target: MemoryTarget; content: string }
  | { op: "replace"; target: MemoryTarget; oldText: string; content: string }
  | { op: "remove"; target: MemoryTarget; oldText: string }
  | { op: "read"; target: MemoryTarget };

export type MemoryToolArgs = {
  action?: unknown;
  target?: unknown;
  content?: unknown;
  old_text?: unknown;
  name?: unknown;
};

export function prepareToolCall(
  args: MemoryToolArgs,
): { error: MemoryToolError } | PreparedOp {
  const action = String(args.action ?? "").trim().toLowerCase();
  const target = String(args.target ?? "memory").trim().toLowerCase();
  const content = args.content == null ? "" : String(args.content);
  const oldText = args.old_text == null ? "" : String(args.old_text);

  if (!(VALID_TARGETS as readonly string[]).includes(target)) {
    return {
      error: {
        success: false,
        error: `Invalid target '${target}'. Use 'memory', 'user', or 'procedure'.`,
      },
    };
  }
  const validTarget = target as MemoryTarget;

  if (action === "add") {
    if (!content) {
      return {
        error: { success: false, error: "content is required for 'add'." },
      };
    }
    if (validTarget === "procedure") {
      const name = String(args.name ?? "").trim();
      if (!name) {
        return {
          error: {
            success: false,
            error:
              "name is required for action='add' with target='procedure'. " +
              "Use kebab-case (e.g. 'deploy-app').",
          },
        };
      }
      if (!PROCEDURE_NAME_RE.test(name)) {
        return {
          error: {
            success: false,
            error: `name '${name}' is not kebab-case. Use lowercase letters/digits/dashes only.`,
          },
        };
      }
      // Prepend the name as a heading so the entry is self-describing in the
      // snapshot: "## deploy-app\n1. ...".
      return { op: "add", target: validTarget, content: `## ${name}\n${content}` };
    }
    return { op: "add", target: validTarget, content };
  }
  if (action === "replace") {
    if (!oldText) {
      return {
        error: { success: false, error: "old_text is required for 'replace'." },
      };
    }
    if (!content) {
      return {
        error: { success: false, error: "content is required for 'replace'." },
      };
    }
    return { op: "replace", target: validTarget, oldText, content };
  }
  if (action === "remove") {
    if (!oldText) {
      return {
        error: { success: false, error: "old_text is required for 'remove'." },
      };
    }
    return { op: "remove", target: validTarget, oldText };
  }
  if (action === "read") {
    return { op: "read", target: validTarget };
  }
  return {
    error: {
      success: false,
      error: `Unknown action '${action}'. Use: add, replace, remove, read.`,
    },
  };
}

/** Run a prepared op against the current entries. */
export function applyToolCall(op: PreparedOp, entries: string[]): ApplyResult {
  switch (op.op) {
    case "add":
      return applyAdd(op.target, entries, op.content);
    case "replace":
      return applyReplace(op.target, entries, op.oldText, op.content);
    case "remove":
      return applyRemove(op.target, entries, op.oldText);
    case "read":
      return applyRead(op.target, entries);
  }
}

// ---------------------------------------------------------------------------
// Snapshot rendering — port of MemoryStore._render_block +
// snapshot_for_prompt (USER, then MEMORY, then PROCEDURES, joined by blank
// lines; empty stores render nothing).
// ---------------------------------------------------------------------------

export function renderBlock(target: MemoryTarget, entries: string[]): string {
  if (!entries.length) return "";
  const limit = charLimit(target);
  const content = entries.join(ENTRY_DELIMITER);
  const current = content.length;
  const pct = limit > 0 ? Math.min(100, Math.trunc((current / limit) * 100)) : 0;
  let header: string;
  if (target === "user") {
    header = `USER PROFILE (who Ulrich is) [${pct}% — ${fmt(current)}/${fmt(limit)} chars]`;
  } else if (target === "procedure") {
    header = `PROCEDURES (named multi-step processes) [${pct}% — ${fmt(current)}/${fmt(limit)} chars]`;
  } else {
    header = `MEMORY (your durable notes) [${pct}% — ${fmt(current)}/${fmt(limit)} chars]`;
  }
  const sep = "═".repeat(46);
  return `${sep}\n${header}\n${sep}\n${content}`;
}

export type MemorySnapshot = {
  user_block: string;
  memory_block: string;
  procedure_block: string;
  snapshot_text: string;
};

export function renderSnapshot(
  userEntries: string[],
  memoryEntries: string[],
  procedureEntries: string[],
): MemorySnapshot {
  const userBlock = renderBlock("user", userEntries);
  const memoryBlock = renderBlock("memory", memoryEntries);
  const procedureBlock = renderBlock("procedure", procedureEntries);
  const snapshotText = [userBlock, memoryBlock, procedureBlock]
    .filter((p) => p)
    .join("\n\n");
  return {
    user_block: userBlock,
    memory_block: memoryBlock,
    procedure_block: procedureBlock,
    snapshot_text: snapshotText,
  };
}
