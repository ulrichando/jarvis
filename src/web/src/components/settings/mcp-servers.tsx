"use client";

import { useState, useEffect } from "react";
import { toast } from "sonner";
import { Plug, Trash2, Loader2, Plus, CheckCircle2, XCircle, Lock, Wifi } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";

type McpServer = {
  id: string;
  name: string;
  url?: string;
  command?: string;
  args?: string[];
  transport: "http" | "sse" | "stdio";
  hasAuth?: boolean;
  enabled: boolean;
};

const RESTART_HINT = "Restart JARVIS voice to apply";

const authHeaders = (t: string): Record<string, string> | undefined => {
  const v = t.trim();
  if (!v) return undefined;
  return { Authorization: v.toLowerCase().startsWith("bearer ") ? v : `Bearer ${v}` };
};

export function McpServersCard() {
  const [servers, setServers] = useState<McpServer[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [transport, setTransport] = useState<"http" | "sse">("http");
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<{ ok: boolean; msg: string } | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);

  const load = () =>
    fetch("/api/mcp")
      .then((r) => r.json())
      .then((d: { servers?: McpServer[] }) => setServers(d.servers ?? []))
      .catch(() => setServers([]));
  useEffect(() => {
    load();
  }, []);

  const runTest = async () => {
    if (!url.trim()) return;
    setBusy(true);
    setTest(null);
    try {
      const r = await fetch("/api/mcp/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, url, transport, headers: authHeaders(token) }),
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

  const add = async () => {
    if (!name.trim() || !url.trim()) return;
    setBusy(true);
    try {
      const r = await fetch("/api/mcp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, url, transport, headers: authHeaders(token) }),
      });
      if (r.ok) {
        toast.success(`Added ${name}`, { description: RESTART_HINT });
        setName("");
        setUrl("");
        setToken("");
        setTest(null);
        setAdding(false);
        await load();
      } else {
        const j = (await r.json().catch(() => ({}))) as { error?: string };
        toast.error(j.error ?? "Failed to add");
      }
    } finally {
      setBusy(false);
    }
  };

  const remove = async (s: McpServer) => {
    await fetch(`/api/mcp?id=${encodeURIComponent(s.id)}`, { method: "DELETE" }).catch(() => {});
    toast.success(`Removed ${s.name}`, { description: RESTART_HINT });
    await load();
  };

  const toggle = async (s: McpServer, enabled: boolean) => {
    setServers((prev) => prev?.map((x) => (x.id === s.id ? { ...x, enabled } : x)) ?? prev);
    const r = await fetch("/api/mcp", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: s.id, enabled }),
    }).catch(() => null);
    if (!r || !r.ok) {
      toast.error("Failed to update");
      await load();
      return;
    }
    toast.success(`${enabled ? "Enabled" : "Disabled"} ${s.name}`, { description: RESTART_HINT });
  };

  const testRow = async (s: McpServer) => {
    setTestingId(s.id);
    try {
      const r = await fetch("/api/mcp/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: s.id }),
      });
      const j = (await r.json()) as { ok: boolean; tools?: string[]; error?: string };
      if (j.ok) toast.success(`${s.name}: connected — ${j.tools?.length ?? 0} tool(s)`);
      else toast.error(`${s.name}: ${j.error ?? "failed"}`);
    } catch (e) {
      toast.error(`${s.name}: ${String(e)}`);
    } finally {
      setTestingId(null);
    }
  };

  return (
    <section>
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Plug className="size-4 text-muted-foreground" />
          <h2 className="text-[17px] font-semibold">MCP servers</h2>
        </div>
        {!adding && (
          <Button variant="outline" size="sm" onClick={() => setAdding(true)}>
            <Plus className="mr-1.5 size-3.5" /> Add server
          </Button>
        )}
      </div>
      <p className="mb-3 text-[13px] text-muted-foreground">
        These are JARVIS&apos;s MCP servers — the same set the voice assistant uses. Add an HTTP or SSE endpoint (with an
        auth token if it needs one) to give JARVIS extra tools. Add/remove/toggle here applies to the web chat
        immediately; the voice assistant picks up changes on its next restart.
      </p>

      {adding && (
        <div className="mb-3 space-y-2 rounded-lg border border-border/60 p-3">
          <div className="flex gap-2">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Name (e.g. Linear)"
              className="w-1/3 rounded-md border border-border/60 bg-accent/20 px-2.5 py-1.5 text-[13px] focus:outline-none focus:ring-1 focus:ring-primary/40"
            />
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://mcp.example.com/mcp"
              className="flex-1 rounded-md border border-border/60 bg-accent/20 px-2.5 py-1.5 text-[13px] focus:outline-none focus:ring-1 focus:ring-primary/40"
            />
            <select
              value={transport}
              onChange={(e) => setTransport(e.target.value as "http" | "sse")}
              className="rounded-md border border-border/60 bg-accent/20 px-2 py-1.5 text-[13px] focus:outline-none"
            >
              <option value="http">HTTP</option>
              <option value="sse">SSE</option>
            </select>
          </div>
          <input
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="Auth token (optional) — sent as Authorization: Bearer …"
            type="password"
            autoComplete="off"
            className="w-full rounded-md border border-border/60 bg-accent/20 px-2.5 py-1.5 text-[13px] focus:outline-none focus:ring-1 focus:ring-primary/40"
          />
          {test && (
            <div className={`flex items-center gap-1.5 text-[12.5px] ${test.ok ? "text-emerald-500" : "text-red-500"}`}>
              {test.ok ? <CheckCircle2 className="size-3.5" /> : <XCircle className="size-3.5" />}
              {test.msg}
            </div>
          )}
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" disabled={busy || !url.trim()} onClick={runTest}>
              {busy && <Loader2 className="mr-1.5 size-3.5 animate-spin" />} Test
            </Button>
            <Button size="sm" disabled={busy || !name.trim() || !url.trim()} onClick={add}>
              Add
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setAdding(false);
                setTest(null);
                setToken("");
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      <div className="border-t border-border/60 divide-y divide-border/60">
        {servers === null ? (
          <div className="flex items-center gap-2 py-3.5 text-[13px] text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" /> Loading…
          </div>
        ) : servers.length === 0 ? (
          <div className="py-3.5 text-[13px] text-muted-foreground">No MCP servers yet.</div>
        ) : (
          servers.map((s) => (
            <div key={s.id} className="flex items-center gap-3 py-3.5">
              <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border/60 bg-card/60">
                <Plug className="size-4 text-muted-foreground" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <p className="truncate text-[14px] font-medium">{s.name}</p>
                  <span className="shrink-0 rounded border border-border/60 px-1 py-px text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                    {s.transport}
                  </span>
                  {s.hasAuth && <Lock className="size-3 shrink-0 text-muted-foreground" />}
                </div>
                <p className="mt-0.5 truncate text-[12.5px] text-muted-foreground">
                  {s.url ?? [s.command, ...(s.args ?? [])].filter(Boolean).join(" ") ?? "—"}
                </p>
              </div>
              {s.url && (
                <button
                  type="button"
                  aria-label="Test connection"
                  title="Test connection"
                  disabled={testingId === s.id}
                  onClick={() => testRow(s)}
                  className="text-muted-foreground hover:text-foreground disabled:opacity-50"
                >
                  {testingId === s.id ? <Loader2 className="size-4 animate-spin" /> : <Wifi className="size-4" />}
                </button>
              )}
              <Switch
                checked={s.enabled}
                onCheckedChange={(v: boolean) => toggle(s, v)}
                aria-label={s.enabled ? "Disable" : "Enable"}
              />
              <button
                type="button"
                aria-label="Remove"
                onClick={() => remove(s)}
                className="text-muted-foreground hover:text-red-500"
              >
                <Trash2 className="size-4" />
              </button>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
