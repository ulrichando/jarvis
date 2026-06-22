"use client";

import { useState, useEffect, useCallback } from "react";
import { toast } from "sonner";
import {
  Copy,
  Eye,
  EyeOff,
  Terminal,
  Loader2,
  CheckCircle2,
  XCircle,
  Trash2,
  ExternalLink,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { McpServersCard } from "./mcp-servers";

function RemoteControlCard() {
  const [token, setToken] = useState<string | null>(null);
  const [revealed, setRevealed] = useState(false);

  useEffect(() => {
    fetch("/api/bridge/token")
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { token?: string } | null) => {
        if (d?.token) setToken(d.token);
      })
      .catch(() => {});
  }, []);

  const base =
    typeof window !== "undefined" ? `${window.location.origin}/api/bridge` : "http://localhost:3000/api/bridge";
  const script = `export JARVIS_BRIDGE_BASE_URL=${base}\nexport JARVIS_BRIDGE_TOKEN=${token ?? "<loading>"}\njarvis --remote-control`;
  const shown = token ? (revealed ? token : `jbr_${"•".repeat(20)}`) : "loading…";

  return (
    <section>
      <div className="mb-3 flex items-center gap-2">
        <Terminal className="size-4 text-muted-foreground" />
        <h2 className="text-[17px] font-semibold">Remote Control</h2>
      </div>
      <p className="mb-3 text-[13px] text-muted-foreground">
        Connect the Jarvis CLI on any machine to drive coding tasks from{" "}
        <span className="font-medium text-foreground">Code</span>. Easiest: run{" "}
        <code className="text-[12px]">jarvis auth login</code> on that machine and sign in with this account — it saves
        these credentials for you. Or paste the setup below, then start a session with{" "}
        <code className="text-[12px]">jarvis --remote-control</code>. Machines you connect appear only under your
        account.
      </p>
      <div className="rounded-lg border border-border/60 bg-card/60 p-3 font-mono text-[12px] leading-relaxed">
        <div className="text-muted-foreground">
          export JARVIS_BRIDGE_BASE_URL=<span className="text-foreground">{base}</span>
        </div>
        <div className="flex items-center gap-2 text-muted-foreground">
          <span>
            export JARVIS_BRIDGE_TOKEN=<span className="text-foreground">{shown}</span>
          </span>
          <button
            type="button"
            aria-label={revealed ? "Hide token" : "Reveal token"}
            onClick={() => setRevealed((r) => !r)}
            className="text-muted-foreground hover:text-foreground"
          >
            {revealed ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
          </button>
        </div>
        <div className="text-foreground">jarvis --remote-control</div>
      </div>
      <div className="mt-2 flex gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={!token}
          onClick={() => {
            navigator.clipboard.writeText(script);
            toast.success("Setup commands copied");
          }}
        >
          <Copy className="mr-1.5 size-3.5" /> Copy setup
        </Button>
      </div>
      <p className="mt-2 text-[12px] text-muted-foreground/70">
        Treat this token like a password — it lets a machine register under your account. It stays on this server and in
        your shell only.
      </p>
    </section>
  );
}

// Curated MCP connector presets. Each writes a REAL server into ~/.jarvis/mcp.json
// via /api/mcp (the same store the MCP servers card uses) — no fake "coming soon".
// The auth model decides the row's affordance:
//   - "none"   → one-click Connect (e.g. a local server, no token).
//   - "bearer" → Connect after pasting a token (PAT / integration token).
//   - "oauth"  → shown honestly as "Needs sign-in": the hosted server only speaks
//     OAuth, which JARVIS's token-based MCP client doesn't do yet, so we link the
//     setup docs rather than a Connect button that would just fail.
type AuthModel = "none" | "bearer" | "oauth";

type Connector = {
  id: string;
  name: string;
  description: string;
  icon: string;
  url?: string;
  transport: "http" | "sse";
  auth: AuthModel;
  tokenLabel?: string; // bearer: what to paste
  helpUrl?: string; // where to get the token / read setup
  note?: string; // extra requirement, e.g. "Figma desktop must be running"
};

