"use client";

import { use, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Group, Panel, Separator } from "react-resizable-panels";
import type { UIMessage } from "ai";
import { Chat } from "@/components/chat/chat";
import { WorkbenchToolbar, type WorkbenchTab, type ViewportPreset } from "@/components/workbench/toolbar";
import { CodeTab } from "@/components/workbench/tabs/code-tab";
import { PreviewTab } from "@/components/workbench/tabs/preview-tab";
import { SettingsTab } from "@/components/workbench/tabs/settings-tab";
import { useUI } from "@/stores/ui";

type Workspace = { id: string; name: string; conversationId?: string };

async function fetchWorkspace(id: string): Promise<Workspace | null> {
  const r = await fetch(`/api/workspace/${id}`);
  if (r.status === 404) return null;
  const j = await r.json();
  return j.workspace ?? null;
}

// Per-workspace conversation persistence. The first chat POST returns
// the new conversation id via X-Conversation-Id; we save it under this
// key so a refresh can re-attach to the same thread instead of starting
// a fresh one.
const conversationKey = (workspaceId: string) =>
  `workbench:${workspaceId}:conversationId`;

async function fetchConversation(
  conversationId: string,
): Promise<{ messages: UIMessage[] } | null> {
  const r = await fetch(`/api/conversations/${conversationId}`);
  if (!r.ok) return null;
  const j = await r.json();
  return { messages: (j.messages as UIMessage[]) ?? [] };
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

  // Load the saved conversation id for THIS workspace.
  //
  // Server-side `_meta.json` is the source of truth (set by the chat
  // route on every workspace turn). localStorage is a fallback for
  // legacy workspaces created before the server-side persistence
  // landed. Order: prefer server `ws.conversationId`; if absent, fall
  // back to localStorage.
  //
  // Reading localStorage during useState's init causes a hydration
  // mismatch — defer to useEffect so first paint matches SSR.
  const [savedConvId, setSavedConvId] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => {
    const fromServer = ws?.conversationId ?? null;
    if (fromServer) {
      setSavedConvId(fromServer);
      setHydrated(true);
      return;
    }
    try {
      setSavedConvId(localStorage.getItem(conversationKey(id)));
    } catch {
      /* private mode / disabled storage — non-fatal */
    }
    setHydrated(true);
  }, [id, ws?.conversationId]);

  // Pull the persisted messages for that conversation. Disabled until we
  // have a saved id, and intentionally not refetched on focus so a long
  // stream-in-progress doesn't get stomped by a stale snapshot.
  const { data: convData, isLoading: convLoading } = useQuery({
    queryKey: ["conversation", savedConvId],
    queryFn: () => (savedConvId ? fetchConversation(savedConvId) : null),
    enabled: !!savedConvId,
    refetchOnWindowFocus: false,
  });

  // Chat invokes this on the FIRST POST (when the server creates the
  // conversation) AND on every subsequent POST (idempotent). We persist
  // the id so future refreshes re-attach to the same thread.
  const onConversationId = useCallback(
    (cid: string) => {
      if (cid === savedConvId) return;
      try {
        localStorage.setItem(conversationKey(id), cid);
      } catch {
        /* private mode / quota exceeded — non-fatal */
      }
      setSavedConvId(cid);
    },
    [id, savedConvId],
  );

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
    // Primary source: sessionStorage (set by the Design tab's Build action
    // — the seed is too large for a URL param). Fallback: legacy ?seed=.
    try {
      const fromStore = sessionStorage.getItem(`workbench:seed:${id}`);
      if (fromStore) return fromStore;
    } catch {
      /* storage disabled — fall through to the URL param */
    }
    return new URLSearchParams(window.location.search).get("seed");
  });
  useEffect(() => {
    if (!seedPrompt) return;
    // Clear both sources so a refresh doesn't replay the build seed.
    try {
      sessionStorage.removeItem(`workbench:seed:${id}`);
    } catch {
      /* ignore */
    }
    router.replace(`/workbench/${id}`);
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

  const reduceMotion = useReducedMotion();

  // Collapse code+terminal into one "view" so toggling between those two
  // top-level tabs doesn't remount CodeTab (which would tear down the live
  // terminal + lose open editor tabs). Preview / Settings animate normally.
  const viewKey =
    active === "preview" ? "preview" : active === "settings" ? "settings" : "code";

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
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={viewKey}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: reduceMotion ? 0 : 0.15, ease: "easeOut" }}
            className="h-full w-full"
          >
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
            {active === "settings" && (
              <SettingsTab
                workspaceId={id}
                workspaceName={ws?.name ?? "workspace"}
              />
            )}
          </motion.div>
        </AnimatePresence>
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
          // The `id` is the ONLY way to put a CSS rule on the Panel's
          // outer wrapper (where flex-basis sizing actually lives).
          // `style`/`className` props on Panel land on the INNER div
          // — useless for constraining the outer flex item. See the
          // chat-panel-floor rule in globals.css that uses this id.
          id="workbench-chat-panel"
          // Match the design tab's chat-column width — ~380px on a
          // typical 1440-wide laptop. 32% was way too wide (~615px on
          // a 1920 screen) and crowded the workbench toolbar/preview.
          defaultSize="26%"
          minSize="18%"
          maxSize="45%"
          // Visual separation between chat and right pane lives on the
          // <Separator> below — DO NOT add border-r here too, that
          // produced a doubled-line seam. globals.css's foreground/15
          // separator color is the single source of truth.
          className="overflow-hidden"
        >
          {/* Show the loading state ONLY after hydration confirms we
              have a saved conversation id and the messages are still
              fetching. Before hydration, we don't yet know if there's
              a saved id, so we mount Chat empty (matching SSR) and let
              the effect either replace it (if a saved conv exists) or
              keep it (fresh chat). */}
          {hydrated && savedConvId && convLoading ? (
            <div className="flex h-full items-center justify-center text-[12px] text-muted-foreground">
              Loading conversation…
            </div>
          ) : (
            <Chat
              chatId={hydrated ? (savedConvId ?? undefined) : undefined}
              initialMessages={
                hydrated ? (convData?.messages ?? undefined) : undefined
              }
              workspaceId={id}
              workspaceName={ws?.name ?? "workspace"}
              seed={seedPrompt ?? undefined}
              embedded
              unifiedUX
              onConversationId={onConversationId}
              composerPlaceholder="Describe what you want to build — frontend, backend, full-stack…"
            />
          )}
        </Panel>

        {/* Single divider between chat and the workbench right pane.
            The CSS design-token --border is set to 22% alpha at the
            theme level (globals.css), so `bg-border` is only ~22%
            opaque and barely visible on the dark workbench background.
            Using bg-foreground/15 directly = 15% of the foreground
            color, which on dark BG reads as a crisp ~15% white line —
            the standard pattern used by VS Code / Cursor / Bolt. */}
        <Separator className="w-px bg-foreground/15 hover:bg-primary/50 transition-colors" />

        {/* Right: workbench toolbar + tabs */}
        <Panel defaultSize="74%" className="overflow-hidden">
          {right}
        </Panel>
      </Group>
    </div>
  );
}
