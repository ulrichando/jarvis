"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ExternalLink,
  FileText,
  Maximize2,
  MessageSquarePlus,
  Minus,
  Plus,
  RefreshCw,
  X,
} from "lucide-react";
import { apiReadFile, type TreeEntry } from "@/lib/workspace/client";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { type Tweak, tweakToMessage } from "@/lib/design/tweaks";
import { ext, IMAGE_EXT } from "./file-classify";

export type DesignComment = {
  filePath: string;
  selector: string;
  tag: string;
  text: string;
  comment: string;
};

export function DesignPreview({
  workspaceId,
  selected,
  showToolbar = false,
  streaming = null,
  onComment,
  tweaks,
  tweakOverrides,
}: {
  workspaceId: string;
  selected: TreeEntry | null;
  /** When true, render the preview toolbar (refresh / zoom / present)
   *  above the content. Used when a file tab is the active center view. */
  showToolbar?: boolean;
  /** Live partial HTML being streamed by the LLM. When present, render this
   *  over the selected-file view so the canvas updates as Claude generates. */
  streaming?: { filePath: string; content: string } | null;
  /** Fired when the user clicks an element on the canvas in comment mode and
   *  submits a targeted change request. The parent (DesignView) prefills the
   *  chat composer with a structured prompt that asks the model to edit
   *  ONLY the picked element. */
  onComment?: (c: DesignComment) => void;
  /** Tweak declarations parsed from the file by the parent. */
  tweaks?: Tweak[];
  /** Current panel-side override values keyed by tweak id. */
  tweakOverrides?: Record<string, Tweak["value"]>;
}) {
  if (streaming && streaming.content) {
    return (
      <div className="flex h-full flex-col">
        {showToolbar && <PreviewToolbar disabled />}
        <StreamingPreview content={streaming.content} filePath={streaming.filePath} />
      </div>
    );
  }

  if (!selected || selected.type === "dir") {
    return (
      <div className="flex h-full flex-col">
        {showToolbar && <PreviewToolbar disabled />}
        <EmptyPreview />
      </div>
    );
  }

  return (
    <PreviewContainer
      workspaceId={workspaceId}
      selected={selected}
      showToolbar={showToolbar}
      onComment={onComment}
      tweaks={tweaks}
      tweakOverrides={tweakOverrides}
    />
  );
}

function StreamingPreview({ content, filePath }: { content: string; filePath: string }) {
  const safe = content.includes("</html>")
    ? content
    : `${content}\n</body></html>`;
  const isHtml = /\.html?$/i.test(filePath);
  return (
    <div className="relative h-full w-full overflow-hidden bg-white">
      <div className="absolute right-3 top-3 z-10 flex items-center gap-2 rounded-full bg-black/70 px-3 py-1 text-[11px] font-medium text-white">
        <span className="size-2 animate-pulse rounded-full bg-emerald-400" />
        Generating {filePath}…
      </div>
      {isHtml ? (
        <iframe
          title={filePath}
          srcDoc={safe}
          sandbox="allow-scripts"
          className="h-full w-full border-0 bg-white"
        />
      ) : (
        <pre className="h-full overflow-auto p-5 font-mono text-[12px] leading-5 text-foreground/90">
          {content}
        </pre>
      )}
    </div>
  );
}

const ZOOM_STEPS = [50, 67, 75, 80, 90, 100, 110, 125, 150, 175, 200] as const;

