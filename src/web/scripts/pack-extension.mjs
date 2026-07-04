#!/usr/bin/env node
// Pack the Jarvis-in-Chrome extension into public/jarvis-in-chrome.zip so the
// web app can serve it (Settings → Jarvis in Chrome → Download). The extension
// source lives at src/extensions/jarvis-screen, OUTSIDE src/web — and the web
// Docker build context is src/web only, so the deployed container can't reach
// the source. Committing the packed zip into public/ is what makes the
// download work in production. Re-run this after changing the extension:
//     node scripts/pack-extension.mjs
//
// If the source dir isn't reachable (e.g. a src/web-only checkout), it leaves
// the committed zip untouched and exits 0 — so `npm run build` never breaks.

import { execFileSync } from "node:child_process";
import { cpSync, existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = join(here, "..");
const src = join(webRoot, "..", "extensions", "jarvis-screen");
const out = join(webRoot, "public", "jarvis-in-chrome.zip");

if (!existsSync(src)) {
  console.log(`[pack-extension] source not present (${src}) — keeping committed zip.`);
  process.exit(0);
}

// Stage a clean copy named jarvis-in-chrome/ (nice folder for "Load unpacked"),
// dropping the test harness + any stray node_modules.
const stage = mkdtempSync(join(tmpdir(), "jarvis-ext-"));
const pkgDir = join(stage, "jarvis-in-chrome");
cpSync(src, pkgDir, {
  recursive: true,
  filter: (p) => !/(^|\/)(tests|node_modules)(\/|$)/.test(p.slice(src.length)),
});

rmSync(out, { force: true });
execFileSync("zip", ["-r", "-q", out, "jarvis-in-chrome"], { cwd: stage });
rmSync(stage, { recursive: true, force: true });
console.log(`[pack-extension] wrote ${out}`);
