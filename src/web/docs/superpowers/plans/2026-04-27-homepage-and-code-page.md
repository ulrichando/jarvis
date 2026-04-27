# Homepage Unified Input + Code Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace homepage chip pills with a function-card grid (with expandable task panels), and add a `/code` page that pixel-accurately replicates the Claude Code interface for Jarvis CLI.

**Architecture:** Part 1 surgically swaps `<Categories>` for two new components (`FunctionGrid` + `TaskPanel`) with no changes to routing or server code. Part 2 adds a new `/code` route with its own full-page layout (sidebar + composer) and suppresses the app shell on that route — same pattern as `/workbench`.

**Tech Stack:** Next.js 15 (App Router), React 19, Tailwind CSS, Lucide React, `motion/react` for animations, `@tanstack/react-query` for settings

---

## File Map

**Part 1 — Homepage:**
| File | Action | Responsibility |
|---|---|---|
| `src/lib/ai/provider-ux.ts` | Modify | Add `description?` and `tasks?` fields to `Chip` type; populate all providers |
| `src/components/chat/function-grid.tsx` | Create | 2–3 col grid of function cards; manages `activeCategory` state internally |
| `src/components/chat/task-panel.tsx` | Create | Expandable panel below grid; header + vertical task list |
| `src/components/chat/chat.tsx` | Modify | Swap `<Categories>` import for `<FunctionGrid>` |
| `src/components/chat/categories.tsx` | Delete | Replaced by `function-grid.tsx` |

**Part 2 — Code Page:**
| File | Action | Responsibility |
|---|---|---|
| `src/components/code/code-sidebar.tsx` | Create | Claude Code-style sidebar: nav, pinned, recents, footer |
| `src/components/code/code-composer.tsx` | Create | 3-row composer: context bar + textarea + toolbar |
| `src/app/(app)/code/page.tsx` | Create | Full Code page: topbar + sidebar + main area |
| `src/components/layout/sidebar.tsx` | Modify | Add Code nav item; suppress open-button on `/code` |
| `src/components/layout/topbar.tsx` | Modify | Return null on `/code` |

---

## Task 1: Extend Chip type and populate tasks + descriptions

**Files:**
- Modify: `src/lib/ai/provider-ux.ts`

- [ ] **Step 1: Add `description` and `tasks` fields to the `Chip` type**

In `src/lib/ai/provider-ux.ts`, find the `Chip` type and replace it:

```typescript
export type Chip = {
  label: string;
  icon?: LucideIcon;
  prompt: string;
  description?: string;
  tasks?: string[];
};
```

- [ ] **Step 2: Populate `description` and `tasks` for the default (Anthropic) provider**

Find `DEFAULT_UX` in `provider-ux.ts`. Replace its `chips` array:

```typescript
chips: [
  {
    label: "Write",
    icon: PenLine,
    prompt: "Help me write ",
    description: "Draft, edit, or improve text",
    tasks: [
      "Draft a blog post",
      "Write a professional email",
      "Edit my writing for clarity",
      "Write a cover letter",
      "Summarize this for me",
    ],
  },
  {
    label: "Learn",
    icon: GraduationCap,
    prompt: "Teach me about ",
    description: "Explain concepts and ideas",
    tasks: [
      "Explain this concept simply",
      "Teach me how this works",
      "Quiz me on a topic",
      "Compare two things for me",
    ],
  },
  {
    label: "Code",
    icon: Code2,
    prompt: "Write a function that ",
    description: "Functions, scripts, debug",
    tasks: [
      "Write a function that…",
      "Debug my code",
      "Create a script to…",
      "Explain this code",
      "Write tests for…",
    ],
  },
  {
    label: "Life stuff",
    icon: Coffee,
    prompt: "Help me think through ",
    description: "Plans, decisions, ideas",
    tasks: [
      "Help me make a decision",
      "Think through a problem with me",
      "Help me plan my week",
      "Brainstorm ideas with me",
    ],
  },
  {
    label: "Claude's choice",
    icon: Apple,
    prompt: "Ask me three questions that'll get me unstuck on whatever I'm working on.",
    description: "Let the AI surprise you",
  },
],
```

- [ ] **Step 3: Populate `description` and `tasks` for the OpenAI provider**

Find `OPENAI_UX` and replace its `chips` array:

