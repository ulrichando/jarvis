# Homepage Unified Input + Code Page Design

**Date:** 2026-04-27
**Status:** Approved for implementation

---

## Overview

Two distinct UI changes:

1. **Homepage empty state** — replace the separate greeting / composer / chip-row stack with a single unified input area, and replace the chip pills with a 2×N grid of function cards. Clicking a card expands a task-suggestion panel below the composer (exact Claude.ai pattern).

2. **Code page (`/code`)** — a new dedicated route that replicates the Claude Code interface, adapted for remote Jarvis CLI sessions. Accessible via a new "Code" item in the left sidebar nav.

These two things are independent. The "Code" card on the homepage is a shortcut that shows coding task suggestions. The "Code" nav item is a full separate page.

---

## Part 1 — Homepage Redesign

### Reference
Claude.ai homepage (user-provided screenshots):
- Greeting centered above composer
- Single rounded composer box (textarea + bottom toolbar)
- Chip row below composer (Write · Learn · Code · Life stuff · Claude's choice)
- Clicking a chip (e.g. Code) → a card panel appears below the composer showing category-specific task prompts with a close button

### What changes

**Current structure** (in `chat.tsx` empty-state when `!embedded`):
```
<EmptyState />          ← greeting
<Composer />            ← input box
<Categories chips />    ← pill chips
```

**New structure:**
```
<EmptyState />          ← greeting (unchanged)
<Composer />            ← input box (unchanged)
<FunctionGrid />        ← NEW: replaces <Categories>
  [Write] [Learn] [Code] [Life stuff] [Claude's choice]
  ↓ clicking one card:
<TaskPanel category />  ← NEW: appears below grid, dismissable
  [task 1] [task 2] [task 3] [task 4] [task 5]
```

### FunctionGrid component

- Replaces `categories.tsx`
- Layout: `grid grid-cols-2 sm:grid-cols-3 gap-3 w-full max-w-2xl mx-auto` — fixed 2-column grid on mobile, 3-column on wider screens
- Each card:
  - Rounded rectangle (`rounded-2xl`), border, subtle background
  - Icon (top-left) + label (bold) + one-line description (muted)
  - Clicking sets `activeCategory` state in the parent
- Cards come from the same `chips` array in `provider-ux.ts` — no new data needed
- The existing chip `label`, `icon`, `prompt` fields are reused; the `prompt` becomes the default fill if no sub-task is picked

**Card set** (unchanged from current chips per provider — Groq default):
| Label | Icon | Prompt starter |
|---|---|---|
| Write | PenLine | "Help me write " |
| Learn | GraduationCap | "Teach me about " |
| Code | Code2 | "Write a function that " |
| Life stuff | Coffee | "Help me think through " |
| Claude's choice | Apple | "Ask me three questions…" |

### TaskPanel component

- Appears **below** the `FunctionGrid` when a card is active
- Exact Claude.ai pattern: rounded card with header row (`⟨/⟩ Code  ✕`) and a vertical list of task items
- Clicking a task item → calls `onPick(prompt)` (same as chip click today) → closes panel
- Clicking ✕ or clicking the same card again → closes panel
- Each provider's `Chip` entry gets an optional `tasks?: string[]` array added to `provider-ux.ts`

**Example tasks per card (Groq/default):**

| Card | Tasks |
|---|---|
| Write | Draft a blog post, Write a professional email, Edit my writing for clarity, Write a cover letter, Summarize this for me |
| Learn | Explain this concept simply, Teach me how X works, Quiz me on a topic, Compare A vs B |
| Code | Write a function that…, Debug my code, Create a script to…, Explain this code, Write tests for… |
| Life stuff | Help me make a decision, Think through a problem with me, Help me plan… |
| Claude's choice | Ask me three questions…, Surprise me, What should I work on? |

### Files touched (Part 1)

| File | Change |
|---|---|
| `src/components/chat/categories.tsx` | Replace with `function-grid.tsx` (or rename + rewrite) |
| `src/components/chat/task-panel.tsx` | New component |
| `src/components/chat/chat.tsx` | Swap `<Categories>` for `<FunctionGrid>` + `<TaskPanel>`, add `activeCategory` state |
| `src/lib/ai/provider-ux.ts` | Add `tasks?: string[]` to `Chip` type, populate for each provider |

---

## Part 2 — Code Page (`/code`)

### Reference
Claude Code screenshot provided by user — pixel-accurate replica, adapted for Jarvis CLI.

### Full-page layout

The `/code` route is a **full-page replacement** — the Jarvis app sidebar and topbar are hidden (same pattern as `/workbench`). The page renders its own two-column layout: a narrow sidebar on the left and a main area on the right.

```
┌──────────────────────────────────────────────────────────────┐
│ Jarvis CLI  [Research preview]              [□] [🔍]         │ ← topbar
├──────────────┬───────────────────────────────────────────────┤
│ + New session│ ✻ What's up next, Ulrich?                     │
│ ⟳ Routines  │                                               │
│ ≡ Customize  │  (large empty space)                          │
│ ∨ More       │                                               │
│              │                                               │
│ Pinned       │                                               │
│ 📌 Drag pin  │                                               │
│              │                                               │
│ Recents      │                                               │
│ ○ Session 1  │                                               │
│ ⚡ Session 2  │                                               │
│              │                                               │
│ (spacer)     │  ┌─────────────────────────────────────────┐  │
│              │  │ ⊙ Default   + Select machine…           │  │ ← context row
│              │  ├─────────────────────────────────────────┤  │
│ [UA] Ulrich  │  │ Describe a task or ask a question    [↵]│  │ ← input
│ [⊞]  [←]    │  ├─────────────────────────────────────────┤  │
│              │  │ Accept edits  [+] [◎] [∨]  Jarvis 4.7 1M│ │ ← toolbar
│              │  └─────────────────────────────────────────┘  │
└──────────────┴───────────────────────────────────────────────┘
```

### Topbar (full-width, above both columns)

Exact Claude Code topbar:
- **Left**: `Jarvis CLI` in bold (replaces "Claude Code"), clickable → goes to homepage
- **Center**: `Research preview` outlined badge pill (keep for now, matches reference exactly)
- **Right**: two icon buttons — `□` (new window / layout toggle) + `🔍` (search)

### Sidebar (~230px wide, dark background)

Exact Claude Code sidebar, item for item:

**Nav section (top):**
- `+ New session` — `Plus` icon, clicking clears the main input (UI only)
- `⟳ Routines` — `RotateCw` icon, placeholder (no action)
- `≡ Customize` — `SlidersHorizontal` icon, placeholder (no action)
- `∨ More` — `ChevronDown` icon, collapsed by default (no action)

**Pinned section:**
- Section label: `Pinned` (small, muted)
- Empty state: `📌 Drag to pin` — `Pin` icon + muted text

**Recents section:**
- Section label: `Recents` (small, muted)
- List of recent session items — each shows a small status icon + truncated session title
- Empty state: no recents message (omit if empty rather than showing placeholder text)

**Footer (pinned to bottom):**
- Left: user avatar circle (`UA` initials) + `Ulrich Ando` name
- Right: two icon buttons — `⊞` (layout grid toggle) + `←` (back / collapse)

### Main area

**Greeting (top-left, NOT centered):**
- `✻ What's up next, Ulrich?` — left-aligned, top of the main area, same ✻ spark and serif font as homepage greeting
- Below it: completely empty dark space

**Bottom composer (3-row, fixed to bottom of main area):**

Row 1 — Context bar (outside/above the input box):
- `⊙ Default` pill — active machine context (placeholder)
- `+ Select machine…` pill — opens placeholder modal on click (replaces Claude's "Select repo…")

Row 2 — Input:
- Full-width textarea, placeholder: `Describe a task or ask a question`
- Send/return icon `↵` on the far right

Row 3 — Toolbar:
- Left: `Accept edits` button (placeholder) + `+` icon button + `◎` icon button + `∨` dropdown button
- Right: model name + context size (e.g. `Jarvis 4.7  1M`) + loading spinner icon

### Routing and navigation

- Route: `/code` — new Next.js page under `src/app/(app)/code/page.tsx`
- App sidebar (`sidebar.tsx`): add `Code` nav item with `Code2` icon, `href: "/code"`, positioned between Chats and Workbench
- App sidebar suppress: same `pathname.startsWith("/code")` check used for `/workbench`
- App topbar suppress: same check — `TopBar` returns null on `/code`

### Functionality scope (UI shell only — no backend)

All interactive elements are shells:
- "New session" → clears the textarea
- "Select machine…" → opens an empty modal (no machine list yet)
- "Routines" / "Customize" / "More" → no-op clicks
- Recents → hardcoded placeholder sessions for now
- "Accept edits" / `+` / `◎` / `∨` → no-op

### Files touched (Part 2)

| File | Change |
|---|---|
| `src/app/(app)/code/page.tsx` | New — full Code page (own layout, suppresses app shell) |
| `src/components/code/code-sidebar.tsx` | New — left sidebar matching Claude Code exactly |
| `src/components/code/code-composer.tsx` | New — 3-row bottom composer (context bar + input + toolbar) |
| `src/components/layout/sidebar.tsx` | Add `Code` nav item between Chats and Workbench |
| `src/components/layout/topbar.tsx` | Suppress on `/code` (already suppressed on `/workbench`) |
| `src/components/layout/sidebar.tsx` | Suppress open-sidebar button on `/code` |

---

## What is NOT in scope

- Backend connection between the Code page and a remote Jarvis CLI machine
- "Routines" or "Customize" functionality on the Code page
- Machine registration / pairing flow
- "Accept edits" diff review functionality
- Any changes to the Workbench page
