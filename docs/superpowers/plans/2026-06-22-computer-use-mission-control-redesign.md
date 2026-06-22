# Computer Use "Mission Control" Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the `/computer-use` web page into the approved "Mission Control" layout — desktop hero + auditable activity timeline + full-width command bar — with zero backend changes.

**Architecture:** Frontend-only, entirely within `src/web/`. The existing `/api/computer-use` SSE proxy and the `:8771` sidecar are untouched. The 621-line page decomposes into focused components under `src/components/computer-use/`, driven by a pure event→timeline mapper in `src/lib/computer-use/timeline.ts`. Per-step screenshot thumbnails are captured client-side from the noVNC canvas. All colors use existing Tailwind tokens (works light + dark).

**Tech Stack:** Next.js 16, React 19, TypeScript, Tailwind (CSS-variable tokens), `motion/react`, `@novnc/novnc`, vitest + jsdom + @testing-library/react.

**Spec:** `docs/superpowers/specs/2026-06-22-computer-use-mission-control-design.md`

---

## ⚠️ Staging discipline (read first)

The working tree currently carries an **unrelated, in-flight 131-file voice-agent refactor** (another session). Therefore:

- **NEVER** `git add -A` / `git add .` / `git commit -a`. Stage **explicit paths only** (every commit step below lists them).
- Optional: `git checkout -b feat/computer-use-mission-control` first. A branch shares the working tree, so it does **not** isolate the voice-agent churn — explicit-path staging is the real safety net.
- Commit messages: plain conventional commits. **No** `Co-Authored-By`, **no** "Generated with Claude Code" (repo rule).
- Run all web commands from `src/web/` (vitest alias breaks from repo root).

## File structure

| File | Action | Responsibility |
|---|---|---|
| `docs/superpowers/specs/2026-06-22-computer-use-mission-control-mockup.html` | create | Durable visual reference (the approved mockup) |
| `src/lib/computer-use/timeline.ts` | create | Types + pure helpers: `eventToPart`, `formatStepTime`, `formatElapsed`, `computeThumbSize` |
| `src/components/computer-use/novnc-view.tsx` | modify | Add `forwardRef` + `snapshot()` imperative handle |
| `src/components/computer-use/model-picker.tsx` | create | Scoped CU model dropdown (extracted from page) |
| `src/components/computer-use/permission-card.tsx` | create | In-chat approve/deny card (extracted) |
| `src/components/computer-use/activity-timeline.tsx` | create | Activity header + entry rendering (Task/Reason/Step/Permission/Done/Blocked) + empty state |
| `src/components/computer-use/desktop-stage.tsx` | create | Framed desktop hero + chrome + overlay + 3 stage states |
| `src/components/computer-use/app-bar.tsx` | create | App bar: brand, status/session chips, segmented mode, take-control, overflow, stop |
| `src/components/computer-use/command-bar.tsx` | create | Full-width footer input + model picker + send + hints |
| `src/app/(app)/computer-use/page.tsx` | rewrite | Orchestrator: state, SSE loop, handlers, region layout |
| `src/web/tests/computer-use/*.test.{ts,tsx}` | create | vitest tests |

## Token map (mockup oklch → Tailwind)

Port structure/proportions from the committed mockup; swap its literal colors for tokens:

| Mockup literal | Tailwind token |
|---|---|
| bg `oklch(0.11…)` | `bg-background` |
| card `oklch(0.16…)` / card2 | `bg-card` / `bg-muted` |
| primary `oklch(0.80 0.155 198)` | `text-primary` / `bg-primary` / ring `ring-primary` |
| primary-dim (14%) | `bg-primary/10` (chips) · `bg-primary/15` |
| primary-fg | `text-primary-foreground` |
| muted-fg `oklch(0.74…)` | `text-muted-foreground` |
| border (22%) / soft (11%) | `border-border/60` / `border-border/40` |
| success | `text-emerald-500` / `bg-emerald-500` |
| danger | `text-destructive` / `bg-destructive` |
| mono | `font-mono` |

Icons: `lucide-react` (already used) — `Monitor, ShieldCheck, Hand, MoreVertical, Plug, Unplug, RotateCcw, RotateCw, Square, Loader2, Check, ChevronDown, Cpu, CornerDownLeft`.

---

## Task 1: Commit the mockup as a durable reference

**Files:**
- Create: `docs/superpowers/specs/2026-06-22-computer-use-mission-control-mockup.html`

- [ ] **Step 1: Copy the approved mockup into committed docs**

Run (the brainstorm dir is gitignored; this makes the reference durable):
```bash
cd /home/ulrich/Documents/Projects/jarvis
cp .superpowers/brainstorm/*/content/mission-control.html \
   docs/superpowers/specs/2026-06-22-computer-use-mission-control-mockup.html
```
If the brainstorm dir is gone, skip — the spec + this plan are sufficient.

- [ ] **Step 2: Commit**
```bash
git add docs/superpowers/specs/2026-06-22-computer-use-mission-control-design.md \
        docs/superpowers/specs/2026-06-22-computer-use-mission-control-mockup.html \
        docs/superpowers/plans/2026-06-22-computer-use-mission-control-redesign.md
git commit -m "docs(computer-use): mission-control redesign spec, mockup, plan"
```

---

## Task 2: Pure timeline helpers (TDD)

