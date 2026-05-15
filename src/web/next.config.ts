import type { NextConfig } from "next";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { existsSync } from "node:fs";
import { config as loadDotenv } from "dotenv";

// Load the repo-root .env first so centralized LLM provider keys
// (GROQ_API_KEY, DEEPSEEK_API_KEY, GOOGLE_API_KEY, etc., consolidated
// 2026-05-15) flow into process.env before Next.js's built-in
// `.env.local` overlay. Next.js auto-loads .env files only from the
// project dir (src/web/), so we have to bring root in manually.
// Subproject-specific vars (KIMI_BASE_URL, NEXT_PUBLIC_APP_URL, etc.)
// stay in src/web/.env.local and override root on collision.
const __here = dirname(fileURLToPath(import.meta.url));
const repoRootEnv = resolve(__here, "../../.env");
if (existsSync(repoRootEnv)) {
  loadDotenv({ path: repoRootEnv });
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
