"use client";

import { useRouter } from "next/navigation";
import {
  CirclePlus,
  FileText,
  Flag,
  Globe,
  ListChecks,
  Palette,
  Zap,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";

// Mirrors claude.ai's "New artifact" category picker. Selecting a category
// opens a fresh chat seeded with a starter the user can edit or send as-is;
// the artifact prompt is already in the chat system prompt, so the model
// returns a <jarvisArtifact> that streams into the panel + saves to the
// gallery. "Start from scratch" just opens an empty chat.
const CATEGORIES: {
  label: string;
  icon: typeof Globe;
  seed?: string;
}[] = [
  {
    label: "Apps and websites",
    icon: Globe,
    seed: "Build me a small interactive web app as an artifact — pick something useful and make it work end to end.",
  },
  {
    label: "Documents and templates",
    icon: FileText,
    seed: "Create a polished document or template as an artifact (a one-pager, README, proposal, or similar).",
  },
  {
    label: "Games",
    icon: Flag,
    seed: "Build me a small, playable browser game as an artifact.",
  },
  {
    label: "Productivity tools",
    icon: Zap,
    seed: "Build me a handy productivity tool as an artifact (timer, tracker, calculator, etc.).",
  },
  {
    label: "Creative projects",
    icon: Palette,
    seed: "Make me a creative interactive artifact — generative art, an animation, or a visual toy.",
  },
  {
    label: "Quiz or survey",
    icon: ListChecks,
    seed: "Build me an interactive quiz or survey as an artifact.",
  },
  { label: "Start from scratch", icon: CirclePlus },
];

export function NewArtifactDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const router = useRouter();

  const pick = (seed?: string) => {
    onOpenChange(false);
    router.push(seed ? `/chat?seed=${encodeURIComponent(seed)}` : "/chat");
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogTitle className="font-serif text-xl font-semibold tracking-tight">
          Let’s get cooking
        </DialogTitle>
        <p className="-mt-1 text-[13px] text-muted-foreground">
          Pick an artifact category, or start building your idea from scratch.
        </p>
        <div className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-3">
          {CATEGORIES.map(({ label, icon: Icon, seed }) => (
            <button
              key={label}
              onClick={() => pick(seed)}
              className="group flex h-24 flex-col justify-between rounded-xl border border-border/60 bg-card/40 p-3 text-left transition-colors hover:border-primary/40 hover:bg-card"
            >
              <span className="text-[13px] font-medium leading-snug text-foreground">
                {label}
              </span>
              <Icon className="size-4 self-end text-muted-foreground transition-colors group-hover:text-primary" />
            </button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
