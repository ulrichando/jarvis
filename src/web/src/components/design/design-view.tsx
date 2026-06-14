"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Check, ChevronDown, Copy, Download, ExternalLink, Hammer, Link2, Loader2, Maximize, Palette, Play, Plus, Share2, Sliders, X } from "lucide-react";
import type { TreeEntry } from "@/lib/workspace/client";
import { Chat } from "@/components/chat/chat";
import { Button } from "@/components/ui/button";
import { useSettings } from "@/hooks/use-settings";
import { SidebarToggle } from "@/components/layout/sidebar-toggle";
import { useConversation } from "@/hooks/use-conversations";
import { useResizableColumn } from "@/hooks/use-resizable-column";
import { cn } from "@/lib/utils";
import { FORMAT_FILE, formatFromFilename } from "@/lib/design/format";
import { extractTweaks, type Tweak } from "@/lib/design/tweaks";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiDeleteEntry, apiReadFile, apiTree } from "@/lib/workspace/client";
import { useDesignComments, type DesignCommentRecord } from "@/hooks/use-design-comments";
import { BrandPanel } from "./brand-panel";
import { DesignFilesPanel } from "./design-files-panel";
import { DesignPreview, type DesignComment } from "./design-preview";
import { TweaksPanel } from "./tweaks-panel";
import { RefineForm } from "./refine-form";
import { ProjectPicker } from "./project-picker";

type DesignTab =
  | { kind: "files" }
  | { kind: "refine" }
  | { kind: "file"; entry: TreeEntry };

function buildEditPrompt(c: DesignComment): string {
  const text = c.text ? `, current content: "${c.text}"` : "";
  return [
    `Edit ONLY this element in ${c.filePath} — leave every other element identical, byte-for-byte.`,
    ``,
    `Element: <${c.tag}>${text}`,
    `CSS path (best-effort): ${c.selector}`,
    ``,
    `Change requested: ${c.comment}`,
    ``,
    `Return the FULL updated ${c.filePath} as a single boltAction file write.`,
  ].join("\n");
}

function tabKey(t: DesignTab): string {
  if (t.kind === "files") return "__files";
  if (t.kind === "refine") return "__refine";
  return `f:${t.entry.path}`;
}

function tabLabel(t: DesignTab): string {
  if (t.kind === "files") return "Design Files";
  if (t.kind === "refine") return "Refine the brief";
  return t.entry.name;
}