```typescript
chips: [
  {
    label: "Create image",
    icon: Images,
    prompt: "Create an image of ",
    description: "Generate images from text",
    tasks: [
      "Create a logo for…",
      "Illustrate a scene where…",
      "Draw a portrait of…",
      "Make a wallpaper that…",
    ],
  },
  {
    label: "Write or edit",
    icon: PenLine,
    prompt: "Help me write ",
    description: "Draft, edit, or improve text",
    tasks: [
      "Draft a blog post",
      "Write a professional email",
      "Edit my writing for clarity",
      "Write a cover letter",
    ],
  },
  {
    label: "Look something up",
    icon: Search,
    prompt: "Look up ",
    description: "Research and summarize",
    tasks: [
      "Summarize recent news on…",
      "What is the current status of…",
      "Find me resources about…",
      "Compare options for…",
    ],
  },
],
```

- [ ] **Step 4: Populate `description` and `tasks` for the Gemini provider**

Find `GEMINI_UX` and replace its `chips` array:

```typescript
chips: [
  {
    label: "Create image",
    icon: Images,
    prompt: "Create an image of ",
    description: "Generate images from text",
    tasks: [
      "Create a logo for…",
      "Illustrate a scene where…",
      "Make a wallpaper that…",
    ],
  },
  {
    label: "Create music",
    icon: Music4,
    prompt: "Create music that ",
    description: "Compose AI-generated music",
    tasks: [
      "Create a calm background track",
      "Make an upbeat workout playlist intro",
      "Compose a melody that feels like…",
    ],
  },
  {
    label: "Boost my day",
    icon: Sparkles,
    prompt: "Help me plan a great day. ",
    description: "Plans and productivity",
    tasks: [
      "Help me plan my morning routine",
      "Suggest a productive schedule for today",
      "Give me a motivational boost",
    ],
  },
  {
    label: "Write anything",
    icon: PenLine,
    prompt: "Help me write ",
    description: "Draft, edit, or improve text",
    tasks: [
      "Draft a blog post",
      "Write a professional email",
      "Edit my writing for clarity",
    ],
  },
  {
    label: "Help me learn",
    icon: BookOpen,
    prompt: "Teach me about ",
    description: "Explain concepts and ideas",
    tasks: [
      "Explain this concept simply",
      "Teach me how this works",
      "Quiz me on a topic",
    ],
  },
],
```

- [ ] **Step 5: Populate `description` and `tasks` for the Groq provider**

Find `GROQ_UX` and replace its `chips` array:

```typescript
chips: [
  {
    label: "Compound",
    icon: AudioLines,
    prompt: "Use Compound to ",
    description: "Multi-step AI workflows",
    tasks: [
      "Use Compound to research and summarize…",
      "Use Compound to analyze and report on…",
      "Use Compound to plan and execute…",
    ],
  },
  {
    label: "Code",
    icon: Code2,
    prompt: "Write a function that ",
    description: "Functions, scripts, debug",
    tasks: [
      "Write a function that…",
      "Debug my code",
      "Create a script to…",
      "Explain this code",
    ],
  },
  {
    label: "Summarize",
    icon: FileText,
    prompt: "Summarize this: ",
    description: "Condense any content",
    tasks: [
      "Summarize this article",
      "Give me the key points of…",
      "TL;DR this for me",
      "Extract the action items from…",
    ],
  },
],
```

- [ ] **Step 6: Commit**

```bash
git add src/web/src/lib/ai/provider-ux.ts
git commit -m "feat: add description and tasks fields to Chip type; populate all providers"
```

---

## Task 2: Create FunctionGrid component

**Files:**
- Create: `src/components/chat/function-grid.tsx`

- [ ] **Step 1: Create the file**

```tsx
"use client";

import { cn } from "@/lib/utils";
import type { Chip } from "@/lib/ai/provider-ux";

type Props = {
  chips: Chip[];
  onPick: (prompt: string) => void;
  activeLabel: string | null;
  onSetActive: (label: string | null) => void;
};

export function FunctionGrid({ chips, onPick, activeLabel, onSetActive }: Props) {
  if (chips.length === 0) return null;

  return (
    <div className="mt-6 w-full max-w-2xl mx-auto">
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        {chips.map((c) => {
          const isActive = activeLabel === c.label;
          const hasTasks = (c.tasks?.length ?? 0) > 0;
          return (
            <button
              key={c.label}
              type="button"
              onClick={() => {
                if (isActive) {
                  onSetActive(null);
                } else if (hasTasks) {
                  onSetActive(c.label);
                } else {
                  onPick(c.prompt);
                }
              }}
              className={cn(
                "flex flex-col gap-2.5 rounded-2xl border p-4 text-left transition-colors",
                isActive
                  ? "border-border bg-card text-foreground"
                  : "border-border/50 bg-card/40 text-foreground/85 hover:bg-card/70 hover:border-border/80",
              )}
            >
              {c.icon && (
                <c.icon
                  className={cn(
                    "size-4 transition-colors",
                    isActive ? "text-primary" : "text-muted-foreground",
                  )}
                />
              )}
              <div>
                <div className="text-[13px] font-semibold leading-snug">{c.label}</div>
                {c.description && (
                  <div className="text-[11.5px] text-muted-foreground mt-0.5 leading-snug">
                    {c.description}
                  </div>
                )}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add src/web/src/components/chat/function-grid.tsx
git commit -m "feat: add FunctionGrid component — 2-3 col card grid replacing chip pills"
```

