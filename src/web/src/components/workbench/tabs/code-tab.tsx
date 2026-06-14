"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Group, Panel, Separator } from "react-resizable-panels";
import {
  GitBranch,
  Search,
  Terminal as TerminalIcon,
  Zap,
  Eye,
  X,
} from "lucide-react";
import { FileTree } from "../file-tree";
import { FileSearch } from "../file-search";
import { Editor } from "../editor";
import { PreviewTab } from "./preview-tab";
import type { ViewportPreset } from "../toolbar";
import { cn } from "@/lib/utils";

const WorkbenchTerminal = dynamic(
  () => import("../terminal").then((m) => m.WorkbenchTerminal),
  { ssr: false },
);

type LeftPaneTab = "files" | "search";
type BottomPaneTab = "terminal" | "bolt";

type Props = {
  workspaceId: string;
  activePath: string | null;
  onOpen: (path: string) => void;
  // When the user closes the editor (X on the breadcrumb), we drop back
  // to the preview iframe — same UX as v0 / Lovable / Bolt where the
  // canvas is the default and the file viewer is opt-in.
  onClosePath: () => void;
  iframeKey: number;
  viewport: ViewportPreset;
};

export function CodeTab({
  workspaceId,
  activePath,
  onOpen,
  onClosePath,
  iframeKey,
  viewport,
}: Props) {
  const [leftTab, setLeftTab] = useState<LeftPaneTab>("files");
  const [bottomTab, setBottomTab] = useState<BottomPaneTab>("terminal");
  // Open editor tabs. activePath (owned by the parent) is the active one;
  // this list is every file the user has opened, so they can switch
  // between them instead of the old one-file-at-a-time model.
  const [openPaths, setOpenPaths] = useState<string[]>([]);

  // Keep the tab list in sync with externally-driven activations (file
  // tree, search, deep-links, recently-edited jumps).
  useEffect(() => {
    if (activePath && !openPaths.includes(activePath)) {
      setOpenPaths((prev) => [...prev, activePath]);
    }
  }, [activePath, openPaths]);

  const handleOpen = (path: string) => {
    setOpenPaths((prev) => (prev.includes(path) ? prev : [...prev, path]));
    onOpen(path);
  };

  const closeTab = (path: string) => {
    setOpenPaths((prev) => {
      const idx = prev.indexOf(path);
      const next = prev.filter((p) => p !== path);
      // If we closed the active file, activate a neighbor or fall back to
      // the preview canvas when nothing's left open.
      if (path === activePath) {
        const fallback = next[idx] ?? next[idx - 1] ?? null;
        if (fallback) onOpen(fallback);
        else onClosePath();
      }
      return next;
    });
  };

  return (
    <Group orientation="horizontal" style={{ height: "100%" }}>
      {/* Left: Files / Search tabs */}
      <Panel
        defaultSize="22%"
        minSize="14%"
        maxSize="40%"
        // border-r removed — the <Separator> below is the only divider
        // between this Files/Search rail and the editor pane. Having
        // both produced a doubled vertical seam. Same pattern fix as
        // workbench/[id]/page.tsx.
        className="overflow-hidden"
      >
        <div className="flex h-full flex-col">
          <div className="flex items-center gap-3 px-3 py-2 border-b border-border/50">
            <LeftTabButton
              active={leftTab === "files"}
              onClick={() => setLeftTab("files")}
              icon={<GitBranch className="size-3.5" />}
              label="Files"
            />
            <LeftTabButton
              active={leftTab === "search"}
              onClick={() => setLeftTab("search")}
              icon={<Search className="size-3.5" />}
              label="Search"
            />
          </div>
          <div className="flex-1 min-h-0">
            {leftTab === "files" ? (
              <FileTree
                workspaceId={workspaceId}
                activePath={activePath}
                onOpen={handleOpen}
              />
            ) : (
              <FileSearch workspaceId={workspaceId} onOpen={handleOpen} />
            )}
          </div>
        </div>
      </Panel>

      <Separator className="w-px bg-foreground/15 hover:bg-primary/50 transition-colors" />

      {/* Right: editor (or preview when no file is open) stacked over a
          tabbed bottom pane. Preview is the default — clicking a file
          swaps it for the editor. */}
      <Panel defaultSize="78%" className="overflow-hidden">
        <Group orientation="vertical" style={{ height: "100%" }}>
          <Panel defaultSize="68%" minSize="20%" className="overflow-hidden">
            <div className="flex h-full flex-col">
              {openPaths.length > 0 && (
                <EditorTabs
                  openPaths={openPaths}
                  activePath={activePath}
                  onSelect={onOpen}
                  onClose={closeTab}
                  onPreview={onClosePath}
                />
              )}
              <div className="flex-1 min-h-0 overflow-hidden">
                {activePath ? (
                  <Editor workspaceId={workspaceId} path={activePath} />
                ) : (
                  <PreviewTab
                    workspaceId={workspaceId}
                    iframeKey={iframeKey}
                    viewport={viewport}
                  />
                )}
              </div>
            </div>
          </Panel>
          <Separator className="h-px bg-foreground/15 hover:bg-primary/50 transition-colors" />
          <Panel defaultSize="32%" minSize="10%" className="overflow-hidden">
            <BottomPane
              workspaceId={workspaceId}
              tab={bottomTab}
              setTab={setBottomTab}
            />
          </Panel>
        </Group>
      </Panel>
    </Group>
  );
}

