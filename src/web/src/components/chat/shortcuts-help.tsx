"use client";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

const isMac = () =>
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

type Row = { keys: string[]; label: string };

/**
 * Keyboard-shortcuts cheatsheet, opened with Cmd/Ctrl+/.
 * Same modal pattern ChatGPT and Linear use — small, scannable, no
 * config or remap surface (yet). Keep the list tight so users can
 * spot what they need without skimming.
 */
export function ShortcutsHelp({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const mod = isMac() ? "⌘" : "Ctrl";
  const rows: Row[] = [
    { keys: ["Enter"], label: "Send message" },
    { keys: ["Shift", "Enter"], label: "New line in composer" },
    { keys: ["Esc"], label: "Stop generating" },
    { keys: ["Shift", "Esc"], label: "Focus composer" },
    { keys: [mod, "Shift", "O"], label: "New chat" },
    { keys: [mod, "/"], label: "Toggle this menu" },
  ];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="text-base">Keyboard shortcuts</DialogTitle>
        </DialogHeader>
        <ul className="mt-2 divide-y divide-border/60">
          {rows.map((r, i) => (
            <li
              key={i}
              className="flex items-center justify-between gap-3 py-2"
            >
              <span className="text-sm text-foreground/90">{r.label}</span>
              <span className="flex items-center gap-1">
                {r.keys.map((k) => (
                  <kbd
                    key={k}
                    className="inline-flex h-6 min-w-6 items-center justify-center rounded border border-border bg-card px-1.5 text-[11px] font-medium text-muted-foreground"
                  >
                    {k}
                  </kbd>
                ))}
              </span>
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  );
}