---

## Task 3: Create TaskPanel component

**Files:**
- Create: `src/components/chat/task-panel.tsx`

- [ ] **Step 1: Create the file**

```tsx
"use client";

import { X } from "lucide-react";
import type { Chip } from "@/lib/ai/provider-ux";

type Props = {
  chip: Chip;
  onPick: (prompt: string) => void;
  onClose: () => void;
};

export function TaskPanel({ chip, onPick, onClose }: Props) {
  if (!chip.tasks?.length) return null;

  return (
    <div className="mt-3 w-full max-w-2xl mx-auto rounded-2xl border border-border/50 bg-card/60 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border/40">
        <div className="flex items-center gap-2 text-[13px] font-medium text-foreground/80">
          {chip.icon && <chip.icon className="size-3.5 shrink-0" />}
          {chip.label}
        </div>
        <button
          type="button"
          onClick={onClose}
          className="flex size-6 items-center justify-center rounded-md text-muted-foreground hover:text-foreground transition-colors"
          aria-label="Close"
        >
          <X className="size-3.5" />
        </button>
      </div>

      {/* Task list */}
      <div className="divide-y divide-border/30">
        {chip.tasks.map((task) => (
          <button
            key={task}
            type="button"
            onClick={() => onPick(task)}
            className="w-full px-4 py-3 text-left text-[13px] text-foreground/75 hover:bg-accent/40 hover:text-foreground transition-colors"
          >
            {task}
          </button>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add src/web/src/components/chat/task-panel.tsx
git commit -m "feat: add TaskPanel component — expandable task list below function cards"
```

---

## Task 4: Wire FunctionGrid + TaskPanel into chat.tsx; remove Categories

**Files:**
- Modify: `src/components/chat/chat.tsx`
- Delete: `src/components/chat/categories.tsx`

- [ ] **Step 1: Update imports in `chat.tsx`**

Find the import block at the top of `src/components/chat/chat.tsx`. Remove the `Categories` import and add the two new ones:

```typescript
// Remove this line:
import { Categories } from "./categories";

// Add these two lines in its place:
import { FunctionGrid } from "./function-grid";
import { TaskPanel } from "./task-panel";
```

- [ ] **Step 2: Add `activeCategory` state near the other useState calls in `chat.tsx`**

Find the block of `useState` calls (around line 107–113). Add one line:

```typescript
const [activeCategory, setActiveCategory] = useState<string | null>(null);
```

- [ ] **Step 3: Replace `<Categories>` with `<FunctionGrid>` + `<TaskPanel>` in the empty non-embedded state**

Find the `if (isEmpty && !embedded)` branch. The current JSX ends with:

```tsx
        <Categories chips={ux.chips} onPick={(p) => setInput(p)} />
```

Replace that one line with:

```tsx
        <FunctionGrid
          chips={ux.chips}
          onPick={(p) => { setInput(p); setActiveCategory(null); }}
          activeLabel={activeCategory}
          onSetActive={setActiveCategory}
        />
        {activeCategory && (() => {
          const chip = ux.chips.find((c) => c.label === activeCategory);
          return chip ? (
            <TaskPanel
              chip={chip}
              onPick={(p) => { setInput(p); setActiveCategory(null); }}
              onClose={() => setActiveCategory(null)}
            />
          ) : null;
        })()}
```

- [ ] **Step 4: Delete `categories.tsx`**

```bash
rm src/web/src/components/chat/categories.tsx
```

- [ ] **Step 5: Verify the dev server compiles without errors**

```bash
cd src/web && bun run dev
```

Expected: no TypeScript errors, homepage shows the card grid instead of pills.

- [ ] **Step 6: Commit**