function PreviewContainer({
  workspaceId,
  selected,
  showToolbar,
  onComment,
  tweaks,
  tweakOverrides,
}: {
  workspaceId: string;
  selected: TreeEntry;
  showToolbar: boolean;
  onComment?: (c: DesignComment) => void;
  tweaks?: Tweak[];
  tweakOverrides?: Record<string, Tweak["value"]>;
}) {
  const qc = useQueryClient();
  const [iframeKey, setIframeKey] = useState(0);
  const [zoom, setZoom] = useState(100);
  const [commentMode, setCommentMode] = useState(false);

  const refresh = () => {
    qc.invalidateQueries({
      queryKey: ["design-file", workspaceId, selected.path],
    });
    setIframeKey((k) => k + 1);
  };

  const presentHref = `/api/workspace/${workspaceId}/file?path=${encodeURIComponent(
    selected.path,
  )}&raw=1`;

  const e = ext(selected.name);
  const isHtml = e === "html" || e === "htm";
  const isImage = IMAGE_EXT.has(e);

  return (
    <div className="flex h-full flex-col">
      {showToolbar && (
        <PreviewToolbar
          onRefresh={refresh}
          presentHref={isHtml || isImage ? presentHref : null}
          zoom={zoom}
          onZoomIn={() =>
            setZoom((z) => ZOOM_STEPS.find((s) => s > z) ?? z)
          }
          onZoomOut={() =>
            setZoom((z) => [...ZOOM_STEPS].reverse().find((s) => s < z) ?? z)
          }
          onZoomReset={() => setZoom(100)}
          commentMode={isHtml ? commentMode : undefined}
          onCommentModeChange={isHtml ? setCommentMode : undefined}
        />
      )}
      <div className="flex-1 min-h-0">
        {isHtml ? (
          <HtmlPreview
            workspaceId={workspaceId}
            path={selected.path}
            iframeKey={iframeKey}
            zoom={zoom}
            commentMode={commentMode}
            onCommentModeOff={() => setCommentMode(false)}
            onComment={onComment}
            tweaks={tweaks}
            tweakOverrides={tweakOverrides}
          />
        ) : isImage ? (
          <ImagePreview
            workspaceId={workspaceId}
            path={selected.path}
            name={selected.name}
            zoom={zoom}
          />
        ) : (
          <CodePreview workspaceId={workspaceId} path={selected.path} />
        )}
      </div>
    </div>
  );
}

function PreviewToolbar({
  onRefresh,
  presentHref,
  zoom = 100,
  onZoomIn,
  onZoomOut,
  onZoomReset,
  disabled = false,
  commentMode,
  onCommentModeChange,
}: {
  onRefresh?: () => void;
  presentHref?: string | null;
  zoom?: number;
  onZoomIn?: () => void;
  onZoomOut?: () => void;
  onZoomReset?: () => void;
  disabled?: boolean;
  commentMode?: boolean;
  onCommentModeChange?: (next: boolean) => void;
}) {
  const showComment = onCommentModeChange != null;
  return (
    <div
      className={cn(
        "flex h-11 shrink-0 items-center gap-2 border-b border-border/50 px-3",
        disabled && "opacity-60",
      )}
    >
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label="Refresh"
        title="Refresh"
        onClick={onRefresh}
        disabled={disabled || !onRefresh}
      >
        <RefreshCw className="size-3.5" />
      </Button>

      {showComment && (
        <Button
          variant={commentMode ? "secondary" : "ghost"}
          size="sm"
          className="rounded-md"
          aria-pressed={commentMode}
          title="Comment on an element — click to highlight, then type a change request"
          onClick={() => onCommentModeChange?.(!commentMode)}
          disabled={disabled}
        >
          <MessageSquarePlus className="size-3.5" />
          Comment
        </Button>
      )}

      <div className="ml-auto flex items-center gap-1">
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Zoom out"
          title="Zoom out"
          onClick={onZoomOut}
          disabled={disabled || zoom <= ZOOM_STEPS[0]}
        >
          <Minus className="size-3.5" />
        </Button>
        <button
          type="button"
          onClick={onZoomReset}
          disabled={disabled}
          className="rounded-md px-2 py-1 font-mono text-[11.5px] tabular-nums text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none"
        >
          {zoom}%
        </button>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Zoom in"
          title="Zoom in"
          onClick={onZoomIn}
          disabled={disabled || zoom >= ZOOM_STEPS[ZOOM_STEPS.length - 1]}
        >
          <Plus className="size-3.5" />
        </Button>
        <span className="mx-1 h-4 w-px bg-border/60" />
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Open full screen"
          title="Open full screen"
          render={
            presentHref ? (
              <a href={presentHref} target="_blank" rel="noreferrer" />
            ) : undefined
          }
          nativeButton={!presentHref}
          disabled={disabled || !presentHref}
        >
          <Maximize2 className="size-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Open in new tab"
          title="Open in new tab"
          render={
            presentHref ? (
              <a href={presentHref} target="_blank" rel="noreferrer" />
            ) : undefined
          }
          nativeButton={!presentHref}
          disabled={disabled || !presentHref}
        >
          <ExternalLink className="size-3.5" />
        </Button>
      </div>
    </div>
  );
}

