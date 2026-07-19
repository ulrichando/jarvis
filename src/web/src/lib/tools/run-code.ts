import { tool } from "ai";
import { z } from "zod";
import { dockerStatus, execInRuntime } from "@/lib/workspace/docker";

// runCode — a ChatGPT/Claude-style code interpreter for plain chats. Runs a
// snippet in the SAME hardened workbench container the /code workspaces use
// (cap-drop ALL, isolated bridge network, 2g mem / 2 cpu / 512 pids caps —
// see scripts/lib/docker.mjs), so there's no second sandbox to secure.
//
// Isolation model: one persistent per-user container (id `ci-<userId>`), lazily
// started on first call and reused after (fast subsequent runs; files persist
// in /workspace so the model can build up state across calls, like a notebook).
// ponytail: one idle container per user. On a single-user box that's one
// container; add an idle-reaper if this ever runs multi-tenant at scale.
//
// Workspace chats DON'T get this tool — they already run code via boltActions
// against their own container. This is the plain-chat path only.

const inputSchema = z.object({
  code: z.string().min(1).describe("The code to execute."),
  language: z
    .enum(["python", "bash", "javascript"])
    .optional()
    .describe("Language to run the code as. Defaults to python."),
});

/** Wall-clock cap per run, enforced INSIDE the container by `timeout(1)` so a
 *  runaway process is actually killed (docker.mjs::exec's own timeout only
 *  kills the `docker exec` client, leaving the in-container process burning
 *  CPU toward the pid cap). The host-side exec timeout is a backstop above it. */
const TIMEOUT_S = 60;
const EXEC_BACKSTOP_MS = (TIMEOUT_S + 10) * 1000;
/** Per-stream output cap so a `while True: print()` can't blow the context. */
const MAX_OUTPUT_CHARS = 10_000;

function truncate(s: string): string {
  if (s.length <= MAX_OUTPUT_CHARS) return s;
  return (
    s.slice(0, MAX_OUTPUT_CHARS) +
    `\n…[truncated ${s.length - MAX_OUTPUT_CHARS} more chars]`
  );
}

/**
 * Build the `bash -lc` command. The snippet is base64-encoded on the host and
 * decoded inside the container into a temp file — this sidesteps ALL shell
 * quoting/escaping issues (base64 is [A-Za-z0-9+/=], safe inside single
 * quotes) that a naive `python3 -c "<code>"` would hit with quotes/newlines.
 */
function buildCommand(language: string, code: string): string {
  const b64 = Buffer.from(code, "utf8").toString("base64");
  const ext = language === "javascript" ? "js" : language === "bash" ? "sh" : "py";
  const interp =
    language === "javascript" ? "node" : language === "bash" ? "bash" : "python3";
  // Decode → run under `timeout` (TERM at TIMEOUT_S, KILL 5s later) → clean up,
  // preserving the exit code. `timeout` exits 124 on TERM-timeout, 137 on KILL.
  return (
    `f=$(mktemp /tmp/jarvis_run.XXXXXX.${ext}) && ` +
    `printf %s '${b64}' | base64 -d > "$f" && ` +
    `timeout -k 5 ${TIMEOUT_S} ${interp} "$f"; rc=$?; rm -f "$f"; exit $rc`
  );
}

/** GNU timeout's exit codes when it had to terminate the command. */
function isTimeoutExit(code: number): boolean {
  return code === 124 || code === 137;
}

/**
 * Build the `runCode` tool for one chat request, bound to the authed user's id
 * so each user's code runs in their own isolated sandbox (same per-request
 * factory pattern as createMemoryTool). execute never throws — sandbox / docker
 * failures come back as { status: "error" } the model can read and relay.
 */
export function createRunCodeTool(userId: string) {
  const sandboxId = `ci-${userId}`;
  return tool({
    description:
      "Run code in a secure sandbox and get the output back. Use this to compute answers, run scripts, analyze data, test snippets, or verify results — instead of guessing at what code would print. " +
      "Supports python (default), bash, and javascript (node). Returns stdout, stderr, and the exit code. " +
      "Files persist between calls in the working directory; a 60s time limit applies. Never run destructive or malicious commands.",
    inputSchema,
    execute: async ({ code, language }) => {
      const lang = language ?? "python";
      try {
        const status = await dockerStatus();
        if (!status.available || !status.imageReady) {
          return {
            status: "error" as const,
            error:
              "Code execution isn't available right now — the sandbox runtime isn't configured on this server.",
          };
        }
        const result = await execInRuntime(sandboxId, buildCommand(lang, code), {
          timeoutMs: EXEC_BACKSTOP_MS,
        });
        const timedOut = isTimeoutExit(result.exitCode);
        return {
          status: "ok" as const,
          language: lang,
          exitCode: result.exitCode,
          stdout: truncate(result.stdout),
          stderr: truncate(result.stderr),
          durationMs: result.durationMs,
          // Surface a real timeout clearly (the process was killed in-container),
          // while still returning whatever it printed before being cut off.
          ...(timedOut
            ? { timedOut: true, note: `Killed: exceeded the ${TIMEOUT_S}s time limit.` }
            : {}),
        };
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        console.error("[run-code] failed:", message);
        // execInRuntime catches command failures internally, so reaching here
        // means the container couldn't start (or the host backstop fired).
        return {
          status: "error" as const,
          language: lang,
          error: /timed out|ETIMEDOUT|killed/i.test(message)
            ? `Execution timed out after ${TIMEOUT_S}s.`
            : `Sandbox error: ${message}`,
        };
      }
    },
  });
}
