"use client";

import { X, Folder, ArrowRight, GitCompare, ListChecks } from "lucide-react";

export type PanelName = "diff" | "background" | "plan";
export type PanelsState = Record<PanelName, boolean>;

function PanelShell({
  header,
  onClose,
  children,
}: {
  header: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col border-l border-border/40">
      <div className="flex h-11 shrink-0 items-center justify-between px-4">
        <div className="flex min-w-0 items-center gap-1.5 text-[13px] text-foreground/80">{header}</div>
        <button
          type="button"
          aria-label="Close panel"
          onClick={onClose}
          className="flex size-6 items-center justify-center rounded text-muted-foreground hover:bg-accent/50 hover:text-foreground"
        >
          <X className="size-4" />
        </button>
      </div>
      <div className="flex flex-1 flex-col items-center justify-center px-4 text-center">{children}</div>
    </div>
  );
}

function Empty({ icon, lines }: { icon: React.ReactNode; lines: string[] }) {
  return (
    <div className="max-w-[200px] text-[12.5px] text-muted-foreground/70">
      <div className="mb-2 flex justify-center text-muted-foreground/50">{icon}</div>
      {lines.map((l, i) => (
        <div key={i} className={i === 0 ? "text-muted-foreground/80" : "mt-1 text-muted-foreground/55"}>
          {l}
        </div>
      ))}
    </div>
  );
}

export function CodePanels({
  panels,
  onClose,
  baseBranch = "main",
  workBranch,
}: {
  panels: PanelsState;
  onClose: (p: PanelName) => void;
  baseBranch?: string;
  workBranch: string;
}) {
  // Diff + Background stack in one column; Plan gets its own column (per the
  // claude.ai/code layout).
  const stacked = panels.diff || panels.background;
  return (
    <div className="flex shrink-0">
      {stacked && (
        <div className="flex w-[380px] flex-col">
          {panels.diff && (
            <PanelShell
              header={
                <>
                  <Folder className="size-3.5 shrink-0 text-muted-foreground" />
                  <span className="text-foreground/70">{baseBranch}</span>
                  <ArrowRight className="size-3 shrink-0 text-muted-foreground/60" />
                  <span className="truncate">{workBranch}</span>
                </>
              }
              onClose={() => onClose("diff")}
            >
              <Empty icon={null} lines={["No changes to show"]} />
            </PanelShell>
          )}
          {panels.background && (
            <PanelShell header={<span>Background tasks</span>} onClose={() => onClose("background")}>
              <Empty icon={<GitCompare className="size-5" />} lines={["Background work appears here"]} />
            </PanelShell>
          )}
        </div>
      )}
      {panels.plan && (
        <div className="flex w-[280px] flex-col">
          <PanelShell header={<span>Plan</span>} onClose={() => onClose("plan")}>
            <Empty
              icon={<ListChecks className="size-5" />}
              lines={["No plan yet", "Jarvis writes the plan here as it explores. Keep chatting."]}
            />
          </PanelShell>
        </div>
      )}
    </div>
  );
}
