"use client";

// The Cookbook runs as a local sidecar (a minimal carve-out of Odysseus's
// Cookbook) on 127.0.0.1:8770, started by ~/.jarvis/start-jarvis-cookbook.ps1.
// We embed its /cookbook view here so model browsing/downloading lives inside
// JARVIS Settings. Override the URL with NEXT_PUBLIC_COOKBOOK_URL if the port
// changes.
const COOKBOOK_URL =
  process.env.NEXT_PUBLIC_COOKBOOK_URL ?? "http://127.0.0.1:8770";

export function CookbookSection() {
  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 border-b border-border/60 px-6 py-3">
        <h2 className="text-[17px] font-semibold">Cookbook</h2>
        <p className="mt-0.5 text-[13px] text-muted-foreground">
          Browse and download hardware-compatible local models from HuggingFace
          into your Ollama. If this stays blank, the local Cookbook service
          isn&apos;t running &mdash; start it with{" "}
          <code className="rounded bg-muted px-1 py-0.5 text-[12px]">
            start-jarvis-cookbook.ps1
          </code>
          .
        </p>
      </div>
      <iframe
        src={`${COOKBOOK_URL}/cookbook`}
        className="w-full flex-1 border-0 bg-background"
        title="Cookbook"
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-downloads allow-modals"
      />
    </div>
  );
}
