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
Claude Code interface (user-provided screenshot) — exact replica adapted for Jarvis CLI.

### Layout

```
┌─────────────────────────────────────────────────────┐
│ [Sidebar]            │ [Main area]                   │
│                      │                               │
│  "Jarvis CLI"        │  ✻ What's up next, Ulrich?   │
│  ─────────────       │                               │
│  + New session       │   (empty — sessions go in     │
│  ⟳ Routines         │    sidebar recents)            │
│  ≡ Customize         │                               │
│  ∨ More              │                               │
│                      │                               │
│  📌 Pinned           │                               │
│  Drag to pin         │                               │
│                      │                               │
│  🕐 Recents          │                               │
│  Set up Tailscale    │                               │
│  Review codebase     │                               │
│                      │                               │
│  ───────────────     │                               │
│  [UA] Ulrich Ando    │  ┌────────────────────────┐  │
│  [⊞] [←]            │  │ Default  + Select mach…│  │
│                      │  ├────────────────────────┤  │
│                      │  │ Describe a task or ask…│  │
│                      │  ├────────────────────────┤  │
│                      │  │Accept edits [+][◎][∨]  │  │
│                      │  │              Opus 4.7 1M│  │
│                      │  └────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

### Sidebar (Code page — its own layout, not the app sidebar)

Exact Claude Code sidebar structure:
- **Top**: "Jarvis CLI" branding + "Research preview" badge (or omit badge initially)
- **Nav items**: `+ New session`, `⟳ Routines` (placeholder), `≡ Customize` (placeholder), `∨ More`
- **Pinned section**: "Drag to pin" empty state
- **Recents section**: list of recent session titles (from server)
- **Footer**: user avatar + name, two icon buttons (layout toggle + back/collapse)

### Main area

- **Greeting**: `✻ What's up next, {name}?` — centered, large, same font as homepage
- **Body**: empty space (sessions are in sidebar, not shown as cards in main)
- **Bottom composer** (three-row structure, exact Claude Code):
  1. **Context bar**: `Default` pill (active machine/context) + `+ Select machine…` pill
  2. **Input**: `Describe a task or ask a question` placeholder, full-width textarea
  3. **Toolbar bar**: left = `Accept edits` button + `+` + `◎` + `∨` dropdown; right = model name + token count + spinner

### Routing and navigation

- Route: `/code` — new Next.js page
- Sidebar nav: add `Code` item (between Chats and Workbench) with `Code2` icon, `href: "/code"`
- The Code page has its **own full-page layout** — it does NOT use the app shell's `<Sidebar>` or `<TopBar>`. It renders its own sidebar (matching Claude Code's structure) and hides the app sidebar automatically (same pattern as `/workbench`).

### Functionality scope (design only — not implemented yet)

The input, "Select machine", and session management are **UI shells** only. No backend wiring in this design phase. Clicking "New session" clears the input. "Select machine…" opens a placeholder modal. Sessions in recents are static placeholders.

### Files touched (Part 2)

| File | Change |
|---|---|
| `src/app/(app)/code/page.tsx` | New page — full Code page layout |
| `src/components/code/code-sidebar.tsx` | New — Claude Code-style sidebar |
| `src/components/code/code-composer.tsx` | New — three-row bottom composer |
| `src/components/layout/sidebar.tsx` | Add "Code" nav item |
| `src/app/(app)/layout.tsx` (or equivalent) | Suppress app sidebar on `/code` |

---

## What is NOT in scope

- Backend connection between the Code page and a remote Jarvis CLI machine
- "Routines" or "Customize" functionality on the Code page
- Machine registration / pairing flow
- "Accept edits" diff review functionality
- Any changes to the Workbench page
