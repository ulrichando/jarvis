// Server-side esbuild bundler for a SINGLE self-contained React artifact.
// Mirrors the workspace bundler's esbuild config (api/workspace/[id]/bundle)
// but bundles a string instead of workspace files: artifacts are DB-backed
// single modules with no filesystem, so there's nothing to resolve from
// disk. Bare (`react`) + https imports stay external — the iframe's import
// map resolves them to a single canonical React via esm.sh. esbuild only
// transpiles (never executes), and the output runs only inside the
// sandboxed preview iframe.

import { build, type Plugin } from "esbuild";

// Mark every bare specifier (react, react-dom/client, lucide-react, …) and
// https URL as external. A single-file artifact has no relative imports to
// resolve; if one appears it errors (caught + surfaced to the user).
function externalizeBareAndHttps(): Plugin {
  return {
    name: "jarvis-artifact-externals",
    setup(b) {
      b.onResolve({ filter: /^https?:\/\// }, (a) => ({
        path: a.path,
        external: true,
      }));
      // Bare package specifiers. React (+ its subpaths) resolve via the
      // iframe import map so JSX-runtime dedupes to one React@18.3.1.
      // EVERYTHING ELSE is rewritten to an esm.sh URL so the model can
      // import ANY npm package (lucide-react, recharts, three, framer-
      // motion, d3, zustand, …) and it just works — broader than claude.ai's
      // fixed allow-list. `?deps` pins it to our React so hooks don't break.
      b.onResolve({ filter: /^[^./]/ }, (a) => {
        if (
          a.path === "react" ||
          a.path === "react-dom" ||
          a.path.startsWith("react/") ||
          a.path.startsWith("react-dom/")
        ) {
          return { path: a.path, external: true };
        }
        return {
          path: `https://esm.sh/${a.path}?deps=react@18.3.1,react-dom@18.3.1`,
          external: true,
        };
      });
      b.onResolve({ filter: /^\.\.?\// }, (a) => ({
        errors: [
          {
            text: `Artifacts are single-file — cannot import local module "${a.path}". Inline it instead.`,
          },
        ],
      }));
    },
  };
}

export async function bundleReactSource(
  source: string,
): Promise<{ js: string } | { error: string }> {
  if (!/export\s+default/.test(source)) {
    return {
      error:
        "A React artifact must `export default` a component (no required props).",
    };
  }
  // Turn the default export into a referable binding, then mount it.
  // `const __ArtifactDefault = function Foo(){}` / `= class {}` / `= () => …`
  // are all valid — replacing the keyword pair works for every form.
  const entry = `
${source.replace(/export\s+default\s+/, "const __ArtifactDefault = ")}
import { createRoot as __createRoot } from "react-dom/client";
import { createElement as __createElement } from "react";
const __rootEl = document.getElementById("root");
if (__rootEl) __createRoot(__rootEl).render(__createElement(__ArtifactDefault));
`;
  try {
    const result = await build({
      stdin: {
        contents: entry,
        loader: "tsx",
        sourcefile: "artifact.tsx",
      },
      bundle: true,
      write: false,
      format: "esm",
      target: "es2022",
      jsx: "automatic",
      // Bare "react" → emits `react/jsx-runtime`, kept external by the
      // plugin and resolved by the iframe import map.
      jsxImportSource: "react",
      sourcemap: "inline",
      platform: "browser",
      logLevel: "silent",
      plugins: [externalizeBareAndHttps()],
    });
    const file = result.outputFiles?.[0];
    if (!file) return { error: "bundle produced no output" };
    return { js: file.text };
  } catch (e) {
    const msg =
      e && typeof e === "object" && "message" in e
        ? String((e as { message: unknown }).message)
        : "bundle failed";
    return { error: msg };
  }
}
