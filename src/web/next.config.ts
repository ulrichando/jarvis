import type { NextConfig } from "next";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { config as loadDotenv } from "dotenv";

// Load the repo-root .env first for non-secret shared config (model ids,
// JARVIS_PG_DSN, LANGCHAIN_*, etc.). Next.js auto-loads .env files only from
// the project dir (src/web/), so we bring root in manually.
const __here = dirname(fileURLToPath(import.meta.url));
const repoRootEnv = resolve(__here, "../../.env");
if (existsSync(repoRootEnv)) {
  loadDotenv({ path: repoRootEnv });
}

// ~/.jarvis/keys.env is the single secret store for LLM provider keys
// (ANTHROPIC/DEEPSEEK/GROQ/KIMI/OPENAI/…), shared with the voice agent and the
// CLI/proxy. Loaded AFTER root with override:true so a rotated key here wins —
// the same last-source-wins order start.sh uses. Provider keys moved out of
// root .env into here 2026-06-15, so this load is what gives web its keys.
const keysEnv = resolve(homedir(), ".jarvis", "keys.env");
if (existsSync(keysEnv)) {
  loadDotenv({ path: keysEnv, override: true });
}

const nextConfig: NextConfig = {
  turbopack: {
    root: dirname(fileURLToPath(import.meta.url)),
  },
  // esbuild ships its native binary inside @esbuild/<platform> packages
  // alongside non-JS files (README.md, LICENSE) that Turbopack tries to
  // walk and chokes on. Marking it external means Node's runtime
  // require/import resolves it directly, no bundler involvement.
  serverExternalPackages: ["esbuild"],
  // Next.js 16 blocks cross-origin access to dev resources (HMR
  // websocket, _next/static chunks, _next/image) by default — the
  // server's canonical origin is `localhost`, so loading the page
  // from `127.0.0.1` (which the tray "Open in Browser" does) gets
  // blocked and JS/CSS chunks fail to load mid-render. Allowlist
  // both names plus the LAN IP wildcard so accessing JARVIS from
  // another device on the same network still works.
  // See: node_modules/next/dist/docs/.../allowedDevOrigins.md
  allowedDevOrigins: ["127.0.0.1", "localhost", "*.local"],
};

export default nextConfig;
