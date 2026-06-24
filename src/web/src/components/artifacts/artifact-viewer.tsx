"use client";

import { useEffect, useRef, useState } from "react";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Code2,
  Copy,
  Download,
  ExternalLink,
  Eye,
  Link2Off,
  Loader2,
  Share2,
  X,
} from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import type { ArtifactKind } from "@/lib/actions/types";
import {
  usePublishArtifact,
  useUnpublishArtifact,
} from "@/hooks/use-artifacts";
import { ArtifactRender } from "./artifact-render";

const KIND_LABEL: Record<ArtifactKind, string> = {
  react: "React",
  html: "HTML",
  svg: "SVG",
  mermaid: "Diagram",
  markdown: "Document",
  code: "Code",
  csv: "Table",
  json: "JSON",
};

const KIND_EXT: Record<ArtifactKind, string> = {
  react: "tsx",
  html: "html",
  svg: "svg",
  mermaid: "mmd",
  markdown: "md",
  code: "txt",
  csv: "csv",
  json: "json",
};

const LANG_EXT: Record<string, string> = {
  typescript: "ts",
  ts: "ts",
  tsx: "tsx",
  javascript: "js",
  js: "js",
  jsx: "jsx",
  python: "py",
  py: "py",
  json: "json",
  css: "css",
  html: "html",
  bash: "sh",
  sh: "sh",
  go: "go",
  rust: "rs",
  java: "java",
  c: "c",
  cpp: "cpp",
};

function downloadFile(filename: string, content: string) {
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export type ArtifactViewerProps = {
  title: string;
  kind: ArtifactKind;
  language?: string | null;
  /** Content per version, v1 = index 0. At least one entry. */
  versions: string[];
  /** DB id — enables Open-in-new-tab + Publish. Absent while a fresh
   *  artifact is still streaming (not yet persisted). */
  artifactId?: string;
  shareToken?: string | null;
  /** Optional header-left slot (the panel's artifact switcher). */
  headerLeft?: React.ReactNode;
  /** When set, shows a close (X) button — the in-chat panel. */
  onClose?: () => void;
};

export function ArtifactViewer({
  title,
  kind,
  language,
  versions,
  artifactId,
  shareToken,
  headerLeft,
  onClose,
}: ArtifactViewerProps) {
  const hasPreview = kind !== "code";
  const [mode, setMode] = useState<"preview" | "code">(
    hasPreview ? "preview" : "code",
  );
  const [vIdx, setVIdx] = useState(versions.length - 1);
  const [copied, setCopied] = useState(false);

  // Jump to the newest version whenever a new one streams/arrives.
  const prevLen = useRef(versions.length);
  useEffect(() => {
    if (versions.length !== prevLen.current) {
      setVIdx(versions.length - 1);
      prevLen.current = versions.length;
    }
  }, [versions.length]);

  const safeIdx = Math.min(vIdx, versions.length - 1);
  const content = versions[safeIdx] ?? "";

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard blocked — no-op */
    }
  };

  const download = () => {
    const ext =
      (language && LANG_EXT[language.toLowerCase()]) || KIND_EXT[kind];
    const base = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "artifact";
    downloadFile(`${base}.${ext}`, content);
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-card/30">
      {/* Header */}
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border/60 px-3">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {headerLeft ?? (
            <span className="truncate text-[13px] font-medium">{title}</span>
          )}
          <span className="shrink-0 rounded border border-border/60 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
            {KIND_LABEL[kind]}
          </span>
        </div>

        {versions.length > 1 && (
          <div className="flex shrink-0 items-center gap-0.5 text-muted-foreground">
            <button
              onClick={() => setVIdx((i) => Math.max(0, i - 1))}
              disabled={safeIdx === 0}
              className="rounded p-1 hover:bg-accent disabled:opacity-30"
              title="Previous version"
            >
              <ChevronLeft className="size-3.5" />
            </button>
            <span className="tabular-nums text-[11px]">
              v{safeIdx + 1}
              <span className="text-muted-foreground/60">/{versions.length}</span>
            </span>
            <button
              onClick={() => setVIdx((i) => Math.min(versions.length - 1, i + 1))}
              disabled={safeIdx === versions.length - 1}
              className="rounded p-1 hover:bg-accent disabled:opacity-30"
              title="Next version"
            >
              <ChevronRight className="size-3.5" />
            </button>
          </div>
        )}

        {hasPreview && (
          <div className="flex shrink-0 items-center rounded-md border border-border/60 p-0.5">
            <ToggleBtn
              active={mode === "preview"}
              onClick={() => setMode("preview")}
              icon={<Eye className="size-3.5" />}
              label="Preview"
            />
            <ToggleBtn
              active={mode === "code"}
              onClick={() => setMode("code")}
              icon={<Code2 className="size-3.5" />}
              label="Code"
            />
          </div>
        )}

        {onClose && (
          <button
            onClick={onClose}
            className="shrink-0 rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
            title="Close"
          >
            <X className="size-4" />
          </button>
        )}
      </div>

      {/* Body */}
      <div className="min-h-0 flex-1 overflow-hidden">
        <ArtifactRender
          key={`${artifactId ?? title}-${safeIdx}-${mode}`}
          kind={kind}
          content={content}
          language={language}
          mode={mode}
        />
      </div>

      {/* Footer controls */}
      <div className="flex h-10 shrink-0 items-center justify-end gap-1 border-t border-border/60 px-3 text-muted-foreground">
        <FooterBtn onClick={copy} title="Copy">
          {copied ? (
            <Check className="size-3.5 text-emerald-500" />
          ) : (
            <Copy className="size-3.5" />
          )}
        </FooterBtn>
        <FooterBtn onClick={download} title="Download">
          <Download className="size-3.5" />
        </FooterBtn>
        {artifactId && (
          <FooterBtn
            onClick={() => window.open(`/artifacts/${artifactId}`, "_blank")}
            title="Open in new tab"
          >
            <ExternalLink className="size-3.5" />
          </FooterBtn>
        )}
        {artifactId && (
          <ShareControl artifactId={artifactId} shareToken={shareToken} />
        )}
      </div>
    </div>
  );
}