// Selection-layer JS injected into the iframe when comment mode is enabled.
// Adds hover outlines, captures clicks, and posts a structured payload back
// to the parent. Disabled by default — the parent posts an "enable" message
// to activate. All listeners are passive when disabled, so this is safe to
// inject unconditionally; we only inject when commentMode flips on so users
// not commenting see clean iframes.
const PICKER_SCRIPT = `
(function(){
  const ACCENT = '#FF6A00';
  const STYLE = 'jarvis-pick-' + Math.random().toString(36).slice(2,8);
  const css = '.' + STYLE + '_hover{outline:2px solid ' + ACCENT + ';outline-offset:-2px;cursor:crosshair;}'
    + '.' + STYLE + '_picked{outline:2px solid ' + ACCENT + ';outline-offset:-2px;box-shadow:0 0 0 9999px rgba(0,0,0,.18);}';
  const styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);

  let enabled = false;
  let lastHover = null;
  let lastPicked = null;

  function selectorFor(el){
    const parts = [];
    let cur = el;
    let depth = 0;
    while (cur && cur !== document.body && cur !== document.documentElement && depth < 6) {
      let part = cur.tagName.toLowerCase();
      if (cur.id) { parts.unshift('#' + cur.id); return parts.join(' > '); }
      const cn = (typeof cur.className === 'string' ? cur.className : '').trim();
      const cls = cn.split(/\\s+/).filter(c => c && !c.startsWith(STYLE)).slice(0, 2);
      if (cls.length) part += '.' + cls.join('.');
      const parent = cur.parentElement;
      if (parent) {
        const sibs = Array.prototype.filter.call(parent.children, s => s.tagName === cur.tagName);
        if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(cur) + 1) + ')';
      }
      parts.unshift(part);
      cur = parent;
      depth++;
    }
    return parts.join(' > ');
  }

  function clearHover(){ if (lastHover) { lastHover.classList.remove(STYLE + '_hover'); lastHover = null; } }
  function clearPick(){ if (lastPicked) { lastPicked.classList.remove(STYLE + '_picked'); lastPicked = null; } }

  document.addEventListener('mousemove', function(e){
    if (!enabled) return;
    const t = e.target;
    if (lastHover === t) return;
    clearHover();
    if (t && t !== document.body && !t.classList.contains(STYLE + '_picked')) {
      t.classList.add(STYLE + '_hover');
      lastHover = t;
    }
  }, true);

  document.addEventListener('mouseleave', clearHover, true);

  document.addEventListener('click', function(e){
    if (!enabled) return;
    e.preventDefault();
    e.stopPropagation();
    const t = e.target;
    clearHover();
    clearPick();
    t.classList.add(STYLE + '_picked');
    lastPicked = t;
    const text = (t.innerText || t.textContent || '').replace(/\\s+/g,' ').trim().slice(0, 200);
    const html = (t.outerHTML || '').slice(0, 400);
    parent.postMessage({
      type: 'jarvis:design:select',
      selector: selectorFor(t),
      tag: t.tagName.toLowerCase(),
      text: text,
      html: html
    }, '*');
  }, true);

  function snakeToCamel(s){ return s.replace(/_([a-z])/g, function(_, c){ return c.toUpperCase(); }); }

  function applyTweak(id, kind, value) {
    if (kind === 'color-swatches' || kind === 'range') {
      document.documentElement.style.setProperty('--' + id, String(value));
    } else if (kind === 'segmented' || kind === 'toggle') {
      // dataset uses camelCase keys, the CSS [data-foo_bar="x"] selector uses
      // the literal id, so set the attribute directly to keep both happy.
      document.body.setAttribute('data-' + id, String(value));
      document.body.dataset[snakeToCamel(id)] = String(value);
    } else if (kind === 'text') {
      var nodes = document.querySelectorAll('[data-tweak-text="' + id + '"]');
      for (var i = 0; i < nodes.length; i++) nodes[i].textContent = String(value);
    }
  }

  window.addEventListener('message', function(e){
    if (!e.data || typeof e.data !== 'object') return;
    if (e.data.type === 'jarvis:design:enable') { enabled = true; }
    if (e.data.type === 'jarvis:design:disable') { enabled = false; clearHover(); clearPick(); }
    if (e.data.type === 'jarvis:design:clear-pick') { clearPick(); }
    if (e.data.type === 'jarvis:design:tweak') { applyTweak(e.data.id, e.data.kind, e.data.value); }
  });
})();
`;

