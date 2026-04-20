// Environment tools — let JARVIS run commands in different contexts:
// local (default), remote SSH host, or inside a docker container.
// Separated from the bash tool so each context can have its own guardrails.

import type { ToolRunner } from "../types.ts";

const MAX_OUTPUT = 16_384;

function truncate(s: string, limit = MAX_OUTPUT): string {
  return s.length > limit ? s.slice(0, limit) + `\n[truncated; original ${s.length} bytes]` : s;
}

async function runArgs(args: string[], timeoutMs: number): Promise<{ output: string; is_error?: boolean }> {
  const proc = Bun.spawn(args, { stdout: "pipe", stderr: "pipe" });
  const timer = setTimeout(() => proc.kill(), timeoutMs);
  try {
    const [out, err] = await Promise.all([
      new Response(proc.stdout).text(),
      new Response(proc.stderr).text(),
    ]);
    await proc.exited;
    const combined = out + (err ? `\n[stderr]\n${err}` : "");
    const is_error = proc.exitCode === null || proc.exitCode !== 0;
    return { output: truncate(combined), is_error };
  } finally {
    clearTimeout(timer);
  }
}

export const sshExecTool: ToolRunner = {
  def: {
    name: "ssh_exec",
    description:
      "Run a shell command on a remote host via SSH. Non-interactive — the host must have key-based auth set up. Use for reaching pentest targets, other lab machines, or cloud VMs.",
    input_schema: {
      type: "object",
      properties: {
        host: { type: "string", description: "user@host or host" },
        command: { type: "string", description: "Shell command to run on the remote" },
        port: { type: "number", description: "SSH port (default 22)" },
        key: { type: "string", description: "Path to SSH identity file (optional)" },
        timeout_ms: { type: "number", description: "Max runtime in ms (default 30000)" },
      },
      required: ["host", "command"],
    },
  },
  async run(input: unknown) {
    const { host, command, port, key, timeout_ms } = input as {
      host: string; command: string; port?: number; key?: string; timeout_ms?: number;
    };
    if (!host || !command) return { output: "ssh_exec: host and command required", is_error: true };
    const args = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=accept-new"];
    if (port) args.push("-p", String(port));
    if (key) args.push("-i", key);
    args.push(host, "--", command);
    return runArgs(args, timeout_ms ?? 30_000);
  },
};

export const dockerExecTool: ToolRunner = {
  def: {
    name: "docker_exec",
    description:
      "Run a shell command inside a running docker container. Use to inspect/operate services that are containerized (postgres, redis, app stacks).",
    input_schema: {
      type: "object",
      properties: {
        container: { type: "string", description: "Container name or ID" },
        command: { type: "string", description: "Shell command to run inside" },
        shell: { type: "string", description: "Shell to invoke (default /bin/sh)" },
        timeout_ms: { type: "number", description: "Max runtime in ms (default 30000)" },
      },
      required: ["container", "command"],
    },
  },
  async run(input: unknown) {
    const { container, command, shell, timeout_ms } = input as {
      container: string; command: string; shell?: string; timeout_ms?: number;
    };
    if (!container || !command) return { output: "docker_exec: container and command required", is_error: true };
    const args = ["docker", "exec", container, shell ?? "/bin/sh", "-c", command];
    return runArgs(args, timeout_ms ?? 30_000);
  },
};

export const distroboxExecTool: ToolRunner = {
  def: {
    name: "distrobox_exec",
    description:
      "Run a shell command inside a distrobox container (Ubuntu, Fedora, Kali, etc. running on the Arch host). Use for testing tools on a different distro without leaving the desktop. The container must already exist — use bash to call 'distrobox create' first if needed.",
    input_schema: {
      type: "object",
      properties: {
        box: { type: "string", description: "Distrobox container name (e.g. 'ubuntu', 'fedora-tools')" },
        command: { type: "string", description: "Shell command to run inside" },
        timeout_ms: { type: "number", description: "Max runtime in ms (default 60000 — distro boxes can be slow on first exec)" },
      },
      required: ["box", "command"],
    },
  },
  async run(input: unknown) {
    const { box, command, timeout_ms } = input as {
      box: string; command: string; timeout_ms?: number;
    };
    if (!box || !command) return { output: "distrobox_exec: box and command required", is_error: true };
    // distrobox-enter -n NAME -- sh -c "CMD"
    const args = ["distrobox", "enter", "--name", box, "--", "sh", "-c", command];
    return runArgs(args, timeout_ms ?? 60_000);
  },
};

export const envListTool: ToolRunner = {
  def: {
    name: "env_list",
    description:
      "List available execution environments: reachable SSH hosts (from ~/.ssh/config + known_hosts), running docker containers, and the local system.",
    input_schema: { type: "object", properties: {}, required: [] },
  },
  async run() {
    const lines: string[] = ["local: Arch/BlackArch (this VM)"];
    // SSH hosts from ~/.ssh/config
    try {
      const cfg = await Bun.file(`${process.env.HOME}/.ssh/config`).text();
      const hosts = [...cfg.matchAll(/^Host\s+(.+)$/gm)]
        .flatMap((m) => m[1]!.split(/\s+/))
        .filter((h) => h && !h.includes("*"));
      if (hosts.length) lines.push(`ssh hosts (from ~/.ssh/config): ${hosts.join(", ")}`);
    } catch { /* no config */ }
    // Docker containers
    try {
      const proc = Bun.spawn(["docker", "ps", "--format", "{{.Names}} ({{.Image}}, {{.Status}})"], {
        stdout: "pipe", stderr: "pipe",
      });
      const out = await new Response(proc.stdout).text();
      await proc.exited;
      if (out.trim()) lines.push("docker containers:\n  " + out.trim().replace(/\n/g, "\n  "));
      else lines.push("docker containers: (none running)");
    } catch { lines.push("docker: not installed or not accessible"); }
    // Distrobox containers (over podman or docker backend)
    try {
      const proc = Bun.spawn(["distrobox", "list", "--no-color"], { stdout: "pipe", stderr: "pipe" });
      const out = await new Response(proc.stdout).text();
      await proc.exited;
      const rows = out.split("\n").filter((l) => l.trim() && !/^\s*id\s+\|/i.test(l));
      if (rows.length) lines.push("distrobox containers:\n  " + rows.join("\n  "));
      else lines.push("distrobox: installed, no containers yet (use 'distrobox create --name X --image IMAGE')");
    } catch { lines.push("distrobox: not installed"); }
    return { output: lines.join("\n") };
  },
};
