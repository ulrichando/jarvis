"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import CodeMirror from "@uiw/react-codemirror";
import { javascript } from "@codemirror/lang-javascript";
import { html } from "@codemirror/lang-html";
import { css } from "@codemirror/lang-css";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { oneDark } from "@codemirror/theme-one-dark";
import type { Extension } from "@codemirror/state";
import { Save, Loader2, ChevronRight, FileText } from "lucide-react";
import { apiReadFile, apiWriteFile } from "@/lib/workspace/client";

type Props = {
  workspaceId: string;
  path: string | null;
};

function langFor(path: string): Extension[] {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  if (["ts", "tsx", "js", "jsx", "mjs", "cjs"].includes(ext))
    return [javascript({ jsx: true, typescript: ext.startsWith("t") })];
  if (ext === "html" || ext === "htm") return [html()];
  if (ext === "css") return [css()];
  if (ext === "json") return [json()];
  if (ext === "md" || ext === "markdown") return [markdown()];
  return [];
}

export function Editor({ workspaceId, path }: Props) {
  const [content, setContent] = useState<string>("");
  const [loaded, setLoaded] = useState<string | null>(null); // path of loaded file
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastLoadedRef = useRef<string>("");

  useEffect(() => {
    if (!path) {
      setContent("");
      setLoaded(null);
      setDirty(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    apiReadFile(workspaceId, path)
      .then((c) => {
        if (cancelled) return;
        setContent(c);
        lastLoadedRef.current = c;
        setLoaded(path);
        setDirty(false);
      })
      .catch((e) => {
        if (cancelled) return;
        setError((e as Error).message ?? "failed to load");
        // Mark `loaded` so the render guard stops showing the spinner —
        // the error block will render instead.
        setLoaded(path);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [workspaceId, path]);

  const extensions = useMemo(() => (path ? langFor(path) : []), [path]);

  const save = useCallback(async () => {
    if (!path || saving) return;
    setSaving(true);
    try {
      await apiWriteFile(workspaceId, path, content);
      lastLoadedRef.current = content;
      setDirty(false);
    } catch (e) {
      // Don't fail silently — the previous version swallowed write errors,
      // so a failed save looked identical to a successful one. Surface it
      // and keep dirty=true so the user knows their change didn't persist.
      toast.error(`Save failed: ${(e as Error).message ?? "unknown error"}`);
    } finally {
      setSaving(false);
    }
  }, [workspaceId, path, content, saving]);

  // Cmd/Ctrl+S. Depend on `save` (a useCallback) so the listener isn't
  // re-registered on every render, but always calls the current closure.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        void save();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save]);

  if (!path) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Pick a file from the tree to start editing.
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-border/50 text-xs">
        <Breadcrumbs path={path} />
        {dirty && (
          <span className="text-[10px] uppercase tracking-wide text-amber-500 mx-2">
            modified
          </span>
        )}
        <button
          onClick={save}
          disabled={!dirty || saving}
          className="flex items-center gap-1 rounded px-2 py-0.5 text-xs hover:bg-accent disabled:opacity-40"
        >
          {saving ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <Save className="size-3" />
          )}
          Save
        </button>
      </div>
      <div className="flex-1 overflow-hidden">
        {error ? (
          <div className="flex h-full items-center justify-center text-xs text-destructive px-4 text-center">
            {error}
          </div>
        ) : loading || loaded !== path ? (
          <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
          </div>
        ) : (
          <CodeMirror
            value={content}
            theme={oneDark}
            extensions={extensions}
            onChange={(v) => {
              setContent(v);
              setDirty(v !== lastLoadedRef.current);
            }}
            height="100%"
            style={{ height: "100%", fontSize: 13 }}
          />
        )}
      </div>
    </div>
  );
}

function Breadcrumbs({ path }: { path: string }) {
  const parts = path.split("/").filter(Boolean);
  const fileName = parts[parts.length - 1] ?? path;
  const dirs = parts.slice(0, -1);
  return (
    <nav className="flex items-center gap-1 min-w-0 font-mono text-[12px] text-muted-foreground">
      {dirs.map((seg, i) => (
        <span key={i} className="flex items-center gap-1 shrink-0">
          <span>{seg}</span>
          <ChevronRight className="size-3 opacity-60" />
        </span>
      ))}
      <span className="flex items-center gap-1 truncate text-foreground">
        <FileText className="size-3.5 shrink-0 opacity-70" />
        <span className="truncate">{fileName}</span>
      </span>
    </nav>
  );
}