**Files:**
- Create: `src/lib/computer-use/timeline.ts`
- Test: `src/web/tests/computer-use/timeline.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// tests/computer-use/timeline.test.ts
import { describe, it, expect } from "vitest";
import { eventToPart, formatStepTime, formatElapsed, computeThumbSize } from "@/lib/computer-use/timeline";

describe("eventToPart", () => {
  it("maps text", () => {
    expect(eventToPart({ type: "text", text: "hi" }, 1000)).toEqual({ kind: "text", text: "hi", ts: 1000 });
  });
  it("maps action", () => {
    expect(eventToPart({ type: "action", summary: "Clicked Firefox" }, 5)).toEqual({ kind: "action", text: "Clicked Firefox", ts: 5 });
  });
  it("maps permission_request with fallback label", () => {
    expect(eventToPart({ type: "permission_request", id: "r1", summary: "type x" }, 7))
      .toEqual({ kind: "permission", reqId: "r1", label: "this action", text: "type x", ts: 7 });
  });
  it("maps done/blocked/error", () => {
    expect(eventToPart({ type: "done" }, 9)).toEqual({ kind: "done", text: "Done", ts: 9 });
    expect(eventToPart({ type: "blocked", summary: "nope" }, 9)?.kind).toBe("blocked");
    expect(eventToPart({ type: "error", error: "boom" }, 9)?.kind).toBe("error");
  });
  it("drops non-rendered frames", () => {
    expect(eventToPart({ type: "ping" }, 1)).toBeNull();
    expect(eventToPart({ type: "start" }, 1)).toBeNull();
    expect(eventToPart({ type: "denied", summary: "x" }, 1)).toBeNull();
    expect(eventToPart({ type: "action" }, 1)).toBeNull(); // no summary
  });
});

describe("formatStepTime", () => {
  it("HH:MM:SS, zero-padded", () => {
    const ts = new Date(2026, 0, 1, 9, 4, 7).getTime();
    expect(formatStepTime(ts)).toBe("09:04:07");
  });
});

describe("formatElapsed", () => {
  it("m:ss", () => {
    expect(formatElapsed(0)).toBe("0:00");
    expect(formatElapsed(38_000)).toBe("0:38");
    expect(formatElapsed(125_000)).toBe("2:05");
    expect(formatElapsed(-50)).toBe("0:00");
  });
});

describe("computeThumbSize", () => {
  it("keeps small canvases, downscales large ones preserving aspect", () => {
    expect(computeThumbSize(100, 60, 128)).toEqual({ w: 100, h: 60 });
    expect(computeThumbSize(256, 160, 128)).toEqual({ w: 128, h: 80 });
    expect(computeThumbSize(0, 0, 128)).toEqual({ w: 0, h: 0 });
  });
});
```

- [ ] **Step 2: Run it; verify it fails**

Run: `cd src/web && npx vitest run tests/computer-use/timeline.test.ts`
Expected: FAIL — `Cannot find module '@/lib/computer-use/timeline'`.

- [ ] **Step 3: Implement**

```ts
// src/lib/computer-use/timeline.ts
export type LoopEvent =
  | { type: "start"; task?: string }
  | { type: "text"; text?: string }
  | { type: "action"; summary?: string }
  | { type: "permission_request"; id?: string; action?: string; kind?: string; label?: string; summary?: string }
  | { type: "blocked"; summary?: string }
  | { type: "denied"; summary?: string }
  | { type: "ping" }
  | { type: "done" }
  | { type: "error"; error?: string };

export type PartKind = "text" | "action" | "error" | "done" | "blocked" | "permission";
export type Part = {
  kind: PartKind;
  text: string;
  reqId?: string;
  label?: string;
  resolved?: "once" | "session" | "deny";
  ts?: number;
  thumb?: string;
};
export type ChatMsg = { role: "user" | "assistant"; parts: Part[] };

/** Map a sidecar SSE frame to a timeline Part, or null for frames that add no row. */
export function eventToPart(evt: LoopEvent, now: number): Part | null {
  switch (evt.type) {
    case "text":   return evt.text ? { kind: "text", text: evt.text, ts: now } : null;
    case "action": return evt.summary ? { kind: "action", text: evt.summary, ts: now } : null;
    case "permission_request":
      return evt.id
        ? { kind: "permission", reqId: evt.id, label: evt.label ?? "this action", text: evt.summary ?? "", ts: now }
        : null;
    case "blocked": return evt.summary ? { kind: "blocked", text: evt.summary, ts: now } : null;
    case "error":   return evt.error ? { kind: "error", text: evt.error, ts: now } : null;
    case "done":    return { kind: "done", text: "Done", ts: now };
    default:        return null; // start / ping / denied → no new row
  }
}

/** "14:32:05" local wall-clock for a step row. */
export function formatStepTime(ts: number): string {
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** "0:38" elapsed-since-run for the Activity counter. */
export function formatElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Downscale w×h to fit maxW, preserving aspect (for canvas thumbnails). */
export function computeThumbSize(w: number, h: number, maxW: number): { w: number; h: number } {
  if (w <= 0 || h <= 0) return { w: 0, h: 0 };
  if (w <= maxW) return { w, h };
  return { w: maxW, h: Math.round(h * (maxW / w)) };
}
```

- [ ] **Step 4: Run; verify pass**

Run: `cd src/web && npx vitest run tests/computer-use/timeline.test.ts`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**
```bash
git add src/web/src/lib/computer-use/timeline.ts src/web/tests/computer-use/timeline.test.ts
git commit -m "feat(computer-use): pure timeline event mapper + format helpers"
```

---

## Task 3: noVNC canvas snapshot ref

**Files:**
- Modify: `src/components/computer-use/novnc-view.tsx`
- Test: `src/web/tests/computer-use/novnc-snapshot.test.tsx`

- [ ] **Step 1: Write the failing test** (jsdom: no canvas is injected when `wsUrl=""`, so `snapshot()` returns null without touching unimplemented canvas APIs)

```tsx
// tests/computer-use/novnc-snapshot.test.tsx
import { describe, it, expect } from "vitest";
import { createRef } from "react";
import { render } from "@testing-library/react";
import { NoVNCView, type NoVNCHandle } from "@/components/computer-use/novnc-view";

describe("NoVNCView snapshot()", () => {
  it("returns null when no canvas is present", () => {
    const ref = createRef<NoVNCHandle>();
    render(<NoVNCView ref={ref} wsUrl="" password="" />); // empty wsUrl → effect early-returns, no RFB/canvas
    expect(ref.current?.snapshot()).toBeNull();
  });
});
```

- [ ] **Step 2: Run; verify it fails**

Run: `cd src/web && npx vitest run tests/computer-use/novnc-snapshot.test.tsx`
Expected: FAIL — `NoVNCView` has no `ref`/`NoVNCHandle` export yet.

- [ ] **Step 3: Implement** — convert to `forwardRef` and add the handle. Edit `novnc-view.tsx`:

Change the imports line and signature:
```tsx
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { computeThumbSize } from "@/lib/computer-use/timeline";

export type NoVNCHandle = { snapshot: (maxW?: number) => string | null };
```
Replace `export function NoVNCView({ wsUrl, password, viewOnly = true, onState, className }: Props) {` with:
```tsx
export const NoVNCView = forwardRef<NoVNCHandle, Props>(function NoVNCView(
  { wsUrl, password, viewOnly = true, onState, className },
  ref,
) {
```
Add this hook right after the existing `viewOnlyRef`/effect block (before the connect effect is fine, anywhere in the body):
```tsx
  useImperativeHandle(ref, () => ({
    snapshot(maxW = 128) {
      try {
        const canvas = containerRef.current?.querySelector("canvas");
        if (!canvas) return null;
        const { w, h } = computeThumbSize(canvas.width, canvas.height, maxW);
        if (!w || !h) return null;
        const off = document.createElement("canvas");
        off.width = w;
        off.height = h;
        const ctx = off.getContext("2d");
        if (!ctx) return null;
        ctx.drawImage(canvas, 0, 0, w, h);
        return off.toDataURL("image/jpeg", 0.5);
      } catch {
        return null; // tainted canvas / unsupported / mid-teardown
      }
    },
  }), []);
```
Close the component: the final `}` of `export function NoVNCView(...)` becomes `});` (forwardRef call).

