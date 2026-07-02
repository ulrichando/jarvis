"use client";

import { useEffect, useState } from "react";

// The Cookbook runs as a local sidecar (a minimal carve-out of Odysseus's
// Cookbook) on 127.0.0.1:8770, started by ~/.jarvis/start-jarvis-cookbook.ps1.
// We embed its /cookbook view here so model browsing/downloading lives inside
// JARVIS Settings. Override the URL with NEXT_PUBLIC_COOKBOOK_URL if the port
// changes. NOTE: NEXT_PUBLIC_* is inlined at build time — set it before
// `npm run build`, not at runtime. Use an http origin matching the app's own
// scheme (an https app embedding this http iframe would be mixed-content-blocked).
const COOKBOOK_URL =
  process.env.NEXT_PUBLIC_COOKBOOK_URL ?? "http://127.0.0.1:8770";

export function CookbookSection() {
  // null = checking, true = reachable, false = down. A no-cors probe resolves
  // (opaque) whenever the sidecar responds at all — even the LoopbackGuard's 403
  // counts as "up" — and rejects only on connection-refused, which is the exact
  // up/down discriminator we want.
  const [alive, setAlive] = useState<boolean | null>(null);

  useEffect(() => {
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
  }, []);

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 border-b border-border/60 px-6 py-3">
        <h2 className="text-[17px] font-semibold">Cookbook</h2>
        <p className="mt-0.5 text-[13px] text-muted-foreground">
          Browse and download hardware-compatible local models from HuggingFace
          into your Ollama. Requires the local Cookbook service and Ollama
          (127.0.0.1:11434) running.
        </p>
      </div>
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
            . Models can also be pulled directly under Providers → Local
            models.
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
