"use client";

import { motion, AnimatePresence } from "motion/react";
import { PanelRightClose, Eye } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useUI } from "@/stores/ui";

export function PreviewPanel() {
  const { previewOpen, togglePreview } = useUI();

  return (
    <AnimatePresence initial={false}>
      {previewOpen && (
        <motion.aside
          initial={{ width: 0, opacity: 0 }}
          animate={{ width: "28rem", opacity: 1 }}
          exit={{ width: 0, opacity: 0 }}
          transition={{ duration: 0.2, ease: "easeOut" }}
          className="shrink-0 overflow-hidden border-l border-border/60 bg-sidebar/50"
        >
          <div className="flex h-full w-[28rem] flex-col">
            <div className="flex h-12 shrink-0 items-center justify-between border-b border-border/60 px-3">
              <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
                <Eye className="size-3.5 text-primary" />
                preview
              </div>
              <Button
                variant="ghost"
                size="icon"
                onClick={togglePreview}
                aria-label="Close preview"
                className="size-7"
              >
                <PanelRightClose className="size-3.5" />
              </Button>
            </div>
            <div className="flex flex-1 flex-col items-center justify-center px-6 text-center">
              <div className="flex size-12 items-center justify-center rounded-lg border border-border/60 bg-card/60">
                <Eye className="size-5 text-muted-foreground" />
              </div>
              <p className="mt-4 text-sm text-foreground/80">Nothing to preview yet.</p>
              <p className="mt-1 max-w-xs text-xs leading-5 text-muted-foreground">
                Artifacts, rendered markdown, code execution, and design mockups
                will appear here while Jarvis works.
              </p>
            </div>
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}

export function PreviewToggle() {
  const { previewOpen, togglePreview } = useUI();
  if (previewOpen) return null;
  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={togglePreview}
      aria-label="Open preview"
      className="size-8"
    >
      <Eye className="size-4" />
    </Button>
  );
}