export function DesignView({
  workspaceId,
  workspaceName,
  projects = [],
}: {
  workspaceId: string;
  workspaceName: string;
  projects?: import("@/lib/workspace/client").Workspace[];
}) {
  const [tabs, setTabs] = useState<DesignTab[]>([{ kind: "files" }]);
  const [activeKey, setActiveKey] = useState<string>("__files");
  const [selected, setSelected] = useState<TreeEntry | null>(null);
  const [showBrand, setShowBrand] = useState(false);
  const [streaming, setStreaming] = useState<{ filePath: string; content: string } | null>(null);
  // Streaming chunks arrive 10–30+ per second on long files. Setting state
  // (and therefore rebuilding the iframe srcDoc) on every chunk thrashes the
  // browser badly enough to crash the tab. Coalesce updates: keep the latest
  // chunk in a ref, flush at most every 300ms via a single timer.
  const pendingStreamingRef = useRef<{ filePath: string; content: string } | null>(null);
  const streamingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flushStreaming = () => {
    if (pendingStreamingRef.current) {
      setStreaming(pendingStreamingRef.current);
      pendingStreamingRef.current = null;
    }
    streamingTimerRef.current = null;
  };
  const queueStreaming = (filePath: string, content: string) => {
    pendingStreamingRef.current = { filePath, content };
    if (streamingTimerRef.current) return; // an update is already scheduled
    streamingTimerRef.current = setTimeout(flushStreaming, 300);
  };
  const [prefillPrompt, setPrefillPrompt] = useState<
    { id: string; text: string; autoSend?: boolean } | undefined
  >(undefined);
  const qc = useQueryClient();
  const [showTweaks, setShowTweaks] = useState(false);
  const [tweakOverrides, setTweakOverrides] = useState<Record<string, Tweak["value"]>>({});
  const [chatTab, setChatTab] = useState<"chat" | "comments">("chat");
  // "Build" button state — POSTs to /api/design/build, gets a fresh
  // workbench workspace + seed prompt, navigates to /workbench/<id>?seed=…
  // The workbench's chat auto-fires the seed, scaffolding the full-stack
  // app from the design files.
  const [buildPending, setBuildPending] = useState(false);
  // Bumping `chatKey` remounts <Chat>, throwing away its in-memory messages so
  // the user starts a fresh thread (the + button in the chat header).
  const [chatKey, setChatKey] = useState(0);
  const designComments = useDesignComments(workspaceId);
  const { data: settings } = useSettings();

  // Reset workspace-scoped UI state when SWITCHING projects (delete +
  // auto-switch, new project, manual project picker change). Skips the
  // very first mount so a page refresh on the same workspace doesn't
  // wipe the chat buffer, file selection, etc. — only an actual
  // workspaceId change triggers the reset.
  const prevWorkspaceRef = useRef<string | null>(null);
  useEffect(() => {
    if (prevWorkspaceRef.current === null) {
      prevWorkspaceRef.current = workspaceId;
      return;
    }
    if (prevWorkspaceRef.current === workspaceId) return;
    prevWorkspaceRef.current = workspaceId;
    setTabs([{ kind: "files" }]);
    setActiveKey("__files");
    setSelected(null);
    setStreaming(null);
    setTweakOverrides({});
    setShowTweaks(false);
    setShowBrand(false);
    setChatKey((k) => k + 1);
    setChatId(null);
  }, [workspaceId]);

  // Sync the resolved workspaceId to the URL so a refresh deterministically
  // re-opens the same workspace. Without this, /design (no query) hits the
  // server's "fall back to all[0]" branch which can pick a DIFFERENT
  // workspace each time depending on how the storage layer orders the list
  // — which manifests as "my conversation disappeared on refresh" because
  // the chatId localStorage key (\`design:chat:\${workspaceId}\`) no longer
  // matches.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    if (params.get("ws") === workspaceId) return;
    params.set("ws", workspaceId);
    const next = `${window.location.pathname}?${params.toString()}`;
    window.history.replaceState(null, "", next);
  }, [workspaceId]);

  // Persist the conversation id per workspace so a refresh restores
  // the same chat thread.
  //
  // Source of truth is server-side `_meta.json` (set by the chat route
  // on every workspace turn). We pull it via the workspace API so the
  // chat history is visible to ANY browser / device that opens this
  // workspace, not just the one that created the conversation.
  // localStorage is the fallback for legacy workspaces created before
  // the server-side persistence landed. Reading localStorage during
  // useState's initializer causes a hydration mismatch — defer to
  // useEffect so first paint matches SSR; upgrade once hydrated.
  const chatIdKey = `design:chat:${workspaceId}`;
  const [chatId, setChatId] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);
  const wsQuery = useQuery({
    queryKey: ["workspace", workspaceId],
    queryFn: async () => {
      const r = await fetch(`/api/workspace/${workspaceId}`);
      if (!r.ok) return null;
      const j = await r.json();
      return j.workspace as { id: string; name: string; conversationId?: string } | null;
    },
    refetchOnWindowFocus: false,
  });
  useEffect(() => {
    const fromServer = wsQuery.data?.conversationId ?? null;
    if (fromServer) {
      setChatId(fromServer);
      setHydrated(true);
      return;
    }
    if (typeof window === "undefined") return;
    setChatId(window.localStorage.getItem(chatIdKey));
    setHydrated(true);
  }, [chatIdKey, wsQuery.data?.conversationId]);
  // Tracks whether the chatId currently in state was assigned by a
  // mid-session onConversationId callback (i.e. the server returned
  // X-Conversation-Id on the very first POST of a brand-new chat).
  // We must NOT defer mounting <Chat> in that case — Chat is already
  // mounted and streaming the response that PRODUCED the id. Treating
  // it like "history loading" here unmounts Chat mid-stream, aborts
  // the fetch, and prevents the assistant message from ever persisting
  // client-side. That was the "messages disappear on refresh" bug.
  const sessionAssignedIdRef = useRef(false);
  const handleConversationId = (id: string) => {
    sessionAssignedIdRef.current = true;
    setChatId(id);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(chatIdKey, id);
    }
  };
  const conversationQuery = useConversation(chatId ?? undefined);
  const initialMessages = conversationQuery.data?.messages ?? [];
  // Defer mounting <Chat> only on FRESH page loads where we already
  // had a chatId from server/localStorage at hydrate time (so we know
  // history exists and we want to avoid mounting with [] then ignoring
  // the resolved data). On mid-session id assignment, Chat is already
  // mounted and we leave it alone.
  const isLoadingChatHistory =
    hydrated &&
    chatId !== null &&
    !sessionAssignedIdRef.current &&
    conversationQuery.isLoading &&
    !conversationQuery.data;

  // Fetch the selected file's content so we can extract its declared tweaks.
  // Same queryKey as HtmlPreview's useQuery — react-query dedupes the fetch.
  const isHtmlFile =
    selected?.type === "file" && /\.html?$/i.test(selected.name);
  const { data: fileContent = "" } = useQuery({
    queryKey: ["design-file", workspaceId, selected?.path],
    queryFn: () =>
      selected ? apiReadFile(workspaceId, selected.path) : Promise.resolve(""),
    enabled: Boolean(isHtmlFile && selected),
  });
  const tweaks = useMemo<Tweak[]>(
    () => (isHtmlFile && fileContent ? extractTweaks(fileContent) : []),
    [isHtmlFile, fileContent],
  );

  // Reset overrides when the selected file changes — overrides are per-file
  // and we don't want last file's accent applied to the next one.
  useEffect(() => {
    setTweakOverrides({});
  }, [selected?.path]);

  // Auto-open questions.html whenever it's present in the tree. Watches
  // the root tree (same queryKey as DesignFilesPanel — react-query
  // dedupes the fetch). Previously we only fired on the absent→present
  // transition, which meant if the user landed on a workspace that
  // ALREADY had questions.html (from a previous session, page refresh,
  // or branch switch) the form sat invisible in the file tree. Opening
  // unconditionally when present is the right default — the questions
  // are always the active surface until the user submits answers.
  const { data: rootTree = [] } = useQuery({
    queryKey: ["design-tree", workspaceId, "", 0],
    queryFn: () => apiTree(workspaceId, ""),
    enabled: !!workspaceId,
  });
  const openedQuestionsRef = useRef(false);
  useEffect(() => {
    openedQuestionsRef.current = false;
  }, [workspaceId]);
  useEffect(() => {
    const q = rootTree.find(
      (e) => e.type === "file" && e.name === "questions.html",
    );
    if (q && !openedQuestionsRef.current) {
      openedQuestionsRef.current = true;
      openFile(q);
    } else if (!q) {
      // Questions deleted (user submitted answers, design proceeded) —
      // reset so a future re-clarification can auto-open again.
      openedQuestionsRef.current = false;
    }
    // openFile is stable across renders. Listing it would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rootTree]);

  // Auto-open the entry HTML when generation lands a new design. Without
  // this the user finishes a turn and is left staring at the file list
  // wondering whether anything happened. Triggers when an entry-format file
  // (slides.html / prototype.html / etc.) appears at the root that wasn't
  // there a moment ago. Skips questions.html (handled by the watcher above)
  // and never overrides a file the user already picked.
  const ENTRY_NAMES = useMemo(
    () => new Set<string>(Object.values(FORMAT_FILE)),
    [],
  );
  const seenEntriesRef = useRef<Set<string> | null>(null);
  useEffect(() => {
    seenEntriesRef.current = null;
  }, [workspaceId]);
  useEffect(() => {
    const currentEntries = new Set<string>();
    for (const e of rootTree) {
      if (e.type === "file" && ENTRY_NAMES.has(e.name)) {
        currentEntries.add(e.name);
      }
    }
    // First snapshot: record what's already there so we don't pop the
    // entry file open just because the user navigated to a workspace.
    if (seenEntriesRef.current === null) {
      seenEntriesRef.current = currentEntries;
      return;
    }
    const newlyAppeared: string[] = [];
    for (const name of currentEntries) {
      if (!seenEntriesRef.current.has(name)) newlyAppeared.push(name);
    }
    seenEntriesRef.current = currentEntries;
    if (newlyAppeared.length === 0) return;
    // Don't yank focus if the user is actively looking at something.
    if (selected) return;
    const pick = newlyAppeared[0];
    const entry = rootTree.find(
      (e) => e.type === "file" && e.name === pick,
    );
    if (entry) openFile(entry);
    // openFile / selected captured intentionally — same closure pattern as
    // the questions watcher above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rootTree, ENTRY_NAMES]);

  // Listen for the clarifying-questions form (questions.html, generated by
  // the model when the brief is sparse) submitting answers. Serialize into
  // an unambiguous "generate now" brief, auto-submit, and clean up the
  // questions.html file so it doesn't sit in the panel during generation.
  useEffect(() => {
    const handler = (e: MessageEvent) => {
      const data = e.data as { type?: string; answers?: Record<string, string> } | null;
      if (!data || data.type !== "jarvis:design:questions:submit") return;

      // eslint-disable-next-line no-console
      console.log("[design] questions form submitted:", data.answers);

      const answers = data.answers ?? {};
      const cleaned: Array<[string, string]> = Object.entries(answers)
        .filter(([, v]) => typeof v === "string" && v.trim().length > 0)
        .map(([k, v]) => [k, v.trim()]);

      const bullets = cleaned.length > 0
        ? cleaned.map(([k, v]) => `- ${k}: ${v}`).join("\n")
        : "- (no answers provided — pick reasonable defaults and proceed)";
      const text = `Use my answers below to generate the design now. Don't ask more questions — these are the brief.\n\n${bullets}`;

      // 1. Auto-submit the brief into the chat (Chat sees autoSend, fires submit()).
      setChatTab("chat");
      setPrefillPrompt({ id: `${Date.now()}`, text, autoSend: true });

      // 2. Delete questions.html so the panel shows a clean slate while
      //    the model generates. Fire-and-forget — don't block the auto-send
      //    on filesystem I/O. Also reset the auto-open ref so a *future*
      //    questions.html (re-clarification) can auto-open again.
      openedQuestionsRef.current = false;
      void apiDeleteEntry(workspaceId, "questions.html")
        .then(() => {
          qc.invalidateQueries({ queryKey: ["design-tree", workspaceId] });
          // Clear selection if questions.html was the open file.
          setSelected((cur) =>
            cur && cur.path === "questions.html" ? null : cur,
          );
          // Close any open file tabs pointing to questions.html.
          setTabs((prev) => prev.filter((t) => t.kind !== "file" || t.entry.path !== "questions.html"));
          if (activeKey === "f:questions.html") setActiveKey("__files");
        })
        .catch((err) => {
          // eslint-disable-next-line no-console
          console.warn("[design] couldn't delete questions.html:", err);
        });
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
    // workspaceId in deps so the handler closes over the *current* workspace
    // when the user switches projects mid-flow.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  const chatColumn = useResizableColumn({
    storageKey: "design.chatColumnWidth",
    defaultWidth: 380,
    // 380px is the floor below which the composer's bottom toolbar
    // (paperclip / model picker / workspace picker / voice / send)
    // gets squeezed past the comfortable point — icons can disappear
    // under flex-shrink. Bumped from 280 → 380 to match the workbench
    // panel's hard minimum.
    min: 380,
    max: 640,
  });

  const closeDetails = (e: React.MouseEvent) => {
    e.currentTarget.closest("details")?.removeAttribute("open");
  };

  const presentInThisTab = () => {
    if (!selected) return;
    window.location.href = `/api/workspace/${workspaceId}/file?path=${encodeURIComponent(selected.path)}&raw=1`;
  };
  const presentFullscreen = () => {
    // Put the live preview iframe into fullscreen — works because the iframe
    // is in our document, not a cross-origin doc. Fall back to a new tab if
    // the browser blocks the request.
    const iframe = document.querySelector<HTMLIFrameElement>(
      "iframe[title]:not([sandbox=''])",
    );
    if (iframe?.requestFullscreen) {
      iframe.requestFullscreen().catch(() => presentInNewTab());
    } else {
      presentInNewTab();
    }
  };
  const presentInNewTab = () => {
    if (!selected) return;
    window.open(
      `/api/workspace/${workspaceId}/file?path=${encodeURIComponent(selected.path)}&raw=1`,
      "_blank",
      "noopener",
    );
  };

  const handleComment = (c: DesignComment) => {
    designComments.add({
      filePath: c.filePath,
      selector: c.selector,
      tag: c.tag,
      text: c.text,
      comment: c.comment,
    });
    setPrefillPrompt({
      id: `${Date.now()}`,
      text: buildEditPrompt(c),
    });
  };

  const handleNewThread = () => {
    setChatKey((k) => k + 1);
    setChatTab("chat");
  };

  const handleReuseComment = (c: DesignCommentRecord) => {
    setChatTab("chat");
    setPrefillPrompt({
      id: `${Date.now()}`,
      text: buildEditPrompt({
        filePath: c.filePath,
        selector: c.selector,
        tag: c.tag,
        text: c.text,
        comment: c.comment,
      }),
    });
  };

  const openRefine = () => {
    setTabs((prev) =>
      prev.some((t) => t.kind === "refine") ? prev : [...prev, { kind: "refine" }],
    );
    setActiveKey("__refine");
  };

  const closeRefine = () => {
    setTabs((prev) => prev.filter((t) => t.kind !== "refine"));
    setActiveKey("__files");
  };

  const handleRefineContinue = (structuredPrompt: string) => {
    setPrefillPrompt({ id: `${Date.now()}`, text: structuredPrompt });
    setChatTab("chat");
    closeRefine();
  };

  const openFile = (entry: TreeEntry) => {
    if (entry.type === "dir") return;
    setSelected(entry);
    setTabs((prev) => {
      const k = `f:${entry.path}`;
      if (prev.some((t) => tabKey(t) === k)) return prev;
      return [...prev, { kind: "file", entry }];
    });
    setActiveKey(`f:${entry.path}`);
  };

  const closeTab = (k: string) => {
    setTabs((prev) => {
      const next = prev.filter((t) => tabKey(t) !== k);
      // Always keep at least the Design Files tab.
      if (next.length === 0 || !next.some((t) => t.kind === "files")) {
        return [{ kind: "files" }, ...next.filter((t) => t.kind !== "files")];
      }
      return next;
    });
    if (activeKey === k) setActiveKey("__files");
  };

  // Called by DesignFilesPanel after a successful single-entry delete. The
  // tree query is already invalidated there; this just makes sure the
  // preview pane and any open tab pointing at the gone file get cleared,
  // so the user doesn't keep staring at the now-deleted preview.
  const handleFileDeleted = (path: string) => {
    const matches = (p: string) => p === path || p.startsWith(path + "/");
    setSelected((cur) => (cur && matches(cur.path) ? null : cur));
    setTabs((prev) =>
      prev.filter((t) => t.kind !== "file" || !matches(t.entry.path)),
    );
    setActiveKey((cur) => {
      if (cur.startsWith("f:") && matches(cur.slice(2))) return "__files";
      return cur;
    });
  };

  // Called by DesignFilesPanel after the workspace is wiped. Drop the
  // selection + every file tab — every preview is stale by definition.
  const handleWorkspaceCleared = () => {
    setSelected(null);
    setTabs([{ kind: "files" }]);
    setActiveKey("__files");
  };

  const router = useRouter();
  const handleBuild = async () => {
    if (buildPending) return;
    setBuildPending(true);
    // Toast + console traces so failures STOP being silent. The
    // previous version only showed alert() on error which fires too
    // late if the request itself never resolves (timeout, navigator
    // dropping the fetch). Surface every step so the user sees
    // exactly what stage stalled.
    const buildToast = toast.loading("Preparing build…", {
      description: "Copying design files into a new workbench workspace",
    });
    try {
      const r = await fetch("/api/design/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sourceWorkspaceId: workspaceId }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        const msg = j.error ?? r.statusText ?? "unknown error";
        console.error("[design/build] failed:", r.status, msg);
        toast.error("Build failed", {
          id: buildToast,
          description: msg,
        });
        return;
      }
      const j = (await r.json()) as {
        workspaceId: string;
        seed: string;
        copiedFiles?: number;
      };
      console.log(
        `[design/build] ok: workspace=${j.workspaceId}, copiedFiles=${j.copiedFiles ?? "?"}, seedLen=${j.seed.length}`,
      );
      toast.success("Build queued", {
        id: buildToast,
        description: `Copied ${j.copiedFiles ?? "?"} design files. Opening workbench…`,
      });
      // Hand the seed to the workbench via sessionStorage, NOT the URL.
      // The seed runs from a few KB up to ~80KB (inlined design source on
      // the codegen-fallback path), which overflows URL/header limits and
      // makes the App Router RSC navigation 431 / truncate — silently
      // breaking the build on larger designs. The workbench reads + clears
      // it on mount and the Chat auto-fires it. URL param kept only as a
      // private-mode fallback (works for small seeds).
      let stashed = false;
      try {
        sessionStorage.setItem(`workbench:seed:${j.workspaceId}`, j.seed);
        stashed = true;
      } catch {
        /* storage disabled — fall back to the URL param */
      }
      router.push(
        stashed
          ? `/workbench/${j.workspaceId}`
          : `/workbench/${j.workspaceId}?seed=${encodeURIComponent(j.seed)}`,
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error("[design/build] threw:", err);
      toast.error("Build failed", {
        id: buildToast,
        description: msg,
      });
    } finally {
      setBuildPending(false);
    }
  };

  const initials = (settings?.user?.name ?? "U").slice(0, 1).toUpperCase();
  const showFiles = activeKey === "__files";
  const showRefine = activeKey === "__refine";

  return (
    <div className="flex h-full flex-col bg-background">
      {/* ── Top bar ─────────────────────────────────────────── */}
      <header className="flex h-12 shrink-0 items-stretch border-b border-border/60">
        <div
          className="flex shrink-0 items-stretch border-r border-border/60"
          style={{ width: chatColumn.width }}
        >
          <SidebarToggle />
          <ProjectPicker
            current={{ id: workspaceId, name: workspaceName }}
            projects={projects}
          />
        </div>

        <div className="flex flex-1 items-stretch overflow-x-auto">
          {tabs.map((t) => {
            const k = tabKey(t);
            const active = activeKey === k;
            const label = tabLabel(t);
            return (
              <button
                key={k}
                type="button"
                onClick={() => {
                  setActiveKey(k);
                  if (t.kind === "file") setSelected(t.entry);
                }}
                className={cn(
                  "group flex items-center gap-2 border-r border-border/60 px-4 text-[13px] transition-colors",
                  active
                    ? "bg-background text-foreground"
                    : "bg-muted/20 text-muted-foreground hover:bg-muted/30",
                )}
              >
                {t.kind === "files" ? (
                  <Palette className="size-3.5 text-muted-foreground" />
                ) : null}
                <span className="truncate max-w-48">{label}</span>
                {t.kind === "file" && (
                  <span
                    role="button"
                    tabIndex={-1}
                    aria-label="Close tab"
                    className="ml-1 rounded p-0.5 text-muted-foreground/60 opacity-60 hover:bg-muted hover:text-foreground hover:opacity-100"
                    onClick={(e) => {
                      e.stopPropagation();
                      closeTab(k);
                    }}
                  >
                    <X className="size-3" />
                  </span>
                )}
              </button>
            );
          })}
        </div>

        <div className="flex shrink-0 items-center gap-2 px-3">
          {/* Build — copies design files to a new workbench workspace and
              fires a seed prompt that scaffolds a full-stack version. */}
          <button
            type="button"
            onClick={handleBuild}
            disabled={buildPending}
            className={cn(
              "flex items-center gap-1.5 rounded-md border border-border/60 px-2.5 py-1 text-[13px] transition-colors",
              "hover:border-primary/50 hover:bg-primary/5 hover:text-foreground",
              "text-muted-foreground",
              buildPending && "opacity-60",
            )}
            title="Build — open this design in the Workbench and scaffold a full-stack app"
          >
            {buildPending ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Hammer className="size-3.5" />
            )}
            Build
          </button>
          {isHtmlFile && (
            <button
              type="button"
              role="switch"
              aria-checked={showTweaks}
              onClick={() => setShowTweaks((v) => !v)}
              className={cn(
                "flex items-center gap-2 rounded-md px-2 py-1 text-[13px] transition-colors",
                showTweaks
                  ? "text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
              title="Tweaks — live knobs the design declared"
            >
              <Sliders className="size-3.5" />
              Tweaks
              <span
                className={cn(
                  "relative inline-flex h-4 w-7 items-center rounded-full transition-colors",
                  showTweaks ? "bg-primary" : "bg-muted",
                )}
              >
                <span
                  className={cn(
                    "inline-block size-3 rounded-full bg-background shadow transition-transform",
                    showTweaks ? "translate-x-3.5" : "translate-x-0.5",
                  )}
                />
              </span>
            </button>
          )}
          {selected && selected.type !== "dir" && (
            <details className="relative">
              <summary className="flex cursor-pointer list-none items-center gap-1 rounded-md px-2 py-1 text-[13px] text-muted-foreground hover:bg-muted">
                Present
                <ChevronDown className="size-3" />
              </summary>
              <div className="absolute right-0 top-full z-20 mt-1 w-52 overflow-hidden rounded-md border border-border/60 bg-popover shadow-md">
                <button
                  type="button"
                  onClick={(e) => { closeDetails(e); presentInThisTab(); }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] hover:bg-muted"
                >
                  <ExternalLink className="size-3.5" />
                  In this tab
                </button>
                <button
                  type="button"
                  onClick={(e) => { closeDetails(e); presentFullscreen(); }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] hover:bg-muted"
                >
                  <Maximize className="size-3.5" />
                  Fullscreen
                </button>
                <button
                  type="button"
                  onClick={(e) => { closeDetails(e); presentInNewTab(); }}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] hover:bg-muted"
                >
                  <Play className="size-3.5" />
                  New tab
                </button>
                <div className="my-1 h-px bg-border/60" />
                <a
                  href={(() => {
                    const f = formatFromFilename(selected.name);
                    const q = `workspaceId=${encodeURIComponent(workspaceId)}&path=${encodeURIComponent(selected.path)}`;
                    return `/api/design/export?${q}${f ? `&format=${f}` : ""}`;
                  })()}
                  className="flex items-center gap-2 px-3 py-2 text-[13px] hover:bg-muted"
                >
                  <Download className="size-3.5" />
                  Export PDF
                </a>
                <a
                  href={`/api/workspace/${workspaceId}/file?path=${encodeURIComponent(selected.path)}&raw=1`}
                  download={selected.name}
                  className="flex items-center gap-2 px-3 py-2 text-[13px] hover:bg-muted"
                >
                  <Download className="size-3.5" />
                  Export HTML
                </a>
              </div>
            </details>
          )}
          <ShareButton workspaceId={workspaceId} />
          <Link
            href="/settings"
            className="flex size-7 items-center justify-center rounded-full bg-primary/20 font-mono text-[11px] font-semibold text-primary"
          >
            {initials}
          </Link>
        </div>
      </header>

      {/* ── Body: chat | files-or-preview-only | preview ──── */}
      <div className="relative flex flex-1 min-h-0">
        {/* Left: chat — width is user-resizable via the divider below */}
        <aside
          className="flex shrink-0 flex-col border-r border-border/60"
          style={{ width: chatColumn.width }}
        >
          <ChatTabsHeader
            tab={chatTab}
            onTabChange={setChatTab}
            commentsCount={designComments.items.length}
            onNewThread={handleNewThread}
          />
          <div className="flex-1 min-h-0">
            {chatTab === "chat" ? (
              isLoadingChatHistory ? (
                <div className="flex h-full items-center justify-center text-[12px] text-muted-foreground/70">
                  loading conversation…
                </div>
              ) : (
              <Chat
                // Include workspaceId so switching/deleting a project remounts
                // the chat — otherwise the previous project's messages bleed
                // into the new project's panel. chatKey is bumped by the
                // explicit "+ new chat" button so a manual reset remounts.
                //
                // chatId is intentionally NOT part of the key. When a brand-new
                // conversation gets its server-assigned id mid-stream (via the
                // X-Conversation-Id header → onConversationId → setChatId),
                // including chatId in the key would trigger a remount during
                // the very first response — aborting the in-flight fetch,
                // wiping the streaming state, and (worst case) preventing the
                // assistant message from ever persisting. That was the
                // "messages disappear on refresh" bug.
                key={`${workspaceId}:${chatKey}`}
                embedded
                hideSidebarToggle
                unifiedUX
                mode="design"
                workspaceId={workspaceId}
                workspaceName={workspaceName}
                chatId={chatId ?? undefined}
                initialMessages={initialMessages}
                onConversationId={handleConversationId}
                composerPlaceholder="Describe what you want to create — slides, prototype, landing, one-pager, infographic…"
                // Streaming preview overlay is intentionally NOT wired here —
                // we want files to appear in the panel as they finish writing
                // (which the runner does on action-close + tree invalidation),
                // not a live in-iframe render that updates per chunk. This is
                // also the biggest speed win: dropping the per-chunk callbacks
                // removes the throttle + re-render storm on long generations.
                prefillPrompt={prefillPrompt}
              />
              )
            ) : (
              <CommentsList
                items={designComments.items}
                onReuse={handleReuseComment}
                onRemove={designComments.remove}
                onClear={designComments.clear}
              />
            )}
          </div>
        </aside>

        {/* Draggable splitter — drag horizontally to resize the chat column.
            The hit-target is wider than the visible bar for easier grabbing. */}
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize chat column"
          onMouseDown={chatColumn.startDrag}
          className={cn(
            "group relative w-1 shrink-0 cursor-col-resize select-none",
            "before:absolute before:inset-y-0 before:-left-1 before:-right-1 before:content-['']",
            chatColumn.dragging
              ? "bg-primary/40"
              : "bg-transparent hover:bg-primary/20",
          )}
        />

        {/* Center: brand editor when toggled; otherwise files+preview or
            preview-only depending on the active tab. */}
        {showBrand ? (
          <div className="flex flex-1 min-w-0">
            <div className="flex flex-1 min-w-0 flex-col border-r border-border/60">
              <BrandPanel workspaceId={workspaceId} />
            </div>
            <div className="flex w-[42%] min-w-80 shrink-0 flex-col">
              <DesignPreview
                workspaceId={workspaceId}
                selected={selected}
                streaming={streaming}
                tweaks={tweaks}
                tweakOverrides={tweakOverrides}
              />
            </div>
          </div>
        ) : showRefine ? (
          <div className="flex flex-1 min-w-0">
            <RefineForm
              onContinue={handleRefineContinue}
              onCancel={closeRefine}
            />
          </div>
        ) : showFiles ? (
          <div className="flex flex-1 min-w-0">
            <div className="flex flex-1 min-w-0 flex-col">
              <DesignFilesPanel
                workspaceId={workspaceId}
                selectedPath={selected?.path ?? null}
                onSelectFile={openFile}
                onStarter={(prompt) =>
                  setPrefillPrompt({ id: `${Date.now()}`, text: prompt })
                }
                onToggleBrand={() => setShowBrand((v) => !v)}
                brandActive={showBrand}
                onRefine={openRefine}
                onFileDeleted={handleFileDeleted}
                onWorkspaceCleared={handleWorkspaceCleared}
              />
            </div>
            <div className="flex w-[42%] min-w-80 shrink-0 flex-col border-l border-border/60">
              <DesignPreview
                workspaceId={workspaceId}
                selected={selected}
                streaming={streaming}
                tweaks={tweaks}
                tweakOverrides={tweakOverrides}
              />
            </div>
          </div>
        ) : (
          <div className="flex flex-1 min-w-0 flex-col">
            <DesignPreview
              workspaceId={workspaceId}
              selected={selected}
              streaming={streaming}
              showToolbar
              onComment={handleComment}
              tweaks={tweaks}
              tweakOverrides={tweakOverrides}
            />
          </div>
        )}

        {showTweaks && isHtmlFile && (
          <TweaksPanel
            tweaks={tweaks}
            values={tweakOverrides}
            onChange={(id, value) =>
              setTweakOverrides((o) => ({ ...o, [id]: value }))
            }
            onClose={() => setShowTweaks(false)}
          />
        )}
      </div>
    </div>
  );
}

// Share — mints a public, read-only link to the design via the existing
// /api/workspace/[id]/share endpoint. The /share/<token> page renders the
// design's entry HTML (served through the token-scoped asset route) so anyone
// with the link sees the live design, never the source files.
function ShareButton({ workspaceId }: { workspaceId: string }) {
  const [loading, setLoading] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const create = async () => {
    if (loading) return;
    setLoading(true);
    try {
      const r = await fetch(`/api/workspace/${workspaceId}/share`, {
        method: "POST",
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.error ?? `HTTP ${r.status}`);
      }
      const j = (await r.json()) as { path: string };
      setUrl(`${window.location.origin}${j.path}`);
    } catch (e) {
      toast.error(`Couldn't create share link: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const revoke = async () => {
    try {
      await fetch(`/api/workspace/${workspaceId}/share`, { method: "DELETE" });
      setUrl(null);
      toast.success("Share link revoked");
    } catch (e) {
      toast.error(`Couldn't revoke link: ${(e as Error).message}`);
    }
  };

  const copy = () => {
    if (!url) return;
    void navigator.clipboard.writeText(url);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <details className="relative">
      <summary className="flex h-7 cursor-pointer list-none items-center gap-1.5 rounded-md bg-foreground px-2.5 text-[13px] font-medium text-background transition-colors hover:bg-foreground/90">
        <Share2 className="size-3.5" />
        Share
      </summary>
      <div className="absolute right-0 top-full z-20 mt-1 w-72 rounded-md border border-border/60 bg-popover p-3 shadow-md">
        {url ? (
          <div className="space-y-2">
            <p className="text-[12px] leading-4 text-muted-foreground">
              Anyone with this link can view this design (read-only, expires in
              7 days).
            </p>
            <div className="flex items-center gap-1.5">
              <input
                readOnly
                value={url}
                onFocus={(e) => e.currentTarget.select()}
                className="min-w-0 flex-1 rounded border border-border/60 bg-card px-2 py-1 text-[12px] text-foreground outline-none"
              />
              <button
                type="button"
                onClick={copy}
                aria-label="Copy link"
                title="Copy link"
                className="flex size-7 shrink-0 items-center justify-center rounded-md border border-border/60 text-muted-foreground transition-colors hover:text-foreground"
              >
                {copied ? (
                  <Check className="size-3.5 text-primary" />
                ) : (
                  <Copy className="size-3.5" />
                )}
              </button>
            </div>
            <div className="flex items-center justify-between pt-0.5">
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1 text-[12px] text-primary hover:underline"
              >
                Open <ExternalLink className="size-3" />
              </a>
              <button
                type="button"
                onClick={revoke}
                className="text-[12px] text-muted-foreground transition-colors hover:text-destructive"
              >
                Revoke link
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-[12px] leading-4 text-muted-foreground">
              Create a public, read-only link to this design that anyone can
              open.
            </p>
            <button
              type="button"
              onClick={create}
              disabled={loading}
              className="flex w-full items-center justify-center gap-1.5 rounded-md bg-primary px-2.5 py-1.5 text-[13px] font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-60"
            >
              {loading ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Link2 className="size-3.5" />
              )}
              Create share link
            </button>
          </div>
        )}
      </div>
    </details>
  );
}

function ChatTabsHeader({
  tab,
  onTabChange,
  commentsCount,
  onNewThread,
}: {
  tab: "chat" | "comments";
  onTabChange: (t: "chat" | "comments") => void;
  commentsCount: number;
  onNewThread: () => void;
}) {
  return (
    <div className="flex h-11 shrink-0 items-center justify-between border-b border-border/60 px-2">
      <div className="flex items-center">
        {(["chat", "comments"] as const).map((t) => {
          const active = t === tab;
          return (
            <button
              key={t}
              type="button"
              onClick={() => onTabChange(t)}
              className={cn(
                "flex items-center gap-1.5 px-2.5 py-1.5 text-[13px] font-medium capitalize transition-colors",
                active
                  ? "text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t}
              {t === "comments" && commentsCount > 0 && (
                <span
                  className={cn(
                    "rounded px-1.5 py-0 font-mono text-[10px]",
                    active ? "bg-foreground text-background" : "bg-muted text-muted-foreground",
                  )}
                >
                  {commentsCount}
                </span>
              )}
            </button>
          );
        })}
      </div>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label="New thread"
        title="New thread (start a fresh chat in this workspace)"
        onClick={onNewThread}
      >
        <Plus className="size-3.5" />
      </Button>
    </div>
  );
}

function CommentsList({
  items,
  onReuse,
  onRemove,
  onClear,
}: {
  items: DesignCommentRecord[];
  onReuse: (c: DesignCommentRecord) => void;
  onRemove: (id: string) => void;
  onClear: () => void;
}) {
  if (items.length === 0) {
    return (
      <div className="flex h-full flex-col items-start justify-start px-4 py-6 text-[13px] leading-5 text-muted-foreground">
        <p>No comments yet.</p>
        <p className="mt-2 max-w-xs">
          Open a design, click <strong>Comment</strong> in the preview toolbar,
          point at any element, type a change request. It&apos;ll show up here
          and prefill the chat.
        </p>
      </div>
    );
  }
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-3 py-2 text-[11px] uppercase tracking-wide text-muted-foreground">
        <span>{items.length} comments</span>
        <button
          type="button"
          onClick={() => {
            if (window.confirm(`Clear all ${items.length} comments?`)) onClear();
          }}
          className="hover:text-foreground"
        >
          Clear all
        </button>
      </div>
      <div className="flex-1 space-y-1 overflow-y-auto px-2 pb-3">
        {items.map((c) => (
          <CommentRow key={c.id} item={c} onReuse={onReuse} onRemove={onRemove} />
        ))}
      </div>
    </div>
  );
}

function CommentRow({
  item,
  onReuse,
  onRemove,
}: {
  item: DesignCommentRecord;
  onReuse: (c: DesignCommentRecord) => void;
  onRemove: (id: string) => void;
}) {
  const ago = relativeTime(item.createdAt);
  return (
    <div className="group rounded-md border border-transparent px-2 py-2 transition-colors hover:border-border/60 hover:bg-muted/30">
      <div className="mb-1 flex items-center gap-2 text-[11px] text-muted-foreground">
        <span className="rounded bg-muted px-1.5 py-0 font-mono text-[10px] text-foreground">
          {item.tag}
        </span>
        <span className="truncate">{item.filePath}</span>
        <span className="ml-auto whitespace-nowrap">{ago}</span>
      </div>
      {item.text && (
        <div className="mb-1 line-clamp-1 text-[12px] text-muted-foreground/90">
          &ldquo;{item.text}&rdquo;
        </div>
      )}
      <div className="text-[13px] leading-5 text-foreground">{item.comment}</div>
      <div className="mt-1.5 flex gap-2 opacity-0 transition-opacity group-hover:opacity-100">
        <button
          type="button"
          onClick={() => onReuse(item)}
          className="text-[11px] text-primary hover:underline"
        >
          Re-send
        </button>
        <button
          type="button"
          onClick={() => onRemove(item.id)}
          className="text-[11px] text-muted-foreground hover:text-destructive"
        >
          Remove
        </button>
      </div>
    </div>
  );
}

function relativeTime(ts: number): string {
  const diff = Math.max(0, Date.now() - ts);
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}d`;
  return new Date(ts).toLocaleDateString();
}
