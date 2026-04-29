# Design Tab Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the JARVIS `/design` tab from "generic chat that scaffolds Vite apps" into a Claude-Design-style canvas: chat → format-aware playbook → brand-aware HTML output → preview → PDF/HTML export.

**Architecture:** Five new modules under `src/web/src/lib/design/` (format catalog, brand storage, playbooks). Two new API routes (`/api/design/brand`, `/api/design/export`). Three new components (`format-selector`, `brand-panel`, `export-menu`). The existing chat route, message parser, workspace storage, and iframe preview are reused as-is — every new piece slots into seams already in the code.

**Tech Stack:** Next.js 16 (Turbopack), React 19, Tailwind 4, TypeScript 5, AI SDK 6, Playwright (new dep for PDF export). No new database tables.

**Testing approach:** This project has no test runner installed. v1 verifies via:
- `tsc --noEmit` and `eslint` after each task (no type/lint regressions)
- Manual smoke tests via curl / dev server / browser at gates (each task lists what to check)
- Final visual QA against the rubric in `docs/superpowers/specs/2026-04-29-design-tab-overhaul-design.md`

Adding vitest is explicitly out of scope for v1 — the test surface for this feature is mostly visual quality, which a runner cannot evaluate.

**Spec:** `docs/superpowers/specs/2026-04-29-design-tab-overhaul-design.md`

**Commit policy:** No `Co-Authored-By` trailers (per project convention). Frequent atomic commits — one per task minimum.

---

## Task 1: Add format catalog module

**Files:**
- Create: `src/web/src/lib/design/format.ts`

- [ ] **Step 1: Create the file**

```typescript
// src/web/src/lib/design/format.ts

export const FORMATS = ["slides", "prototype", "landing", "onepager", "infographic"] as const;
export type Format = (typeof FORMATS)[number];

export const DEFAULT_FORMAT: Format = "slides";

export const FORMAT_LABEL: Record<Format, string> = {
  slides: "Slides",
  prototype: "Prototype",
  landing: "Landing",
  onepager: "One-pager",
  infographic: "Infographic",
};

export const FORMAT_FILE: Record<Format, string> = {
  slides: "slides.html",
  prototype: "prototype.html",
  landing: "landing.html",
  onepager: "onepager.html",
  infographic: "infographic.html",
};

export type FontPairing = {
  id: string;
  display: { family: string; weights: string };
  body: { family: string; weights: string };
  bias: Format[];
};

// All entries are Google Fonts (no licensing cost, no self-hosting).
export const FONT_PAIRINGS: FontPairing[] = [
  {
    id: "editorial",
    display: { family: "Playfair Display", weights: "wght@500;700;900" },
    body: { family: "Inter", weights: "wght@400;500;600" },
    bias: ["infographic", "onepager"],
  },
  {
    id: "modern-sans",
    display: { family: "Bricolage Grotesque", weights: "wght@600;700;800" },
    body: { family: "IBM Plex Sans", weights: "wght@400;500;600" },
    bias: ["landing", "prototype"],
  },
  {
    id: "technical",
    display: { family: "Space Grotesk", weights: "wght@500;700" },
    body: { family: "JetBrains Mono", weights: "wght@400;500;700" },
    bias: ["slides", "prototype"],
  },
  {
    id: "serif-warm",
    display: { family: "Fraunces", weights: "opsz,wght@9..144,500;9..144,700" },
    body: { family: "Inter", weights: "wght@400;500;600" },
    bias: ["onepager", "landing"],
  },
  {
    id: "editorial-modern",
    display: { family: "Newsreader", weights: "wght@500;700" },
    body: { family: "Manrope", weights: "wght@400;500;600;700" },
    bias: ["slides", "onepager"],
  },
];

export function pickFontPairing(format: Format, seed?: number): FontPairing {
  const biased = FONT_PAIRINGS.filter((p) => p.bias.includes(format));
  const pool = biased.length > 0 ? biased : FONT_PAIRINGS;
  const idx = seed != null ? Math.abs(seed) % pool.length : Math.floor(Math.random() * pool.length);
  return pool[idx];
}

export function googleFontsUrl(p: FontPairing): string {
  const display = `family=${encodeURIComponent(p.display.family).replace(/%20/g, "+")}:${p.display.weights}`;
  const body = `family=${encodeURIComponent(p.body.family).replace(/%20/g, "+")}:${p.body.weights}`;
  return `https://fonts.googleapis.com/css2?${display}&${body}&display=swap`;
}
```

- [ ] **Step 2: Verify typecheck passes**

Run: `cd src/web && bunx tsc --noEmit`
Expected: no errors mentioning `format.ts`

- [ ] **Step 3: Commit**

```bash
git add src/web/src/lib/design/format.ts
git commit -m "design: format catalog + curated Google Fonts pairings"
```

---

## Task 2: Brand storage module

**Files:**
- Create: `src/web/src/lib/design/brand.ts`

- [ ] **Step 1: Create the file**

```typescript
// src/web/src/lib/design/brand.ts
import { promises as fs } from "node:fs";
import path from "node:path";
import { resolveWorkspacePath } from "@/lib/workspace/storage";

export type BrandColors = {
  bg: string;
  fg: string;
  accent: string;
  muted: string;
  supporting: string;
};

export type BrandFont = {
  family: string;
  googleFontsUrl?: string;
};

export type Brand = {
  version: 1;
  name: string;
  logoPath?: string; // relative to workspace root, e.g. ".jarvis/brand/logo.svg"
  colors: BrandColors;
  fonts: { display: BrandFont; body: BrandFont };
  voice?: string;
  references?: { path: string; note?: string }[];
};

const BRAND_DIR_REL = ".jarvis/brand";
const BRAND_FILE_REL = ".jarvis/brand.json";

