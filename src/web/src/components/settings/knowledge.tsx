"use client";

import { BookOpen, Upload, Trash2 } from "lucide-react";

/**
 * Personal-scoped knowledge base — files / docs the AI can reference
 * across ALL chats (not workspace-scoped). Workspace-scoped knowledge
 * lives in the workbench Settings tab → Knowledge.
 *
 * Stub for now. The infrastructure to wire this up is documented at
 * the bottom so the next implementation pass has a clear punch list.
 */
export function KnowledgeSection() {
  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center gap-2">
          <BookOpen className="size-4 text-muted-foreground" />
          <h2 className="text-lg font-semibold">Knowledge</h2>
          <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-amber-500">
            Coming soon
          </span>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Documents JARVIS can reference in every chat — your CV, brand
          guidelines, recurring project specs, anything you&apos;d want the
          model to remember without re-explaining it each turn.
        </p>
      </div>

      {/* Hero upload zone (disabled) */}
      <div className="flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed border-border/60 bg-card/30 px-8 py-12 text-center">
        <div className="flex size-12 items-center justify-center rounded-full bg-muted">
          <Upload className="size-5 text-muted-foreground" />
        </div>
        <div>
          <div className="text-sm font-medium">Drop files to add to your knowledge</div>
          <div className="mt-1 text-xs text-muted-foreground">
            PDF, markdown, or text · 25MB per file
          </div>
        </div>
        <button
          type="button"
          disabled
          className="mt-1 cursor-not-allowed rounded-md border border-border/60 bg-card/40 px-4 py-1.5 text-[12px] text-muted-foreground/70"
        >
          Choose files
        </button>
      </div>

      {/* Empty list shape so the layout is intuitive even before files exist */}
      <div className="rounded-lg border border-border/50 bg-card/20 p-6 text-center text-[13px] text-muted-foreground">
        No documents yet.
      </div>

      {/* What this will do once wired up */}
      <div className="rounded-lg border border-border/50 bg-card/30 p-4">
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          What this section will do
        </h3>
        <ul className="space-y-1.5 text-[13px] text-foreground/85">
          <li className="flex items-start gap-2">
            <span className="mt-1.5 size-1 shrink-0 rounded-full bg-primary/60" />
            <span>Drag-drop PDFs, markdown, or text — chunked + embedded automatically.</span>
          </li>
          <li className="flex items-start gap-2">
            <span className="mt-1.5 size-1 shrink-0 rounded-full bg-primary/60" />
            <span>Top-K retrieval per chat turn — relevant chunks injected into the system prompt.</span>
          </li>
          <li className="flex items-start gap-2">
            <span className="mt-1.5 size-1 shrink-0 rounded-full bg-primary/60" />
            <span>Per-document toggle — disable a doc without deleting it.</span>
          </li>
          <li className="flex items-start gap-2">
            <span className="mt-1.5 size-1 shrink-0 rounded-full bg-primary/60" />
            <span>&ldquo;Forget this&rdquo; — single click delete, embeddings purged.</span>
          </li>
        </ul>
      </div>

      <div className="rounded-lg border border-border/40 bg-card/20 p-4">
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          What&apos;s needed to wire it up
        </h3>
        <ul className="space-y-1 text-[12px] text-muted-foreground">
          <li className="font-mono">· Embeddings provider config (Voyage AI / OpenAI / local)</li>
          <li className="font-mono">· Vector store (sqlite-vss for local, pgvector for hosted)</li>
          <li className="font-mono">· DB schema: knowledge_docs(id, name, mime, bytes, status), knowledge_chunks(doc_id, content, embedding[])</li>
          <li className="font-mono">· Upload + chunk worker (POST /api/knowledge/upload)</li>
          <li className="font-mono">· Retrieval middleware in chat route (top-K → system prompt addendum)</li>
        </ul>
      </div>

      {/* Faux row preview so the user can SEE what the populated state looks like */}
      <div>
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Preview — what populated state looks like
        </h3>
        <div className="space-y-1.5 opacity-60">
          {[
            { name: "ulrich-cv.pdf", size: "412 KB", chunks: 24 },
            { name: "brand-guidelines.md", size: "18 KB", chunks: 6 },
            { name: "ohada-legal-summary.pdf", size: "1.2 MB", chunks: 84 },
          ].map((d) => (
            <div
              key={d.name}
              className="flex items-center justify-between rounded-md border border-border/40 px-3 py-2 text-[12px]"
            >
              <span className="font-mono">{d.name}</span>
              <span className="text-muted-foreground">
                {d.size} · {d.chunks} chunks
              </span>
              <button
                type="button"
                disabled
                className="cursor-not-allowed text-muted-foreground/50"
                aria-label="Remove"
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
