import { NextResponse } from "next/server";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { execInRuntime, dockerStatus } from "@/lib/workspace/docker";
import { runFixers, type FixResult } from "@/lib/fixers";

const execFileP = promisify(execFile);

export const runtime = "nodejs";
export const maxDuration = 600;

/**
 * POST /api/workspace/[id]/verify
 *
 * Verification-as-gate: after JARVIS finishes a turn, the chat layer
 * calls this to confirm the project is in a green state. We:
 *
 *   1. Run rule-based fixers (deterministic regex transforms for known
 *      bugs like deprecated <Link><a>, missing `next/link` imports,
 *      missing data/ dir for SQLite). These don't burn an LLM turn.
 *
 *   2. Run typecheck (`bunx tsc --noEmit`) and capture errors.
 *
 *   3. Curl the dev server (port 5173) to confirm it returns 200.
 *
 *   4. Return a structured result the chat layer feeds into the
 *      auto-retry decision: if anything failed, fire a retry with the
 *      actual error injected; if green, the turn is done.
 *
 * This is what Bolt / Lovable / Replit Agent run after every artifact —
 * production agents don't trust the LLM's "✅ verified" claim, they
 * verify themselves.
 */
type VerifyOutcome = {
  ok: boolean;
  fixers: FixResult[];
  typecheck: { ran: boolean; ok: boolean; output: string };
  preview: { ran: boolean; ok: boolean; status: number | null };
  // Headless-Chromium screenshot of the live preview at "/" (when the
  // dev server is up). Embedded as a data URL so the chat layer can
  // ship it back to the multimodal model as an image part — the model
  // can then visually compare against any design reference.
  screenshot?: { dataUrl: string; bytes: number; target: string };
  durationMs: number;
};

export async function POST(
  _req: Request,
  ctx: RouteContext<"/api/workspace/[id]/verify">,
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

  // Phase 1: rule-based fixers (cheap, no LLM, no docker).
  const fixerResults = await runFixers(id);

  // Phase 2: typecheck. We run via `bunx tsc --noEmit` if the
  // workspace has a tsconfig — otherwise skip. 60s timeout.
  let typecheck: VerifyOutcome["typecheck"] = {
    ran: false,
    ok: true,
    output: "",
  };
  try {
    const tscProbe = await execInRuntime(
      id,
      "test -f tsconfig.json && echo HAS_TSCONFIG || echo NO_TSCONFIG",
      { timeoutMs: 5_000 },
    );
    if (tscProbe.stdout.includes("HAS_TSCONFIG")) {
      // Try common typecheck entrypoints in order. bunx isn't always on
      // the bash login shell's PATH inside the container even when bun
      // itself is — fall back to npx (which the workbench image always
      // has) or to running the local node_modules/.bin/tsc directly.
      const r = await execInRuntime(
        id,
        "{ command -v bunx >/dev/null && bunx tsc --noEmit; } || { command -v npx >/dev/null && npx --no-install tsc --noEmit; } || ./node_modules/.bin/tsc --noEmit 2>&1 | head -200",
        { timeoutMs: 90_000 },
      );
      typecheck = {
        ran: true,
        ok: r.exitCode === 0,
        // Cap output so a multi-thousand-error tsc dump doesn't blow
        // the next prompt's context. Head + tail = enough signal.
        output:
          r.stdout.length > 4_000
            ? `${r.stdout.slice(0, 2_500)}\n…[clipped]…\n${r.stdout.slice(-1_500)}`
            : r.stdout,
      };
    }
  } catch (err) {
    typecheck = {
      ran: true,
      ok: false,
      output: `verify: tsc exec error: ${(err as Error).message}`,
    };
  }

  // Phase 3: curl the dev server. We hit container's localhost:5173
  // from inside the container — the dev server should be up if a
  // start action ran earlier in the conversation.
  let preview: VerifyOutcome["preview"] = {
    ran: false,
    ok: true,
    status: null,
  };
  try {
    const r = await execInRuntime(
      id,
      "curl -sS -o /dev/null -w '%{http_code}' http://localhost:5173 --max-time 5 || echo CONNREFUSED",
      { timeoutMs: 10_000 },
    );
    const code = r.stdout.trim();
    if (code === "CONNREFUSED" || code === "") {
      // No dev server running yet — not a failure of THIS turn,
      // just no preview to verify. ok=true so we don't trigger a
      // retry on a workspace that doesn't have a `start` action yet.
      preview = { ran: true, ok: true, status: null };
    } else {
      const status = parseInt(code, 10);
      preview = {
        ran: true,
        ok: status >= 200 && status < 400,
        status,
      };
    }
  } catch {
    preview = { ran: true, ok: true, status: null };
  }

  // Phase 4: screenshot the live preview if it's up. We only attempt
  // when the curl above returned a real HTTP code (so the dev server
  // is listening). Failure swallowed — the verify result without a
  // screenshot is still useful, the model just won't have visual
  // comparison this turn.
  let screenshot: VerifyOutcome["screenshot"] | undefined;
  if (preview.ran && preview.status && preview.status < 500) {
    try {
      // Resolve the host port for container's 5173.
      const containerName = `jarvis-ws-${id}`;
      const portOut = await execFileP("docker", [
        "port",
        containerName,
        "5173/tcp",
      ]);
      const m = portOut.stdout.match(/:(\d+)\s*$/m);
      if (m) {
        const hostPort = parseInt(m[1], 10);
        const target = `http://localhost:${hostPort}/`;
        const { chromium } = await import("playwright");
        const browser = await chromium.launch({ headless: true });
        try {
          const page = await browser.newPage({
            viewport: { width: 1280, height: 800 },
            deviceScaleFactor: 1,
          });
          await page
            .goto(target, { waitUntil: "networkidle", timeout: 12_000 })
            .catch(() =>
              page.goto(target, { waitUntil: "load", timeout: 5_000 }),
            );
          await page.waitForTimeout(300);
          const png = await page.screenshot({ type: "png", fullPage: false });
          screenshot = {
            dataUrl: `data:image/png;base64,${png.toString("base64")}`,
            bytes: png.byteLength,
            target,
          };
          await page.close().catch(() => {});
        } finally {
          await browser.close().catch(() => {});
        }
      }
    } catch {
      /* screenshot is best-effort; verify result is fine without it */
    }
  }

  const ok = typecheck.ok && preview.ok;
  return NextResponse.json({
    ok,
    fixers: fixerResults,
    typecheck,
    preview,
    screenshot,
    durationMs: Date.now() - start,
  } satisfies VerifyOutcome);
}
