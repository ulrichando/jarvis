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
};

export default nextConfig;
