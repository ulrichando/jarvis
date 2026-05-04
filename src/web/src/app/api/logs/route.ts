import { promises as fs } from "node:fs";

export const runtime = "nodejs";

// Keep in sync with the allowlist in /api/logs/stream.
const FILES: Array<{ name: string; path: string; label: string }> = [
  { name: "jarvis-desktop.log", path: "/tmp/jarvis-desktop.log", label: "Tray (desktop)" },
  { name: "jarvis-web.log", path: "/tmp/jarvis-web.log", label: "Web (Next.js)" },
  { name: "jarvis-bridge.log", path: "/tmp/jarvis-bridge.log", label: "Bridge" },
  { name: "jarvis-proxy.log", path: "/tmp/jarvis-proxy.log", label: "Proxy" },
  { name: "jarvis-hub.log", path: "/tmp/jarvis-hub.log", label: "Hub" },
  { name: "jarvis-voice-agent.log", path: "/tmp/jarvis-voice-agent.log", label: "Voice agent" },
  { name: "jarvis-voice-client.log", path: "/tmp/jarvis-voice-client.log", label: "Voice client" },
  { name: "jarvis-launch.log", path: "/tmp/jarvis-launch.log", label: "Launcher" },
  { name: "jarvis-web-chat-dbg.log", path: "/tmp/jarvis-web-chat-dbg.log", label: "Chat debug" },
];

export async function GET() {
  const entries = await Promise.all(
    FILES.map(async (f) => {
      try {
        const s = await fs.stat(f.path);
        return {
          name: f.name,
          label: f.label,
          path: f.path,
          size: s.size,
          mtime: s.mtimeMs,
          present: true,
        };
      } catch {
        return {
          name: f.name,
          label: f.label,
          path: f.path,
          size: 0,
          mtime: 0,
          present: false,
        };
      }
    }),
  );
  return Response.json({ logs: entries });
}
