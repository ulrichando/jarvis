import { NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import path from "node:path";
import {
  createWorkspace,
  getWorkspace,
  listAllFiles,
  resolveSafe,
  workspaceRoot,
  writeFile,
} from "@/lib/workspace/storage";
import { getBrand } from "@/lib/design/brand";
import { generateScaffold } from "@/lib/design/codegen";
import { execInRuntime, spawnDetached } from "@/lib/workspace/docker";

export const runtime = "nodejs";

// Map the seed's format hint to the primary HTML file that holds the
// design's structure. Used to ensure the primary file is at the
// front of the inlined <design_source> blocks (so the model sees it
// first if the seed has to be truncated).
function FORMAT_FILE_FOR(
  formatHint: string,
  available: string[],
): string | null {
  const map: Record<string, string> = {
    "landing page": "landing.html",
    "app prototype": "prototype.html",
    "presentation": "slides.html",
    "one-pager": "onepager.html",
    "infographic": "infographic.html",
  };
  const expected = map[formatHint];
  if (expected && available.includes(expected)) return expected;
  // Fall back to the first .html file.
  return available.find((f) => /\.html?$/i.test(f)) ?? null;
}

/**
 * POST /api/design/build
 * Body: { sourceWorkspaceId: string }
 *
 * "Build" button in the design tab. Takes the design's frontend files,
 * copies them into a fresh workbench workspace under a `design/` folder,
 * and returns a seed prompt the workbench chat will auto-fire to
 * scaffold the full-stack app.
 *
 * The workbench has a real Docker container runtime + DB tab + dev
 * server, so it can build the actual full-stack version of what the
 * design tab mocked. The design files become the visual reference.
 */
export async function POST(req: Request) {
  const body = (await req.json().catch(() => ({}))) as {
    sourceWorkspaceId?: string;
  };
  const sourceId = body.sourceWorkspaceId;
  if (!sourceId) {
    return NextResponse.json(
      { error: "missing sourceWorkspaceId" },
      { status: 400 },
    );
  }

  const source = await getWorkspace(sourceId);
  if (!source) {
    return NextResponse.json({ error: "source workspace not found" }, { status: 404 });
  }

  // List the design files. Skip questions.html (it's a clarify-mode
  // artifact, useless for the build) and references/ (uploaded user
  // assets — copied below as raw files, not text).
  const allFiles = await listAllFiles(sourceId);
  const designFiles = allFiles.filter(
    (p) => p !== "questions.html" && !p.startsWith("references/"),
  );
  if (designFiles.length === 0) {
    return NextResponse.json(
      { error: "source workspace has no design files yet — generate a design first" },
      { status: 400 },
    );
  }

  // Detect the format from the entry HTML name. landing → "the website";
  // prototype → "the app prototype"; etc. Used in the seed prompt.
  const formatHint =
    designFiles.includes("landing.html")
      ? "landing page"
      : designFiles.includes("prototype.html")
        ? "app prototype"
        : designFiles.includes("slides.html")
          ? "presentation"
          : designFiles.includes("onepager.html")
            ? "one-pager"
            : designFiles.includes("infographic.html")
              ? "infographic"
              : "design";

  // Create the new workbench workspace. Tagged kind="workbench" so it
  // appears in the Workbench tab's project picker but NOT in Design's
  // (the source design workspace stays separate).
  const newWs = await createWorkspace(`Build: ${source.name}`, "workbench");

  // Copy design files into `design/` so the workbench has them as a
  // visual reference, but doesn't confuse them with the new full-stack
  // project's own source. Workbench scaffolds the app at the workspace
  // root; the original frontend lives in design/ for the model to read.
  //
  // ALSO: collect the raw HTML content of each design file so we can
  // embed it directly into the seed prompt below. Telling the model
  // to `cat design/*.html` is unreliable — many models skim or skip
  // the step. Putting the source text RIGHT IN THE PROMPT makes it
  // impossible to skip; the model sees the exact markup, copy, colors,
  // and image URLs as part of its initial context.
  const designSources: { rel: string; content: string }[] = [];
  for (const rel of designFiles) {
    const absSrc = resolveSafe(sourceId, rel);
    try {
      const content = await fs.readFile(absSrc, "utf8");
      await writeFile(newWs.id, `design/${rel}`, content);
      // Capture html files (and small json/css) for inline embedding.
      // Skip files >50KB to keep the seed under control.
      if (
        content.length <= 50 * 1024 &&
        /\.(html?|css|jsx?|tsx?|svg|json)$/i.test(rel)
      ) {
        designSources.push({ rel, content });
      }
    } catch (err) {
      console.warn(`[build] couldn't copy ${rel}:`, err);
    }
  }

  // Also copy any uploaded references (images, brand logos) — they're
  // potentially useful as the workbench builds the app.
  const refs = allFiles.filter((p) => p.startsWith("references/"));
  for (const rel of refs) {
    try {
      const absSrc = resolveSafe(sourceId, rel);
      const buf = await fs.readFile(absSrc);
      const dest = path.join(workspaceRoot(newWs.id), rel);
      await fs.mkdir(path.dirname(dest), { recursive: true });
      await fs.writeFile(dest, buf);
    } catch {}
  }

  // Brand carries over too if the source workspace had one.
  const brand = await getBrand(sourceId);
  const brandLine = brand
    ? `Brand: ${brand.name}. Voice: ${brand.voice}. Use the brand's exact colors (--bg ${brand.colors.bg}, --fg ${brand.colors.fg}, --accent ${brand.colors.accent}) and fonts (${brand.fonts.display.family} display, ${brand.fonts.body.family} body).`
    : "";

  // ── Server-side codegen: turn the design HTML into a complete
  //    Next.js scaffold BEFORE the model gets the seed. The frontend
  //    is now 100% finished by the time stage 1 starts; the model's
  //    only job is the backend (API routes + DB + form-handler
  //    hydration). Drift is impossible because the model never
  //    touches the design HTML.
  const primaryHtmlFile =
    FORMAT_FILE_FOR(formatHint, designFiles) ??
    designFiles.find((f) => /\.html?$/i.test(f));
  const scaffoldFiles: string[] = [];
  let detectedSections: string[] = [];
  let detectedForms: { selector: string; intent: string }[] = [];
  let detectedHasAuth = false;
  let detectedHasUploads = false;
  if (primaryHtmlFile) {
    try {
      const primaryAbs = resolveSafe(sourceId, primaryHtmlFile);
      const primaryHtml = await fs.readFile(primaryAbs, "utf8");
      const scaffold = generateScaffold({
        html: primaryHtml,
        workspaceName: source.name,
        brand: brand ?? undefined,
      });
      // Write every generated file into the new workbench workspace.
      for (const f of scaffold.files) {
        await writeFile(newWs.id, f.path, f.content);
        scaffoldFiles.push(f.path);
      }
      detectedSections = scaffold.sections;
      detectedForms = scaffold.forms;
      detectedHasAuth = scaffold.hasAuth;
      detectedHasUploads = scaffold.hasUploads;
      console.log(
        `[build] codegen: wrote ${scaffold.files.length} files, ${scaffold.sections.length} sections, ${scaffold.forms.length} forms, auth=${scaffold.hasAuth}, uploads=${scaffold.hasUploads}`,
      );
    } catch (err) {
      console.error(
        "[build] codegen failed; falling back to model-only build:",
        err,
      );
    }
  }
  const codegenSucceeded = scaffoldFiles.length > 0;

  // ── Auto-install + dev-server boot — in the BACKGROUND.
  // The user's first chat experience should be a running app, but
  // awaiting `bun install` here (60–120s on a cold container) pushed
  // the whole request past Cloudflare's ~100s proxy timeout in
  // production: the client saw a 524 "Build failed" toast while the
  // build actually completed server-side. Nothing downstream needs the
  // result synchronously — the workbench Preview tab polls the runtime
  // port and lights up when the dev server is ready — so return now
  // and let install + boot finish on their own.
  if (codegenSucceeded) {
    void (async () => {
      try {
        const r = await execInRuntime(newWs.id, "cd /workspace && bun install", {
          timeoutMs: 120_000,
        });
        console.log(
          `[build] bun install (bg): exit=${r.exitCode} duration=${r.durationMs}ms`,
        );
        if (r.exitCode !== 0) {
          console.error(
            `[build] bun install (bg) failed:\n${(r.stdout + "\n" + r.stderr).slice(-2000)}`,
          );
          return;
        }
        // Start dev server detached. Container keeps it alive; the
        // Preview tab auto-detects port 5173 via the workbench's
        // runtime polling.
        await spawnDetached(newWs.id, "cd /workspace && bun run dev");
        console.log(`[build] dev server detached (bg)`);
      } catch (err) {
        console.error("[build] background install/dev-server failed:", err);
      }
    })();
  }

  // Compose the seed prompt the workbench chat auto-fires.
  //
  // CRITICAL: this build MUST be split across 3 stages via the
  // <jarvisplan stages="3"> mechanism. A single-turn full-stack
  // build blows past the model's 8K output cap (DeepSeek) and
  // truncates mid-artifact, leaving the workspace half-built. The
  // runtime auto-progresses through stages once each one verifies
  // green — the user gets a complete project without re-prompting.
  const designFileList = designFiles.map((f) => `  - design/${f}`).join("\n");

  // Build the inline <design_source> block. Cap the total at ~80KB so
  // the seed stays within token-budget headroom even on context-light
  // models. If the captured sources exceed that, prioritize the file
  // that matches the format hint (landing.html for landing-page
  // builds, etc.) and add others until the cap.
  const SEED_SOURCE_CAP = 80 * 1024;
  const sortedSources = [...designSources].sort((a, b) => {
    // Primary file first (the format-matching one), then by size ascending.
    const aPrim = a.rel === FORMAT_FILE_FOR(formatHint, designFiles) ? 0 : 1;
    const bPrim = b.rel === FORMAT_FILE_FOR(formatHint, designFiles) ? 0 : 1;
    if (aPrim !== bPrim) return aPrim - bPrim;
    return a.content.length - b.content.length;
  });
  let used = 0;
  const inlinedSources: typeof sortedSources = [];
  for (const src of sortedSources) {
    if (used + src.content.length > SEED_SOURCE_CAP) continue;
    inlinedSources.push(src);
    used += src.content.length;
  }
  const designSourceBlock = inlinedSources
    .map(
      (s) =>
        `<design_source filename="design/${s.rel}">\n${s.content}\n</design_source>`,
    )
    .join("\n\n");

  // Two seed paths:
  //   - codegenSucceeded === true  →  Frontend + backend + deps +
  //     dev server are ALREADY DONE by the build pipeline. The chat
  //     is for ITERATION only (refinements like "make the hero red"
  //     or "add a contact form").
  //   - codegenSucceeded === false →  Fall back to the old
  //     translation-based path so the user isn't stuck if codegen
  //     can't parse a particular design.
  let seed: string;
  if (codegenSucceeded) {
    const sectionList = detectedSections
      .map((n) => `  - components/${n}.tsx`)
      .join("\n");
    const formList = detectedForms
      .map((f) => `  - ${f.selector} → /api/${f.intent.toLowerCase().replace(/[^a-z0-9]+/g, "-")} (table submissions_${f.intent.toLowerCase().replace(/[^a-z0-9_]+/g, "_")})`)
      .join("\n");
    seed = [
      `The build is COMPLETE. The user's design will render in the Preview tab as soon as the dev server finishes booting (a minute or two on a cold start).`,
      ``,
      `What's already done (no model turns needed):`,
      `  ✓ Frontend split into ${detectedSections.length} component files at components/<Name>.tsx (byte-copied from the design — DO NOT modify these unless explicitly asked, regenerating loses visual fidelity)`,
      `  ✓ app/layout.tsx with Google Fonts + metadata pulled from the design head`,
      `  ✓ app/page.tsx composing every section in the original body order`,
      `  ✓ app/globals.css with the design's CSS vars + custom styles`,
      `  ✓ package.json + tsconfig + Tailwind 4 + Next 15 setup`,
      `  ⏳ \`bun install\` + dev server (0.0.0.0:5173) are starting in the background — the Preview tab connects automatically when ready. Don't re-run them.`,
      detectedForms.length > 0
        ? `  ✓ ${detectedForms.length} form(s) detected, API routes generated:\n${formList}`
        : `  ✓ No forms detected — backend is minimal`,
      `  ✓ lib/db.ts — dual-mode: SQLite for local dev, Postgres in production (set DATABASE_URL in Settings → Secrets to flip)`,
      `  ✓ components/FormsHydrate.tsx — real onSubmit hydration (FormData → JSON → /api/<intent>)`,
      detectedHasAuth
        ? `  ✓ Auth.js v5 scaffolded (auth.ts + handlers + middleware) — credentials provider works out of the box; OAuth providers (Google, GitHub) auto-activate when GOOGLE_CLIENT_ID/GITHUB_CLIENT_ID are set in Secrets`
        : "",
      detectedHasUploads
        ? `  ✓ /api/upload route scaffolded — local-disk storage in dev (uploads/), production swap to Vercel Blob is a one-line change documented in the file`
        : "",
      ``,
      `═══ YOUR ROLE ═══`,
      `The user is here to iterate, not to build. They'll ask for changes like:`,
      `  • "Make the hero text bigger"`,
      `  • "Change the accent color to navy"`,
      `  • "Add a section about pricing"`,
      `  • "The reservations form should require a phone number"`,
      ``,
      `When they ask, edit the relevant file(s) directly with a boltAction. You DO NOT need to plan stages, run \`bun install\`, or start the dev server — those run automatically. The dev server hot-reloads on every file save.`,
      ``,
      `Files you can edit freely:`,
      `  • components/<Name>.tsx — section components (HTML lives in the \`HTML\` template literal; preserve byte-perfect unless asked to change)`,
      `  • app/globals.css — global styles, design tokens, custom CSS`,
      `  • app/layout.tsx — metadata, fonts, layout`,
      `  • app/api/*/route.ts — API handlers (auto-generated for detected forms; tighten zod schemas, add validation, add email notifications, etc.)`,
      `  • lib/db.ts — schema (currently a generic JSON-payload table per form; refactor into proper schemas if asked)`,
      `  • components/FormsHydrate.tsx — form behavior (success states, validation, multi-step flows)`,
      ``,
      `Greet the user briefly with a one-sentence summary of what you built (e.g. "Built ${detectedSections.length} sections + ${detectedForms.length} form route${detectedForms.length === 1 ? "" : "s"} from your design — check Preview. What would you like to tweak?"), then wait for their first refinement.`,
      ``,
      brandLine,
    ]
      .filter(Boolean)
      .join("\n");
  } else {
    // Fallback path — original translation-based seed.
    seed = [
      `Take the ${formatHint} design in the \`design/\` folder and ship a real, working full-stack version of it that VISUALLY MATCHES the design exactly. You are the senior software engineer building this — own every layer end-to-end.`,
      ``,
      `Design files copied for you:`,
      designFileList,
      ``,
      `═══ DESIGN SOURCE — read this BEFORE writing any code ═══`,
      designSourceBlock,
      `═══ END DESIGN SOURCE ═══`,
      ``,
      `Copy EXACT colors, fonts, copy text, image URLs, and class names from the design source above. Improvising = wrong.`,
      ``,
      `MANDATORY: emit \`<jarvisplan stages="3">\` at the start.`,
      `  Stage 1 — Scaffold + faithful frontend (one component file per section).`,
      `  Stage 2 — Backend (sqlite + API routes).`,
      `  Stage 3 — Wire forms + start dev server.`,
      ``,
      `Dev server binds 0.0.0.0:5173 (only exposed port).`,
      ``,
      brandLine,
    ]
      .filter(Boolean)
      .join("\n");
  }

  return NextResponse.json({
    workspaceId: newWs.id,
    workspaceName: newWs.name,
    seed,
    copiedFiles: designFiles.length,
  });
}
