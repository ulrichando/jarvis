import { NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import {
  dockerStatus,
  execInRuntime,
  spawnDetached,
} from "@/lib/workspace/docker";
import { resolveSafe } from "@/lib/workspace/storage";

export const runtime = "nodejs";
export const maxDuration = 300;

/**
 * POST /api/workspace/[id]/heal
 *
 * Self-healing dev server. Reads the tail of .jarvis/dev.log, detects
 * known crash signatures, and applies the canonical fix:
 *
 *   - EADDRINUSE → kill any existing dev process inside the container,
 *     then respawn with the patched dev script.
 *   - "Cannot find module 'X'" / "Module not found" → install the
 *     missing package with bun, then restart.
 *   - Server exited with no error / silent crash → just restart.
 *   - Unrecognized error → return { acted: false, hint } so the UI
 *     can surface it to the user.
 *
 * This mirrors what Lovable does (and what Replit Agent does) for
 * runtime resilience: don't bother the user — and don't bother the
 * LLM — for known-pattern recoveries.
 */

type HealAction =
  | "restart"
  | "kill-and-restart"
  | "install-and-restart"
  | "no-op";

type HealResult = {
  acted: boolean;
  signature: string | null;
  action: HealAction;
  details: string;
  installed?: string;
  durationMs: number;
};

// Pattern → diagnosis. First match wins. Most-specific patterns first.
type Signature = {
  name: string;
  match: RegExp;
  // Returns the action + optional payload (e.g., the missing module name).
  diagnose: (m: RegExpMatchArray) => {
    action: HealAction;
    payload?: string;
  };
};

const SIGNATURES: Signature[] = [
  {
    name: "EADDRINUSE",
    match: /EADDRINUSE.*:\s*(\d+)/,
    diagnose: () => ({ action: "kill-and-restart" }),
  },
  {
    name: "address-already-in-use",
    match: /address already in use\s+\S*:?(\d+)?/i,
    diagnose: () => ({ action: "kill-and-restart" }),
  },
  {
    name: "module-not-found",
    match: /Cannot find module ['"]([^'"]+)['"]/,
    diagnose: (m) => ({
      action: "install-and-restart",
      // Capture the bare package name (drop deep paths). 'foo/bar' → 'foo';
      // '@scope/foo/bar' → '@scope/foo'.
      payload: extractPkg(m[1]),
    }),
  },
  {
    name: "module-not-resolved",
    match: /Module not found: Can't resolve ['"]([^'"]+)['"]/,
    diagnose: (m) => ({
      action: "install-and-restart",
      payload: extractPkg(m[1]),
    }),
  },
  {
    name: "uncaughtException",
    match: /uncaughtException|Unhandled (?:promise )?rejection/i,
    diagnose: () => ({ action: "restart" }),
  },
];

function extractPkg(spec: string): string | undefined {
  // Skip relative imports — we can't install those.
  if (spec.startsWith(".") || spec.startsWith("/")) return undefined;
  if (spec.startsWith("@")) {
    // @scope/name(/sub/path)
    const parts = spec.split("/");
    return parts.slice(0, 2).join("/");
  }
  return spec.split("/")[0];
}

async function readDevLogTail(
  id: string,
  bytes = 16_384,
): Promise<string> {
  const logPath = resolveSafe(id, ".jarvis/dev.log");
  try {
    const stat = await fs.stat(logPath);
    if (stat.size <= bytes) return await fs.readFile(logPath, "utf8");
    const fh = await fs.open(logPath, "r");
    try {
      const buf = Buffer.alloc(bytes);
      await fh.read(buf, 0, bytes, stat.size - bytes);
      return buf.toString("utf8");
    } finally {
      await fh.close();
    }
  } catch {
    return "";
  }
}

async function killDevServer(id: string): Promise<void> {
  // Best-effort. We try `pkill` patterns common to next/vite/express
  // entrypoints. Failures swallowed — the next start attempt would
  // crash with EADDRINUSE again if a process really survived.
  await execInRuntime(
    id,
    "pkill -KILL -f 'next-server' 2>&1 || true; pkill -KILL -f 'next dev' 2>&1 || true; pkill -KILL -f 'bun run dev' 2>&1 || true; pkill -KILL -f 'vite' 2>&1 || true; sleep 1",
    { timeoutMs: 10_000 },
  );
}

async function startDevServer(id: string): Promise<void> {
  await spawnDetached(id, "cd /workspace && bun run dev");
}

export async function POST(
  _req: Request,
  ctx: RouteContext<"/api/workspace/[id]/heal">,
) {
  const { id } = await ctx.params;
  const start = Date.now();

  const status = await dockerStatus();
  if (!status.available || !status.imageReady) {
    return NextResponse.json(
      { error: "docker_not_ready" },
      { status: 503 },
    );
  }

  const log = await readDevLogTail(id);
  if (!log) {
    return NextResponse.json({
      acted: false,
      signature: null,
      action: "no-op",
      details: "No dev.log to read.",
      durationMs: Date.now() - start,
    } satisfies HealResult);
  }

  // Find the FIRST matching signature, scanning the most-recent lines
  // first. We reverse so a later, fresher error wins over a stale one.
  const lines = log.split("\n").reverse();
  let matched: { sig: Signature; m: RegExpMatchArray } | null = null;
  for (const line of lines) {
    for (const sig of SIGNATURES) {
      const m = line.match(sig.match);
      if (m) {
        matched = { sig, m };
        break;
      }
    }
    if (matched) break;
  }

  if (!matched) {
    // No known signature. Return a hint with the last error-ish line so
    // the UI can show the user what went wrong.
    const errLine = lines.find((l) => /error|fail|exception/i.test(l));
    return NextResponse.json({
      acted: false,
      signature: null,
      action: "no-op",
      details: errLine
        ? `Unrecognized error: ${errLine.trim().slice(0, 200)}`
        : "No recognized error pattern in dev.log.",
      durationMs: Date.now() - start,
    } satisfies HealResult);
  }

  const { action, payload } = matched.sig.diagnose(matched.m);

  let installed: string | undefined;
  let details = "";
  try {
    if (action === "kill-and-restart") {
      await killDevServer(id);
      await startDevServer(id);
      details = `Killed stale dev process (${matched.sig.name}) and respawned.`;
    } else if (action === "install-and-restart" && payload) {
      // Install via bun. Skip if the module name doesn't pass a sanity
      // filter (no spaces, no shell metachars).
      if (!/^[@a-zA-Z0-9_./-]+$/.test(payload)) {
        details = `Refused to install suspicious package name: ${payload}`;
        return NextResponse.json({
          acted: false,
          signature: matched.sig.name,
          action: "no-op",
          details,
          durationMs: Date.now() - start,
        } satisfies HealResult);
      }
      await execInRuntime(
        id,
        `cd /workspace && bun add ${payload}`,
        { timeoutMs: 120_000 },
      );
      installed = payload;
      await killDevServer(id);
      await startDevServer(id);
      details = `Installed missing module '${payload}' and respawned dev server.`;
    } else if (action === "restart") {
      await killDevServer(id);
      await startDevServer(id);
      details = `Restarted dev server after ${matched.sig.name}.`;
    } else {
      details = "no action selected";
    }
  } catch (e) {
    return NextResponse.json({
      acted: false,
      signature: matched.sig.name,
      action,
      details: `Heal failed: ${(e as Error).message}`,
      durationMs: Date.now() - start,
    } satisfies HealResult);
  }

  return NextResponse.json({
    acted: true,
    signature: matched.sig.name,
    action,
    details,
    installed,
    durationMs: Date.now() - start,
  } satisfies HealResult);
}
