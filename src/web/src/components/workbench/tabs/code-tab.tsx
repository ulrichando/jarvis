"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import { Group, Panel, Separator } from "react-resizable-panels";
import {
  GitBranch,
  Search,
  Terminal as TerminalIcon,
  Zap,
  Plus,
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

  return (
    <Group orientation="horizontal" style={{ height: "100%" }}>
      {/* Left: Files / Search tabs */}
      <Panel
        defaultSize="22%"
        minSize="14%"
        maxSize="40%"
        className="border-r border-border/50 overflow-hidden"
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
                onOpen={onOpen}
              />
            ) : (
              <FileSearch workspaceId={workspaceId} onOpen={onOpen} />
            )}
          </div>
        </div>
      </Panel>

      <Separator className="w-px bg-border/50 hover:bg-primary/40 transition-colors" />

      {/* Right: editor (or preview when no file is open) stacked over a
          tabbed bottom pane. Preview is the default — clicking a file
          swaps it for the editor. */}
      <Panel defaultSize="78%" className="overflow-hidden">
        <Group orientation="vertical" style={{ height: "100%" }}>
          <Panel defaultSize="68%" minSize="20%" className="overflow-hidden">
            {activePath ? (
              <div className="flex h-full flex-col">
                <div className="flex items-center justify-end px-2 py-1 border-b border-border/40 bg-muted/10">
                  <button
                    onClick={onClosePath}
                    title="Close file (back to preview)"
                    className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-muted-foreground hover:text-foreground hover:bg-accent/40 transition-colors"
                  >
                    <X className="size-3" />
                    <span>Preview</span>
                  </button>
                </div>
                <div className="flex-1 min-h-0 overflow-hidden">
                  <Editor workspaceId={workspaceId} path={activePath} />
                </div>
              </div>
            ) : (
              <PreviewTab
                workspaceId={workspaceId}
                iframeKey={iframeKey}
                viewport={viewport}
              />
            )}
          </Panel>
          <Separator className="h-px bg-border/50 hover:bg-primary/40 transition-colors" />
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
        <button
          className="ml-1 flex size-5 items-center justify-center rounded text-muted-foreground hover:bg-accent/40 hover:text-foreground"
          title="New tab (coming soon)"
          disabled
        >
          <Plus className="size-3" />
        </button>
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
