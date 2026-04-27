"use client";

import { useState } from "react";
import Link from "next/link";
import { Square, Search, Sparkles } from "lucide-react";
import { CodeSidebar } from "@/components/code/code-sidebar";
import { CodeComposer } from "@/components/code/code-composer";

export default function CodePage() {
  const [input, setInput] = useState("");
  const [machineModalOpen, setMachineModalOpen] = useState(false);

  return (
    <div className="flex h-screen flex-col bg-background text-foreground overflow-hidden">
      <header className="flex h-11 shrink-0 items-center justify-between border-b border-border/50 px-4">
        <Link href="/chat" className="font-bold text-[14px] text-foreground">
          Jarvis CLI
        </Link>
        <span className="rounded-full border border-border/60 px-2.5 py-0.5 text-[11px] text-foreground/60">
          Research preview
        </span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            aria-label="New window"
            className="flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent/50 hover:text-foreground transition-colors"
          >
            <Square className="size-4" />
          </button>
          <button
            type="button"
            aria-label="Search"
            className="flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent/50 hover:text-foreground transition-colors"
          >
            <Search className="size-4" />
          </button>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <CodeSidebar onNewSession={() => setInput("")} />

        <main className="flex flex-1 flex-col overflow-hidden">
          <div className="px-8 pt-8">
            <div className="flex items-center gap-2 text-[22px] font-serif font-semibold text-foreground/90">
              <Sparkles className="size-5 text-primary" />
              <span>What&apos;s up next, Ulrich?</span>
            </div>
          </div>
          <div className="flex-1" />
          <div className="px-6 pb-6">
            <CodeComposer
              value={input}
              onChange={setInput}
              onSubmit={() => {}}
              onSelectMachine={() => setMachineModalOpen(true)}
            />
          </div>
        </main>
      </div>

      {machineModalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setMachineModalOpen(false)}
        >
          <div
            className="rounded-2xl border border-border bg-card p-6 text-[13px] text-muted-foreground shadow-lg"
            onClick={(e) => e.stopPropagation()}
          >
            Machine selection coming soon.
          </div>
        </div>
      )}
    </div>
  );
}
