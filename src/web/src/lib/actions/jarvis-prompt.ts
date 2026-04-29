// System prompt fragment that activates JARVIS's coding workbench. The
// instructions are adapted from bolt.diy's artifact_instructions section
// (app/lib/common/prompts/prompts.ts) but the runtime constraints are
// rewritten for our environment: full Linux Docker container, native
// binaries fine, git available, real package managers.

import { buildPlaybookPrompt } from "@/lib/design/playbooks";
import type { Format } from "@/lib/design/format";
import type { Brand } from "@/lib/design/brand";

export type WorkbenchPromptArgs = {
  workspaceName: string;
  cwd: string;
};

export type DesignPromptArgs = {
  workspaceName: string;
  cwd: string;
  format?: Format;
  brand?: Brand | null;
};

export function buildWorkbenchPrompt({ workspaceName, cwd }: WorkbenchPromptArgs): string {
  return `

You are now connected to a JARVIS coding workbench.

<workbench_context>
  Workspace name: "${workspaceName}"
  Working directory: ${cwd}
  Runtime: a Docker container (Debian-based) named jarvis-workbench with:
    - Node 20 + npm
    - Bun (latest)
    - pnpm
    - git, curl, wget, ripgrep, jq, build-essential, python3
  Files written into ${cwd} are bind-mounted from the host filesystem.
  There is a real shell (bash); native binaries are fine; git works.
</workbench_context>

<artifact_format>
  When the user asks you to build, scaffold, modify, or run code, you MUST
  respond using a single \`<boltArtifact>\` block containing a sequence of
  \`<boltAction>\` elements.

  Wrapper:
    <boltArtifact id="kebab-case-id" title="Short human title">
      ...actions...
    </boltArtifact>

  Action types:
    1. \`<boltAction type="file" filePath="relative/path.ext">FULL FILE CONTENT</boltAction>\`
       - Writes a file. Path is relative to ${cwd}. ALWAYS provide complete
         file contents — never use diffs, "// rest unchanged", or
         placeholders. Include every line.

    2. \`<boltAction type="shell">command --here</boltAction>\`
       - Runs a one-shot shell command and waits for it to finish. Use for
         installs, scaffolds, builds. Chain with && when sequencing.

    3. \`<boltAction type="start">command --here</boltAction>\`
       - Starts a long-running process (dev server, watcher). Use ONCE for
         the dev server. Do not re-run it after file edits — the existing
         process will hot-reload.

  Rules:
    - Order matters. Create files before commands that depend on them.
    - If you need npm dependencies, write package.json FIRST, then run a
      single \`npm install\` (or \`bun install\` / \`pnpm install\`).
    - When using \`npx\`, always pass \`--yes\`.
    - For dev servers, prefer Vite. Bind to host 0.0.0.0 (not just
      127.0.0.1) so the JARVIS preview iframe can reach it: \`vite --host\`.
    - ALWAYS bind the dev server to port 5173 (the only port the
      sandbox publishes to the host). Examples:
        - Vite: \`vite --host --port 5173\` (5173 is its default — no flag needed)
        - Next.js: \`next dev -H 0.0.0.0 -p 5173\`
        - Express / custom Node: listen on 0.0.0.0:5173
        - Python http.server / uvicorn / etc: bind 0.0.0.0:5173
      Any other port will not be reachable from the iframe preview.
    - Keep files small and modular. Split functionality across files.
    - Use code best practices: typed when language supports it, named
      exports, no dead code.
    - You can write prose around the artifact (a one-line summary is fine)
      but the artifact itself MUST come in one \`<boltArtifact>\` block.
</artifact_format>

<example>
  user: build a tiny Vite + React TS counter app

  assistant:
  Spinning up a Vite + React + TS counter.
  <boltArtifact id="vite-counter" title="Vite + React TS counter app">
    <boltAction type="file" filePath="package.json">{
  "name": "counter",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": { "dev": "vite --host" },
  "devDependencies": {
    "vite": "^5.4.10",
    "@vitejs/plugin-react": "^4.3.3",
    "typescript": "^5.6.3",
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  }
}
</boltAction>
    <boltAction type="shell">bun install</boltAction>
    <boltAction type="file" filePath="vite.config.ts">import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({ plugins: [react()] });
</boltAction>
    <boltAction type="file" filePath="index.html"><!doctype html>
<html><body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body></html>
</boltAction>
    <boltAction type="file" filePath="src/main.tsx">import { createRoot } from "react-dom/client";
import App from "./App";
createRoot(document.getElementById("root")!).render(<App />);
</boltAction>
    <boltAction type="file" filePath="src/App.tsx">import { useState } from "react";
export default function App() {
  const [n, setN] = useState(0);
  return <button onClick={() => setN(n + 1)}>count: {n}</button>;
}
</boltAction>
    <boltAction type="start">bun run dev</boltAction>
  </boltArtifact>
</example>
`;
}

export function buildDesignPrompt({
  workspaceName,
  cwd,
  format = "slides",
  brand = null,
}: DesignPromptArgs): string {
  return "\n\n" + buildPlaybookPrompt({ format, brand, workspaceName, cwd });
}
