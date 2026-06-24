"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import {
  Braces,
  ChevronDown,
  Code2,
  FileText,
  Globe,
  History,
  Image as ImageIcon,
  Loader2,
  MoreHorizontal,
  Package,
  Pencil,
  Search,
  Share2,
  Table2,
  Trash2,
  Workflow,
} from "lucide-react";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { formatLongRelativeTime } from "@/components/projects/relative-time";
import {
  useAllArtifacts,
  useBackfillArtifacts,
  useDeleteArtifact,
  useRenameArtifact,
  type ArtifactSummary,
  type ArtifactKind,
} from "@/hooks/use-artifacts";
import { NewArtifactDialog } from "./new-artifact-dialog";
import { ArtifactRender } from "./artifact-render";

type SortKey = "activity" | "name" | "created";
const SORT_LABEL: Record<SortKey, string> = {
  activity: "Activity",
  name: "Name",
  created: "Date created",
};

const KIND_ICON: Record<ArtifactKind, typeof Code2> = {
  react: Code2,
  code: Code2,
  html: Globe,
  svg: ImageIcon,
  mermaid: Workflow,
  markdown: FileText,
  csv: Table2,
  json: Braces,
};

export function ArtifactsView() {
  const { data: artifacts = [], isLoading } = useAllArtifacts();
  const backfill = useBackfillArtifacts();
  const runImport = () => {
    if (backfill.isPending) return;
    backfill.mutate(undefined, {
      onSuccess: (r) =>
        toast.success(
          r.artifacts > 0
            ? `Imported — ${r.artifacts} artifact${r.artifacts === 1 ? "" : "s"} from ${r.scanned} messages.`
            : `Scanned ${r.scanned} messages — no artifacts found yet.`,
        ),
      onError: () => toast.error("Import failed. Check the server logs."),
    });
  };
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortKey>("activity");
  const [sortOpen, setSortOpen] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [newOpen, setNewOpen] = useState(false);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q
      ? artifacts.filter((a) => a.title.toLowerCase().includes(q))
      : [...artifacts];
    list.sort((a, b) => {
      if (sort === "name") return a.title.localeCompare(b.title);
      if (sort === "created")
        return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime();
      return new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime();
    });
    return list;
  }, [artifacts, query, sort]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-4xl px-6 pt-10 pb-12">
          <div className="flex items-center justify-between">
            <h1 className="font-serif text-[34px] font-semibold leading-none tracking-tight">
              Artifacts
            </h1>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                className="rounded-md text-muted-foreground"
                onClick={runImport}
                disabled={backfill.isPending}
                title="Scan your chat history for artifacts"
              >
                {backfill.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <History className="size-3.5" />
                )}
                Import from chats
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="rounded-md"
                onClick={() => setNewOpen(true)}
              >
                <Package className="size-3.5" />
                New artifact
              </Button>
            </div>
          </div>
          <p className="mt-2 text-[13px] text-muted-foreground">
            Self-contained things you’ve built in chat — React components, pages,
            diagrams, docs. Open one to preview, edit, or share it.
          </p>

          <div className="relative mt-6">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground/70" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search artifacts..."
              className="h-10 rounded-lg pl-9"
            />
          </div>

          <div className="mt-3 flex items-center justify-end gap-2 text-[13px]">
            <span className="text-muted-foreground">Sort by</span>
            <div className="relative">
              <Button
                variant="outline"
                size="sm"
                className="rounded-md"
                onClick={() => setSortOpen((v) => !v)}
                onBlur={() => setTimeout(() => setSortOpen(false), 120)}
              >
                {SORT_LABEL[sort]}
                <ChevronDown className="size-3.5" />
              </Button>
              {sortOpen && (
                <div className="absolute right-0 top-full z-20 mt-1 min-w-36 rounded-md border border-border/60 bg-popover p-1 text-popover-foreground shadow-md">
                  {(Object.keys(SORT_LABEL) as SortKey[]).map((k) => (
                    <button
                      key={k}
                      type="button"
                      onMouseDown={(e) => {
                        e.preventDefault();
                        setSort(k);
                        setSortOpen(false);
                      }}
                      className={cn(
                        "flex w-full items-center rounded-sm px-2 py-1.5 text-left text-[13px] transition-colors hover:bg-muted",
                        sort === k && "text-primary",
                      )}
                    >
                      {SORT_LABEL[k]}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="mt-6">
            {isLoading && artifacts.length === 0 ? (
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                {[0, 1, 2, 3].map((i) => (
                  <div
                    key={i}
                    className="h-28 animate-pulse rounded-xl border border-border/60 bg-card/40"
                  />
                ))}
              </div>
            ) : filtered.length === 0 ? (
              <EmptyState
                hasQuery={query.trim().length > 0}
                onNew={() => setNewOpen(true)}
              />
            ) : (
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                {filtered.map((a) => (
                  <ArtifactCard
                    key={a.id}
                    artifact={a}
                    renaming={renamingId === a.id}
                    onStartRename={() => setRenamingId(a.id)}
                    onEndRename={() => setRenamingId(null)}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
      <NewArtifactDialog open={newOpen} onOpenChange={setNewOpen} />
    </div>
  );
}

function ArtifactCard({
  artifact,
  renaming,
  onStartRename,
  onEndRename,
}: {
  artifact: ArtifactSummary;
  renaming: boolean;
  onStartRename: () => void;
  onEndRename: () => void;
}) {
  const rename = useRenameArtifact();
  const del = useDeleteArtifact();
  const [draft, setDraft] = useState(artifact.title);
  const Icon = KIND_ICON[artifact.kind] ?? Package;
  // Token presence = published. Expiry is enforced server-side
  // (getArtifactByShareToken returns null past expiry), so the badge
  // doesn't need a render-time clock read.
  const published = !!artifact.shareToken;

  const commit = () => {
    const title = draft.trim();
    if (title && title !== artifact.title) rename.mutate({ id: artifact.id, title });
    onEndRename();
  };

  return (
    <div className="group relative flex h-52 flex-col overflow-hidden rounded-xl border border-border/60 bg-card/40 transition-colors hover:border-border hover:bg-card">
      {/* Full-card click target (on top, so the whole card opens the detail
          view). Interactive bits below sit above it via z-20. */}
      <Link
        href={`/artifacts/${artifact.id}`}
        className="absolute inset-0 z-10"
        aria-label={artifact.title}
      />
      {/* Live preview thumbnail */}
      <div className="pointer-events-none relative h-32 shrink-0 overflow-hidden border-b border-border/50 bg-white">
        <ArtifactThumbnail
          kind={artifact.kind}
          content={artifact.latestContent}
          language={artifact.latestLanguage}
        />
      </div>
      {/* Info */}
      <div className="flex flex-1 flex-col px-4 py-3">
        <div className="flex items-start gap-2">
          <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          {renaming ? (
            <input
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commit}
              onKeyDown={(e) => {
                if (e.key === "Enter") commit();
                if (e.key === "Escape") onEndRename();
              }}
              className="relative z-20 min-w-0 flex-1 rounded border border-border bg-background px-1.5 py-0.5 text-[14px] font-semibold outline-none focus:border-primary"
            />
          ) : (
            <h3 className="min-w-0 flex-1 truncate text-[14px] font-semibold tracking-tight text-foreground">
              {artifact.title}
            </h3>
          )}
          <div className="relative z-20 opacity-0 transition-opacity group-hover:opacity-100">
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <button
                    className="rounded p-1 hover:bg-accent"
                    aria-label="Artifact actions"
                  >
                    <MoreHorizontal className="size-4" />
                  </button>
                }
              />
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  onClick={() => {
                    setDraft(artifact.title);
                    onStartRename();
                  }}
                >
                  <Pencil className="size-3.5" />
                  Rename
                </DropdownMenuItem>
                <DropdownMenuItem
                  variant="destructive"
                  onClick={() => {
                    if (
                      confirm(`Delete “${artifact.title}”? This can’t be undone.`)
                    )
                      del.mutate(artifact.id);
                  }}
                >
                  <Trash2 className="size-3.5" />
                  Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
        <div className="mt-auto flex items-center gap-2 pt-2 text-[11.5px] text-muted-foreground/70">
          <span className="uppercase tracking-wide">{artifact.kind}</span>
          <span>· {formatLongRelativeTime(artifact.updatedAt)}</span>
          {published && (
            <span className="ml-auto inline-flex items-center gap-1 text-emerald-500">
              <Share2 className="size-3" />
              Shared
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

// Live, non-interactive preview for a gallery card. Visual kinds render in
// the sandboxed iframe; text kinds show an excerpt (cheaper + readable).
// ponytail: one iframe per visual card — fine at personal scale; lazify with
// an IntersectionObserver if galleries ever grow large.
function ArtifactThumbnail({
  kind,
  content,
  language,
}: {
  kind: ArtifactKind;
  content: string;
  language: string | null;
}) {
  if (!content) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground/40">
        <Package className="size-6" />
      </div>
    );
  }
  if (kind === "markdown") {
    return (
      <div className="h-full overflow-hidden whitespace-pre-wrap bg-card/40 p-3 text-[10px] leading-relaxed text-muted-foreground">
        {content.slice(0, 320)}
      </div>
    );
  }
  if (kind === "code") {
    return (
      <pre className="h-full overflow-hidden bg-card/40 p-3 font-mono text-[9px] leading-snug text-muted-foreground">
        {content.split("\n").slice(0, 16).join("\n")}
      </pre>
    );
  }
  return (
    <ArtifactRender
      kind={kind}
      content={content}
      language={language}
      mode="preview"
    />
  );
}

function EmptyState({
  hasQuery,
  onNew,
}: {
  hasQuery: boolean;
  onNew: () => void;
}) {
  if (hasQuery) {
    return (
      <div className="rounded-xl border border-dashed border-border/60 px-6 py-14 text-center">
        <p className="text-sm text-muted-foreground">
          No artifacts match your search.
        </p>
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border/60 px-6 py-16 text-center">
      <Package className="size-7 text-muted-foreground/60" />
      <p className="mt-3 text-sm text-muted-foreground">
        No artifacts yet. Ask in chat to build a component, page, diagram, or
        doc — it’ll show up here.
      </p>
      <Button
        size="sm"
        variant="outline"
        className="mt-4 rounded-md"
        onClick={onNew}
      >
        New artifact
      </Button>
    </div>
  );
}