const CONNECTORS: Connector[] = [
  {
    id: "github",
    name: "GitHub",
    description: "Access repositories and reference code in conversations.",
    icon: "GH",
    // Official remote GitHub MCP server. The old @modelcontextprotocol/server-github
    // npm package is deprecated; GitHub hosts this and accepts a PAT as a Bearer
    // token (the alternative OAuth path needs a browser flow the client doesn't run).
    url: "https://api.githubcopilot.com/mcp/",
    transport: "http",
    auth: "bearer",
    tokenLabel: "GitHub personal access token",
    helpUrl: "https://github.com/settings/personal-access-tokens",
  },
  {
    id: "figma",
    name: "Figma",
    description: "Pull design context and component specs directly into chat.",
    icon: "FG",
    // Figma Dev Mode runs a local MCP server inside the desktop app — no token,
    // but the app must be open with Dev Mode MCP enabled.
    url: "http://127.0.0.1:3845/mcp",
    transport: "http",
    auth: "none",
    note: "Requires the Figma desktop app open with Dev Mode MCP enabled.",
    helpUrl:
      "https://help.figma.com/hc/en-us/articles/32132100833559-Guide-to-the-Figma-MCP-server",
  },
  {
    id: "vercel",
    name: "Vercel",
    description: "View deployments and logs without leaving Jarvis.",
    icon: "VC",
    url: "https://mcp.vercel.com",
    transport: "http",
    auth: "oauth",
    helpUrl: "https://vercel.com/docs/mcp/vercel-mcp",
  },
  {
    id: "google-drive",
    name: "Google Drive",
    description: "Reference documents and spreadsheets in your conversations.",
    icon: "GD",
    // Google's managed remote MCP is OAuth/enterprise-gated; no plain bearer URL.
    transport: "http",
    auth: "oauth",
    helpUrl:
      "https://developers.google.com/workspace/drive/api/guides/configure-mcp-server",
  },
  {
    id: "notion",
    name: "Notion",
    description: "Search and reference Notion pages and databases.",
    icon: "NO",
    url: "https://mcp.notion.com/mcp",
    transport: "http",
    auth: "oauth",
    helpUrl: "https://developers.notion.com/docs/get-started-with-mcp",
  },
];

type LiveServer = { id: string; name: string; url?: string };

const bearerHeaders = (t: string): Record<string, string> | undefined => {
  const v = t.trim();
  if (!v) return undefined;
  return { Authorization: v.toLowerCase().startsWith("bearer ") ? v : `Bearer ${v}` };
};

