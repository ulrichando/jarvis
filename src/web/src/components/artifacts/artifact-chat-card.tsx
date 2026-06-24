"use client";

import {
  Braces,
  ChevronRight,
  Code2,
  FileText,
  Globe,
  Image as ImageIcon,
  Package,
  Table2,
  Workflow,
} from "lucide-react";
import type { ArtifactKind } from "@/lib/actions/types";

const ICON: Record<ArtifactKind, typeof Code2> = {
  react: Code2,
  code: Code2,
  html: Globe,
  svg: ImageIcon,
  mermaid: Workflow,
  markdown: FileText,
  csv: Table2,
  json: Braces,
};

const LABEL: Record<ArtifactKind, string> = {
  react: "Interactive artifact",
  code: "Code",
  html: "Interactive artifact",
  svg: "SVG image",
  mermaid: "Diagram",
  markdown: "Document",
  csv: "Data table",
  json: "JSON",
};

// In-conversation card (claude.ai style): click to open/focus the artifact in
// the side panel. Rendered under the assistant message that produced it.
export function ArtifactChatCard({
  title,
  kind,
  onOpen,
}: {
  title: string;
  kind: ArtifactKind;
  onOpen: () => void;
}) {
  const Icon = ICON[kind] ?? Package;
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group mt-3 flex w-full max-w-md items-center gap-3 rounded-xl border border-border/60 bg-card/40 px-4 py-3 text-left transition-colors hover:border-primary/40 hover:bg-card"
    >
      <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border/50 bg-background/60">
        <Icon className="size-4 text-muted-foreground" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13.5px] font-medium text-foreground">
          {title}
        </div>
        <div className="text-[11px] text-muted-foreground">
          {LABEL[kind] ?? "Artifact"} · click to open
        </div>
      </div>
      <ChevronRight className="size-4 shrink-0 text-muted-foreground/60 transition-transform group-hover:translate-x-0.5" />
    </button>
  );
}
