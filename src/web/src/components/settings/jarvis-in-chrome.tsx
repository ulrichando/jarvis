"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Globe, Plus, X, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { useSettings, useUpdateSettings } from "@/hooks/use-settings";

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-4">
      <h2 className="text-[17px] font-semibold">{children}</h2>
      <div className="mt-2 border-t border-border/60" />
    </div>
  );
}

type ChromeStatus = { bridgeReachable: boolean; extensionConnected: boolean | null };

// Normalize a user-typed site to a bare host (drop scheme/path/www) so the
// blocklist matches how the extension will compare against location.host.
function normalizeSite(raw: string): string {
  let s = raw.trim().toLowerCase();
  if (!s) return "";
  s = s.replace(/^https?:\/\//, "").replace(/\/.*$/, "").replace(/^www\./, "");
  return s;
}

export function JarvisInChromeSection() {
  const { data: settings } = useSettings();
  const update = useUpdateSettings();
  const chrome = settings?.chrome ?? { defaultPolicy: "allow" as const, blockedSites: [] as string[] };
  const [status, setStatus] = useState<ChromeStatus | null>(null);
  const [newSite, setNewSite] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  // Live status of the bridge + extension (polled; the bridge only runs while
  // the Jarvis desktop app is open).
  const loadStatus = useCallback(async () => {
    try {
      const r = await fetch("/api/chrome/status", { cache: "no-store" });
      if (r.ok) setStatus((await r.json()) as ChromeStatus);
    } catch {
      /* keep prior status */
    }
  }, []);
  useEffect(() => {
    void loadStatus();
    const id = setInterval(() => void loadStatus(), 10_000);
    return () => clearInterval(id);
  }, [loadStatus]);

  const setPolicy = async (defaultPolicy: "allow" | "block") => {
    setBusy("policy");
    try {
      await update.mutateAsync({ chrome: { defaultPolicy } });
    } catch {
      toast.error("Couldn't save that.");
    } finally {
      setBusy(null);
    }
  };

  const addSite = async () => {
    const site = normalizeSite(newSite);
    if (!site) return;
    if (chrome.blockedSites.includes(site)) {
      toast.info(`${site} is already blocked.`);
      setNewSite("");
      return;
    }
    setBusy("add");
    try {
      await update.mutateAsync({ chrome: { blockedSites: [...chrome.blockedSites, site] } });
      setNewSite("");
    } catch {
      toast.error("Couldn't add that site.");
    } finally {
      setBusy(null);
    }
  };

  const removeSite = async (site: string) => {
    setBusy(`rm:${site}`);
    try {
      await update.mutateAsync({
        chrome: { blockedSites: chrome.blockedSites.filter((s) => s !== site) },
      });
    } catch {
      toast.error("Couldn't remove that site.");
    } finally {
      setBusy(null);
    }
  };

  const connected = status?.extensionConnected === true;
  const bridgeUp = status?.bridgeReachable === true;
  const statusTone = connected ? "bg-emerald-500" : bridgeUp ? "bg-amber-500" : "bg-muted-foreground/50";
  const statusText = connected
    ? "Extension connected — Jarvis can act on the current page."
    : bridgeUp
      ? "Local bridge is running, but no browser extension is connected yet."
      : status === null
        ? "Checking the local bridge…"
        : "Local bridge isn't running — open the Jarvis desktop app to start it.";

  return (
    <div className="space-y-10">
      <section>
        <div className="mb-6 flex items-start gap-3 rounded-xl border border-border/60 bg-card/40 p-4">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10">
            <Globe className="size-[18px] text-primary" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <p className="text-[14px] font-semibold">Jarvis in Chrome</p>
              <span className="inline-flex items-center gap-1.5 rounded-full border border-border/60 bg-background/50 px-2 py-0.5 text-[11px] text-muted-foreground">
                <span className={cn("size-1.5 rounded-full", statusTone)} />
                {connected ? "Connected" : bridgeUp ? "No extension" : "Offline"}
              </span>
            </div>
            <p className="mt-1 text-[13px] leading-5 text-muted-foreground">{statusText}</p>
            <p className="mt-1.5 text-[12.5px] leading-5 text-muted-foreground/80">
              Jarvis acts on web pages through a browser extension that connects to the local
              bridge. The extension isn&apos;t published yet — the preferences below are saved now
              and take effect the moment it connects.
            </p>
          </div>
        </div>
      </section>

      <section>
        <SectionTitle>Jarvis in Chrome settings</SectionTitle>
        <div className="divide-y divide-border/60">
          <div className="flex items-center justify-between py-3.5">
            <div>
              <p className="text-[14px] font-medium">Default for all sites</p>
              <p className="mt-0.5 text-[13px] text-muted-foreground">
                Whether Jarvis may act on a site unless you block it below.
              </p>
            </div>
            <Select
              value={chrome.defaultPolicy}
              onValueChange={(v) => setPolicy(v as "allow" | "block")}
              disabled={busy === "policy"}
            >
              <SelectTrigger className="w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="allow">Allow on all sites</SelectItem>
                <SelectItem value="block">Block on all sites</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="mt-3 rounded-lg border border-border/50 bg-card/30 px-4 py-3">
          <p className="text-[13px] text-muted-foreground">
            {chrome.defaultPolicy === "allow"
              ? "Jarvis works everywhere except the sites you block below."
              : "Jarvis is blocked everywhere. (Per-site allow lists come with the extension.)"}
          </p>
        </div>
      </section>

      <section>
        <SectionTitle>Blocked sites</SectionTitle>
        <div className="flex items-center gap-2">
          <input
            value={newSite}
            onChange={(e) => setNewSite(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void addSite();
              }
            }}
            placeholder="example.com"
            spellCheck={false}
            className="h-9 flex-1 rounded-lg border border-border/60 bg-background/50 px-3 text-[13px] outline-none focus:border-primary/50"
          />
          <Button variant="outline" size="sm" className="gap-1.5" onClick={addSite} disabled={busy === "add" || !newSite.trim()}>
            {busy === "add" ? <Loader2 className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />}
            Add
          </Button>
        </div>

        {chrome.blockedSites.length === 0 ? (
          <p className="mt-3 text-[13px] text-muted-foreground">
            No blocked sites. Jarvis in Chrome cannot be used on sites you add here.
          </p>
        ) : (
          <div className="mt-3 flex flex-wrap gap-2">
            {chrome.blockedSites.map((site) => (
              <span
                key={site}
                className="inline-flex items-center gap-1.5 rounded-full border border-border/60 bg-card/40 py-1 pl-3 pr-1.5 text-[12.5px]"
              >
                <span className="font-mono text-foreground/90">{site}</span>
                <button
                  type="button"
                  onClick={() => removeSite(site)}
                  disabled={busy === `rm:${site}`}
                  aria-label={`Remove ${site}`}
                  className="inline-flex size-4 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
                >
                  {busy === `rm:${site}` ? <Loader2 className="size-3 animate-spin" /> : <X className="size-3" />}
                </button>
              </span>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