function ConnectorRow({
  connector,
  connected,
  onChanged,
}: {
  connector: Connector;
  connected: LiveServer | undefined;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<{ ok: boolean; msg: string } | null>(null);

  const reset = () => {
    setOpen(false);
    setToken("");
    setTest(null);
  };

  const runTest = async () => {
    if (!connector.url) return;
    setBusy(true);
    setTest(null);
    try {
      const r = await fetch("/api/mcp/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: connector.name,
          url: connector.url,
          transport: connector.transport,
          headers: bearerHeaders(token),
        }),
      });
      const j = (await r.json()) as { ok: boolean; tools?: string[]; error?: string };
      setTest(
        j.ok
          ? { ok: true, msg: `Connected — ${j.tools?.length ?? 0} tool(s)` }
          : { ok: false, msg: j.error ?? "Failed" },
      );
    } catch (e) {
      setTest({ ok: false, msg: String(e) });
    } finally {
      setBusy(false);
    }
  };

  const connect = async () => {
    if (!connector.url) return;
    if (connector.auth === "bearer" && !token.trim()) {
      toast.error(`${connector.name} needs ${connector.tokenLabel ?? "a token"}`);
      return;
    }
    setBusy(true);
    try {
      const r = await fetch("/api/mcp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: connector.name,
          url: connector.url,
          transport: connector.transport,
          headers: bearerHeaders(token),
        }),
      });
      if (r.ok) {
        toast.success(`Connected ${connector.name}`, { description: "Restart JARVIS voice to apply" });
        reset();
        onChanged();
      } else {
        const j = (await r.json().catch(() => ({}))) as { error?: string };
        toast.error(j.error ?? "Failed to connect");
      }
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    if (!connected) return;
    await fetch(`/api/mcp?id=${encodeURIComponent(connected.id)}`, { method: "DELETE" }).catch(() => {});
    toast.success(`Disconnected ${connector.name}`, { description: "Restart JARVIS voice to apply" });
    onChanged();
  };

  // OAuth connect: ask the server to begin the flow, then hand the browser to
  // the provider's sign-in page. We come back at /api/mcp/oauth/callback →
  // /settings?mcp=connected (handled in ConnectorsSection).
  const startOAuth = async () => {
    if (!connector.url) return;
    setBusy(true);
    try {
      const r = await fetch("/api/mcp/oauth/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: connector.name, url: connector.url, transport: connector.transport }),
      });
      const j = (await r.json().catch(() => ({}))) as { authUrl?: string | null; error?: string };
      if (!r.ok) {
        toast.error(j.error ?? "Couldn't start sign-in");
        return;
      }
      if (j.authUrl) {
        window.location.href = j.authUrl; // → provider sign-in
        return;
      }
      // Server didn't require sign-in — add it directly.
      await fetch("/api/mcp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: connector.name, url: connector.url, transport: connector.transport }),
      });
      toast.success(`Connected ${connector.name}`, { description: "Restart JARVIS voice to apply" });
      onChanged();
    } catch (e) {
      toast.error(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="py-3.5">
      <div className="flex items-center gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border/60 bg-card/60 font-mono text-[11px] font-bold text-muted-foreground">
          {connector.icon}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[14px] font-medium">{connector.name}</p>
          <p className="mt-0.5 truncate text-[13px] text-muted-foreground">{connector.description}</p>
        </div>
        {connected ? (
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-medium text-primary">Connected</span>
            <button
              type="button"
              aria-label={`Disconnect ${connector.name}`}
              onClick={disconnect}
              className="text-muted-foreground hover:text-red-500"
            >
              <Trash2 className="size-4" />
            </button>
          </div>
        ) : connector.auth === "oauth" ? (
          connector.url ? (
            <Button variant="outline" size="sm" disabled={busy} onClick={() => void startOAuth()}>
              {busy && <Loader2 className="mr-1.5 size-3.5 animate-spin" />} Sign in
            </Button>
          ) : (
            <a
              href={connector.helpUrl}
              target="_blank"
              rel="noreferrer"
              title="This provider's managed MCP endpoint is enterprise/OAuth-gated — opens setup docs."
              className="inline-flex items-center gap-1.5 rounded-md border border-border/60 px-2.5 py-1 text-[12.5px] text-muted-foreground hover:text-foreground"
            >
              Setup <ExternalLink className="size-3" />
            </a>
          )
        ) : (
          <Button variant="outline" size="sm" onClick={() => setOpen((v) => !v)}>
            {open ? "Cancel" : "Connect"}
          </Button>
        )}
      </div>

      {open && connector.auth !== "oauth" && !connected && (
        <div className="ml-12 mt-2.5 space-y-2 rounded-lg border border-border/60 p-3">
          {connector.note && <p className="text-[12.5px] text-muted-foreground">{connector.note}</p>}
          {connector.auth === "bearer" && (
            <input
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder={connector.tokenLabel ?? "Auth token"}
              type="password"
              autoComplete="off"
              className="w-full rounded-md border border-border/60 bg-accent/20 px-2.5 py-1.5 text-[13px] focus:outline-none focus:ring-1 focus:ring-primary/40"
            />
          )}
          {test && (
            <div className={`flex items-center gap-1.5 text-[12.5px] ${test.ok ? "text-emerald-500" : "text-red-500"}`}>
              {test.ok ? <CheckCircle2 className="size-3.5" /> : <XCircle className="size-3.5" />}
              {test.msg}
            </div>
          )}
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" disabled={busy} onClick={runTest}>
              {busy && <Loader2 className="mr-1.5 size-3.5 animate-spin" />} Test
            </Button>
            <Button size="sm" disabled={busy} onClick={connect}>
              Connect
            </Button>
            {connector.helpUrl && (
              <a
                href={connector.helpUrl}
                target="_blank"
                rel="noreferrer"
                className="ml-auto inline-flex items-center gap-1 text-[12px] text-muted-foreground hover:text-foreground"
              >
                {connector.auth === "bearer" ? "Get token" : "Setup"} <ExternalLink className="size-3" />
              </a>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function ConnectorsSection() {
  const [servers, setServers] = useState<LiveServer[]>([]);

  const loadServers = useCallback(() => {
    fetch("/api/mcp")
      .then((r) => r.json())
      .then((d: { servers?: LiveServer[] }) => setServers(d.servers ?? []))
      .catch(() => setServers([]));
  }, []);
  useEffect(() => {
    loadServers();
    // Handle the OAuth callback return: /settings?mcp=connected|error&…
    const sp = new URLSearchParams(window.location.search);
    const mcp = sp.get("mcp");
    if (mcp === "connected") {
      toast.success(`Connected ${sp.get("mcp_name") ?? "server"}`, {
        description: "Restart JARVIS voice to apply",
      });
    } else if (mcp === "error") {
      toast.error(`Sign-in failed: ${sp.get("mcp_msg") ?? "unknown error"}`);
    }
    if (mcp) {
      ["mcp", "mcp_name", "mcp_msg"].forEach((k) => sp.delete(k));
      const qs = sp.toString();
      window.history.replaceState(null, "", window.location.pathname + (qs ? `?${qs}` : ""));
    }
  }, [loadServers]);

  const matchOf = (c: Connector): LiveServer | undefined =>
    servers.find((s) => (c.url && s.url === c.url) || s.name === c.name || s.id === c.id);

  return (
    <div className="space-y-10">
      <RemoteControlCard />

      <McpServersCard />

      <section>
        <div className="mb-4">
          <h2 className="text-[17px] font-semibold">Connectors</h2>
          <p className="mt-0.5 text-[13px] text-muted-foreground">
            One-click MCP servers for popular apps. Connecting adds the server to JARVIS — it appears
            under MCP servers above, the web chat sees it immediately, and the voice assistant picks it
            up on its next restart.
          </p>
        </div>
        <div className="divide-y divide-border/60 border-t border-border/60">
          {CONNECTORS.map((c) => (
            <ConnectorRow key={c.id} connector={c} connected={matchOf(c)} onChanged={loadServers} />
          ))}
        </div>
      </section>
    </div>
  );
}