async function workspaceRoot(workspaceId: string): Promise<string> {
  // resolveWorkspacePath returns the absolute path of a file or "" for root.
  return resolveWorkspacePath(workspaceId, "");
}

export async function getBrand(workspaceId: string): Promise<Brand | null> {
  const root = await workspaceRoot(workspaceId);
  const file = path.join(root, BRAND_FILE_REL);
  try {
    const buf = await fs.readFile(file, "utf8");
    const parsed = JSON.parse(buf);
    if (parsed?.version !== 1) return null;
    return parsed as Brand;
  } catch {
    return null;
  }
}

export async function putBrand(workspaceId: string, brand: Brand): Promise<void> {
  const root = await workspaceRoot(workspaceId);
  const dir = path.join(root, ".jarvis");
  await fs.mkdir(dir, { recursive: true });
  const file = path.join(root, BRAND_FILE_REL);
  await fs.writeFile(file, JSON.stringify(brand, null, 2), "utf8");
}

export async function putBrandAsset(
  workspaceId: string,
  filename: string,
  data: Buffer,
): Promise<string> {
  // Reject any path component that could escape the brand dir.
  const safe = path.basename(filename);
  if (safe !== filename || safe.startsWith(".")) {
    throw new Error("invalid asset filename");
  }
  const root = await workspaceRoot(workspaceId);
  const dir = path.join(root, BRAND_DIR_REL);
  await fs.mkdir(dir, { recursive: true });
  const dest = path.join(dir, safe);
  await fs.writeFile(dest, data);
  return path.join(BRAND_DIR_REL, safe); // workspace-relative path
}
```

- [ ] **Step 2: Verify `resolveWorkspacePath` exists with that signature**

Run: `grep -n "export.*resolveWorkspacePath" src/web/src/lib/workspace/storage.ts`
Expected: a single match with signature `(workspaceId: string, relPath: string) => Promise<string>` or similar.

If the helper does not exist or the signature differs, **before continuing**, read `src/web/src/lib/workspace/storage.ts` and either (a) call the existing helper that resolves a workspace's absolute root path, or (b) add a `resolveWorkspacePath(id, "")` wrapper there. Adapt the import in `brand.ts` to match.

- [ ] **Step 3: Verify typecheck passes**

Run: `cd src/web && bunx tsc --noEmit`
Expected: no errors mentioning `brand.ts`

- [ ] **Step 4: Commit**

```bash
git add src/web/src/lib/design/brand.ts
git commit -m "design: brand.json read/write + asset upload (path-safe)"
```

---

## Task 3: Format playbooks module

**Files:**
- Create: `src/web/src/lib/design/playbooks.ts`

- [ ] **Step 1: Create the file**

```typescript
// src/web/src/lib/design/playbooks.ts
import { type Format, FORMAT_FILE, type FontPairing, googleFontsUrl, pickFontPairing } from "./format";
import type { Brand } from "./brand";

export type PlaybookArgs = {
  format: Format;
  brand: Brand | null;
  workspaceName: string;
  cwd: string;
};

export function buildPlaybookPrompt({ format, brand, workspaceName, cwd }: PlaybookArgs): string {
  const pairing = brand ? null : pickFontPairing(format);
  return [
    designerHeader({ workspaceName, cwd }),
    formatBlock(format),
    brand ? brandBlock(brand) : pairingBlock(pairing!),
    sharedBaseBlock(),
    antiSlopBlock(),
    artifactRulesBlock(format),
    examplesBlock(format),
  ].join("\n\n");
}

function designerHeader({ workspaceName, cwd }: { workspaceName: string; cwd: string }): string {
  return `
You are now JARVIS in design mode. You are a designer working in HTML — not a programmer. The user is your manager. You ship single, self-contained HTML files that look like a thoughtful designer made them.

<design_context>
  Workspace: "${workspaceName}"
  Working directory: ${cwd}
  Files written here render in the live preview iframe.
  Output medium: ONE self-contained HTML file. No build step, no package.json, no dev server.
</design_context>`.trim();
}

function formatBlock(format: Format): string {
  const file = FORMAT_FILE[format];
  const map: Record<Format, string> = {
    slides: `
<format>
  Type: presentation deck.
  File path: "${file}"
  Canvas: 1920×1080, fixed aspect.
  Anatomy: cover slide, agenda (optional), 5–10 content slides, ending slide. Each slide is a \`<section class="slide">\` taking the full canvas.
  Navigation: arrow keys advance/retreat (left/right + up/down + space). Show a small "1 / N" indicator bottom-right.
  Per-slide layout: vary aggressively — full-bleed image slide, two-column slide, big-number slide, quote slide. Never 8 identical bullet slides in a row.
  Forbidden: 8-tile feature grid, "thank you" slide as the only ending, bullet lists with >5 items.
</format>`,
    prototype: `
<format>
  Type: interactive product prototype.
  File path: "${file}"
  Canvas: device frame at the right aspect ratio. Default iPhone (390×844). For Android use 412×915.
  Anatomy: minimum 3 screens. Each screen is a \`<section data-screen="<name>">\`. Buttons with \`data-route="<screen-name>"\` switch screens via a small JS controller.
  Visual: status bar, content, optional bottom nav. Real iconography (Lucide via CDN or inline SVG). Real product copy.
  Forbidden: blurry placeholder rectangles, "Lorem ipsum" copy, placeholder.com images.
</format>`,
    landing: `
<format>
  Type: landing page mock.
  File path: "${file}"
  Canvas: fluid width, scrollable.
  Anatomy: hero (NOT centered, NOT "Welcome to X"), 2–4 content sections each with a distinct layout (split, full-bleed, card grid, testimonial, pricing), footer.
  Imagery: use \`images.unsplash.com\` URLs you know exist. Skip imagery rather than guessing.
  Forbidden: centered hero with one CTA, "Trusted by" logo bar with fictional company names, lavender→teal gradient hero.
</format>`,
    onepager: `
<format>
  Type: A4 print-grade one-pager.
  File path: "${file}"
  Canvas: A4 portrait (210mm × 297mm). \`<body>\` is exactly one page, no scroll.
  Anatomy: masthead with title and date, 2–3 content blocks with distinct hierarchy, footer with credit/source line.
  Typography: deliberate scale (e.g., 96pt display headline, 14pt body, 10pt caption). Print-grade leading.
  Forbidden: scroll, web nav menus, anything that wouldn't print well.
</format>`,
    infographic: `
<format>
  Type: vertical infographic / poster.
  File path: "${file}"
  Canvas: 1080×1920 (vertical), fixed.
  Anatomy: title, 3–6 data sections each with a different visualization (bar, donut, sparkline, pictogram), source line at bottom.
  Data: invent specific, plausible numbers if not given. Cite a source line ("Source: …") even if invented; mark invented data with "(illustrative)" near the source.
  Forbidden: decorative emoji, flat icon clipart, generic clip-art treatments.
</format>`,
  };
  return map[format];
}

