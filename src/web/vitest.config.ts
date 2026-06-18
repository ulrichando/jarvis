import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
    exclude: ["node_modules/**", ".next/**"],
    // The bridge integration tests `await import()` Next route handlers, which
    // pull in drizzle + better-sqlite3 on first load. Under the full parallel
    // run the cold import alone can exceed the default 5s per-test timeout —
    // not a hang, just a slow first import. 20s gives headroom while still
    // failing genuinely stuck tests.
    testTimeout: 20000,
    hookTimeout: 20000,
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      include: ["src/lib/ai/kimi/**", "src/components/chat/kimi-*.tsx"],
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      // server-only's default export throws if loaded outside React Server
      // Components. Alias to the package's own empty.js so server modules
      // can be unit-tested directly. Same trick the Next.js docs use.
      "server-only": path.resolve(
        __dirname,
        "./node_modules/server-only/empty.js",
      ),
    },
  },
});