```bash
git add src/web/src/components/chat/chat.tsx
git rm src/web/src/components/chat/categories.tsx
git commit -m "feat: replace chip pills with FunctionGrid + TaskPanel on homepage empty state"
```

---

## Task 5: Create CodeSidebar component

**Files:**
- Create: `src/components/code/code-sidebar.tsx`

- [ ] **Step 1: Create the directory and file**

```bash
mkdir -p src/web/src/components/code
```

```tsx
"use client";

import {
  ArrowLeft,
  ChevronDown,
  LayoutGrid,
  Pin,
  Plus,
  RotateCw,
  SlidersHorizontal,
} from "lucide-react";
import Link from "next/link";
import { useSettings } from "@/hooks/use-settings";

type Session = { id: string; title: string };

function initials(name?: string | null) {
  if (!name) return "UA";
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]!.toUpperCase())
    .join("");
}

type Props = {
  sessions: Session[];
  onNewSession: () => void;
};

export function CodeSidebar({ sessions, onNewSession }: Props) {
  const { data: settings } = useSettings();
  const displayName = settings?.user?.name ?? "You";

  return (
    <div className="flex h-full w-[230px] shrink-0 flex-col overflow-hidden border-r border-border/30 bg-sidebar text-sidebar-foreground">
      {/* Nav */}
      <nav className="flex flex-col gap-px p-2 pt-3">
        <button
          type="button"
          onClick={onNewSession}
          className="flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13.5px] text-sidebar-foreground/90 transition-colors hover:bg-sidebar-accent/60"
        >
          <Plus className="size-3.5 shrink-0" />
          New session
        </button>
        <button
          type="button"
          className="flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13.5px] text-sidebar-foreground/90 transition-colors hover:bg-sidebar-accent/60"
        >
          <RotateCw className="size-3.5 shrink-0" />
          Routines
        </button>
        <button
          type="button"
          className="flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13.5px] text-sidebar-foreground/90 transition-colors hover:bg-sidebar-accent/60"
        >
          <SlidersHorizontal className="size-3.5 shrink-0" />
          Customize
        </button>
        <button
          type="button"
          className="flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13.5px] text-sidebar-foreground/90 transition-colors hover:bg-sidebar-accent/60"
        >
          <ChevronDown className="size-3.5 shrink-0" />
          More
        </button>
      </nav>

      {/* Pinned */}
      <div className="mt-3 px-2">
        <div className="px-2.5 pb-1 text-[11px] text-sidebar-foreground/50">Pinned</div>
        <div className="flex items-center gap-2 px-2.5 py-1.5 text-[13px] text-sidebar-foreground/35 select-none">
          <Pin className="size-3 shrink-0" />
          Drag to pin
        </div>
      </div>

      {/* Recents */}
      <div className="mt-3 flex-1 overflow-y-auto px-2">
        <div className="px-2.5 pb-1 text-[11px] text-sidebar-foreground/50">Recents</div>
        <div className="space-y-px">
          {sessions.map((s) => (
            <button
              key={s.id}
              type="button"
              className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] text-sidebar-foreground/75 transition-colors hover:bg-sidebar-accent/60"
            >
              <span className="size-1.5 shrink-0 rounded-full bg-muted-foreground/30" />
              <span className="truncate">{s.title}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Footer */}
      <div className="border-t border-border/30 px-2 py-2">
        <div className="flex items-center justify-between px-1">
          <div className="flex items-center gap-2">
            <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/20 font-mono text-[10px] font-semibold text-primary">
              {initials(displayName)}
            </div>
            <span className="text-[13px] text-sidebar-foreground/80 truncate max-w-[100px]">
              {displayName}
            </span>
          </div>
          <div className="flex items-center gap-0.5">
            <button
              type="button"
              title="Layout"
              className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-foreground"
            >
              <LayoutGrid className="size-3.5" />
            </button>
            <Link
              href="/chat"
              title="Back to chat"
              className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-foreground"
            >
              <ArrowLeft className="size-3.5" />
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add src/web/src/components/code/code-sidebar.tsx
git commit -m "feat: add CodeSidebar component — Claude Code-style sidebar for /code page"
```

---

## Task 6: Create CodeComposer component

**Files:**
- Create: `src/components/code/code-composer.tsx`

- [ ] **Step 1: Create the file**

