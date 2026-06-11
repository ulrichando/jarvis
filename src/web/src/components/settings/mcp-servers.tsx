"use client";

import { useState, useEffect } from "react";
import { toast } from "sonner";
import { Plug, Trash2, Loader2, Plus, CheckCircle2, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";

type McpServer = {
  id: string;
  name: string;
  url: string;
  transport: "http" | "sse";
  enabled: boolean;
};

export function McpServersCard() {
  const [servers, setServers] = useState<McpServer[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [transport, setTransport] = useState<"http" | "sse">("http");
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<{ ok: boolean; msg: string } | null>(null);

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
        body: JSON.stringify({ name, url, transport }),
      });
      const j = (await r.json()) as { ok: boolean; tools?: string[]; error?: string };
      setTest(j.ok ? { ok: true, msg: `Connected — ${j.tools?.length ?? 0} tool(s)` } : { ok: false, msg: j.error ?? "Failed" });
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
        body: JSON.stringify({ name, url, transport }),
      });
      if (r.ok) {
        toast.success(`Added ${name}`);
        setName("");
        setUrl("");
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

  const remove = async (id: string) => {
    await fetch(`/api/mcp?id=${encodeURIComponent(id)}`, { method: "DELETE" }).catch(() => {});
    await load();
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
        Connect Model Context Protocol servers to give Jarvis extra tools in chat. Add an HTTP or SSE endpoint; its tools
        become available to the assistant.
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
            <Button variant="ghost" size="sm" onClick={() => { setAdding(false); setTest(null); }}>
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
                <p className="text-[14px] font-medium">{s.name}</p>
                <p className="mt-0.5 truncate text-[12.5px] text-muted-foreground">
                  {s.transport.toUpperCase()} · {s.url}
                </p>
              </div>
              <button
                type="button"
                aria-label="Remove"
                onClick={() => remove(s.id)}
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
