import "server-only";
import { promises as fs } from "node:fs";
import path from "node:path";
import { resolveSafe, listAllFiles } from "@/lib/workspace/storage";

// Scaffolds: pre-baked starter projects we copy into a workspace so
// JARVIS doesn't have to write boilerplate from scratch (package.json,
// tsconfig, build configs, dev script, root layout, CSS resets…).
//
// Production AI coders (Bolt, Lovable, v0, Replit Agent) all ship 30+
// scaffolds — it's the single biggest reliability unlock. Scaffolds
// also bake in runtime fixes (polling watchOptions, host bind, port,
// data/ dir) that the model frequently forgets to write.
//
// Scaffold definitions live on disk under src/web/scaffolds/<name>/
// so adding a new one is just dropping a directory; no code change.

export type Scaffold = {
  id: string;
  label: string;
  description: string;
  // What the scaffold builds (informational, surfaced in UI).
  stack: string[];
  // Hints for LLM context — shown to the model after apply so it knows
  // what to extend and which paths are sacred.
  hints: string;
};

export const SCAFFOLDS: Scaffold[] = [
  {
    id: "next-14-tailwind",
    label: "Next.js 14 + Tailwind",
    description:
      "App-router Next 14 with Tailwind, dark theme tokens, polling hot-reload pre-wired.",
    stack: ["Next 14", "React 18", "Tailwind 3", "TypeScript"],
    hints: [
      "Pages live under app/. Add new routes as app/<route>/page.tsx.",
      "Color tokens: var(--bg), var(--fg), var(--accent), var(--muted), var(--supporting). Set via :root in app/globals.css.",
      "Dev script binds 5173:0.0.0.0 and uses CHOKIDAR/WATCHPACK polling — DO NOT change.",
      "next.config.js has webpack.watchOptions for Docker hot-reload — DO NOT change.",
    ].join("\n"),
  },
  {
    id: "vite-react-tailwind",
    label: "Vite + React + Tailwind",
    description:
      "Vite 5 + React 18 SPA with Tailwind, polling watch, host binding pre-wired.",
    stack: ["Vite 5", "React 18", "Tailwind 3", "TypeScript"],
    hints: [
      "Entry: src/main.tsx → src/App.tsx. Add routes via react-router-dom if needed.",
      "vite.config.ts has server.watch.usePolling — DO NOT change.",
    ].join("\n"),
  },
  {
    id: "express-sqlite-api",
    label: "Express + SQLite API",
    description:
      "Bun + Express + better-sqlite3 starter with health route and migrations init.",
    stack: ["Bun", "Express 4", "better-sqlite3", "Zod"],
    hints: [
      "App entry: src/index.ts. DB module: src/db.ts (auto-creates data/ + WAL).",
      "Add new tables in db.ts init(). Use prepared statements for queries.",
      "Routes: app.get / app.post on the express() instance.",
    ].join("\n"),
  },
];

export function findScaffold(id: string): Scaffold | undefined {
  return SCAFFOLDS.find((s) => s.id === id);
}

const scaffoldsDir = () =>
  path.resolve(process.cwd(), "scaffolds");

/**
 * Copy every file from scaffolds/<id>/ into the workspace. Skips files
 * that already exist in the workspace — never overwrites the user's
 * work. Returns the list of files actually copied (for the response
 * + for the chat layer to surface in the next prompt).
 */
export async function applyScaffold({
  workspaceId,
  scaffoldId,
}: {
  workspaceId: string;
  scaffoldId: string;
}): Promise<{ copied: string[]; skipped: string[] }> {
  const scaffold = findScaffold(scaffoldId);
  if (!scaffold) throw new Error(`unknown scaffold: ${scaffoldId}`);
  const srcRoot = path.join(scaffoldsDir(), scaffold.id);
  const copied: string[] = [];
  const skipped: string[] = [];

  // Discover existing files first so we can skip without overwriting.
  const existing = new Set(await listAllFiles(workspaceId));

  async function walk(rel: string) {
    const abs = path.join(srcRoot, rel);
    const entries = await fs.readdir(abs, { withFileTypes: true });
    for (const e of entries) {
      const childRel = rel ? path.posix.join(rel, e.name) : e.name;
      if (e.isDirectory()) {
        await walk(childRel);
        continue;
      }
      if (existing.has(childRel)) {
        skipped.push(childRel);
        continue;
      }
      const dest = resolveSafe(workspaceId, childRel);
      await fs.mkdir(path.dirname(dest), { recursive: true });
      const buf = await fs.readFile(path.join(srcRoot, childRel));
      await fs.writeFile(dest, buf);
      copied.push(childRel);
    }
  }

  await walk("");
  return { copied, skipped };
}