function pairingBlock(p: FontPairing): string {
  return `
<typography>
  Use these fonts (load via Google Fonts):
    Display: "${p.display.family}"
    Body:    "${p.body.family}"
  Embed in <head>:
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="${googleFontsUrl(p)}" rel="stylesheet">
  Default scale: display 96/72/48px (h1/h2/h3), body 16px, caption 13px. Vary aggressively, not by 1px.
  Inter, Roboto, Open Sans, Lato, Montserrat are FORBIDDEN as the display font.
</typography>`;
}

function brandBlock(b: Brand): string {
  const logoLine = b.logoPath
    ? `Logo path (workspace-relative): "${b.logoPath}". Reference it in HTML as ./${b.logoPath}. Use ONLY on cover/hero areas, never as decorative repeat.`
    : `No logo set.`;
  const voice = b.voice ? `Voice: ${b.voice}` : "";
  return `
<brand_system>
  Brand: ${b.name}
  ${voice}
  Use ONLY these CSS variables — do not invent new colors:
    --bg: ${b.colors.bg}
    --fg: ${b.colors.fg}
    --accent: ${b.colors.accent}
    --muted: ${b.colors.muted}
    --supporting: ${b.colors.supporting}
  Fonts (load via Google Fonts in <head>):
    Display: "${b.fonts.display.family}"
    Body:    "${b.fonts.body.family}"
  ${logoLine}
  This brand block is the highest-priority guidance. It overrides any color/font default elsewhere.
</brand_system>`;
}

function sharedBaseBlock(): string {
  return `
<base_rules>
  Embed an 8pt grid as CSS variables:
    --space-1: 0.5rem;  --space-2: 1rem;   --space-3: 1.5rem;
    --space-4: 2rem;    --space-6: 3rem;   --space-8: 4rem;
    --space-12: 6rem;   --space-16: 8rem;
  All spacing in the file uses these tokens.
  Default easing for any motion: cubic-bezier(0.22, 1, 0.36, 1). Never 'linear' unless intentional.
  Self-contained: <style> and <script> inline. External assets only via fonts.googleapis.com, cdn.jsdelivr.net, esm.sh, images.unsplash.com.
  No package.json, no npm, no Vite, no dev server.
</base_rules>`;
}

function antiSlopBlock(): string {
  return `
<anti_slop>
  Avoid these defaults — they're how AI design gets caught:
  - Centered "Welcome to [Product]" hero with one CTA. Replace with content-led layouts.
  - 8 identical feature cards in a 4×2 grid with emoji icons.
  - Generic stock photos of laptops on white desks.
  - "Trusted by" logo bar with fictional company names.
  - Lavender/teal gradient backgrounds.
  - Lorem ipsum. "Company X". "Lorem Solutions". Use specific, plausible names and numbers.
  - More than one orchestrated entrance animation per file. Restraint > sparkle.
</anti_slop>`;
}

function artifactRulesBlock(format: Format): string {
  const file = FORMAT_FILE[format];
  return `
<artifact_format>
  Wrap your output in:
    <boltArtifact id="kebab-case-id" title="Short human title">
      <boltAction type="file" filePath="${file}">FULL HTML</boltAction>
    </boltArtifact>
  Provide complete file contents — never diffs, never "// rest unchanged", never placeholders.
  Do NOT emit shell or start actions.
  You may write a single line of prose before the artifact summarizing what you built. Nothing after the artifact.
</artifact_format>`;
}

function examplesBlock(format: Format): string {
  // Format-specific one-line example anchor — keeps the prompt grounded without ballooning token cost.
  const map: Record<Format, string> = {
    slides: `Example brief: "5-slide pitch for a coffee subscription called Kindling" → 5 \`<section class="slide">\` blocks, arrow-key navigation, deliberate typography hierarchy, real Unsplash imagery on cover.`,
    prototype: `Example brief: "iOS app for tracking daily reading" → 3 screens (home/library/timer) inside a 390×844 device frame, \`data-route\` buttons that switch screens.`,
    landing: `Example brief: "landing page for a B2B SaaS that schedules legal hearings" → asymmetric hero, two content sections, pricing or testimonial, footer.`,
    onepager: `Example brief: "team weekly briefing" → masthead + 3 content blocks + footer, exactly fits an A4 page, prints clean.`,
    infographic: `Example brief: "the 2026 Cameroon ride-hailing market in 6 stats" → 6 data sections with distinct chart treatments, "(illustrative)" if numbers are invented.`,
  };
  return `<example>${map[format]}</example>`;
}
```

- [ ] **Step 2: Typecheck**

Run: `cd src/web && bunx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add src/web/src/lib/design/playbooks.ts
git commit -m "design: per-format playbooks (slides/prototype/landing/onepager/infographic)"
```

---

## Task 4: Refactor `buildDesignPrompt` to use playbooks

**Files:**
- Modify: `src/web/src/lib/actions/jarvis-prompt.ts`

- [ ] **Step 1: Replace the existing `buildDesignPrompt` function**

Open `src/web/src/lib/actions/jarvis-prompt.ts`. Find the existing `buildDesignPrompt` function and `DesignPromptArgs` type added in the previous session. Replace both with:

```typescript
import { buildPlaybookPrompt } from "@/lib/design/playbooks";
import type { Format } from "@/lib/design/format";
import type { Brand } from "@/lib/design/brand";