```tsx
"use client";

import {
  ChevronDown,
  CornerDownLeft,
  Plus,
  Settings2,
} from "lucide-react";
import { useCallback, useEffect, useRef } from "react";

type Props = {
  value: string;
  onChange: (v: string) => void;
};

export function CodeComposer({ value, onChange }: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);

  const autoSize = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, []);

  useEffect(autoSize, [value, autoSize]);

  return (
    <div className="px-6 pb-6">
      {/* Row 1: context bar */}
      <div className="mb-2 flex items-center gap-2">
        <button
          type="button"
          className="flex items-center gap-1.5 rounded-md border border-border/50 bg-muted/20 px-2.5 py-1 text-[12px] text-muted-foreground transition-colors hover:text-foreground"
        >
          <span className="size-1.5 rounded-full bg-muted-foreground/50" />
          Default
        </button>
        <button
          type="button"
          className="flex items-center gap-1 rounded-md border border-border/50 bg-muted/20 px-2.5 py-1 text-[12px] text-muted-foreground transition-colors hover:text-foreground"
        >
          <Plus className="size-3" />
          Select machine…
        </button>
      </div>

      {/* Rows 2 + 3: input box */}
      <div className="rounded-xl border border-border/50 bg-muted/10">
        {/* Row 2: textarea */}
        <div className="flex items-start gap-2 px-3 pb-2 pt-3">
          <textarea
            ref={ref}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            rows={1}
            placeholder="Describe a task or ask a question"
            className="flex-1 resize-none bg-transparent text-[14px] leading-6 outline-none placeholder:text-muted-foreground/50"
          />
          <button
            type="button"
            title="Send"
            className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-foreground"
          >
            <CornerDownLeft className="size-3.5" />
          </button>
        </div>

        {/* Row 3: toolbar */}
        <div className="flex items-center justify-between border-t border-border/30 px-2 py-1.5">
          <div className="flex items-center gap-0.5">
            <button
              type="button"
              className="rounded-md border border-border/40 px-2.5 py-1 text-[11.5px] text-muted-foreground transition-colors hover:text-foreground"
            >
              Accept edits
            </button>
            <button
              type="button"
              className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-foreground"
            >
              <Plus className="size-3.5" />
            </button>
            <button
              type="button"
              className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-foreground"
            >
              <Settings2 className="size-3.5" />
            </button>
            <button
              type="button"
              className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-foreground"
            >
              <ChevronDown className="size-3.5" />
            </button>
          </div>
          <div className="flex items-center gap-2 text-[11px] text-muted-foreground/60">
            <span>Jarvis 4.7</span>
            <span>1M</span>
          </div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add src/web/src/components/code/code-composer.tsx
git commit -m "feat: add CodeComposer component — 3-row context/input/toolbar composer for /code"
```

---

## Task 7: Create the /code page

**Files:**
- Create: `src/app/(app)/code/page.tsx`

- [ ] **Step 1: Create the directory and file**

```bash
mkdir -p src/web/src/app/\(app\)/code
```

```tsx
"use client";

import { useState, useEffect } from "react";
import { Maximize2, Search } from "lucide-react";
import Link from "next/link";
import { useSettings } from "@/hooks/use-settings";
import { useUI } from "@/stores/ui";
import { CodeSidebar } from "@/components/code/code-sidebar";
import { CodeComposer } from "@/components/code/code-composer";

const PLACEHOLDER_SESSIONS = [
  { id: "1", title: "Set up alternative to Tailscale" },
  { id: "2", title: "Review codebase for errors" },
];

export default function CodePage() {
  const [input, setInput] = useState("");
  const { data: settings } = useSettings();
  const setSidebarOpen = useUI((s) => s.setSidebarOpen);

  const firstName = settings?.user?.name?.split(" ")[0] ?? "there";

  // Collapse the app sidebar when entering this page.
  useEffect(() => {
    setSidebarOpen(false);
  }, [setSidebarOpen]);

  return (
    <div className="flex h-full w-full flex-col overflow-hidden">
      {/* Topbar */}
      <header className="flex h-10 shrink-0 items-center justify-between border-b border-border/30 bg-background px-4">
        <div className="flex items-center gap-3">
          <Link
            href="/chat"
            className="text-[14px] font-semibold text-foreground hover:opacity-80 transition-opacity"
          >
            Jarvis CLI
          </Link>
          <span className="rounded-full border border-border/50 px-2 py-0.5 text-[10px] text-muted-foreground/70">
            Research preview
          </span>
        </div>
        <div className="flex items-center gap-0.5">
          <button
            type="button"
            title="Open in new window"
            className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-foreground"
          >
            <Maximize2 className="size-3.5" />
          </button>
          <button
            type="button"
            title="Search"
            className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-foreground"
          >
            <Search className="size-3.5" />
          </button>
        </div>
      </header>

      {/* Body */}
      <div className="flex flex-1 overflow-hidden">
        <CodeSidebar
          sessions={PLACEHOLDER_SESSIONS}
          onNewSession={() => setInput("")}
        />

        <main className="flex flex-1 flex-col overflow-hidden">
          {/* Greeting — top-left, not centered */}
          <div className="px-8 pt-8">
            <h1 className="font-serif text-3xl font-normal tracking-tight text-foreground">
              <span className="text-primary">✻</span>{" "}
              What&apos;s up next, {firstName}?
            </h1>
          </div>

          {/* Spacer */}
          <div className="flex-1" />

          {/* Bottom composer */}
          <CodeComposer value={input} onChange={setInput} />
        </main>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add src/web/src/app/\(app\)/code/page.tsx
git commit -m "feat: add /code page — Claude Code-style layout for Jarvis CLI"
```

