import type { ToolRunner } from "../types.ts";
import { readdirSync } from "node:fs";

const MAX_OUTPUT_BYTES = 16_384;

/**
 * misty-core runs under systemd user but without a graphical login session,
 * so DISPLAY + XAUTHORITY aren't inherited. When the user-scope X server is
 * running (started via ~/.bash_profile on tty1), we can pick up its auth
 * cookie from /tmp/serverauth.* and hand it to spawned children. Without
 * this, GUI apps (kitty, firefox, eww) that misty tries to launch fail with
 * "cannot open display" errors.
 */
function findXEnv(): Record<string, string> {
  try {
    const files = readdirSync("/tmp")
      .filter((f) => f.startsWith("serverauth."))
      .map((f) => `/tmp/${f}`);
    if (files.length === 0) return {};
    const xauth = files[0]!;
    return { DISPLAY: ":0", XAUTHORITY: xauth };
  } catch {
    return {};
  }
}

export const bashTool: ToolRunner = {
  def: {
    name: "bash",
    description: "Execute a shell command. Returns combined stdout+stderr. Output truncated to 16KB.",
    input_schema: {
      type: "object",
      properties: {
        command: { type: "string", description: "Shell command to execute" },
        timeout_ms: { type: "number", description: "Optional max runtime in ms (default 30000)" },
      },
      required: ["command"],
    },
  },
  async run(input: unknown): Promise<{ output: string; is_error?: boolean }> {
    const { command, timeout_ms } = input as { command: string; timeout_ms?: number };
    if (typeof command !== "string" || command.length === 0) {
      return { output: "bash: empty command", is_error: true };
    }
    const timeout = timeout_ms ?? 30_000;

    const proc = Bun.spawn(["bash", "-c", command], {
      stdout: "pipe",
      stderr: "pipe",
      env: { ...process.env, ...findXEnv() },
    });

    const timer = setTimeout(() => proc.kill(), timeout);
    try {
      const [stdout, stderr] = await Promise.all([
        new Response(proc.stdout).text(),
        new Response(proc.stderr).text(),
      ]);
      await proc.exited;
      const combined = stdout + (stderr ? `\n[stderr]\n${stderr}` : "");
      const truncated = combined.length > MAX_OUTPUT_BYTES
        ? combined.slice(0, MAX_OUTPUT_BYTES) + `\n[truncated; original ${combined.length} bytes]`
        : combined;
      // is_error is true when the process failed OR was killed (exitCode null = killed by timeout).
      const is_error = proc.exitCode === null || proc.exitCode !== 0;
      return { output: truncated, is_error };
    } finally {
      clearTimeout(timer);
    }
  },
};