export type DesignPromptArgs = {
  workspaceName: string;
  cwd: string;
  format?: Format;
  brand?: Brand | null;
};

export function buildDesignPrompt({ workspaceName, cwd, format = "slides", brand = null }: DesignPromptArgs): string {
  return "\n\n" + buildPlaybookPrompt({ format, brand, workspaceName, cwd });
}
```

The existing `buildWorkbenchPrompt` is **unchanged**.

- [ ] **Step 2: Smoke check the import path**

Run: `cd src/web && bunx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add src/web/src/lib/actions/jarvis-prompt.ts
git commit -m "design: route prompt through format-aware playbooks"
```

---

## Task 5: Wire `format` and `brand` into `/api/chat`

**Files:**
- Modify: `src/web/src/app/api/chat/route.ts`

- [ ] **Step 1: Add `format` to the request body type and pass brand+format into `buildDesignPrompt`**

Find the `Body` type. Replace it with:

```typescript
import type { Format } from "@/lib/design/format";
import { getBrand } from "@/lib/design/brand";

type ChatMode = "design";

type Body = {
  id?: string;
  messages: UIMessage[];
  model?: string;
  system?: string;
  workspaceId?: string;
  mode?: ChatMode;
  format?: Format;
};
```

Find the line `const { id, messages, model, system, workspaceId, mode }: Body = await req.json();` and add `format` to the destructuring:

```typescript
const { id, messages, model, system, workspaceId, mode, format }: Body = await req.json();
```

Find the existing block that selects between `buildDesignPrompt` and `buildWorkbenchPrompt`. Replace it with:

```typescript
  let finalSystem = system ?? settings.defaults.systemPrompt ?? buildDefaultSystemPrompt();
  if (workspaceId) {
    const ws = await getWorkspace(workspaceId);
    if (ws) {
      if (mode === "design") {
        const brand = await getBrand(workspaceId);
        finalSystem += buildDesignPrompt({
          workspaceName: ws.name,
          cwd: "/workspace",
          format,
          brand,
        });
      } else {
        finalSystem += buildWorkbenchPrompt({
          workspaceName: ws.name,
          cwd: "/workspace",
        });
      }
    }
  }
```

- [ ] **Step 2: Typecheck**

Run: `cd src/web && bunx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Smoke test in the browser**

