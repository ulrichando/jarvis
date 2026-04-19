// Hyprland IPC client.
// Talks to $XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket.sock

import { connect } from "node:net";

export type HyprIpc = {
  /** Send a command and return the raw response text. Opens, writes, reads, closes per call. */
  sendCommand(command: string): Promise<string>;
};

export type HyprIpcOpts = {
  /** Override the socket path for testing. */
  socketPath?: string;
};

export function resolveSocketPath(env: Record<string, string | undefined> = process.env): string {
  const runtime = env.XDG_RUNTIME_DIR;
  const sig = env.HYPRLAND_INSTANCE_SIGNATURE;
  if (!runtime) throw new Error("XDG_RUNTIME_DIR not set — not a user session?");
  if (!sig) throw new Error("HYPRLAND_INSTANCE_SIGNATURE not set — is Hyprland running?");
  return `${runtime}/hypr/${sig}/.socket.sock`;
}

export function createHyprIpc(opts: HyprIpcOpts = {}): HyprIpc {
  const socketPath = opts.socketPath ?? resolveSocketPath();
  return {
    async sendCommand(command: string): Promise<string> {
      return new Promise((resolve, reject) => {
        const sock = connect(socketPath);
        const chunks: Buffer[] = [];
        let settled = false;
        const settle = (fn: () => void) => {
          if (settled) return;
          settled = true;
          fn();
        };
        sock.on("connect", () => sock.write(command));
        sock.on("data", (chunk: Buffer) => chunks.push(chunk));
        sock.on("end", () => settle(() => resolve(Buffer.concat(chunks).toString("utf8"))));
        sock.on("error", (err: Error) => settle(() => reject(err)));
        const timer = setTimeout(() => settle(() => {
          sock.destroy();
          reject(new Error(`hyprland ipc timeout after 5s for command: ${command.slice(0, 80)}`));
        }), 5000);
        sock.on("close", () => clearTimeout(timer));
      });
    },
  };
}
