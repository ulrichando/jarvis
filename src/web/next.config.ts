import type { NextConfig } from "next";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

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