- [ ] **Step 4: Run; verify pass**

Run: `cd src/web && npx vitest run tests/computer-use/novnc-snapshot.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add src/web/src/components/computer-use/novnc-view.tsx src/web/tests/computer-use/novnc-snapshot.test.tsx
git commit -m "feat(computer-use): expose snapshot() handle on NoVNCView for per-step thumbnails"
```

---

## Task 4: Extract `model-picker.tsx`

**Files:**
- Create: `src/components/computer-use/model-picker.tsx`

- [ ] **Step 1: Move `CU_MODELS` + `ModelPicker` out of page.tsx into the new file**, exporting both. Final file:

```tsx
// src/components/computer-use/model-picker.tsx
"use client";
import { Cpu, ChevronDown, Check } from "lucide-react";
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from "@/components/ui/dropdown-menu";

export const CU_MODELS = [
  { id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6", hint: "Balanced", provider: "anthropic" },
  { id: "claude-opus-4-8", label: "Claude Opus 4.8", hint: "Most capable", provider: "anthropic" },
  { id: "claude-haiku-4-5", label: "Claude Haiku 4.5", hint: "Fastest", provider: "anthropic" },
  { id: "gpt-5.5", label: "GPT-5.5", hint: "OpenAI", provider: "openai" },
  { id: "gemini-3-flash-preview", label: "Gemini 3 Flash", hint: "Google", provider: "gemini" },
] as const;

export function ModelPicker({
  model, setModel, disabled, providers,
}: { model: string; setModel: (m: string) => void; disabled?: boolean; providers?: Record<string, boolean> }) {
  const current = CU_MODELS.find((m) => m.id === model) ?? CU_MODELS[0];
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <button
            disabled={disabled}
            className="inline-flex items-center gap-2 rounded-lg border border-border/40 bg-muted/40 px-2.5 py-1.5 text-[12px] text-foreground transition-colors hover:border-border/60 disabled:opacity-50"
            title="Model that drives the desktop"
          />
        }
      >
        <Cpu className="size-3.5 text-muted-foreground" />
        {current.label}
        <span className="rounded bg-primary/10 px-1 py-px text-[8.5px] font-bold tracking-wide text-primary">NATIVE</span>
        <ChevronDown className="size-3 opacity-60" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-56">
        {CU_MODELS.map((m) => {
          const avail = !providers || providers[m.provider] !== false;
          return (
            <DropdownMenuItem key={m.id} disabled={!avail} onClick={() => { if (avail) setModel(m.id); }} className="flex items-center justify-between gap-3">
              <span className="flex items-center gap-2">
                {m.id === model ? <Check className="size-3.5 text-primary" /> : <span className="size-3.5" />}
                {m.label}
              </span>
              <span className="text-[10px] text-muted-foreground">{avail ? m.hint : "no key"}</span>
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
```

- [ ] **Step 2: Verify it compiles**

Run: `cd src/web && npx tsc --noEmit 2>&1 | grep computer-use/model-picker || echo OK`
Expected: `OK`.

- [ ] **Step 3: Commit**
```bash
git add src/web/src/components/computer-use/model-picker.tsx
git commit -m "refactor(computer-use): extract ModelPicker into its own component"
```

---

## Task 5: Extract `permission-card.tsx` (with test)

**Files:**
- Create: `src/components/computer-use/permission-card.tsx`
- Test: `src/web/tests/computer-use/permission-card.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// tests/computer-use/permission-card.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PermissionCard } from "@/components/computer-use/permission-card";

describe("PermissionCard", () => {
  it("fires onApprove with the chosen decision", () => {
    const onApprove = vi.fn();
    render(<PermissionCard part={{ kind: "permission", reqId: "r1", label: "type a URL", text: 'type "x"' }} onApprove={onApprove} />);
    fireEvent.click(screen.getByText("For session"));
    expect(onApprove).toHaveBeenCalledWith("r1", "session");
  });
  it("shows a resolved state instead of buttons", () => {
    render(<PermissionCard part={{ kind: "permission", reqId: "r1", label: "x", text: "", resolved: "deny" }} onApprove={() => {}} />);
    expect(screen.getByText(/Denied/)).toBeTruthy();
    expect(screen.queryByText("Approve")).toBeNull();
  });
});
```

- [ ] **Step 2: Run; verify it fails**

Run: `cd src/web && npx vitest run tests/computer-use/permission-card.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement** (token-themed extraction of the current card)

```tsx
// src/components/computer-use/permission-card.tsx
"use client";
import type { Part } from "@/lib/computer-use/timeline";

