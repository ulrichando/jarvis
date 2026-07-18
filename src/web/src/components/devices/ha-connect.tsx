"use client";

/**
 * Devices — the "Connect Home Assistant" panel.
 *
 * Saves { home_assistant: { url, token } } to the sidecar via POST
 * /api/iot/config; connected state comes from GET /api/iot/config (the token
 * itself is never returned, only token_set). A URL-only save preserves the
 * stored token (sidecar contract). Once saved, HA entities (lights, plugs,
 * climate, media players) appear in the device inventory — the parent
 * refetches /devices via onSaved.
 */

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ChevronRight, Home } from "lucide-react";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { fetchHaConfig, saveHaConfig } from "./api";

const DEFAULT_HA_URL = "http://127.0.0.1:8123";

export function HomeAssistantPanel({ onSaved }: { onSaved: () => void }) {
  const [url, setUrl] = useState(DEFAULT_HA_URL);
  const [token, setToken] = useState("");
  const [tokenSet, setTokenSet] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void fetchHaConfig().then((cfg) => {
      if (cancelled) return;
      if (cfg) {
        if (cfg.url) setUrl(cfg.url);
        setTokenSet(cfg.token_set);
        setOpen(!cfg.token_set); // not connected yet → invite setup
      }
      setLoaded(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const save = async () => {
    const trimmedUrl = url.trim() || DEFAULT_HA_URL;
    setSaving(true);
    const res = await saveHaConfig(trimmedUrl, token.trim());
    setSaving(false);
    if (!res.ok) {
      toast.error(res.error ?? "Could not save Home Assistant settings");
      return;
    }
    if (token.trim()) setTokenSet(true);
    setToken("");
    toast.success("Home Assistant connected — refreshing devices…");
    onSaved(); // pull the HA entities into the inventory
  };

  return (
    <section
      id="ha-connect"
      className="mt-2 rounded-xl border border-border/40 bg-card/20"
    >
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition-colors hover:bg-muted/50"
      >
        <span className="grid size-8 shrink-0 place-items-center rounded-lg border border-border/40 bg-card text-muted-foreground">
          <Home className="size-4" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[13.5px] font-medium text-foreground">
            Home Assistant
          </span>
          <span className="block truncate text-[11.5px] text-muted-foreground">
            Bring in lights, plugs, thermostats, and media players
          </span>
        </span>
        {loaded && (
          <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-border/40 bg-card px-2.5 py-0.5 text-[11px] text-muted-foreground">
            <span
              className={cn(
                "size-1.5 rounded-full",
                tokenSet ? "bg-emerald-500" : "bg-muted-foreground/60",
              )}
            />
            {tokenSet ? "Connected" : "Not connected"}
          </span>
        )}
        <ChevronRight
          className={cn(
            "size-3.5 shrink-0 text-muted-foreground/60 transition-transform",
            open && "rotate-90",
          )}
        />
      </button>

      {open && (
        <div className="border-t border-border/40 px-4 py-3.5">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1.5">
              <span className="text-[11.5px] text-muted-foreground">URL</span>
              <Input
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder={DEFAULT_HA_URL}
                spellCheck={false}
                autoComplete="off"
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[11.5px] text-muted-foreground">
                Long-lived access token
                {tokenSet && (
                  <span className="text-muted-foreground/60"> — saved; leave blank to keep</span>
                )}
              </span>
              <Input
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder={tokenSet ? "••••••••" : "Paste token"}
                autoComplete="new-password"
              />
            </label>
          </div>
          <div className="mt-3 flex items-center justify-between gap-3">
            <p className="text-[11.5px] text-muted-foreground/80">
              In Home Assistant → Profile → Long-lived access tokens → Create.
            </p>
            <button
              onClick={() => void save()}
              disabled={saving || (!tokenSet && !token.trim())}
              className="inline-flex h-[30px] shrink-0 items-center rounded-lg bg-primary px-3.5 text-[12px] font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
