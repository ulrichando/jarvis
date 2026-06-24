"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { Loader2 } from "lucide-react";
import { javascript } from "@codemirror/lang-javascript";
import { html as htmlLang } from "@codemirror/lang-html";
import { css } from "@codemirror/lang-css";
import { json } from "@codemirror/lang-json";
import { markdown as markdownLang } from "@codemirror/lang-markdown";
import { oneDark } from "@codemirror/theme-one-dark";
import type { Extension } from "@codemirror/state";
import { Markdown } from "@/components/markdown/markdown";
import type { ArtifactKind } from "@/lib/actions/types";
import {
  ARTIFACT_IFRAME_SANDBOX,
  buildHtmlDoc,
  buildReactDoc,
  buildSvgDoc,
} from "@/lib/artifacts/iframe";

const CodeMirror = dynamic(() => import("@uiw/react-codemirror"), {
  ssr: false,
});

type Props = {
  kind: ArtifactKind;
  content: string;
  language?: string | null;
  mode: "preview" | "code";
  // Public page passes server-bundled JS so no authed bundle fetch is needed.
  bundledJs?: string;
};

export function ArtifactRender({
  kind,
  content,
  language,
  mode,
  bundledJs,
}: Props) {
  // `code` has no live preview — always the source view.
  if (mode === "code" || kind === "code") {
    return <CodeView content={content} kind={kind} language={language} />;
  }
  if (kind === "markdown") {
    return (
      <div className="mx-auto max-w-2xl px-6 py-6">
        <Markdown content={content} />
      </div>
    );
  }
  if (kind === "mermaid") return <MermaidView content={content} />;
  if (kind === "csv") return <CsvTable content={content} />;
  if (kind === "json") return <JsonView content={content} />;
  if (kind === "react")
    return <ReactPreview source={content} bundledJs={bundledJs} />;
  // html + svg → static sandboxed doc.
  const doc = kind === "svg" ? buildSvgDoc(content) : buildHtmlDoc(content);
  return <Sandbox doc={doc} />;
}

function Sandbox({ doc }: { doc: string }) {
  const ref = useRef<HTMLIFrameElement>(null);

  // AI-powered-apps bridge: handle window.jarvis.{complete,callTool} RPC from
  // THIS iframe only. The parent holds the session, so the iframe (opaque
  // origin, no cookies) can reach the LLM/MCP without us exposing creds to
  // untrusted code. Capped per mount against runaway loops.
  useEffect(() => {
    const state = { calls: 0 };
    const onMsg = async (e: MessageEvent) => {
      const win = ref.current?.contentWindow;
      if (!win || e.source !== win) return; // only our own iframe
      const d = e.data as {
        __jarvis_rpc_req?: boolean;
        id?: string;
        method?: string;
        payload?: unknown;
      };
      if (!d || !d.__jarvis_rpc_req || !d.id) return;
      const reply = (b: Record<string, unknown>) =>
        win.postMessage({ __jarvis_rpc_res: true, id: d.id, ...b }, "*");
      // ponytail: flat 100-call cap per mount stops runaway loops; raise if a
      // legit AI-app needs more.
      if (state.calls++ >= 100)
        return reply({ error: "artifact call limit reached" });
      const endpoint =
        d.method === "complete"
          ? "/api/artifacts/complete"
          : d.method === "mcp"
            ? "/api/artifacts/mcp"
            : null;
      if (!endpoint) return reply({ error: "unknown method" });
      try {
        const r = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(d.payload ?? {}),
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || j.error)
          return reply({ error: j.error ?? `request failed (${r.status})` });
        reply({ result: d.method === "complete" ? j.text : j.result });
      } catch (err) {
        reply({ error: String((err as Error)?.message ?? err) });
      }
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, []);

  return (
    <iframe
      ref={ref}
      // Keying on the doc forces a clean reload on version/content switch.
      key={doc.length}
      title="Artifact preview"
      srcDoc={doc}
      sandbox={ARTIFACT_IFRAME_SANDBOX}
      className="h-full w-full border-0 bg-white"
    />
  );
}

function ReactPreview({
  source,
  bundledJs,
}: {
  source: string;
  bundledJs?: string;
}) {
  const [js, setJs] = useState<string | null>(bundledJs ?? null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!bundledJs);

  useEffect(() => {
    if (bundledJs) {
      setJs(bundledJs);
      setLoading(false);
      return;
    }
    let alive = true;
    setError(null);
    // Debounce: while the artifact streams, `source` changes on every chunk
    // and is usually mid-write (an unterminated string/regex). Bundling that
    // flickered build errors. Wait ~500ms for the source to settle, then
    // bundle once. We DON'T flip back to the spinner here, so an already-
    // rendered preview keeps showing while a revision re-bundles.
    const timer = setTimeout(() => {
      fetch("/api/artifacts/bundle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source }),
      })
        .then(async (r) => {
          if (!r.ok) throw new Error((await r.text()) || `bundle ${r.status}`);
          return r.text();
        })
        .then((text) => {
          if (alive) {
            setJs(text);
            setError(null);
            setLoading(false);
          }
        })
        .catch((e) => {
          if (!alive) return;
          const msg = String(e?.message ?? e);
          // "Unterminated string/regexp", "unexpected end of file" → the
          // source is still mid-stream (or was truncated). Keep showing
          // "Building…" instead of flashing a red error; the next settled
          // bundle renders it. Only surface genuine errors.
          if (/unterminated|unexpected end|end of (file|input)/i.test(msg)) {
            setLoading(true);
          } else {
            setError(msg);
            setLoading(false);
          }
        });
    }, 500);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [source, bundledJs]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        <span className="ml-2 text-xs">Building preview…</span>
      </div>
    );
  }
  if (error || !js) {
    return (
      <div className="h-full overflow-auto bg-[#190b0b] p-5 font-mono text-[12.5px] leading-relaxed text-rose-300 whitespace-pre-wrap">
        ⚠ {error ?? "no preview"}
      </div>
    );
  }
  return <Sandbox doc={buildReactDoc(js)} />;
}

