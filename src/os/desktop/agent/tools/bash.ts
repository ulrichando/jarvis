import type { ToolRunner } from "../types.ts";

const MAX_OUTPUT_BYTES = 16_384;

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
      return { output: truncated, is_error: proc.exitCode !== 0 };
    } finally {
      clearTimeout(timer);
    }
  },
};
