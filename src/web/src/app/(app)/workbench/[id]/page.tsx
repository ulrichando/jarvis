"use client";

import { use, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Group, Panel, Separator } from "react-resizable-panels";
import { Chat } from "@/components/chat/chat";
import { WorkbenchToolbar, type WorkbenchTab, type ViewportPreset } from "@/components/workbench/toolbar";
import { CodeTab } from "@/components/workbench/tabs/code-tab";
import { PreviewTab } from "@/components/workbench/tabs/preview-tab";
import { DatabaseTab } from "@/components/workbench/tabs/database-tab";
import { SettingsTab } from "@/components/workbench/tabs/settings-tab";
import { useUI } from "@/stores/ui";

type Workspace = { id: string; name: string };

async function fetchWorkspace(id: string): Promise<Workspace | null> {
  const r = await fetch(`/api/workspace/${id}`);
  if (r.status === 404) return null;
  const j = await r.json();
  return j.workspace ?? null;
}

export default function WorkbenchEditPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const searchParams = useSearchParams();
  const setSidebarOpen = useUI((s) => s.setSidebarOpen);

  // Default to Code; promotes Preview only when a dev server is
  // detected (see toolbar). A fresh workbench has nothing to preview,
  // so the user lands looking at files + the empty editor instead of a
  // greyed-out URL bar.
  const [active, setActive] = useState<WorkbenchTab>("code");
  const [activePath, setActivePath] = useState<string | null>(null);
  const [iframeKey, setIframeKey] = useState(0);
  const [fullscreen, setFullscreen] = useState(false);
  const [viewport, setViewport] = useState<ViewportPreset>("desktop");

  // Auto-collapse the global app sidebar when the user enters a
  // workbench so the chat panel + workbench have the full viewport.
  // We don't restore on unmount — leaving the user with whatever
  // collapsed/open state they had when they navigate away.
  useEffect(() => {
    setSidebarOpen(false);
  }, [setSidebarOpen]);

  const { data: ws } = useQuery({
    queryKey: ["workspace", id],
    queryFn: () => fetchWorkspace(id),
    refetchOnWindowFocus: false,
  });

  // Bounce on 404 (workspace was deleted from another tab/curl).
  useEffect(() => {
    if (ws === null) router.replace("/workbench");
  }, [ws, router]);

  // Deep-link from a non-embedded chat: /workbench/<id>?preview=<port>
  // jumps to Preview. Less critical now that the chat is embedded in
  // this same page, but kept for the bookmark/back-button case.
  useEffect(() => {
    const p = searchParams.get("preview");
    if (p && /^\d+$/.test(p)) {
      setActive("preview");
      router.replace(`/workbench/${id}`);
    }
  }, [searchParams, id, router]);

  // Seed prompt from the Design tab's "Build" action — auto-fires the
  // chat on first mount so the workbench scaffolds the full-stack app
  // from the design files without the user having to re-type the brief.
  // Read once via useState init so a re-render with the same URL doesn't
  // re-fire (the Chat itself is already ref-guarded but we also clean
  // the URL so a manual reload doesn't replay either).
  const [seedPrompt] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    const params = new URLSearchParams(window.location.search);
    return params.get("seed");
  });
  useEffect(() => {
    if (seedPrompt) {
      // Clean the URL so a refresh doesn't re-trigger.
      router.replace(`/workbench/${id}`);
    }
  }, [seedPrompt, id, router]);

  // Esc exits fullscreen.
  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFullscreen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  const refresh = () => setIframeKey((k) => k + 1);

  const right = (
    <div className="flex h-full w-full flex-col">
      <WorkbenchToolbar
        workspaceId={id}
        workspaceName={ws?.name ?? "workspace"}
        active={active}
        onTabChange={setActive}
        iframeKey={iframeKey}
        onRefresh={refresh}
        fullscreen={fullscreen}
        onToggleFullscreen={() => setFullscreen((v) => !v)}
        viewport={viewport}
        onViewportChange={setViewport}
      />
      <div className="flex-1 min-h-0 overflow-hidden">
        {(active === "code" || active === "terminal") && (
          <CodeTab
            workspaceId={id}
            activePath={activePath}
            onOpen={setActivePath}
            onClosePath={() => setActivePath(null)}
            iframeKey={iframeKey}
            viewport={viewport}
          />
        )}
        {active === "preview" && (
          <PreviewTab workspaceId={id} iframeKey={iframeKey} viewport={viewport} />
        )}
        {active === "database" && <DatabaseTab workspaceId={id} />}
        {active === "settings" && (
          <SettingsTab
            workspaceId={id}
            workspaceName={ws?.name ?? "workspace"}
          />
        )}
      </div>
    </div>
  );

  if (fullscreen) {
    return <div className="fixed inset-0 z-50 bg-background">{right}</div>;
  }

  return (
    <div className="h-full w-full">
      <Group orientation="horizontal" style={{ height: "100%" }}>
        {/* Left: chat panel pinned to this workspace */}
        <Panel
          // Match the design tab's chat-column width — ~380px on a
          // typical 1440-wide laptop. 32% was way too wide (~615px on
          // a 1920 screen) and crowded the workbench toolbar/preview.
          defaultSize="26%"
          minSize="18%"
          maxSize="45%"
          className="border-r border-border/50 overflow-hidden"
        >
          <Chat
            workspaceId={id}
            workspaceName={ws?.name ?? "workspace"}
            seed={seedPrompt ?? undefined}
            embedded
            unifiedUX
            composerPlaceholder="Describe what you want to build — frontend, backend, full-stack…"
          />
        </Panel>

        <Separator className="w-px bg-border/50 hover:bg-primary/40 transition-colors" />

        {/* Right: workbench toolbar + tabs */}
        <Panel defaultSize="74%" className="overflow-hidden">
          {right}
        </Panel>
      </Group>
    </div>
  );
}