export function PermissionCard({
  part, onApprove,
}: { part: Part; onApprove: (reqId: string, decision: "once" | "session" | "deny") => void }) {
  return (
    <div className="rounded-xl border border-primary bg-primary/10 p-3">
      <div className="text-[13px] text-foreground">
        Allow Jarvis to <span className="font-semibold">{part.label}</span>?
      </div>
      {part.text ? <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">{part.text}</div> : null}
      {part.resolved ? (
        <div className="mt-2 text-[12px] text-muted-foreground">
          {part.resolved === "deny" ? "✗ Denied" : part.resolved === "session" ? "✓ Approved for the session" : "✓ Approved"}
        </div>
      ) : (
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          <button onClick={() => part.reqId && onApprove(part.reqId, "once")} className="rounded-md bg-primary px-3 py-1.5 text-[11.5px] font-medium text-primary-foreground transition-opacity hover:opacity-90">Approve</button>
          <button onClick={() => part.reqId && onApprove(part.reqId, "session")} className="rounded-md border border-border/60 bg-card px-3 py-1.5 text-[11.5px] text-foreground transition-colors hover:border-border">For session</button>
          <button onClick={() => part.reqId && onApprove(part.reqId, "deny")} className="ml-auto rounded-md border border-border/40 px-3 py-1.5 text-[11.5px] text-destructive transition-colors hover:border-destructive/40">Deny</button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run; verify pass**

Run: `cd src/web && npx vitest run tests/computer-use/permission-card.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add src/web/src/components/computer-use/permission-card.tsx src/web/tests/computer-use/permission-card.test.tsx
git commit -m "feat(computer-use): extract token-themed PermissionCard"
```

---

## Task 6: `activity-timeline.tsx` (with test)

**Files:**
- Create: `src/components/computer-use/activity-timeline.tsx`
- Test: `src/web/tests/computer-use/activity-timeline.test.tsx`

Reference mockup section: the right-hand `.activity` panel (header, `.task`, `.reason`, `.step`/`.rail`/`.thumb`, `.perm`).

- [ ] **Step 1: Write the failing test**

```tsx
// tests/computer-use/activity-timeline.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ActivityTimeline } from "@/components/computer-use/activity-timeline";
import type { ChatMsg } from "@/lib/computer-use/timeline";

const thread: ChatMsg[] = [
  { role: "user", parts: [{ kind: "text", text: "Open Firefox" }] },
  { role: "assistant", parts: [
    { kind: "text", text: "I'll launch Firefox." },
    { kind: "action", text: "Clicked Firefox", ts: new Date(2026, 0, 1, 14, 32, 2).getTime() },
  ] },
];

describe("ActivityTimeline", () => {
  it("renders the task, reasoning, and a timestamped step", () => {
    render(<ActivityTimeline thread={thread} running={false} elapsedMs={0} ready onApprove={() => {}} onRunExample={() => {}} />);
    expect(screen.getByText("Open Firefox")).toBeTruthy();
    expect(screen.getByText("I'll launch Firefox.")).toBeTruthy();
    expect(screen.getByText("Clicked Firefox")).toBeTruthy();
    expect(screen.getByText("14:32:02")).toBeTruthy();
  });
  it("shows examples and runs one when empty + ready", () => {
    const onRunExample = vi.fn();
    render(<ActivityTimeline thread={[]} running={false} elapsedMs={0} ready onApprove={() => {}} onRunExample={onRunExample} />);
    fireEvent.click(screen.getByText("Take a screenshot and tell me what's open"));
    expect(onRunExample).toHaveBeenCalledWith("Take a screenshot and tell me what's open");
  });
});
```

- [ ] **Step 2: Run; verify it fails.** Run: `cd src/web && npx vitest run tests/computer-use/activity-timeline.test.tsx` — module not found.

- [ ] **Step 3: Implement**

```tsx
// src/components/computer-use/activity-timeline.tsx
"use client";
import { Loader2, Check } from "lucide-react";
import { Markdown } from "@/components/markdown/markdown";
import { PermissionCard } from "./permission-card";
import { type ChatMsg, type Part, formatStepTime, formatElapsed } from "@/lib/computer-use/timeline";

const EXAMPLES = [
  "Take a screenshot and tell me what's open",
  "Open Firefox and go to news.ycombinator.com",
  "Open the file manager and list my home folder",
];

export function ActivityTimeline({
  thread, running, elapsedMs, ready, onApprove, onRunExample,
}: {
  thread: ChatMsg[];
  running: boolean;
  elapsedMs: number;
  ready: boolean;
  onApprove: (reqId: string, decision: "once" | "session" | "deny") => void;
  onRunExample: (ex: string) => void;
}) {
  const stepCount = thread.reduce((n, m) => n + m.parts.filter((p) => p.kind === "action").length, 0);
  const lastAssistant = [...thread].reverse().find((m) => m.role === "assistant");
  const showTrailingWork = running && lastAssistant && !lastAssistant.parts.some((p) => p.kind === "done");

  return (
    <aside className="flex w-[404px] shrink-0 flex-col border-l border-border/40 bg-card/30">
      <div className="flex h-[46px] shrink-0 items-center gap-2 border-b border-border/40 px-4">
        <span className="text-[13px] font-semibold">Activity</span>
        {running && (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-primary/10 px-2 py-0.5 text-[11px] text-primary">
            <span className="size-1.5 animate-pulse rounded-full bg-primary" /> Working
          </span>
        )}
        {stepCount > 0 && (
          <span className="ml-auto text-[11px] tabular-nums text-muted-foreground">{stepCount} steps · {formatElapsed(elapsedMs)}</span>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        {thread.length === 0 ? (
          <div className="pt-4">
            <p className="text-center text-xs text-muted-foreground">
              Tell Jarvis what to do on the desktop. It watches the screen and works step by step — take control any time for logins or captchas.
            </p>
            <div className="mt-5 space-y-1.5">
              {EXAMPLES.map((ex) => (
                <button key={ex} onClick={() => onRunExample(ex)} disabled={!ready}
                  className="block w-full rounded-lg border border-border/50 bg-card/40 px-3 py-2 text-left text-xs text-foreground/90 transition-colors hover:border-primary/40 hover:bg-card disabled:opacity-40">
                  {ex}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div>
            {thread.map((m, i) =>
              m.role === "user" ? (
                <div key={i} className="mb-4 rounded-xl border border-border/40 bg-card p-3">
                  <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-primary">Task</div>
                  <div className="text-[13.5px] leading-snug">{m.parts[0]?.text}</div>
                </div>
              ) : (
                <div key={i}>{m.parts.map((p, j) => <Entry key={j} part={p} onApprove={onApprove} />)}</div>
              ),
            )}
            {showTrailingWork && (
              <div className="flex items-center gap-2.5 pb-2 text-[12px] text-muted-foreground">
                <Loader2 className="size-3.5 animate-spin text-primary" /> Working…
              </div>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}

function Entry({ part, onApprove }: { part: Part; onApprove: (reqId: string, decision: "once" | "session" | "deny") => void }) {
  if (part.kind === "text") return <div className="mb-4 border-l-2 border-border/40 pl-3 text-[12.5px] leading-relaxed text-muted-foreground"><Markdown content={part.text} /></div>;
  if (part.kind === "permission") return <div className="mb-4"><PermissionCard part={part} onApprove={onApprove} /></div>;
  if (part.kind === "done") return <div className="mb-4 flex items-center gap-2 text-[12px] text-emerald-500"><Check className="size-3.5" /> {part.text}</div>;
  if (part.kind === "blocked") return <div className="mb-4 rounded-md bg-destructive/10 px-2.5 py-1.5 text-[12px] text-destructive">⛔ {part.text}</div>;
  if (part.kind === "error") return <div className="mb-4 rounded-lg bg-destructive/10 px-3 py-2 text-[13px] text-destructive">{part.text}</div>;
  // action → step row with rail + status + optional thumb + timestamp
  return (
    <div className="mb-4 flex gap-3">
      <div className="flex flex-col items-center">
        <div className="grid size-[22px] place-items-center rounded-full border border-emerald-500/40 bg-emerald-500/15 text-emerald-500"><Check className="size-3" /></div>
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-[13px] leading-snug">{part.text}</div>
        {part.ts ? <div className="mt-0.5 text-[10.5px] tabular-nums text-muted-foreground/70">{formatStepTime(part.ts)}</div> : null}
      </div>
      {part.thumb ? <img src={part.thumb} alt="" className="mt-0.5 h-10 w-16 shrink-0 rounded-md border border-border/40 object-cover" /> : null}
    </div>
  );
}
```

- [ ] **Step 4: Run; verify pass.** Run: `cd src/web && npx vitest run tests/computer-use/activity-timeline.test.tsx` — PASS.

- [ ] **Step 5: Commit**
```bash
git add src/web/src/components/computer-use/activity-timeline.tsx src/web/tests/computer-use/activity-timeline.test.tsx
git commit -m "feat(computer-use): auditable activity timeline component"
```

---

## Task 7: `desktop-stage.tsx`

**Files:**
- Create: `src/components/computer-use/desktop-stage.tsx`
- Test: `src/web/tests/computer-use/desktop-stage.test.tsx`

Reference mockup: the `.desktop` / `.frame` / `.chrome` / `.overlay` block. Props carry the three states + takeover.

- [ ] **Step 1: Write the failing test**

```tsx
// tests/computer-use/desktop-stage.test.tsx
import { describe, it, expect } from "vitest";
import { createRef } from "react";
import { render, screen } from "@testing-library/react";
import { DesktopStage } from "@/components/computer-use/desktop-stage";
import type { NoVNCHandle } from "@/components/computer-use/novnc-view";

const base = {
  novncRef: createRef<NoVNCHandle>(),
  takeover: false, running: false,
  onTakeControl: () => {}, onGiveControl: () => {}, onConnect: () => {}, onRecheck: () => {}, onVncState: () => {},
};

describe("DesktopStage", () => {
  it("shows the services checklist when not ready", () => {
    render(<DesktopStage {...base} status={{ ready: false, streamUp: false, sidecarUp: true, wsUrl: "", password: null, hint: "run the stream" }} connected />);
    expect(screen.getByText(/Desktop stream not ready/)).toBeTruthy();
    expect(screen.getByText(/computer-use sidecar/)).toBeTruthy();
  });
  it("shows a reconnect card when disconnected", () => {
    render(<DesktopStage {...base} status={{ ready: true, streamUp: true, sidecarUp: true, wsUrl: "ws://x", password: "p", hint: null }} connected={false} />);
    expect(screen.getByText(/Disconnected from the desktop/)).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run; verify it fails.** `cd src/web && npx vitest run tests/computer-use/desktop-stage.test.tsx` — module not found.

- [ ] **Step 3: Implement** — define the `Status` type here (shared with page) and render the framed stage. Port the chrome/overlay markup from the mockup using tokens.

```tsx
// src/components/computer-use/desktop-stage.tsx
"use client";
import { forwardRef, type RefObject } from "react";
import { Hand, Plug, RotateCw, Loader2 } from "lucide-react";
import { NoVNCView, type NoVNCHandle } from "./novnc-view";

export type Status = {
  ready: boolean; streamUp: boolean; sidecarUp: boolean;
  providers?: Record<string, boolean>;
  wsUrl: string; password: string | null; hint: string | null;
};

export function DesktopStage({
  status, connected, takeover, running, novncRef,
  onTakeControl, onGiveControl, onConnect, onRecheck, onVncState,
}: {
  status: Status | null;
  connected: boolean;
  takeover: boolean;
  running: boolean;
  novncRef: RefObject<NoVNCHandle | null>;
  onTakeControl: () => void;
  onGiveControl: () => void;
  onConnect: () => void;
  onRecheck: () => void;
  onVncState: (s: "connecting" | "connected" | "disconnected") => void;
}) {
  return (
    <section className="min-w-0 flex-1 p-3.5">
      <div className={`flex h-full flex-col overflow-hidden rounded-2xl border bg-background shadow-2xl transition-colors ${takeover ? "border-primary" : "border-border/60"}`}>
        <div className="flex h-[34px] shrink-0 items-center gap-2.5 border-b border-border/40 bg-card/40 px-3 text-[11.5px] text-muted-foreground">
          <span className="flex gap-1.5">{[0, 1, 2].map((i) => <span key={i} className="size-2 rounded-full bg-muted-foreground/30" />)}</span>
          <span>Live desktop</span>
        </div>
        <div className="relative flex-1 bg-black/40">
          {status?.ready && status.password && connected ? (
            <>
              <NoVNCView ref={novncRef} wsUrl={status.wsUrl} password={status.password} viewOnly={!takeover} onState={onVncState} className="h-full w-full" />
              <div className="pointer-events-auto absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-2.5 rounded-full border border-border/60 bg-background/80 px-3.5 py-1.5 shadow-xl backdrop-blur">
                {takeover ? (
                  <>
                    <span className="text-[11.5px] text-primary">You're in control</span>
                    <button onClick={onGiveControl} className="rounded-full bg-primary px-3 py-1 text-[11.5px] font-medium text-primary-foreground">Give control</button>
                  </>
                ) : (
                  <>
                    <span className="inline-flex items-center gap-1.5 text-[11.5px] text-muted-foreground">
                      {running ? <><span className="size-2 animate-pulse rounded-full bg-primary" />Jarvis is working</> : <>Idle</>}
                    </span>
                    <button onClick={onTakeControl} className="inline-flex items-center gap-1.5 rounded-full bg-primary px-3 py-1 text-[11.5px] font-medium text-primary-foreground"><Hand className="size-3" />Take control</button>
                  </>
                )}
              </div>
            </>
          ) : status?.ready && !connected ? (
            <Centered>
              <div className="text-muted-foreground">Disconnected from the desktop.</div>
              <StageBtn onClick={onConnect}><Plug className="size-3.5" /> Connect</StageBtn>
            </Centered>
          ) : (
            <Centered>
              <div className="font-medium text-foreground">Desktop stream not ready</div>
              <ul className="space-y-1 text-xs text-muted-foreground">
                <li className={status?.streamUp ? "text-emerald-500" : ""}>{status?.streamUp ? "✓" : "•"} VNC stream (:6080)</li>
                <li className={status?.sidecarUp ? "text-emerald-500" : ""}>{status?.sidecarUp ? "✓" : "•"} computer-use sidecar (:8771)</li>
              </ul>
              {status?.hint && <pre className="max-w-md overflow-x-auto rounded-md border border-border/60 bg-card/40 p-2 text-[10.5px] leading-5 text-muted-foreground">{status.hint}</pre>}
              <StageBtn onClick={onRecheck}><RotateCw className="size-3.5" /> Re-check</StageBtn>
            </Centered>
          )}
        </div>
      </div>
    </section>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex h-full items-center justify-center"><div className="flex max-w-md flex-col items-center gap-3 rounded-xl border border-border/60 bg-card/40 p-6 text-center text-sm">{children}</div></div>;
}
function StageBtn({ children, onClick }: { children: React.ReactNode; onClick: () => void }) {
  return <button onClick={onClick} className="inline-flex items-center gap-1.5 rounded-lg border border-border/60 bg-card px-3 py-1.5 text-[12px] transition-colors hover:border-border">{children}</button>;
}
```

- [ ] **Step 4: Run; verify pass.** `cd src/web && npx vitest run tests/computer-use/desktop-stage.test.tsx` — PASS.

- [ ] **Step 5: Commit**
```bash
git add src/web/src/components/computer-use/desktop-stage.tsx src/web/tests/computer-use/desktop-stage.test.tsx
git commit -m "feat(computer-use): framed desktop stage with control overlay + states"
```

---

## Task 8: `app-bar.tsx`

**Files:**
- Create: `src/components/computer-use/app-bar.tsx`
- Test: `src/web/tests/computer-use/app-bar.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// tests/computer-use/app-bar.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CuAppBar } from "@/components/computer-use/app-bar";

const base = {
  connStatus: "connected" as const, sessionId: "9f3a0000c12", supervised: true, takeover: false,
  connected: true, running: false, hasThread: true,
  onToggleMode: () => {}, onToggleTakeover: () => {}, onToggleConnected: () => {}, onNewChat: () => {}, onStop: () => {}, onRefresh: () => {},
};

describe("CuAppBar", () => {
  it("toggles mode", () => {
    const onToggleMode = vi.fn();
    render(<CuAppBar {...base} onToggleMode={onToggleMode} />);
    fireEvent.click(screen.getByRole("radio", { name: /Auto/ }));
    expect(onToggleMode).toHaveBeenCalled();
  });
  it("shows Stop while running", () => {
    render(<CuAppBar {...base} running />);
    expect(screen.getByTitle("Stop the agent")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run; verify it fails.** `cd src/web && npx vitest run tests/computer-use/app-bar.test.tsx` — module not found.

- [ ] **Step 3: Implement** — segmented mode = radiogroup; overflow menu holds Connect/New/Refresh.

```tsx
// src/components/computer-use/app-bar.tsx
"use client";
import { Monitor, ShieldCheck, Hand, Square, MoreVertical, Plug, Unplug, RotateCcw, RotateCw } from "lucide-react";
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from "@/components/ui/dropdown-menu";

export type ConnStatus = "connected" | "connecting" | "disconnected" | "offline";

export function CuAppBar({
  connStatus, sessionId, supervised, takeover, connected, running, hasThread,
  onToggleMode, onToggleTakeover, onToggleConnected, onNewChat, onStop, onRefresh,
}: {
  connStatus: ConnStatus; sessionId: string; supervised: boolean; takeover: boolean;
  connected: boolean; running: boolean; hasThread: boolean;
  onToggleMode: () => void; onToggleTakeover: () => void; onToggleConnected: () => void;
  onNewChat: () => void; onStop: () => void; onRefresh: () => void;
}) {
  const dot = connStatus === "connected" ? "bg-emerald-500" : connStatus === "connecting" ? "bg-amber-500" : "bg-muted-foreground";
  return (
    <header className="flex h-[52px] shrink-0 items-center gap-3 border-b border-border/40 bg-card/30 px-4">
      <div className="flex items-center gap-2">
        <span className="grid size-[26px] place-items-center rounded-lg bg-primary/10 text-primary"><Monitor className="size-4" /></span>
        <span className="text-[14.5px] font-medium tracking-tight">Computer Use</span>
      </div>
      <span className="inline-flex items-center gap-1.5 rounded-full border border-border/40 bg-card px-2.5 py-0.5 text-[11.5px] text-muted-foreground">
        <span className={`size-1.5 rounded-full ${dot}`} />{connStatus}
      </span>
      <span className="hidden font-mono text-[10.5px] text-muted-foreground/80 sm:inline">session · {sessionId.slice(0, 4)}…{sessionId.slice(-3)}</span>

      <div className="flex-1" />

      <div role="radiogroup" aria-label="Approval mode" className="flex gap-0.5 rounded-lg border border-border/40 bg-card/40 p-0.5">
        <button role="radio" aria-checked={supervised} aria-label="Supervised" onClick={() => { if (!supervised) onToggleMode(); }}
          className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11.5px] transition-colors ${supervised ? "bg-primary/15 font-medium text-primary" : "text-muted-foreground"}`}>
          <ShieldCheck className="size-3.5" />Supervised
        </button>
        <button role="radio" aria-checked={!supervised} aria-label="Auto" onClick={() => { if (supervised) onToggleMode(); }}
          className={`rounded-md px-2.5 py-1 text-[11.5px] transition-colors ${!supervised ? "bg-primary/15 font-medium text-primary" : "text-muted-foreground"}`}>
          Auto
        </button>
      </div>

      {connected && (
        <button onClick={onToggleTakeover} title={takeover ? "Give control back to Jarvis" : "Take control of the desktop"}
          className={`inline-flex h-[30px] items-center gap-1.5 rounded-lg border px-3 text-[12px] transition-colors ${takeover ? "border-primary/60 bg-primary/10 text-primary" : "border-border/40 bg-card hover:border-border"}`}>
          <Hand className="size-3.5" />{takeover ? "Give control" : "Take control"}
        </button>
      )}

      {running && (
        <button onClick={onStop} title="Stop the agent" className="inline-flex h-[30px] items-center gap-1.5 rounded-lg border border-destructive/40 bg-destructive/10 px-3 text-[12px] text-destructive transition-colors hover:border-destructive/60">
          <Square className="size-3.5" />Stop
        </button>
      )}

      <DropdownMenu>
        <DropdownMenuTrigger render={<button title="More" className="grid size-[30px] place-items-center rounded-lg border border-border/40 bg-card text-muted-foreground transition-colors hover:border-border" />}>
          <MoreVertical className="size-4" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-44">
          <DropdownMenuItem onClick={onToggleConnected}>{connected ? <><Unplug className="size-4" /> Disconnect</> : <><Plug className="size-4" /> Connect</>}</DropdownMenuItem>
          {hasThread && <DropdownMenuItem onClick={onNewChat}><RotateCcw className="size-4" /> New session</DropdownMenuItem>}
          <DropdownMenuItem onClick={onRefresh}><RotateCw className="size-4" /> Refresh</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  );
}
```

- [ ] **Step 4: Run; verify pass.** `cd src/web && npx vitest run tests/computer-use/app-bar.test.tsx` — PASS.

- [ ] **Step 5: Commit**
```bash
git add src/web/src/components/computer-use/app-bar.tsx src/web/tests/computer-use/app-bar.test.tsx
git commit -m "feat(computer-use): mission-control app bar (segmented mode, overflow)"
```

---

## Task 9: `command-bar.tsx` (with test)

**Files:**
- Create: `src/components/computer-use/command-bar.tsx`
- Test: `src/web/tests/computer-use/command-bar.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// tests/computer-use/command-bar.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CommandBar } from "@/components/computer-use/command-bar";

const base = { value: "", onChange: () => {}, onSubmit: vi.fn(), running: false, disabled: false, model: "claude-sonnet-4-6", setModel: () => {}, placeholder: "Tell Jarvis…" };

describe("CommandBar", () => {
  it("submits on Enter, not Shift+Enter", () => {
    const onSubmit = vi.fn();
    render(<CommandBar {...base} value="do it" onSubmit={onSubmit} />);
    const ta = screen.getByPlaceholderText("Tell Jarvis…");
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: true });
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run; verify it fails.** `cd src/web && npx vitest run tests/computer-use/command-bar.test.tsx` — module not found.

- [ ] **Step 3: Implement**

```tsx
// src/components/computer-use/command-bar.tsx
"use client";
import { CornerDownLeft, Loader2 } from "lucide-react";
import { ModelPicker } from "./model-picker";

export function CommandBar({
  value, onChange, onSubmit, running, disabled, model, setModel, providers, placeholder,
}: {
  value: string; onChange: (v: string) => void; onSubmit: () => void;
  running: boolean; disabled: boolean; model: string; setModel: (m: string) => void;
  providers?: Record<string, boolean>; placeholder: string;
}) {
  return (
    <footer className="shrink-0 border-t border-border/40 bg-card/30 px-4 pb-3.5 pt-3">
      <div className="flex items-center gap-2.5 rounded-2xl border border-border/60 bg-card px-2 py-2 pl-3.5 ring-4 ring-primary/5 focus-within:border-primary/40">
        <ModelPicker model={model} setModel={setModel} disabled={running} providers={providers} />
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSubmit(); } }}
          rows={1}
          placeholder={placeholder}
          disabled={disabled}
          className="max-h-32 flex-1 resize-none self-center bg-transparent text-[14px] text-foreground outline-none placeholder:text-muted-foreground/70 disabled:opacity-50"
        />
        <button onClick={onSubmit} disabled={disabled || !value.trim()} title="Send"
          className="grid size-9 shrink-0 place-items-center rounded-xl bg-primary text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40">
          {running ? <Loader2 className="size-4 animate-spin" /> : <CornerDownLeft className="size-4" />}
        </button>
      </div>
      <div className="mt-2 flex gap-3.5 px-1 text-[11px] text-muted-foreground/70">
        <span><kbd className="rounded border border-border/40 bg-muted px-1 py-px font-mono text-[10px]">Enter</kbd> send</span>
        <span><kbd className="rounded border border-border/40 bg-muted px-1 py-px font-mono text-[10px]">⇧ Enter</kbd> newline</span>
      </div>
    </footer>
  );
}
```

- [ ] **Step 4: Run; verify pass.** `cd src/web && npx vitest run tests/computer-use/command-bar.test.tsx` — PASS.

- [ ] **Step 5: Commit**
```bash
git add src/web/src/components/computer-use/command-bar.tsx src/web/tests/computer-use/command-bar.test.tsx
git commit -m "feat(computer-use): full-width command bar"
```

---

## Task 10: Rewrite `page.tsx` as the orchestrator

**Files:**
- Rewrite: `src/app/(app)/computer-use/page.tsx`

This task wires the components together. It owns all state, the SSE read loop (now using `eventToPart` and capturing a canvas thumbnail on `action` frames via the stage's `novncRef`), an elapsed-time ticker, and the region layout (app bar → body[stage + timeline] → command bar).

- [ ] **Step 1: Replace the whole file**

```tsx
// src/app/(app)/computer-use/page.tsx
"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { CuAppBar, type ConnStatus } from "@/components/computer-use/app-bar";
import { DesktopStage, type Status } from "@/components/computer-use/desktop-stage";
import { ActivityTimeline } from "@/components/computer-use/activity-timeline";
import { CommandBar } from "@/components/computer-use/command-bar";
import { CU_MODELS } from "@/components/computer-use/model-picker";
import type { NoVNCHandle } from "@/components/computer-use/novnc-view";
import { eventToPart, type ChatMsg, type Part, type LoopEvent } from "@/lib/computer-use/timeline";

const newSessionId = () => (typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : String(Date.now()));

export default function ComputerUsePage() {
  const [status, setStatus] = useState<Status | null>(null);
  const [vnc, setVnc] = useState<"connecting" | "connected" | "disconnected">("connecting");
  const [task, setTask] = useState("");
  const [running, setRunning] = useState(false);
  const [takeover, setTakeover] = useState(false);
  const [connected, setConnected] = useState(true);
  const [supervised, setSupervised] = useState(true);
  const [model, setModel] = useState<string>(CU_MODELS[0].id);
  const [thread, setThread] = useState<ChatMsg[]>([]);
  const [sessionId, setSessionId] = useState(newSessionId);
  const [runStart, setRunStart] = useState<number | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const abortRef = useRef<AbortController | null>(null);
  const novncRef = useRef<NoVNCHandle | null>(null);

  const refreshStatus = useCallback(async () => {
    try {
      const r = await fetch("/api/computer-use", { cache: "no-store" });
      setStatus((await r.json()) as Status);
    } catch {
      setStatus({ ready: false, streamUp: false, sidecarUp: false, wsUrl: "", password: null, hint: "Could not reach the web API." });
    }
  }, []);
  useEffect(() => { void refreshStatus(); }, [refreshStatus]);

  // Elapsed-time ticker while running.
  useEffect(() => {
    if (!running || runStart == null) return;
    const id = setInterval(() => setElapsedMs(Date.now() - runStart), 500);
    return () => clearInterval(id);
  }, [running, runStart]);

  const appendPart = useCallback((part: Part) => {
    setThread((prev) => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      if (last.role !== "assistant") return prev;
      const copy = prev.slice();
      copy[copy.length - 1] = { ...last, parts: [...last.parts, part] };
      return copy;
    });
  }, []);

  const stop = useCallback(() => { abortRef.current?.abort(); abortRef.current = null; setRunning(false); }, []);
  const takeControl = useCallback(() => { stop(); setTakeover(true); }, [stop]);
  const newChat = useCallback(() => { stop(); setThread([]); setSessionId(newSessionId()); }, [stop]);
  const disconnect = useCallback(() => { stop(); setConnected(false); setVnc("disconnected"); }, [stop]);
  const connect = useCallback(() => { setVnc("connecting"); setConnected(true); }, []);

  const resolvePermission = useCallback(async (reqId: string, decision: "once" | "session" | "deny") => {
    setThread((prev) => prev.map((m) => ({ ...m, parts: m.parts.map((p) => (p.kind === "permission" && p.reqId === reqId ? { ...p, resolved: decision } : p)) })));
    try {
      await fetch("/api/computer-use/approve", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ request_id: reqId, decision }) });
    } catch { /* loop times out → denies */ }
  }, []);

  const runTask = useCallback(async (override?: string) => {
    const t = (override ?? task).trim();
    if (!t || running || !status?.ready) return;
    setTakeover(false); setRunning(true); setTask(""); setRunStart(Date.now()); setElapsedMs(0);
    setThread((prev) => [...prev, { role: "user", parts: [{ kind: "text", text: t }] }, { role: "assistant", parts: [] }]);
    const ctrl = new AbortController(); abortRef.current = ctrl;
    try {
      const res = await fetch("/api/computer-use", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ task: t, session_id: sessionId, supervised, model }), signal: ctrl.signal });
      if (!res.body) throw new Error("no stream");
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const frame = buf.slice(0, idx); buf = buf.slice(idx + 2);
          const dataLine = frame.split("\n").find((l) => l.startsWith("data:"));
          if (!dataLine) continue;
          let evt: LoopEvent;
          try { evt = JSON.parse(dataLine.slice(5).trim()) as LoopEvent; } catch { continue; }
          const part = eventToPart(evt, Date.now());
          if (!part) continue;
          if (part.kind === "action") part.thumb = novncRef.current?.snapshot() ?? undefined;
          appendPart(part);
        }
      }
    } catch (err) {
      if (!ctrl.signal.aborted) appendPart({ kind: "error", text: err instanceof Error ? err.message : "run failed", ts: Date.now() });
    } finally {
      if (abortRef.current === ctrl) abortRef.current = null;
      setRunning(false);
    }
  }, [task, running, status?.ready, sessionId, supervised, model, appendPart]);

  const connStatus: ConnStatus = !status?.ready ? "offline" : connected ? vnc : "disconnected";
  const placeholder = running ? "Working… press Stop to interrupt" : takeover ? "You're in control of the desktop" : "Tell Jarvis what to do on the desktop…";

  return (
    <div className="flex h-full flex-col">
      <CuAppBar
        connStatus={connStatus} sessionId={sessionId} supervised={supervised} takeover={takeover}
        connected={connected} running={running} hasThread={thread.length > 0}
        onToggleMode={() => setSupervised((v) => !v)}
        onToggleTakeover={takeover ? () => setTakeover(false) : takeControl}
        onToggleConnected={connected ? disconnect : connect}
        onNewChat={newChat} onStop={stop} onRefresh={() => void refreshStatus()}
      />
      <div className="flex min-h-0 flex-1">
        <DesktopStage
          status={status} connected={connected} takeover={takeover} running={running} novncRef={novncRef}
          onTakeControl={takeControl} onGiveControl={() => setTakeover(false)} onConnect={connect} onRecheck={() => void refreshStatus()} onVncState={setVnc}
        />
        <ActivityTimeline
          thread={thread} running={running} elapsedMs={elapsedMs} ready={!!status?.ready}
          onApprove={resolvePermission} onRunExample={(ex) => void runTask(ex)}
        />
      </div>
      <CommandBar
        value={task} onChange={setTask} onSubmit={() => void runTask()} running={running}
        disabled={!status?.ready || running || takeover} model={model} setModel={setModel}
        providers={status?.providers} placeholder={placeholder}
      />
    </div>
  );
}
```

- [ ] **Step 2: Typecheck the whole touched set**

Run: `cd src/web && npx tsc --noEmit 2>&1 | grep -E "computer-use|timeline" || echo "OK — no errors in touched files"`
Expected: `OK — no errors in touched files`.

- [ ] **Step 3: Commit**
```bash
git add src/web/src/app/\(app\)/computer-use/page.tsx
git commit -m "feat(computer-use): wire mission-control layout in the page orchestrator"
```

---

## Task 11: Full verification

- [ ] **Step 1: Run the computer-use test suite**

Run: `cd src/web && npx vitest run tests/computer-use/`
Expected: all PASS (timeline, novnc-snapshot, permission-card, activity-timeline, desktop-stage, app-bar, command-bar).

- [ ] **Step 2: Typecheck + build**

Run: `cd src/web && npx tsc --noEmit && npm run build`
Expected: tsc clean; build compiles `/computer-use`.

- [ ] **Step 3: Manual walkthrough** (dev server already runs at `127.0.0.1:3000`)

Visit `http://127.0.0.1:3000/computer-use` and confirm:
- not-ready → services checklist renders in the framed stage.
- connected + idle → live desktop + floating "Idle/Take control" overlay; app bar shows `● connected`, segmented Supervised/Auto, `⋯` menu.
- run a task → user "Task" block + reasoning + step rows with timestamps + a thumbnail per action; trailing "Working…" row; Stop appears in the app bar.
- a permission prompt → inline card; Approve/For session/Deny resolve in place.
- Take control → frame border + overlay go cyan, "Give control" works.
- light theme (if toggle available) → colors still correct (tokens, not literals).

- [ ] **Step 4: Final commit (if any manual fixes were needed)**
```bash
git add src/web/src/app/\(app\)/computer-use/page.tsx src/web/src/components/computer-use/
git commit -m "fix(computer-use): manual-pass polish for mission-control"
```

---

## Self-review notes (author)

- **Spec coverage:** app bar (T8), desktop stage + overlay + states (T7), activity timeline + thumbnails + permission + honest trailing-work row (T6, T2, T10), command bar (T9), decomposition (T4–T9), client-side thumbnails via canvas (T3, T10), tokens not literals (token map + every component). ✓
- **No new backend events** — `eventToPart` consumes only the existing frames; `denied`/`ping`/`start` produce no row. ✓
- **Type consistency:** `Part`/`ChatMsg`/`LoopEvent` defined once in `timeline.ts` and imported everywhere; `NoVNCHandle` from `novnc-view`; `Status` from `desktop-stage`; `ConnStatus` from `app-bar`. ✓
- **Deferred (out of scope, per spec §10):** structured action params / true per-step status / replay (need sidecar changes).
