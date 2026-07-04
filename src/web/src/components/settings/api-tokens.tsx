"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { KeyRound, Copy, Trash2, Loader2, Plus, Check } from "lucide-react";
import { Button } from "@/components/ui/button";

type ApiToken = {
  name: string;
  created_at: number;
  last_used_at: number | null;
  hint: string;
};

function fmtDate(ms: number | null): string {
  if (!ms) return "never";
  return new Date(ms).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

async function errText(r: Response): Promise<string> {
  const j = (await r.json().catch(() => null)) as
    | { error?: { message?: string } }
    | null;
  return j?.error?.message ?? `Request failed (${r.status})`;
}

export function ApiTokensSection() {
  const [tokens, setTokens] = useState<ApiToken[] | null>(null);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  // The full secret is returned only once, on create — hold it here until the
  // user copies + dismisses. Never refetched.
  const [fresh, setFresh] = useState<{ name: string; token: string } | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/bridge/api-tokens");
      if (r.ok) setTokens(((await r.json()) as { tokens: ApiToken[] }).tokens);
      else setTokens([]);
    } catch {
      setTokens([]);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async () => {
    const clean = name.trim();
    if (!clean) return;
    setCreating(true);
    try {
      const r = await fetch("/api/bridge/api-tokens", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: clean }),
      });
      if (!r.ok) {
        toast.error(await errText(r));
        return;
      }
      const j = (await r.json()) as { name: string; token: string };
      setFresh(j);
      setCopied(false);
      setName("");
      void load();
    } catch {
      toast.error("Could not create token");
    } finally {
      setCreating(false);
    }
  };

  const revoke = async (tokenName: string) => {
    if (!confirm(`Revoke "${tokenName}"? Anything using it loses access immediately.`)) return;
    try {
      const r = await fetch("/api/bridge/api-tokens", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: tokenName }),
      });
      if (!r.ok) {
        toast.error(await errText(r));
        return;
      }
      toast.success(`Revoked "${tokenName}"`);
      if (fresh?.name === tokenName) setFresh(null);
      void load();
    } catch {
      toast.error("Could not revoke token");
    }
  };

  return (
    <section>
      <div className="mb-3 flex items-center gap-2">
        <KeyRound className="size-4 text-muted-foreground" />
        <h2 className="text-[17px] font-semibold">API Tokens</h2>
      </div>
      <p className="mb-4 text-[13px] text-muted-foreground">
        Personal access tokens for the Jarvis API — use one as a{" "}
        <code className="text-[12px]">Bearer</code> token, with{" "}
        <code className="text-[12px]">jarvis auth login --token &lt;token&gt;</code>, or in scripts and{" "}
        <code className="text-[12px]">curl</code>. Each token acts as your account; treat it like a password. The
        secret is shown once — copy it now, you can&apos;t see it again.
      </p>

      {/* Freshly-created token — the one-time reveal. */}
      {fresh && (
        <div className="mb-4 rounded-lg border border-primary/40 bg-primary/5 p-3">
          <p className="mb-2 text-[13px] font-medium text-foreground">
            New token <span className="font-mono">{fresh.name}</span> — copy it now
          </p>
          <div className="flex items-center gap-2">
            <code className="flex-1 truncate rounded-md border border-border/60 bg-card/60 px-2.5 py-1.5 font-mono text-[12px]">
              {fresh.token}
            </code>
            <Button
              size="sm"
              onClick={() => {
                navigator.clipboard.writeText(fresh.token);
                setCopied(true);
                toast.success("Token copied");
              }}
            >
              {copied ? <Check className="mr-1.5 size-3.5" /> : <Copy className="mr-1.5 size-3.5" />}
              {copied ? "Copied" : "Copy"}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setFresh(null)}>
              Done
            </Button>
          </div>
        </div>
      )}

      {/* Create */}
      <div className="mb-5 flex items-center gap-2">
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !creating) void create();
          }}
          placeholder="Token name (e.g. laptop, ci)"
          maxLength={60}
          className="w-full max-w-xs rounded-md border border-border bg-transparent px-3 py-1.5 text-[13px] focus:border-primary focus:outline-none"
        />
        <Button size="sm" disabled={creating || !name.trim()} onClick={create}>
          {creating ? (
            <Loader2 className="mr-1.5 size-3.5 animate-spin" />
          ) : (
            <Plus className="mr-1.5 size-3.5" />
          )}
          Generate
        </Button>
      </div>

      {/* List */}
      {tokens === null ? (
        <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" /> Loading…
        </div>
      ) : tokens.length === 0 ? (
        <p className="text-[13px] text-muted-foreground/70">No API tokens yet.</p>
      ) : (
        <div className="divide-y divide-border/60 rounded-lg border border-border/60">
          {tokens.map((t) => (
            <div key={t.name} className="flex items-center gap-3 px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-[13px] font-medium text-foreground">{t.name}</span>
                  <code className="text-[11px] text-muted-foreground/70">…{t.hint}</code>
                </div>
                <div className="text-[11.5px] text-muted-foreground">
                  Created {fmtDate(t.created_at)} · last used {fmtDate(t.last_used_at)}
                </div>
              </div>
              <button
                type="button"
                aria-label={`Revoke ${t.name}`}
                onClick={() => revoke(t.name)}
                className="text-muted-foreground hover:text-red-500"
              >
                <Trash2 className="size-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