function ToggleBtn({
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
        "flex items-center gap-1 rounded px-2 py-0.5 text-[12px] font-medium transition-colors",
        active
          ? "bg-accent text-foreground"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {icon}
      {label}
    </button>
  );
}

function FooterBtn({
  onClick,
  title,
  children,
}: {
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] hover:bg-accent hover:text-foreground transition-colors"
    >
      {children}
    </button>
  );
}

function ShareControl({
  artifactId,
  shareToken,
}: {
  artifactId: string;
  shareToken?: string | null;
}) {
  const publish = usePublishArtifact();
  const unpublish = useUnpublishArtifact();
  const [linkCopied, setLinkCopied] = useState(false);
  const published = Boolean(shareToken);
  const url =
    typeof window !== "undefined" && shareToken
      ? `${window.location.origin}/a/${shareToken}`
      : "";

  const copyLink = async () => {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setLinkCopied(true);
      setTimeout(() => setLinkCopied(false), 1400);
    } catch {
      /* no-op */
    }
  };

  if (!published) {
    return (
      <FooterBtn
        onClick={() => publish.mutate(artifactId)}
        title="Publish a public link"
      >
        {publish.isPending ? (
          <Loader2 className="size-3.5 animate-spin" />
        ) : (
          <Share2 className="size-3.5" />
        )}
        Share
      </FooterBtn>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <button
            title="Sharing options"
            className="flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] text-emerald-500 hover:bg-accent transition-colors"
          >
            <Share2 className="size-3.5" />
            Shared
          </button>
        }
      />
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuItem onClick={copyLink}>
          {linkCopied ? (
            <Check className="size-3.5 text-emerald-500" />
          ) : (
            <Copy className="size-3.5" />
          )}
          {linkCopied ? "Link copied" : "Copy public link"}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => window.open(url, "_blank")}>
          <ExternalLink className="size-3.5" />
          Open public link
        </DropdownMenuItem>
        <DropdownMenuItem
          onClick={() => unpublish.mutate(artifactId)}
          variant="destructive"
        >
          <Link2Off className="size-3.5" />
          Unpublish
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
