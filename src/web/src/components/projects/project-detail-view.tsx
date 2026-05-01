"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import {
  ArrowLeft,
  AudioLines,
  Lock,
  MoreHorizontal,
  Plus,
  Star,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useChatStore } from "@/stores/chat";
import { MODELS_META } from "@/lib/ai/models-meta";
import { cn } from "@/lib/utils";
import {
  useProject,
  useProjectConversations,
  useUpdateProject,
} from "@/hooks/use-projects";
import { formatLongRelativeTime } from "./relative-time";

export function ProjectDetailView({ projectId }: { projectId: string }) {
  const router = useRouter();
  const qc = useQueryClient();
  const { data: project, isLoading } = useProject(projectId);
  const { data: conversations = [], isLoading: convLoading } =
    useProjectConversations(projectId);
  const update = useUpdateProject(projectId);

  const modelId = useChatStore((s) => s.model);
  const modelMeta = MODELS_META[modelId];
  const modelLabel = modelMeta?.label
    ? modelMeta.label.replace(/^Claude\s+/, "")
    : "Opus 4.7";

  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // 404 → bounce to list
  useEffect(() => {
    if (!isLoading && !project) router.replace("/projects");
  }, [isLoading, project, router]);

  if (!project) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        loading…
      </div>
    );
  }

  const hasChats = conversations.length > 0;
  const placeholder = hasChats
    ? "How can I help you today?"
    : "Type / for skills";

  const submitDraft = async () => {
    const text = draft.trim();
    if (!text || submitting) return;
    setSubmitting(true);
    try {
      const res = await fetch(
        `/api/projects/${projectId}/conversations`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title: text.slice(0, 80),
            model: modelId,
          }),
        },
      );
      if (!res.ok) throw new Error(await res.text());
      const { conversation } = (await res.json()) as {
        conversation: { id: string };
      };
      // Invalidate so when the user comes back to this project the new
      // chat is in the list (and the global recents list too).
      qc.invalidateQueries({ queryKey: ["project-conversations", projectId] });
      qc.invalidateQueries({ queryKey: ["conversations"] });
      const params = new URLSearchParams({ seed: text });
      router.push(`/chat/${conversation.id}?${params.toString()}`);
    } catch {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-5xl px-6 pt-6 pb-12">
          {/* Back link */}
          <Link
            href="/projects"
            className="inline-flex items-center gap-1.5 text-[13px] text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft className="size-3.5" />
            All projects
          </Link>

          <div className="mt-4 grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
            {/* ── LEFT: header + composer + chats ─────────────── */}
            <div className="min-w-0">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h1 className="font-serif text-3xl font-semibold tracking-tight leading-tight">
                    {project.name}
                  </h1>
                  {project.description && (
                    <p className="mt-1 text-[13px] text-muted-foreground">
                      {project.description}
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-0.5">
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="More"
                    title="More"
                  >
                    <MoreHorizontal className="size-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="Favorite"
                    title="Favorite"
                    onClick={() =>
                      update.mutate({ isFavorite: !project.isFavorite })
                    }
                  >
                    <Star
                      className={cn(
                        "size-4",
                        project.isFavorite && "fill-foreground text-foreground",
                      )}
                    />
                  </Button>
                </div>
              </div>

              {/* Composer card */}
              <div className="mt-5 rounded-xl border border-border/60 bg-card/40 px-4 py-3">
                <textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      submitDraft();
                    }
                  }}
                  placeholder={placeholder}
                  rows={1}
                  className="block w-full resize-none bg-transparent text-[14px] leading-6 text-foreground placeholder:text-muted-foreground/70 outline-none"
                />
                <div className="mt-2 flex items-center justify-between">
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="Attach"
                    title="Attach"
                  >
                    <Plus className="size-4" />
                  </Button>
                  <div className="flex items-center gap-2 text-[12px] text-muted-foreground">
                    <span className="text-foreground/85">{modelLabel}</span>
                    <span className="text-muted-foreground/60">Adaptive</span>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label="Voice"
                      title="Voice"
                    >
                      <AudioLines className="size-4" />
                    </Button>
                  </div>
                </div>
              </div>

              {/* Chats / empty state */}
              <div className="mt-4">
                {convLoading ? (
                  <div className="h-14 rounded-xl border border-border/60 bg-card/40 animate-pulse" />
                ) : hasChats ? (
                  <ul className="space-y-1">
                    {conversations.map((c) => (
                      <li key={c.id}>
                        <Link
                          href={`/chat/${c.id}`}
                          className="group block rounded-md border border-transparent px-3 py-2 transition-colors hover:border-border/70 hover:bg-card/60"
                        >
                          <div className="truncate text-[14px] text-foreground/90">
                            {c.title || "Untitled"}
                          </div>
                          <div className="mt-0.5 text-[11.5px] text-muted-foreground">
                            Last message {formatLongRelativeTime(c.updatedAt)}
                          </div>
                        </Link>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="flex items-center justify-center rounded-xl border border-dashed border-border/60 px-6 py-8 text-center text-[13px] leading-6 text-muted-foreground">
                    Start a chat to keep conversations
                    <br />
                    organized and re-use project knowledge.
                  </div>
                )}
              </div>
            </div>

            {/* ── RIGHT: side panel ───────────────────────────── */}
            <aside className="space-y-px lg:sticky lg:top-6 lg:self-start">
              <div className="rounded-xl border border-border/60 bg-card/40 px-4 py-3">
                <PanelMemory
                  hasChats={hasChats}
                  description={project.description}
                  updatedAt={project.updatedAt}
                />
                <Divider />
                <PanelInstructions
                  value={project.instructions}
                  onSave={(v) => update.mutate({ instructions: v })}
                />
                <Divider />
                <PanelFiles />
              </div>
            </aside>
          </div>
        </div>
      </div>
    </div>
  );
}

function Divider() {
  return <div className="my-3 h-px bg-border/60" />;
}

function PanelMemory({
  hasChats,
  description,
  updatedAt,
}: {
  hasChats: boolean;
  description: string;
  updatedAt: string;
}) {
  return (
    <div>
      <div className="flex items-center justify-between">
        <h3 className="text-[14px] font-semibold tracking-tight">Memory</h3>
        <div className="flex items-center gap-1.5">
          <span className="inline-flex items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 text-[10.5px] text-muted-foreground">
            <Lock className="size-2.5" />
            Only you
          </span>
          <button
            type="button"
            aria-label="Memory settings"
            className="text-muted-foreground/70 transition-colors hover:text-foreground"
          >
            <MoreHorizontal className="size-3.5 -rotate-90" />
          </button>
        </div>
      </div>
      {hasChats && description ? (
        <>
          <p className="mt-2 line-clamp-3 text-[12.5px] leading-5 text-muted-foreground">
            Purpose & context — {description}
          </p>
          <p className="mt-2 text-[11.5px] text-muted-foreground/70">
            Last updated {formatLongRelativeTime(updatedAt)}
          </p>
        </>
      ) : (
        <p className="mt-2 text-[12.5px] text-muted-foreground">
          Project memory will show here after a few chats.
        </p>
      )}
    </div>
  );
}

function PanelInstructions({
  value,
  onSave,
}: {
  value: string;
  onSave: (v: string) => void;
}) {
  const [editing, setEditing] = useState(false);

  if (editing) {
    return (
      <InstructionsEditor
        // Remount-keyed on `value` so external updates reset the draft
        // without a setState-in-effect anti-pattern.
        key={value}
        initial={value}
        onCancel={() => setEditing(false)}
        onSave={(v) => {
          onSave(v);
          setEditing(false);
        }}
      />
    );
  }

  return (
    <button
      type="button"
      onClick={() => setEditing(true)}
      className="block w-full text-left"
    >
      <div className="flex items-center justify-between">
        <h3 className="text-[14px] font-semibold tracking-tight">
          Instructions
        </h3>
        <span
          aria-label="Add instructions"
          className="text-muted-foreground/70 transition-colors group-hover:text-foreground"
        >
          <Plus className="size-3.5" />
        </span>
      </div>
      <p className="mt-1.5 line-clamp-2 text-[12.5px] leading-5 text-muted-foreground">
        {value
          ? value
          : "Add instructions to tailor Jarvis's responses"}
      </p>
    </button>
  );
}

function InstructionsEditor({
  initial,
  onCancel,
  onSave,
}: {
  initial: string;
  onCancel: () => void;
  onSave: (v: string) => void;
}) {
  const [draft, setDraft] = useState(initial);
  return (
    <div>
      <h3 className="text-[14px] font-semibold tracking-tight">Instructions</h3>
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        rows={4}
        className="mt-2 w-full resize-none rounded-md border border-border/60 bg-background px-2.5 py-2 text-[13px] outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
        placeholder="Tailor how Jarvis responds inside this project."
        autoFocus
      />
      <div className="mt-2 flex justify-end gap-1.5">
        <Button variant="ghost" size="xs" onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="outline" size="xs" onClick={() => onSave(draft)}>
          Save
        </Button>
      </div>
    </div>
  );
}

function PanelFiles() {
  return (
    <div>
      <div className="flex items-center justify-between">
        <h3 className="text-[14px] font-semibold tracking-tight">Files</h3>
        <span
          aria-label="Add file"
          className="text-muted-foreground/70 transition-colors hover:text-foreground"
        >
          <Plus className="size-3.5" />
        </span>
      </div>
      <div className="mt-3 flex flex-col items-center justify-center rounded-lg border border-dashed border-border/50 bg-background/40 px-4 py-6 text-center">
        <FilesGlyph />
        <p className="mt-3 text-[12px] leading-5 text-muted-foreground">
          Add PDFs, documents, or other text to
          <br />
          reference in this project.
        </p>
      </div>
    </div>
  );
}

function FilesGlyph() {
  return (
    <div className="relative h-12 w-16">
      <div className="absolute left-0 top-1 h-10 w-7 rounded-md border border-border/60 bg-muted/40" />
      <div className="absolute left-7 top-3 h-9 w-7 rounded-md border border-border/60 bg-muted/30" />
      <div className="absolute left-3 top-0 h-11 w-8 rounded-md border border-border/70 bg-muted/60 shadow-sm" />
      <Plus className="absolute left-1/2 top-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 text-muted-foreground/80" />
    </div>
  );
}