Dev server should already be running on port 3001. Visit [http://localhost:3001/design](http://localhost:3001/design). Chat: "make me a 3-slide deck about espresso." After it streams, the Design Files panel should show `slides.html`. Click → preview should render a real deck (not a Vite scaffold), with non-Inter display font, proper sectioning, arrow-key nav.

- [ ] **Step 4: Commit**

```bash
git add src/web/src/app/api/chat/route.ts
git commit -m "design: chat route loads brand + selects playbook by format"
```

---

## Task 6: Pass `format` from `<Chat>` to the API

**Files:**
- Modify: `src/web/src/components/chat/chat.tsx`

- [ ] **Step 1: Add `format` to the props type and destructuring**

Find the `ChatProps` type. After the existing `mode?: "design";` line, add:

```typescript
  // Selects the design playbook the API uses. Only meaningful when mode === "design".
  format?: import("@/lib/design/format").Format;
```

Find the function signature `export function Chat({ … mode, }: ChatProps) {`. Add `format` to the destructuring:

```typescript
export function Chat({
  chatId,
  initialMessages,
  workspaceId: workspaceIdProp,
  workspaceName: workspaceNameProp,
  embedded = false,
  seed,
  composerPlaceholder,
  mode,
  format,
}: ChatProps) {
```

Find the POST body block. Add `format` to it:

```typescript
        body: JSON.stringify({
          id: chatId,
          model,
          messages: historyForApi,
          workspaceId: targetWorkspaceId ?? undefined,
          mode,
          format,
        }),
```

- [ ] **Step 2: Typecheck**

Run: `cd src/web && bunx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add src/web/src/components/chat/chat.tsx
git commit -m "design: forward 'format' prop to chat API"
```

---

## Task 7: Format selector chip component

**Files:**
- Create: `src/web/src/components/design/format-selector.tsx`

- [ ] **Step 1: Create the file**

```typescript
"use client";

import { FORMATS, FORMAT_LABEL, type Format } from "@/lib/design/format";
import { cn } from "@/lib/utils";

export function FormatSelector({
  value,
  onChange,
}: {
  value: Format;
  onChange: (next: Format) => void;
}) {
  return (
    <div
      role="radiogroup"
      aria-label="Design format"
      className="flex items-center gap-1 border-b border-border/50 px-2 py-1.5"
    >
      {FORMATS.map((f) => {
        const active = f === value;
        return (
          <button
            key={f}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(f)}
            className={cn(
              "rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors",
              active
                ? "bg-foreground text-background"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            {FORMAT_LABEL[f]}
          </button>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd src/web && bunx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add src/web/src/components/design/format-selector.tsx
git commit -m "design: format chip selector (slides/prototype/landing/onepager/infographic)"
```

---

## Task 8: Wire format selector into the design view

**Files:**
- Modify: `src/web/src/components/design/design-view.tsx`

- [ ] **Step 1: Add format state and render the selector above the chat**

In `design-view.tsx`, add the import:

```typescript
import { DEFAULT_FORMAT, type Format } from "@/lib/design/format";
import { FormatSelector } from "./format-selector";
```

Inside `DesignView`, add state:

```typescript
const [format, setFormat] = useState<Format>(DEFAULT_FORMAT);
```

Replace the `<Chat … />` block in the left aside with:

```tsx
<div className="flex flex-1 min-h-0 flex-col">
  <FormatSelector value={format} onChange={setFormat} />
  <div className="flex-1 min-h-0">
    <Chat
      embedded
      mode="design"
      format={format}
      workspaceId={workspaceId}
      workspaceName={workspaceName}
      composerPlaceholder={`Describe the ${format} you want to create…`}
    />
  </div>
</div>
```

- [ ] **Step 2: Typecheck**

Run: `cd src/web && bunx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Visual check in browser**

Refresh [/design](http://localhost:3001/design). The format chips appear at the top of the chat column. Click "Prototype" — the placeholder should change to "Describe the prototype you want to create…". Send "iOS reading-tracker app, 3 screens" — `prototype.html` should appear in Files (390×844).

- [ ] **Step 4: Commit**

```bash
git add src/web/src/components/design/design-view.tsx
git commit -m "design: format chip selector above chat"
```

---

## Task 9: Brand API route

**Files:**
- Create: `src/web/src/app/api/design/brand/route.ts`

- [ ] **Step 1: Create the file**

```typescript
import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";
import { getBrand, putBrand, putBrandAsset } from "@/lib/design/brand";

export const runtime = "nodejs";

const ColorsSchema = z.object({
  bg: z.string().regex(/^#[0-9a-fA-F]{6}$/),
  fg: z.string().regex(/^#[0-9a-fA-F]{6}$/),
  accent: z.string().regex(/^#[0-9a-fA-F]{6}$/),
  muted: z.string().regex(/^#[0-9a-fA-F]{6}$/),
  supporting: z.string().regex(/^#[0-9a-fA-F]{6}$/),
});

const FontSchema = z.object({
  family: z.string().min(1).max(80),
  googleFontsUrl: z.string().url().optional(),
});

const BrandSchema = z.object({
  version: z.literal(1),
  name: z.string().min(1).max(80),
  logoPath: z.string().optional(),
  colors: ColorsSchema,
  fonts: z.object({ display: FontSchema, body: FontSchema }),
  voice: z.string().max(400).optional(),
  references: z.array(z.object({ path: z.string(), note: z.string().optional() })).optional(),
});

const PutSchema = z.object({
  brand: BrandSchema,
  logoBase64: z.string().optional(), // optional logo upload as base64 data URL
  logoFilename: z.string().optional(),
});

export async function GET(req: NextRequest) {
  const id = req.nextUrl.searchParams.get("workspaceId");
  if (!id) return NextResponse.json({ error: "workspaceId required" }, { status: 400 });
  const brand = await getBrand(id);
  return NextResponse.json({ brand });
}

export async function PUT(req: NextRequest) {
  const id = req.nextUrl.searchParams.get("workspaceId");
  if (!id) return NextResponse.json({ error: "workspaceId required" }, { status: 400 });

  const body = PutSchema.parse(await req.json());
  let next = body.brand;

  if (body.logoBase64 && body.logoFilename) {
    // Strip optional data URL prefix
    const m = body.logoBase64.match(/^data:[^;]+;base64,(.+)$/);
    const b64 = m ? m[1] : body.logoBase64;
    const data = Buffer.from(b64, "base64");
    if (data.length > 2 * 1024 * 1024) {
      return NextResponse.json({ error: "logo > 2MB" }, { status: 413 });
    }
    const stored = await putBrandAsset(id, body.logoFilename, data);
    next = { ...next, logoPath: stored };
  }

  await putBrand(id, next);
  return NextResponse.json({ brand: next });
}
```

- [ ] **Step 2: Verify `zod` is installed**

Run: `cd src/web && grep '"zod"' package.json`

If missing: `cd src/web && bun add zod` and commit the package.json + lockfile change in step 4.

- [ ] **Step 3: Typecheck and curl smoke test**

```bash
cd src/web && bunx tsc --noEmit
# Get a workspace id from the existing list
curl -s http://localhost:3001/api/workspace | head -200
# Replace <WS> below with the design workspace's id
curl -s "http://localhost:3001/api/design/brand?workspaceId=<WS>"
# Should return: {"brand":null}
curl -s -X PUT "http://localhost:3001/api/design/brand?workspaceId=<WS>" \
  -H "Content-Type: application/json" \
  -d '{"brand":{"version":1,"name":"Pretva","colors":{"bg":"#0B0B0F","fg":"#F4F4F5","accent":"#FF6A00","muted":"#71717A","supporting":"#27272A"},"fonts":{"display":{"family":"Bricolage Grotesque"},"body":{"family":"IBM Plex Sans"}}}}'
# Should echo the brand back
curl -s "http://localhost:3001/api/design/brand?workspaceId=<WS>"
# Should now return the saved brand
```

- [ ] **Step 4: Commit**

```bash
git add src/web/src/app/api/design/brand/route.ts src/web/package.json src/web/bun.lock
git commit -m "design: brand API (GET/PUT) with zod validation + logo upload"
```

---

## Task 10: Brand panel UI component

**Files:**
- Create: `src/web/src/components/design/brand-panel.tsx`
- Create: `src/web/src/hooks/use-brand.ts`

- [ ] **Step 1: Create the hook**

```typescript
// src/web/src/hooks/use-brand.ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Brand } from "@/lib/design/brand";

const KEY = (id: string) => ["design-brand", id] as const;

export function useBrand(workspaceId: string) {
  return useQuery({
    queryKey: KEY(workspaceId),
    queryFn: async (): Promise<Brand | null> => {
      const r = await fetch(`/api/design/brand?workspaceId=${workspaceId}`);
      if (!r.ok) throw new Error(`brand ${r.status}`);
      const j = await r.json();
      return j.brand;
    },
  });
}

export function usePutBrand(workspaceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      brand: Brand;
      logoBase64?: string;
      logoFilename?: string;
    }) => {
      const r = await fetch(`/api/design/brand?workspaceId=${workspaceId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      });
      if (!r.ok) throw new Error(`brand put ${r.status}`);
      const j = await r.json();
      return j.brand as Brand;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY(workspaceId) });
    },
  });
}
```

- [ ] **Step 2: Create the panel**

```typescript
// src/web/src/components/design/brand-panel.tsx
"use client";

import { useEffect, useState } from "react";
import { Loader2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useBrand, usePutBrand } from "@/hooks/use-brand";
import type { Brand } from "@/lib/design/brand";

const EMPTY: Brand = {
  version: 1,
  name: "",
  colors: { bg: "#0B0B0F", fg: "#F4F4F5", accent: "#FF6A00", muted: "#71717A", supporting: "#27272A" },
  fonts: { display: { family: "Bricolage Grotesque" }, body: { family: "IBM Plex Sans" } },
};

export function BrandPanel({ workspaceId }: { workspaceId: string }) {
  const { data: existing, isLoading } = useBrand(workspaceId);
  const put = usePutBrand(workspaceId);
  const [draft, setDraft] = useState<Brand>(EMPTY);
  const [logoFile, setLogoFile] = useState<{ name: string; base64: string } | null>(null);

  useEffect(() => {
    if (existing) setDraft(existing);
  }, [existing]);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-[13px] text-muted-foreground">
        loading…
      </div>
    );
  }

  const onLogo = async (file: File) => {
    const buf = await file.arrayBuffer();
    const b64 = Buffer.from(buf).toString("base64");
    setLogoFile({ name: file.name, base64: b64 });
  };

  const save = () => {
    put.mutate({
      brand: draft,
      logoBase64: logoFile?.base64,
      logoFilename: logoFile?.name,
    });
  };

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-5">
      <Field label="Brand name">
        <input
          className="w-full rounded-md border border-border/60 bg-background px-3 py-1.5 text-[13px]"
          value={draft.name}
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          placeholder="Pretva"
        />
      </Field>

      <Field label="Logo">
        <label className="flex cursor-pointer items-center gap-2 rounded-md border border-dashed border-border/60 px-3 py-3 text-[13px] text-muted-foreground hover:bg-muted/30">
          <Upload className="size-4" />
          {logoFile?.name ?? draft.logoPath ?? "Upload logo (PNG/SVG, ≤2MB)"}
          <input
            type="file"
            accept="image/png,image/svg+xml,image/jpeg"
            className="hidden"
            onChange={(e) => e.target.files?.[0] && onLogo(e.target.files[0])}
          />
        </label>
      </Field>

      <Field label="Colors">
        <div className="grid grid-cols-5 gap-2">
          {(["bg", "fg", "accent", "muted", "supporting"] as const).map((k) => (
            <label key={k} className="flex flex-col gap-1 text-[11px] text-muted-foreground">
              <span className="uppercase tracking-wide">{k}</span>
              <input
                type="color"
                value={draft.colors[k]}
                onChange={(e) => setDraft({ ...draft, colors: { ...draft.colors, [k]: e.target.value } })}
                className="h-8 w-full cursor-pointer rounded-md border border-border/60 bg-background"
              />
            </label>
          ))}
        </div>
      </Field>

      <Field label="Fonts (Google Fonts family names)">
        <div className="grid grid-cols-2 gap-2">
          <input
            className="rounded-md border border-border/60 bg-background px-3 py-1.5 text-[13px]"
            placeholder="Display (e.g. Bricolage Grotesque)"
            value={draft.fonts.display.family}
            onChange={(e) =>
              setDraft({ ...draft, fonts: { ...draft.fonts, display: { family: e.target.value } } })
            }
          />
          <input
            className="rounded-md border border-border/60 bg-background px-3 py-1.5 text-[13px]"
            placeholder="Body (e.g. IBM Plex Sans)"
            value={draft.fonts.body.family}
            onChange={(e) =>
              setDraft({ ...draft, fonts: { ...draft.fonts, body: { family: e.target.value } } })
            }
          />
        </div>
      </Field>

      <Field label="Voice (optional)">
        <textarea
          rows={3}
          className="w-full rounded-md border border-border/60 bg-background px-3 py-1.5 text-[13px]"
          placeholder="Confident, concise, founder-direct. Avoid jargon."
          value={draft.voice ?? ""}
          onChange={(e) => setDraft({ ...draft, voice: e.target.value })}
        />
      </Field>

      <div className="flex items-center gap-2">
        <Button size="sm" onClick={save} disabled={put.isPending || !draft.name}>
          {put.isPending && <Loader2 className="size-3.5 animate-spin" />}
          Save brand
        </Button>
        {put.isSuccess && <span className="text-[12px] text-muted-foreground">Saved.</span>}
        {put.isError && <span className="text-[12px] text-red-500">Failed to save.</span>}
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}
```

- [ ] **Step 3: Typecheck**

Run: `cd src/web && bunx tsc --noEmit`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add src/web/src/components/design/brand-panel.tsx src/web/src/hooks/use-brand.ts
git commit -m "design: brand panel UI + react-query hook"
```

---

## Task 11: Wire brand toggle into design view header

**Files:**
- Modify: `src/web/src/components/design/design-view.tsx`

- [ ] **Step 1: Add a "Brand" toggle that swaps the center panel**

Open `design-view.tsx`. Add the import:

```typescript
import { Sparkles } from "lucide-react";
import { BrandPanel } from "./brand-panel";
```

Inside `DesignView`, add state:

```typescript
const [showBrand, setShowBrand] = useState(false);
```

Add a button to the top bar, immediately before the existing "Share" button block. Replace the existing right-side `<div className="flex shrink-0 items-center gap-2 px-3">` content's first child by inserting the brand button:

```tsx
<Button
  variant={showBrand ? "secondary" : "ghost"}
  size="sm"
  className="rounded-md"
  onClick={() => setShowBrand((v) => !v)}
>
  <Sparkles className="size-3.5" />
  Brand
</Button>
```

Find the `showFiles ? (…) : (…)` block. Replace the `showFiles ? (…)` branch with logic that renders the brand panel when `showBrand` is true:

```tsx
{showBrand ? (
  <div className="flex flex-1 min-w-0">
    <div className="flex flex-1 min-w-0 flex-col border-r border-border/60">
      <BrandPanel workspaceId={workspaceId} />
    </div>
    <div className="flex w-[42%] min-w-80 shrink-0 flex-col">
      <DesignPreview workspaceId={workspaceId} selected={selected} />
    </div>
  </div>
) : showFiles ? (
  // …existing block unchanged…
) : (
  // …existing else branch unchanged…
)}
```

- [ ] **Step 2: Typecheck**

Run: `cd src/web && bunx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Visual check**

Refresh `/design`. Click "Brand" in the top bar — center panel swaps to the brand editor. Set name=Pretva, accent=#FF6A00, save. Click "Brand" again to toggle back. Send "make me a 3-slide deck about espresso." — the generated HTML should use accent #FF6A00 for highlights.

- [ ] **Step 4: Commit**

```bash
git add src/web/src/components/design/design-view.tsx
git commit -m "design: brand toggle in top bar swaps center panel"
```

---

## Task 12: Install Playwright

**Files:**
- Modify: `src/web/package.json`

- [ ] **Step 1: Install playwright + the chromium binary**

```bash
cd src/web && bun add playwright
bunx playwright install chromium --with-deps
```

The second command may need `sudo` for system deps. If `--with-deps` fails, run `bunx playwright install chromium` (just the binary) and rely on already-installed system libs.

- [ ] **Step 2: Smoke test the install**

```bash
cd src/web && bun -e 'import("playwright").then(async (pw) => { const b = await pw.chromium.launch(); const p = await b.newPage(); await p.setContent("<h1>ok</h1>"); console.log((await p.content()).includes("ok")); await b.close(); })'
```

Expected: prints `true`.

- [ ] **Step 3: Commit**

```bash
git add src/web/package.json src/web/bun.lock
git commit -m "design: add playwright for PDF export"
```

---

## Task 13: PDF export route

**Files:**
- Create: `src/web/src/app/api/design/export/route.ts`

- [ ] **Step 1: Create the file**

```typescript
import { type NextRequest, NextResponse } from "next/server";
import { chromium } from "playwright";
import { z } from "zod";
import { type Format } from "@/lib/design/format";

export const runtime = "nodejs";
export const maxDuration = 120; // PDF render can take ~10-30s

const QuerySchema = z.object({
  workspaceId: z.string().min(1),
  path: z.string().min(1),
  format: z.enum(["slides", "prototype", "landing", "onepager", "infographic"]).optional(),
  output: z.enum(["pdf"]).default("pdf"),
});

type PageSize =
  | { format: "A4" | "Letter" }
  | { width: string; height: string };

function pageSizeFor(format: Format | undefined): PageSize {
  switch (format) {
    case "slides":
      return { width: "1920px", height: "1080px" };
    case "infographic":
      return { width: "1080px", height: "1920px" };
    case "prototype":
      return { width: "390px", height: "844px" };
    case "onepager":
      return { format: "A4" };
    case "landing":
    default:
      return { format: "Letter" };
  }
}

export async function GET(req: NextRequest) {
  const params = QuerySchema.parse(Object.fromEntries(req.nextUrl.searchParams));
  const origin = req.nextUrl.origin;
  const fileUrl = `${origin}/api/workspace/${encodeURIComponent(params.workspaceId)}/file?path=${encodeURIComponent(params.path)}&raw=1`;

  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const resp = await page.goto(fileUrl, { waitUntil: "networkidle", timeout: 60_000 });
    if (!resp || !resp.ok()) {
      return NextResponse.json({ error: `fetch ${resp?.status() ?? "?"}` }, { status: 502 });
    }
    // Wait for fonts so Google Fonts render in the PDF.
    await page.evaluate(() => (document as Document & { fonts: { ready: Promise<unknown> } }).fonts.ready);

    const size = pageSizeFor(params.format);
    const pdfBuffer = await page.pdf({
      ...size,
      printBackground: true,
      margin: { top: "0", right: "0", bottom: "0", left: "0" },
    });

    const baseName = params.path.split("/").pop()?.replace(/\.html?$/i, "") ?? "design";
    return new NextResponse(pdfBuffer, {
      status: 200,
      headers: {
        "Content-Type": "application/pdf",
        "Content-Disposition": `attachment; filename="${baseName}.pdf"`,
        "Cache-Control": "no-store",
      },
    });
  } finally {
    await browser.close();
  }
}
```

- [ ] **Step 2: Typecheck**

Run: `cd src/web && bunx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Smoke test**

In the design tab, generate a slides file. Then in the terminal:

```bash
# Replace <WS> and <PATH>
curl -s "http://localhost:3001/api/design/export?workspaceId=<WS>&path=slides.html&format=slides" -o /tmp/test-slides.pdf
file /tmp/test-slides.pdf
# Expected: "/tmp/test-slides.pdf: PDF document, ..."
ls -la /tmp/test-slides.pdf
# Expected: size > 50KB for a real deck
```

- [ ] **Step 4: Commit**

```bash
git add src/web/src/app/api/design/export/route.ts
git commit -m "design: PDF export via Playwright (per-format page sizes)"
```

---

## Task 14: Export menu in design view header

**Files:**
- Modify: `src/web/src/components/design/design-view.tsx`

- [ ] **Step 1: Replace the existing "Present" button with a dropdown**

The Present button currently renders an `<a target="_blank">`. Replace it with a `DropdownMenu` (Base UI dropdown is already available via existing components — search the project for "dropdown" first; if absent, use a small details/summary fallback).

Add the import:

```typescript
import { Download } from "lucide-react";
```

Replace the `selected && <Button>` Present block with:

```tsx
{selected && selected.type !== "dir" && (
  <details className="relative">
    <summary className="flex cursor-pointer list-none items-center gap-1 rounded-md px-2 py-1 text-[13px] text-muted-foreground hover:bg-muted">
      <Play className="size-3.5" />
      Present
      <ChevronDown className="size-3" />
    </summary>
    <div className="absolute right-0 top-full z-20 mt-1 w-48 overflow-hidden rounded-md border border-border/60 bg-popover shadow-md">
      <a
        href={`/api/workspace/${workspaceId}/file?path=${encodeURIComponent(selected.path)}&raw=1`}
        target="_blank"
        rel="noreferrer"
        className="flex items-center gap-2 px-3 py-2 text-[13px] hover:bg-muted"
      >
        <Play className="size-3.5" />
        Open in new tab
      </a>
      <a
        href={`/api/workspace/${workspaceId}/file?path=${encodeURIComponent(selected.path)}&raw=1`}
        download={selected.name}
        className="flex items-center gap-2 px-3 py-2 text-[13px] hover:bg-muted"
      >
        <Download className="size-3.5" />
        Download HTML
      </a>
      <a
        href={`/api/design/export?workspaceId=${encodeURIComponent(workspaceId)}&path=${encodeURIComponent(selected.path)}&format=${format}`}
        className="flex items-center gap-2 px-3 py-2 text-[13px] hover:bg-muted"
      >
        <Download className="size-3.5" />
        Download PDF
      </a>
    </div>
  </details>
)}
```

(`format` is the state added in Task 8 — the dropdown closes when the link navigates because `<details>` blurs.)

- [ ] **Step 2: Typecheck**

Run: `cd src/web && bunx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Visual check**

Refresh `/design`. Generate a slides file, click it, click "Present". Three options appear: open in new tab / download HTML / download PDF. Click each and verify each works (PDF takes ~10-30s).

- [ ] **Step 4: Commit**

```bash
git add src/web/src/components/design/design-view.tsx
git commit -m "design: export menu (open / download HTML / download PDF)"
```

---

## Task 15: Visual QA against the rubric

**Files:**
- Create: `docs/superpowers/specs/2026-04-29-design-rubric-results.md`

- [ ] **Step 1: Generate one design per format with no brand**

In the design tab, with brand cleared, run these prompts and capture screenshots into `/tmp/qa/`:

| format | prompt |
|---|---|
| slides | "5-slide deck pitching a coffee subscription called Kindling" |
| prototype | "iOS app for tracking daily reading time, 3 screens" |
| landing | "landing page for a B2B SaaS that schedules legal hearings" |
| onepager | "weekly team briefing for a 20-person startup, this week's wins/blockers/next" |
| infographic | "the 2026 Cameroon ride-hailing market in 6 stats, vertical poster" |

- [ ] **Step 2: Score each output 1-5 on five axes**

In `docs/superpowers/specs/2026-04-29-design-rubric-results.md`, record a table:

```
| format | typography | layout | color | specificity | no-slop | avg |
|---|---|---|---|---|---|---|
| slides       | … | … | … | … | … | … |
| prototype    | … | … | … | … | … | … |
| landing      | … | … | … | … | … | … |
| onepager     | … | … | … | … | … | … |
| infographic  | … | … | … | … | … | … |
```

- [ ] **Step 3: Set a brand and re-run slides + onepager only**

Set name=Pretva, accent=#FF6A00, fonts=Bricolage Grotesque + IBM Plex Sans. Re-run the slides and onepager prompts. Confirm the accent color and fonts appear in the generated HTML. Add to the rubric file:

```
## With brand

| format | brand applied? | notes |
|---|---|---|
| slides   | yes/no | … |
| onepager | yes/no | … |
```

- [ ] **Step 4: Make targeted prompt fixes**

Any axis with avg < 4 → revise the relevant section in `playbooks.ts`. Re-run that format and re-score until ≥ 4.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-04-29-design-rubric-results.md src/web/src/lib/design/playbooks.ts
git commit -m "design: v1 visual QA results + targeted playbook tuning"
```

---

## Self-review checklist (read after writing the plan)

**Spec coverage** — the spec lists these requirements; each is mapped to a task:

- [x] Format playbooks (5 formats) — Task 3
- [x] Brand storage + injection — Tasks 2, 5, 9
- [x] Brand UI — Tasks 10, 11
- [x] Format selector chip — Tasks 7, 8
- [x] PDF export with per-format page sizes — Tasks 12, 13
- [x] HTML download (existing route, just exposed in menu) — Task 14
- [x] Anti-slop guardrails baked into shared base block — Task 3
- [x] Curated font catalog (Google Fonts, no Inter as display) — Task 1
- [x] Visual rubric pass — Task 15

The spec mentions a "Make variants" button as deferrable. **Not in this plan** — explicit v2.

**Placeholders / red flags scan:** none — every step has either code, a command, or a specific check. The path-safety code in Task 2 is fully shown. The brand schema in Task 9 is fully shown. The PDF route in Task 13 is fully shown.

**Type consistency:** `Format`, `Brand`, `BrandColors`, `FontPairing` all defined in Tasks 1-2 and used consistently in Tasks 3-13.

---

## Execution choice

Two ways to run this:

1. **Subagent-driven** (recommended for >10 tasks like this) — fresh subagent per task, two-stage review.
2. **Inline** — execute tasks here in this session.

Auto mode is on, so unless overridden the next step is inline execution via the `executing-plans` skill.