function MermaidView({ content }: { content: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        // strict: mermaid sanitizes labels + forbids click/script in the
        // emitted SVG. We then render that SVG inside the sandboxed iframe
        // (not the parent DOM), so there's no innerHTML sink to exploit.
        mermaid.initialize({
          startOnLoad: false,
          theme: "neutral",
          securityLevel: "strict",
        });
        const id = "artifact-mmd-" + Math.random().toString(36).slice(2);
        const { svg } = await mermaid.render(id, content);
        if (alive) setSvg(svg);
      } catch (e) {
        if (alive) setError(String((e as Error)?.message ?? e));
      }
    })();
    return () => {
      alive = false;
    };
  }, [content]);

  if (error) {
    return (
      <div className="p-5 font-mono text-[12.5px] text-destructive whitespace-pre-wrap">
        ⚠ {error}
      </div>
    );
  }
  if (!svg) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
      </div>
    );
  }
  // Isolated in the sandbox iframe — no parent-DOM innerHTML.
  return <Sandbox doc={buildSvgDoc(svg)} />;
}

// ponytail: naive CSV split (handles quotes, not multi-line quoted fields).
// Fine for preview rendering; upgrade to a real parser if needed.
function parseCsv(text: string): string[][] {
  return text
    .trim()
    .split(/\r?\n/)
    .map((line) => {
      const cells: string[] = [];
      let cur = "";
      let q = false;
      for (let i = 0; i < line.length; i++) {
        const ch = line[i];
        if (q) {
          if (ch === '"') {
            if (line[i + 1] === '"') {
              cur += '"';
              i++;
            } else q = false;
          } else cur += ch;
        } else if (ch === ",") {
          cells.push(cur);
          cur = "";
        } else if (ch === '"') q = true;
        else cur += ch;
      }
      cells.push(cur);
      return cells;
    });
}

function CsvTable({ content }: { content: string }) {
  const rows = useMemo(() => parseCsv(content), [content]);
  if (rows.length === 0)
    return <CodeView content={content} kind="csv" language="csv" />;
  const [head, ...body] = rows;
  return (
    <div className="h-full overflow-auto bg-background p-3 text-[12px]">
      <table className="w-full border-collapse">
        <thead>
          <tr>
            {head.map((h, i) => (
              <th
                key={i}
                className="border border-border/50 bg-muted/40 px-2 py-1 text-left font-medium"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((r, ri) => (
            <tr key={ri}>
              {r.map((c, ci) => (
                <td key={ci} className="border border-border/40 px-2 py-1">
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function JsonView({ content }: { content: string }) {
  const pretty = useMemo(() => {
    try {
      return JSON.stringify(JSON.parse(content), null, 2);
    } catch {
      return content; // not valid JSON → show raw
    }
  }, [content]);
  return (
    <pre className="h-full overflow-auto whitespace-pre bg-background p-4 font-mono text-[12px] leading-relaxed text-foreground">
      {pretty}
    </pre>
  );
}

function CodeView({
  content,
  kind,
  language,
}: {
  content: string;
  kind: ArtifactKind;
  language?: string | null;
}) {
  const extensions = useMemo<Extension[]>(() => {
    const lang = (language ?? "").toLowerCase();
    if (kind === "react" || ["ts", "tsx", "js", "jsx", "mjs", "cjs"].includes(lang))
      return [javascript({ jsx: true, typescript: lang.startsWith("t") || kind === "react" })];
    if (kind === "html" || kind === "svg" || ["html", "htm", "xml", "svg"].includes(lang))
      return [htmlLang()];
    if (kind === "markdown" || ["md", "markdown"].includes(lang))
      return [markdownLang()];
    if (lang === "css") return [css()];
    if (lang === "json") return [json()];
    if (["py", "python"].includes(lang)) return [];
    return [];
  }, [kind, language]);

  return (
    <div className="h-full overflow-auto">
      <CodeMirror
        value={content}
        theme={oneDark}
        extensions={extensions}
        editable={false}
        basicSetup={{
          lineNumbers: true,
          foldGutter: false,
          highlightActiveLine: false,
        }}
        style={{ fontSize: 13 }}
      />
    </div>
  );
}