function EditorTabs({
  openPaths,
  activePath,
  onSelect,
  onClose,
  onPreview,
}: {
  openPaths: string[];
  activePath: string | null;
  onSelect: (path: string) => void;
  onClose: (path: string) => void;
  onPreview: () => void;
}) {
  const reduce = useReducedMotion();
  return (
    <div className="flex items-center gap-0.5 overflow-x-auto border-b border-border/40 bg-muted/10 px-1.5 py-1">
      <AnimatePresence initial={false}>
        {openPaths.map((p) => {
          const name = p.split("/").pop() ?? p;
          const active = p === activePath;
          return (
            <motion.div
              key={p}
              layout={!reduce}
              initial={reduce ? false : { opacity: 0, scale: 0.92 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.92 }}
              transition={{ duration: reduce ? 0 : 0.12, ease: "easeOut" }}
              onClick={() => onSelect(p)}
              title={p}
              className={cn(
                "group flex shrink-0 cursor-pointer items-center gap-1 rounded px-2 py-0.5 text-[11px] transition-colors",
                active
                  ? "bg-accent/50 text-foreground"
                  : "text-muted-foreground hover:bg-accent/30 hover:text-foreground",
              )}
            >
              <span className="max-w-[12rem] truncate">{name}</span>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onClose(p);
                }}
                className="rounded p-0.5 opacity-50 transition-opacity hover:bg-destructive/20 hover:text-destructive group-hover:opacity-100"
                aria-label={`Close ${name}`}
                title={`Close ${name}`}
              >
                <X className="size-3" />
              </button>
            </motion.div>
          );
        })}
      </AnimatePresence>
      {/* Preview pill — back to the live canvas without closing tabs. */}
      <button
        onClick={onPreview}
        title="Back to preview"
        className={cn(
          "ml-1 flex shrink-0 items-center gap-1 rounded px-2 py-0.5 text-[11px] transition-colors",
          activePath === null
            ? "bg-accent/50 text-foreground"
            : "text-muted-foreground hover:bg-accent/30 hover:text-foreground",
        )}
      >
        <Eye className="size-3" />
        <span>Preview</span>
      </button>
    </div>
  );
}

function LeftTabButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 text-[12px] transition-colors",
        active
          ? "text-foreground"
          : "text-muted-foreground hover:text-foreground/80",
      )}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}

function BottomPane({
  workspaceId,
  tab,
  setTab,
}: {
  workspaceId: string;
  tab: BottomPaneTab;
  setTab: (t: BottomPaneTab) => void;
}) {
  return (
    <div className="flex h-full flex-col bg-[#0b0b0d]">
      <div className="flex items-center gap-1 px-2 py-1.5 border-b border-border/40">
        <BottomTabButton
          active={tab === "bolt"}
          onClick={() => setTab("bolt")}
          icon={<Zap className="size-3" />}
          label="Jarvis"
        />
        <BottomTabButton
          active={tab === "terminal"}
          onClick={() => setTab("terminal")}
          icon={<TerminalIcon className="size-3" />}
          label="Terminal"
        />
      </div>
      <div className="flex-1 min-h-0">
        {tab === "terminal" && <WorkbenchTerminal workspaceId={workspaceId} />}
        {tab === "bolt" && <BoltOutputPlaceholder />}
      </div>
    </div>
  );
}

function BottomTabButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 rounded-md px-2 py-0.5 text-[11px] transition-colors",
        active
          ? "bg-muted/40 text-foreground"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}

function BoltOutputPlaceholder() {
  return (
    <div className="flex h-full items-center justify-center px-6 text-center text-[11px] text-muted-foreground">
      Action log will appear here when Jarvis is building. Use the chat panel
      on the left to ask for a project.
    </div>
  );
}
