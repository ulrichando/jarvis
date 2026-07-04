"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Monitor, ArrowRight } from "lucide-react";

// The Cookbook runs as a local sidecar (a minimal carve-out of Odysseus's
// Cookbook) on 127.0.0.1:8770, started alongside the Jarvis desktop app. We
// embed its /cookbook view here so model browsing/downloading lives inside
// JARVIS Settings. Override the URL with NEXT_PUBLIC_COOKBOOK_URL if the port
// changes (NEXT_PUBLIC_* is inlined at build time — set it before build).
const COOKBOOK_URL =
  process.env.NEXT_PUBLIC_COOKBOOK_URL ?? "http://127.0.0.1:8770";

function Header() {
  return (
    <div className="shrink-0 border-b border-border/60 px-6 py-3">
      <h2 className="text-[17px] font-semibold">Cookbook</h2>
      <p className="mt-0.5 text-[13px] text-muted-foreground">
        Browse and download hardware-compatible local models from HuggingFace
        into your Ollama. Runs with the Jarvis desktop app.
      </p>
    </div>
  );
}

export function CookbookSection() {
  // Mixed content: an https page (e.g. 0wlan.com) cannot embed or fetch an
  // http://127.0.0.1 origin — the browser blocks it, so the iframe never loads
  // and the health probe always "fails". Detect that post-mount (window isn't
  // available during SSR, and reading it in render would desync hydration) and
  // show an honest desktop-only explainer instead of a retry that can't work.
  // null = detecting; true = blocked (hosted https); false = embeddable (local).
  const [blocked, setBlocked] = useState<boolean | null>(null);
  // Sidecar reachability — only meaningful when the embed can actually work.
  const [alive, setAlive] = useState<boolean | null>(null);

  useEffect(() => {
    setBlocked(
      window.location.protocol === "https:" && COOKBOOK_URL.startsWith("http://"),
    );
  }, []);

  useEffect(() => {
    if (blocked !== false) return; // don't probe a URL the browser will block
    let cancelled = false;
    const ping = () =>
      fetch(`${COOKBOOK_URL}/api/health`, { mode: "no-cors" })
        .then(() => !cancelled && setAlive(true))
        .catch(() => !cancelled && setAlive(false));
    ping();
    const id = setInterval(ping, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [blocked]);

  // Hosted (https) → the local sidecar is unreachable by design. Explain,
  // rather than showing a spinner or a false "isn't running" with a dead retry.
  if (blocked === true) {
    return (
      <div className="flex h-full flex-col">
        <Header />
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
          <div className="flex size-11 items-center justify-center rounded-xl bg-primary/10">
            <Monitor className="size-5 text-primary" />
          </div>
          <div className="max-w-md">
            <p className="text-[14px] font-medium text-foreground">
              The Cookbook runs in the Jarvis desktop app
            </p>
            <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
              It browses and downloads models into the Ollama on your own
              machine, so it needs a local service the browser can&apos;t reach
              from this website. Open the Cookbook in the desktop app instead.
            </p>
          </div>
          <Link
            href="/settings?tab=providers"
            className="mt-1 inline-flex items-center gap-1.5 rounded-full border border-border px-4 py-1.5 text-[13px] text-foreground/85 transition-colors hover:bg-accent/40"
          >
            Configure model providers <ArrowRight className="size-3.5" />
          </Link>
        </div>
      </div>
    );
  }

  // Detecting (first paint before the effect runs).
  if (blocked === null) {
    return (
      <div className="flex h-full flex-col">
        <Header />
      </div>
    );
  }

  // Local desktop (http) → the sidecar is embeddable; keep the live view.
  return (
    <div className="flex h-full flex-col">
      <Header />
      {alive === false ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center text-[13px] text-muted-foreground">
          <p>The Cookbook sidecar isn&apos;t running at {COOKBOOK_URL}.</p>
          <p>
            Start the local Cookbook service, then{" "}
            <button
              type="button"
              className="underline underline-offset-2 hover:text-foreground"
              onClick={() => setAlive(null)}
            >
              retry
            </button>
            . Models can also be pulled directly under Providers → Local models.
          </p>
        </div>
      ) : (
        <iframe
          src={`${COOKBOOK_URL}/cookbook`}
          className="w-full flex-1 border-0 bg-background"
          title="Cookbook — local model browser"
          // allow-same-origin: the embedded SPA needs its own origin to call its
          // API + read its storage. allow-popups: HuggingFace external links.
          // allow-downloads: model/file downloads. (allow-modals dropped — the
          // cookbook view doesn't need alert/confirm and it's a UI-redress vector.)
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-downloads"
        />
      )}
    </div>
  );
}
