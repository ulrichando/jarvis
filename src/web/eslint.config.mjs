import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
  {
    // React Compiler migration debt (downgraded 2026-06-09, was 36
    // errors). The Next 16 / react-hooks v6 upgrade promoted these
    // compiler diagnostics to errors across long-standing patterns in
    // the design/workbench/chat suites. Blind mechanical fixes are NOT
    // safe: e.g. moving a localStorage read from an effect into a lazy
    // useState initializer satisfies set-state-in-effect but introduces
    // an SSR hydration mismatch. Each site needs a per-component pass
    // with the UI exercised. Until that migration happens, keep the
    // signal visible as warnings instead of failing the lint run.
    rules: {
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/refs": "warn",
      "react-hooks/immutability": "warn",
      // Honor the underscore convention for intentionally-unused
      // identifiers — handler signatures that must match a type but
      // ignore a param (`_ctx`), destructured-rest discards, caught
      // errors that are deliberately swallowed. Keeps the real
      // unused-var signal while dropping these false positives.
      "@typescript-eslint/no-unused-vars": [
        "warn",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
          destructuredArrayIgnorePattern: "^_",
        },
      ],
    },
  },
]);

export default eslintConfig;