function injectPickerScript(html: string): string {
  const tag = `<script>${PICKER_SCRIPT}</script>`;
  if (html.includes("</body>")) return html.replace("</body>", `${tag}</body>`);
  if (html.includes("</html>")) return html.replace("</html>", `${tag}</html>`);
  return html + tag;
}

function HtmlPreview({
  workspaceId,
  path,
  iframeKey,
  zoom,
  commentMode,
  onCommentModeOff,
  onComment,
  tweaks = [],
  tweakOverrides,
}: {
  workspaceId: string;
  path: string;
  iframeKey: number;
  zoom: number;
  commentMode: boolean;
  onCommentModeOff: () => void;
  onComment?: (c: DesignComment) => void;
  /** Tweak declarations parsed from the file. Used to look up the kind for
   *  postMessage when we apply overrides. */
  tweaks?: Tweak[];
  /** Current panel-side override values keyed by tweak id. When this object
   *  changes (or the iframe reloads) we replay every override into the iframe. */
  tweakOverrides?: Record<string, Tweak["value"]>;
}) {
  const { data: content = "", isLoading, isError } = useQuery({
    queryKey: ["design-file", workspaceId, path],
    queryFn: () => apiReadFile(workspaceId, path),
  });
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const [picked, setPicked] = useState<{
    selector: string;
    tag: string;
    text: string;
    html: string;
  } | null>(null);
  const [comment, setComment] = useState("");

  // Always inject the picker script. Selection (highlight + click-to-comment)
  // is gated by an enable/disable message so users not commenting see a clean
  // iframe; tweak-application listeners run unconditionally so the right-side
  // panel can drive live changes regardless of comment mode.
  const html = useMemo(() => injectPickerScript(content), [content]);

  // Replay every tweak override whenever they change. Posting again is
  // idempotent (setting a CSS variable to the same value is a no-op) so we
  // don't track which messages were already delivered.
  useEffect(() => {
    const win = iframeRef.current?.contentWindow;
    if (!win || !tweakOverrides) return;
    for (const [id, value] of Object.entries(tweakOverrides)) {
      const t = tweaks.find((x) => x.id === id);
      if (!t) continue;
      win.postMessage(tweakToMessage(t, value), "*");
    }
  }, [tweakOverrides, tweaks, iframeKey, html]);

  // Listen for picks coming out of the iframe.
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.source !== iframeRef.current?.contentWindow) return;
      const data = event.data as { type?: string } | null;
      if (!data || data.type !== "jarvis:design:select") return;
      const { selector, tag, text, html } = data as unknown as {
        selector: string;
        tag: string;
        text: string;
        html: string;
      };
      setPicked({ selector, tag, text, html });
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, []);

  // Tell the iframe when commentMode flips. The iframe is reloaded via
  // srcDoc when html changes, so on re-mount we send 'enable' once it's ready.
  useEffect(() => {
    const win = iframeRef.current?.contentWindow;
    if (!win) return;
    win.postMessage(
      { type: commentMode ? "jarvis:design:enable" : "jarvis:design:disable" },
      "*",
    );
    if (!commentMode) {
      setPicked(null);
      setComment("");
    }
  }, [commentMode, html]);

  const cancel = () => {
    setPicked(null);
    setComment("");
    iframeRef.current?.contentWindow?.postMessage(
      { type: "jarvis:design:clear-pick" },
      "*",
    );
  };

  const submit = () => {
    if (!picked || !comment.trim()) return;
    onComment?.({
      filePath: path,
      selector: picked.selector,
      tag: picked.tag,
      text: picked.text,
      comment: comment.trim(),
    });
    setPicked(null);
    setComment("");
    iframeRef.current?.contentWindow?.postMessage(
      { type: "jarvis:design:clear-pick" },
      "*",
    );
    onCommentModeOff();
  };

  if (isLoading) return <PreviewLoading />;
  if (isError) return <PreviewError />;

  const scale = zoom / 100;
  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-white">
      <div className="relative flex-1 min-h-0">
        <div
          className="origin-top-left h-full w-full"
          style={{
            width: `${100 / scale}%`,
            height: `${100 / scale}%`,
            transform: `scale(${scale})`,
          }}
        >
          <iframe
            key={iframeKey}
            ref={iframeRef}
            title={path}
            srcDoc={html}
            sandbox="allow-scripts"
            className="h-full w-full border-0 bg-white"
            onLoad={() => {
              const win = iframeRef.current?.contentWindow;
              if (!win) return;
              if (commentMode) {
                win.postMessage({ type: "jarvis:design:enable" }, "*");
              }
              // Replay every override so the iframe matches the panel state
              // immediately on (re)load — without this the file would render
              // with its declared defaults until the user touched a control.
              if (tweakOverrides) {
                for (const [id, value] of Object.entries(tweakOverrides)) {
                  const t = tweaks?.find((x) => x.id === id);
                  if (!t) continue;
                  win.postMessage(tweakToMessage(t, value), "*");
                }
              }
            }}
          />
        </div>

        {commentMode && !picked && (
          <div className="pointer-events-none absolute left-1/2 top-3 z-10 -translate-x-1/2 rounded-full bg-black/75 px-3 py-1 text-[11px] font-medium text-white">
            Comment mode — click any element to leave a change request
          </div>
        )}
      </div>

      {picked && commentMode && (
        <CommentPopover
          tag={picked.tag}
          text={picked.text || picked.selector}
          comment={comment}
          onCommentChange={setComment}
          onCancel={cancel}
          onSubmit={submit}
        />
      )}
    </div>
  );
}