---

## Task 8: Add Code nav item to app sidebar; suppress on /code

**Files:**
- Modify: `src/components/layout/sidebar.tsx`

- [ ] **Step 1: Add `Terminal` to the lucide imports in `sidebar.tsx`**

Find the lucide-react import block. Add `Terminal` to it:

```typescript
import {
  ChevronDown,
  Code2,
  MessagesSquare,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  Terminal,  // ← add this
} from "lucide-react";
```

- [ ] **Step 2: Add the Code nav item to `CORE_NAV`**

Find the `CORE_NAV` constant. Replace it entirely:

```typescript
const CORE_NAV = [
  { href: "/chat", label: "New chat", icon: Plus },
  { href: "/search", label: "Search", icon: Search },
  { href: "/chats", label: "Chats", icon: MessagesSquare },
  { href: "/code", label: "Code", icon: Terminal },
  { href: "/workbench", label: "Workbench", icon: Code2 },
] as const;
```

- [ ] **Step 3: Suppress the open-sidebar button on `/code`**

Find the block at the bottom of the `Sidebar` component that renders the `PanelLeftOpen` button. Currently it reads:

```tsx
      {!sidebarOpen && !pathname.startsWith("/workbench") && (
```

Change it to:

```tsx
      {!sidebarOpen && !pathname.startsWith("/workbench") && !pathname.startsWith("/code") && (
```

- [ ] **Step 4: Commit**

```bash
git add src/web/src/components/layout/sidebar.tsx
git commit -m "feat: add Code nav item to sidebar; suppress sidebar open-button on /code"
```

---

## Task 9: Suppress TopBar on /code

**Files:**
- Modify: `src/components/layout/topbar.tsx`

- [ ] **Step 1: Update the pathname check in `topbar.tsx`**

The file currently reads:

```tsx
  if (pathname.startsWith("/workbench")) return null;
```

Change it to:

```tsx
  if (pathname.startsWith("/workbench") || pathname.startsWith("/code")) return null;
```

- [ ] **Step 2: Verify the /code page renders without double topbar**

```bash
cd src/web && bun run dev
# Navigate to http://localhost:3000/code
```

Expected: the page shows only the CodePage's own topbar ("Jarvis CLI"), not the app's TopBar.

- [ ] **Step 3: Commit**

```bash
git add src/web/src/components/layout/topbar.tsx
git commit -m "feat: suppress app TopBar on /code route"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| FunctionGrid: 2-3 col grid, card with icon + label + description | Task 2 |
| TaskPanel: header with icon+label+X, vertical list of tasks | Task 3 |
| Clicking card with tasks → TaskPanel; clicking same card again → closes | Task 2 (button logic) |
| Clicking task → fills input, closes panel | Task 4 (onPick handler) |
| `tasks?: string[]` on Chip type, populated for all providers | Task 1 |
| Code page: own topbar "Jarvis CLI" + "Research preview" + Maximize2 + Search | Task 7 |
| Code page: own sidebar with New session, Routines, Customize, More, Pinned, Recents, footer | Task 5 |
| Code page: greeting "✻ What's up next, {name}?" top-left | Task 7 |
| Code page: 3-row composer (Default + Select machine | textarea | Accept edits toolbar) | Task 6 |
| Code nav item in app sidebar between Chats and Workbench | Task 8 |
| App sidebar open-button suppressed on /code | Task 8 |
| App TopBar suppressed on /code | Task 9 |
| App sidebar auto-collapses on /code | Task 7 (useEffect setSidebarOpen) |
| All Code page interactions are UI shells only | Tasks 5–7 (no fetch calls) |

No gaps found.