function CommentPopover({
  tag,
  text,
  comment,
  onCommentChange,
  onCancel,
  onSubmit,
}: {
  tag: string;
  text: string;
  comment: string;
  onCommentChange: (next: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  return (
    <div className="shrink-0 border-t border-border/60 bg-background p-3">
      <div className="mb-2 flex items-center gap-2 text-[11px] text-muted-foreground">
        <span className="rounded bg-muted px-2 py-0.5 font-mono text-foreground">
          {tag}
        </span>
        <span className="line-clamp-1 max-w-[60ch]">{text}</span>
      </div>
      <div className="flex items-end gap-2">
        <textarea
          value={comment}
          onChange={(e) => onCommentChange(e.target.value)}
          placeholder="What should change about this element? (e.g. 'make this bigger and warmer in tone')"
          rows={2}
          autoFocus
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              onSubmit();
            }
            if (e.key === "Escape") {
              e.preventDefault();
              onCancel();
            }
          }}
          className="flex-1 resize-none rounded-md border border-border/60 bg-background px-3 py-1.5 text-[13px] focus:border-foreground/40 focus:outline-none"
        />
        <div className="flex gap-1">
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label="Cancel"
            title="Cancel (Esc)"
            onClick={onCancel}
          >
            <X className="size-3.5" />
          </Button>
          <Button
            size="sm"
            onClick={onSubmit}
            disabled={!comment.trim()}
            title="Send (Cmd/Ctrl+Enter)"
          >
            Send
          </Button>
        </div>
      </div>
    </div>
  );
}

function ImagePreview({
  workspaceId,
  path,
  name,
  zoom,
}: {
  workspaceId: string;
  path: string;
  name: string;
  zoom: number;
}) {
  const scale = zoom / 100;
  return (
    <div className="flex h-full items-center justify-center overflow-auto bg-muted/20">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={`/api/workspace/${workspaceId}/file?path=${encodeURIComponent(
          path,
        )}&raw=1`}
        alt={name}
        className="max-h-full max-w-full object-contain"
        style={{ transform: `scale(${scale})`, transformOrigin: "center" }}
      />
    </div>
  );
}

function CodePreview({
  workspaceId,
  path,
}: {
  workspaceId: string;
  path: string;
}) {
  const { data: content = "", isLoading, isError } = useQuery({
    queryKey: ["design-file", workspaceId, path],
    queryFn: () => apiReadFile(workspaceId, path),
  });
  if (isLoading) return <PreviewLoading />;
  if (isError) return <PreviewError />;
  return (
    <div className="h-full overflow-auto bg-background">
      <pre className="m-0 whitespace-pre-wrap wrap-break-word p-5 font-mono text-[12px] leading-5 text-foreground/90">
        {content}
      </pre>
    </div>
  );
}

function EmptyPreview() {
  return (
    <div className="flex flex-1 items-center justify-center text-[13px] text-muted-foreground">
      Select a file to preview
    </div>
  );
}

function PreviewLoading() {
  return (
    <div className="flex h-full items-center justify-center text-[13px] text-muted-foreground">
      loading…
    </div>
  );
}

function PreviewError() {
  return (
    <div className="flex h-full items-center justify-center gap-2 text-[13px] text-muted-foreground">
      <FileText className="size-4" /> Couldn&apos;t read this file.
    </div>
  );
}
