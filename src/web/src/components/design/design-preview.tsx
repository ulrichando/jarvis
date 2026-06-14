"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Copy,
  ExternalLink,
  FileText,
  Maximize2,
  MessageSquarePlus,
  Minus,
  Pencil,
  Plus,
  RefreshCw,
  X,
} from "lucide-react";
import { codeToHtml } from "shiki";
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
  // Streaming preview only takes over the canvas when the user hasn't
  // explicitly selected a different file. Otherwise the user can't pull up
  // the source of an already-finished file while a *new* file is mid-write.
  const userPickedDifferentFile =
    streaming &&
    selected &&
    selected.type === "file" &&
    selected.path !== streaming.filePath;

  if (streaming && streaming.content && !userPickedDifferentFile) {
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
          // Match HtmlPreview's sandbox flags so streaming previews can
          // also fetch the path-mirror endpoints for relative imports.
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
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
  const [editMode, setEditMode] = useState(false);
  // Preview ⇄ Code view toggle (Claude-artifact parity). Sticky across file
  // switches so a user reading the source stays in source.
  const [viewMode, setViewMode] = useState<"preview" | "code">("preview");

  // Comment and Edit are mutually exclusive — turning one on disables the other.
  const setCommentModeExclusive = (next: boolean) => {
    setCommentMode(next);
    if (next) setEditMode(false);
  };
  const setEditModeExclusive = (next: boolean) => {
    setEditMode(next);
    if (next) setCommentMode(false);
  };

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
          viewMode={isHtml ? viewMode : undefined}
          onViewModeChange={isHtml ? setViewMode : undefined}
          commentMode={isHtml && viewMode === "preview" ? commentMode : undefined}
          onCommentModeChange={
            isHtml && viewMode === "preview" ? setCommentModeExclusive : undefined
          }
          editMode={isHtml && viewMode === "preview" ? editMode : undefined}
          onEditModeChange={
            isHtml && viewMode === "preview" ? setEditModeExclusive : undefined
          }
        />
      )}
      <div className="flex-1 min-h-0">
        {isHtml ? (
          viewMode === "code" ? (
            <CodePreview workspaceId={workspaceId} path={selected.path} />
          ) : (
            <HtmlPreview
              workspaceId={workspaceId}
              path={selected.path}
              iframeKey={iframeKey}
              zoom={zoom}
              commentMode={commentMode}
              onCommentModeOff={() => setCommentMode(false)}
              onComment={onComment}
              editMode={editMode}
              tweaks={tweaks}
              tweakOverrides={tweakOverrides}
            />
          )
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
  viewMode,
  onViewModeChange,
  commentMode,
  onCommentModeChange,
  editMode,
  onEditModeChange,
}: {
  onRefresh?: () => void;
  presentHref?: string | null;
  zoom?: number;
  onZoomIn?: () => void;
  onZoomOut?: () => void;
  onZoomReset?: () => void;
  disabled?: boolean;
  viewMode?: "preview" | "code";
  onViewModeChange?: (next: "preview" | "code") => void;
  commentMode?: boolean;
  onCommentModeChange?: (next: boolean) => void;
  editMode?: boolean;
  onEditModeChange?: (next: boolean) => void;
}) {
  const showView = onViewModeChange != null;
  const showComment = onCommentModeChange != null;
  const showEdit = onEditModeChange != null;
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

      {showView && (
        <div className="flex items-center rounded-md border border-border/60 p-0.5 text-[12px]">
          {(["preview", "code"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => onViewModeChange?.(m)}
              disabled={disabled}
              aria-pressed={viewMode === m}
              className={cn(
                "rounded px-2 py-0.5 capitalize transition-colors",
                viewMode === m
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {m}
            </button>
          ))}
        </div>
      )}

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

      {showEdit && (
        <Button
          variant={editMode ? "secondary" : "ghost"}
          size="sm"
          className="rounded-md"
          aria-pressed={editMode}
          title="Edit text inline — click any text to edit. Enter to commit, Esc to revert."
          onClick={() => onEditModeChange?.(!editMode)}
          disabled={disabled}
        >
          <Pencil className="size-3.5" />
          Edit
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
//
// Also installs an inline error overlay so when a module import 404s,
// React fails to mount, or a component throws, the user sees the actual
// error in the iframe instead of a silent black page. Without this we
// have to ask the user to open devtools every time something breaks.
const PICKER_SCRIPT = `
(function(){
  // ── ERROR OVERLAY ───────────────────────────────────────────────
  // Catches uncaught errors + unhandled rejections + module-load failures
  // and renders them on top of the iframe content. Cheap, no deps,
  // fixed-position so it always wins z-index.
  function showErr(msg){
    try {
      var box = document.getElementById('__jarvis_err__');
      if (!box) {
        box = document.createElement('div');
        box.id = '__jarvis_err__';
        box.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:#0a0a0a;color:#ff6a6a;font:13px/1.5 ui-monospace,Menlo,Consolas,monospace;padding:24px;overflow:auto;white-space:pre-wrap;';
        (document.body || document.documentElement).appendChild(box);
        var h = document.createElement('div');
        h.style.cssText = 'color:#fff;font:600 13px/1.5 ui-sans-serif,system-ui;margin-bottom:12px;';
        h.textContent = 'Design failed to load';
        box.appendChild(h);
      }
      var line = document.createElement('div');
      line.textContent = '• ' + msg;
      line.style.marginTop = '6px';
      box.appendChild(line);
    } catch (e) { /* nowhere left to surface this */ }
  }
  // Per-resource error capture — the window-level error event fires
  // with an empty message/filename when a <script src=…> fails to
  // load (404, MIME mismatch, network blocked). Browsers scrub the
  // payload because the script never parsed, so the only useful
  // info is on the EventTarget itself: ev.target.src.
  //
  // ONLY treat SCRIPT and LINK (stylesheet) failures as fatal — they
  // break the page. IMG load failures are NOT fatal: the browser
  // already shows a broken-image icon and the surrounding layout
  // works. Surfacing them as a fullscreen overlay turned every
  // Unsplash blip / hotlink-block / ad-blocker hit into "Design
  // failed to load", which is wildly misleading.
  window.addEventListener('error', function(ev){
    var t = ev.target;
    if (t && t !== window && (t.tagName === 'SCRIPT' || t.tagName === 'LINK')) {
      var url = (t.src || t.href || '').replace(location.origin, '') || '(no src)';
      showErr(t.tagName.toLowerCase() + ' load failed: ' + url);
      return;
    }
    // Non-fatal load failures (img, video, audio, etc.) — let the
    // browser's native broken-resource UI handle them.
    if (t && t !== window && (t.tagName === 'IMG' || t.tagName === 'VIDEO' || t.tagName === 'AUDIO' || t.tagName === 'SOURCE')) {
      return;
    }
    var src = (ev.filename || '').replace(location.origin, '') || 'inline';
    var msg = ev.message || '';
    if (!msg && ev.error && ev.error.message) msg = ev.error.message;
    if (!msg) {
      // Last-resort: enumerate scripts so the user sees WHICH file
      // is in flight. Empty error events almost always come from
      // script-tag load failures the browser refused to detail.
      var scripts = document.querySelectorAll('script[src]');
      var srcs = [];
      for (var i = 0; i < scripts.length; i++) {
        srcs.push(scripts[i].src.replace(location.origin, ''));
      }
      msg = 'script-load failure (no detail). Loaded scripts: ' + (srcs.join(', ') || 'none');
    }
    showErr(src + ':' + (ev.lineno||'?') + '  ' + msg);
  }, true);
  window.addEventListener('unhandledrejection', function(ev){
    var r = ev.reason;
    var msg = (r && (r.stack || r.message)) || String(r);
    showErr('unhandled rejection: ' + msg);
  });
})();

(function(){
  // ── AUTO-FIT FIXED CANVASES ────────────────────────────────────
  // Slides (1920×1080), infographics (1080×1920), onepagers (A4) are
  // designed at a fixed pixel canvas and need to scale to fit the
  // preview iframe. The playbook tells the model to ship a scale-to-fit
  // script, but if it forgets, we auto-detect and inject one.
  //
  // Heuristic: after layout, if body.scrollWidth or scrollHeight exceed
  // the viewport by >5%, find the first top-level wrapper child (section,
  // div, main) and apply transform:scale(min(vw/cw, vh/ch)) to it so
  // the natural-sized design shrinks to fit. We only do this once on
  // load and on resize — never overrides an explicit transform the
  // model already set.
  function autoFit() {
    var body = document.body;
    if (!body) return;
    // Multi-slide deck path: if the page contains a stack of <.slide> blocks
    // (or any sibling fixed-size sections), scale ALL of them uniformly to
    // fit the viewport WIDTH so the user can scroll through every slide
    // at the same shrink factor. This is what most stacked-slide decks
    // need when the model forgot the scale script.
    var slides = document.querySelectorAll('.slide');
    if (slides.length >= 2) {
      var first = slides[0];
      var sw = first.scrollWidth || first.offsetWidth;
      if (sw && sw > window.innerWidth * 1.05) {
        var s = Math.min(1, window.innerWidth / sw);
        for (var i = 0; i < slides.length; i++) {
          var sl = slides[i];
          if ((sl.style.transform || '').indexOf('scale') !== -1) continue;
          sl.style.transformOrigin = '0 0';
          sl.style.transform = 'scale(' + s + ')';
          // Compensate vertical layout: scaled element occupies natural
          // pre-scale height. Set explicit margin-bottom that collapses the
          // gap so visually slides stack at the new size.
          var nh = sl.scrollHeight || sl.offsetHeight;
          sl.style.marginBottom = (nh * s - nh + 32) + 'px';
        }
        body.style.overflow = 'auto';
        return;
      }
    }
    // Single fixed canvas path — ONLY fires when the design is a
    // genuinely fixed-size canvas (slide/infographic/onepager). The
    // signal: the first element has an EXPLICIT pixel width set via
    // inline style or a fixed-pixel CSS class. A scrollable landing
    // page never sets that, so this path is skipped and the page
    // scrolls normally.
    //
    // Without this guard the auto-fit was firing on tall landings,
    // setting body.style.overflow="hidden" + explicit width/height,
    // which killed scroll AND triggered a ResizeObserver loop
    // (each style change re-fired the observer = flicker).
    var firstEl = body.firstElementChild;
    if (!firstEl) return;
    var existing = firstEl.style.transform || '';
    if (existing.indexOf('scale') !== -1) return;
    // Detect fixed-pixel canvas: must have explicit width:Npx in inline
    // style. Tailwind arbitrary-value classes like w-[1920px] compile
    // to inline style so this also catches model output that uses them.
    var inlineW = (firstEl.style.width || '').toString();
    var hasFixedCanvas = /^\d+(?:\.\d+)?px$/.test(inlineW);
    if (!hasFixedCanvas) return;
    var natW = firstEl.scrollWidth || firstEl.offsetWidth;
    var natH = firstEl.scrollHeight || firstEl.offsetHeight;
    if (!natW || !natH) return;
    var vw = window.innerWidth;
    var vh = window.innerHeight;
    if (natW <= vw * 1.05 && natH <= vh * 1.05) return;
    var s2 = Math.min(vw / natW, vh / natH);
    if (s2 >= 0.99) return;
    firstEl.style.transformOrigin = '0 0';
    firstEl.style.transform = 'scale(' + s2 + ')';
    body.style.margin = '0';
    body.style.overflow = 'hidden';
    body.style.width = (natW * s2) + 'px';
    body.style.height = (natH * s2) + 'px';
    body.style.marginLeft = 'auto';
    body.style.marginRight = 'auto';
  }
  // Reset every transform we previously set so autoFit() can measure
  // the natural pre-scale dimensions correctly. Clears scales on ALL
  // .slide elements (not just the first child) — the prior bug was
  // re-fitting only slide[0] on resize, leaving slides[1..N] stuck at
  // the old scale and overflowing the new viewport.
  function resetScales() {
    var body = document.body;
    if (!body) return;
    var slides = document.querySelectorAll('.slide');
    for (var i = 0; i < slides.length; i++) {
      slides[i].style.transform = '';
      slides[i].style.marginBottom = '';
    }
    var firstEl = body.firstElementChild;
    if (firstEl) {
      firstEl.style.transform = '';
    }
    // Reset body sizing too (single-canvas path sets explicit width/height).
    body.style.width = '';
    body.style.height = '';
  }
  // Loop-breaker. ResizeObserver fires whenever ANY layout-affecting
  // style changes — and refit() itself changes body width/height/transform.
  // Without this flag the observer would catch refit's own mutations and
  // schedule another refit, ad infinitum. We mute the observer for ~120ms
  // after each fit to let layout settle, then re-arm.
  var fitting = false;
  function refit() {
    if (fitting) return;
    fitting = true;
    resetScales();
    requestAnimationFrame(function(){
      setTimeout(function(){
        autoFit();
        // Re-arm after layout settles — if no further user interaction,
        // the observer stays silent. If the user resizes the panel
        // again, the next event triggers a fresh refit.
        setTimeout(function(){ fitting = false; }, 120);
      }, 0);
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function(){ setTimeout(autoFit, 0); });
  } else {
    setTimeout(autoFit, 0);
  }
  // Re-fit when React first mounts content.
  var rootObserved = false;
  function observeRoot() {
    if (rootObserved) return;
    rootObserved = true;
    var mo = new MutationObserver(function(){ setTimeout(autoFit, 0); });
    mo.observe(document.body, { childList: true, subtree: true });
    setTimeout(function(){ mo.disconnect(); }, 1500);
  }
  if (document.body) observeRoot();
  else document.addEventListener('DOMContentLoaded', observeRoot);
  // Re-fit on viewport resize. Debounced via rAF so a continuous drag
  // (splitter resize) doesn't fire 60+ refits per second.
  var resizeRaf = null;
  window.addEventListener('resize', function(){
    if (resizeRaf !== null) return;
    resizeRaf = requestAnimationFrame(function(){
      resizeRaf = null;
      refit();
    });
  }, { passive: true });
  // Also watch the iframe's documentElement for size changes — covers
  // edge cases where window.resize doesn't fire (some embed contexts)
  // but the iframe element itself was resized by the parent.
  if (typeof ResizeObserver !== 'undefined') {
    try {
      var ro = new ResizeObserver(function(){
        if (resizeRaf !== null) return;
        resizeRaf = requestAnimationFrame(function(){
          resizeRaf = null;
          refit();
        });
      });
      ro.observe(document.documentElement);
    } catch (e) { /* best effort */ }
  }
})();

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

  // ── Edit mode: contentEditable on every element that owns a direct
  //    text node, postMessage on commit.
  //
  // Previous version had a hardcoded tag allowlist (p, h1-h6, li,
  // span, etc.) and only marked LEAVES (no children). That missed:
  //   - <button>Submit</button>             (tag not in list)
  //   - <a>Click here</a>                   (tag not in list)
  //   - <div>Section title</div>            (div not in list)
  //   - <h1><span>Title</span></h1>         (h1 has children, span IS in list but only its text was editable)
  //   - <strong>/<em>/<small>/<label>/...   (none in list)
  // So large parts of typical landing pages were unclickable in edit mode.
  //
  // New approach: walk every element. If it has at least ONE direct
  // text node child with non-whitespace content, mark it editable.
  // That handles all the cases above. Skip a denylist of structural /
  // interactive / scripty tags that should never become contenteditable.
  let editEnabled = false;
  let editFocused = null;
  let editOriginal = '';
  const EDIT_SKIP_TAGS = {
    SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, IFRAME: 1, OBJECT: 1, EMBED: 1,
    INPUT: 1, TEXTAREA: 1, SELECT: 1, OPTION: 1, OPTGROUP: 1,
    CANVAS: 1, SVG: 1, MATH: 1, IMG: 1, VIDEO: 1, AUDIO: 1,
    SOURCE: 1, TRACK: 1, PICTURE: 1, BR: 1, HR: 1, WBR: 1,
  };

  function hasDirectTextNode(el) {
    if (!el || !el.childNodes) return false;
    for (let i = 0; i < el.childNodes.length; i++) {
      const n = el.childNodes[i];
      // nodeType 3 = TEXT_NODE
      if (n.nodeType === 3 && (n.textContent || '').trim().length > 0) {
        return true;
      }
    }
    return false;
  }

  function enableEdit() {
    editEnabled = true;
    document.body.setAttribute('data-jarvis-edit', '1');
    const all = document.body.getElementsByTagName('*');
    for (let i = 0; i < all.length; i++) {
      const el = all[i];
      if (EDIT_SKIP_TAGS[el.tagName]) continue;
      if (!hasDirectTextNode(el)) continue;
      el.setAttribute('contenteditable', 'plaintext-only');
      el.classList.add(STYLE + '_edit');
    }
    if (!document.getElementById(STYLE + '-editstyle')) {
      const s = document.createElement('style');
      s.id = STYLE + '-editstyle';
      s.textContent = '.' + STYLE + '_edit{cursor:text;}'
        + '.' + STYLE + '_edit:hover{outline:1px dashed ' + ACCENT + ';outline-offset:2px;}'
        + '.' + STYLE + '_edit:focus{outline:2px solid ' + ACCENT + ';outline-offset:2px;background:rgba(255,170,0,0.06);}'
        // While in edit mode, anchors must NOT navigate on click —
        // otherwise clicking a "Click here" link blows the iframe away
        // and exits edit mode. Pointer-events stays auto so the cursor
        // can still place inside the anchor; click default is suppressed
        // by the capture-phase handler below.
        + 'body[data-jarvis-edit] a{cursor:text;}';
      document.head.appendChild(s);
    }
  }

  function disableEdit() {
    editEnabled = false;
    document.body.removeAttribute('data-jarvis-edit');
    const marked = document.getElementsByClassName(STYLE + '_edit');
    // Snapshot to a static array — removing classes mutates the live HTMLCollection.
    const arr = Array.prototype.slice.call(marked);
    for (let i = 0; i < arr.length; i++) {
      arr[i].removeAttribute('contenteditable');
      arr[i].classList.remove(STYLE + '_edit');
    }
  }

  // While edit mode is active, suppress anchor navigation + button
  // submission. Otherwise clicking an editable <a> would navigate the
  // iframe away and clicking a <button type="submit"> would post the
  // form. The user is here to type, not to navigate.
  document.addEventListener('click', function(e){
    if (!editEnabled) return;
    const t = e.target;
    if (!t) return;
    // Walk up to find an anchor or button ancestor.
    let el = t;
    while (el && el !== document.body) {
      const tag = el.tagName;
      if (tag === 'A' || tag === 'BUTTON') {
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      el = el.parentElement;
    }
  }, true);

  document.addEventListener('focusin', function(e){
    if (!editEnabled) return;
    const t = e.target;
    if (t && t.classList && t.classList.contains(STYLE + '_edit')) {
      editFocused = t;
      editOriginal = t.textContent || '';
    }
  }, true);

  document.addEventListener('focusout', function(e){
    if (!editEnabled) return;
    const t = e.target;
    if (t === editFocused) {
      const next = (t.textContent || '').replace(/\\s+$/g, '');
      const prev = editOriginal.replace(/\\s+$/g, '');
      if (next !== prev) {
        parent.postMessage({
          type: 'jarvis:design:edit:commit',
          selector: selectorFor(t),
          tag: t.tagName.toLowerCase(),
          oldText: editOriginal,
          newText: t.textContent || '',
        }, '*');
      }
      editFocused = null;
      editOriginal = '';
    }
  }, true);

  document.addEventListener('keydown', function(e){
    if (!editEnabled || !editFocused) return;
    if (e.key === 'Escape') {
      editFocused.textContent = editOriginal;
      editFocused.blur();
    } else if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      editFocused.blur();
    }
  }, true);

  window.addEventListener('message', function(e){
    if (!e.data || typeof e.data !== 'object') return;
    if (e.data.type === 'jarvis:design:enable') { enabled = true; }
    if (e.data.type === 'jarvis:design:disable') { enabled = false; clearHover(); clearPick(); }
    if (e.data.type === 'jarvis:design:clear-pick') { clearPick(); }
    if (e.data.type === 'jarvis:design:tweak') { applyTweak(e.data.id, e.data.kind, e.data.value); }
    if (e.data.type === 'jarvis:design:edit:enable') { enableEdit(); }
    if (e.data.type === 'jarvis:design:edit:disable') { disableEdit(); }
  });

  // ── Defensive wiring for questions.html ──────────────────────────────
  // The model's own <script> in questions.html sometimes doesn't execute
  // (wrong button types, broken syntax, model dropped it entirely). The
  // parent side guarantees the form works as long as the model got the
  // structural attributes right (form id, [data-question] groups,
  // [data-value] chips, [data-other-for] inputs, a submit button).
  function wireQuestionsForm(){
    var form = document.getElementById('jarvis-questions');
    if (!form) return;
    if (form.getAttribute('data-jarvis-wired') === '1') return;
    form.setAttribute('data-jarvis-wired', '1');

    var selected = {};
    var groups = form.querySelectorAll('[data-question]');

    function wireGroup(group){
      var qid = group.getAttribute('data-question');
      var chips = group.querySelectorAll('[data-value]');
      for (var j = 0; j < chips.length; j++) {
        chips[j].addEventListener('click', function(e){
          e.preventDefault();
          for (var k = 0; k < chips.length; k++) chips[k].removeAttribute('data-selected');
          this.setAttribute('data-selected', 'true');
          selected[qid] = this.getAttribute('data-value');
        });
      }
    }
    for (var i = 0; i < groups.length; i++) wireGroup(groups[i]);

    function collectAndPost(e){
      if (e && e.preventDefault) e.preventDefault();
      var answers = {};
      for (var i = 0; i < groups.length; i++) {
        var qid = groups[i].getAttribute('data-question');
        var input = form.querySelector('[data-other-for="' + qid + '"]');
        if (input && input.value && input.value.trim()) {
          answers[qid] = input.value.trim();
        } else if (selected[qid]) {
          answers[qid] = selected[qid];
        }
      }
      try {
        parent.postMessage({ type: 'jarvis:design:questions:submit', answers: answers }, '*');
      } catch (err) { /* sandboxed — best effort */ }
    }

    // Catch the form's submit event AND a direct click on any submit-button
    // OR a button with data-jarvis-submit. Belt-and-braces because the model
    // sometimes forgets type="submit".
    form.addEventListener('submit', collectAndPost);
    var submits = form.querySelectorAll('button[type="submit"], [data-jarvis-submit]');
    for (var s = 0; s < submits.length; s++) {
      submits[s].addEventListener('click', collectAndPost);
    }
    // Fallback: ANY button inside the form whose text matches /continue|submit|generate|done/i
    var allBtns = form.querySelectorAll('button');
    for (var b = 0; b < allBtns.length; b++) {
      var btn = allBtns[b];
      if (btn.getAttribute('data-value')) continue; // skip chip buttons
      if (btn.getAttribute('type') === 'submit') continue; // already wired
      var label = (btn.textContent || '').toLowerCase();
      if (/continue|submit|generate|done|next|go/.test(label)) {
        btn.addEventListener('click', collectAndPost);
      }
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wireQuestionsForm);
  } else {
    wireQuestionsForm();
  }
  // Also retry once on a delay — covers the case where the form is added
  // late by the model's own script. The data-jarvis-wired guard prevents
  // double-wiring.
  setTimeout(wireQuestionsForm, 200);
})();
`;

function injectPickerScript(html: string): string {
  const tag = `<script>${PICKER_SCRIPT}</script>`;
  if (html.includes("</body>")) return html.replace("</body>", `${tag}</body>`);
  if (html.includes("</html>")) return html.replace("</html>", `${tag}</html>`);
  return html + tag;
}

/**
 * Inject `<base href>` so relative imports inside the iframe resolve
 * against our path-mirroring file API. Without this, an entry HTML loaded
 * via `srcDoc` runs at `about:srcdoc` and `./App.jsx` cannot fetch — the
 * whole multi-file React design renders blank.
 *
 * The base must be the FIRST child of <head> so subsequent <link>, <script
 * src>, and module imports all resolve through it.
 */
function injectBaseHref(html: string, baseHref: string): string {
  const tag = `<base href="${baseHref}">`;
  // If the model already wrote a <base>, leave it alone.
  if (/<base\s/i.test(html)) return html;
  if (/<head\b[^>]*>/i.test(html)) {
    return html.replace(/(<head\b[^>]*>)/i, `$1${tag}`);
  }
  if (/<html\b[^>]*>/i.test(html)) {
    return html.replace(/(<html\b[^>]*>)/i, `$1<head>${tag}</head>`);
  }
  return `<head>${tag}</head>${html}`;
}

/**
 * Pin every React URL variant the model might write (or that esbuild
 * might emit) to a single canonical version. Without this, esm.sh
 * serves a different React copy for each unique URL — react-dom ends
 * up with its own React instance, hooks return null contexts, and the
 * design crashes with `Cannot read properties of null (reading 'useContext')`.
 *
 * The `?deps=react@18.3.1` query tells esm.sh to use THIS exact React
 * for the package's own React peer dep, so motion/radix/etc. all share
 * the same instance.
 */
const IMPORT_MAP = {
  imports: {
    react: "https://esm.sh/react@18.3.1",
    "react/": "https://esm.sh/react@18.3.1/",
    "react-dom": "https://esm.sh/react-dom@18.3.1?deps=react@18.3.1",
    "react-dom/": "https://esm.sh/react-dom@18.3.1&deps=react@18.3.1/",
    "react/jsx-runtime": "https://esm.sh/react@18.3.1/jsx-runtime",
    "react/jsx-dev-runtime": "https://esm.sh/react@18.3.1/jsx-dev-runtime",
    "react-dom/client": "https://esm.sh/react-dom@18.3.1/client?deps=react@18.3.1",
    // Redirect the URL-form imports the model writes ("https://esm.sh/react@18")
    // to the same canonical version so model-written and esbuild-emitted
    // imports collapse to ONE module instance.
    "https://esm.sh/react@18": "https://esm.sh/react@18.3.1",
    "https://esm.sh/react@18/jsx-runtime":
      "https://esm.sh/react@18.3.1/jsx-runtime",
    "https://esm.sh/react@18/jsx-dev-runtime":
      "https://esm.sh/react@18.3.1/jsx-dev-runtime",
    "https://esm.sh/react-dom@18":
      "https://esm.sh/react-dom@18.3.1?deps=react@18.3.1",
    "https://esm.sh/react-dom@18/client":
      "https://esm.sh/react-dom@18.3.1/client?deps=react@18.3.1",
    // Pin downstream libs to the same React via ?deps so they don't
    // bring their own copy.
    "https://esm.sh/motion@12/react":
      "https://esm.sh/motion@12/react?deps=react@18.3.1",
    "https://esm.sh/@radix-ui/react-dialog@1":
      "https://esm.sh/@radix-ui/react-dialog@1?deps=react@18.3.1,react-dom@18.3.1",
    "https://esm.sh/@radix-ui/react-tabs@1":
      "https://esm.sh/@radix-ui/react-tabs@1?deps=react@18.3.1,react-dom@18.3.1",
    // Radix primitives used by the curated shadcn bundle (jarvis-shadcn.mjs).
    // Pin all of them to react@18.3.1 so they share React state with the host.
    "https://esm.sh/@radix-ui/react-tooltip@1":
      "https://esm.sh/@radix-ui/react-tooltip@1?deps=react@18.3.1,react-dom@18.3.1",
    "https://esm.sh/@radix-ui/react-separator@1":
      "https://esm.sh/@radix-ui/react-separator@1?deps=react@18.3.1,react-dom@18.3.1",
    "https://esm.sh/lucide-react@0.469":
      "https://esm.sh/lucide-react@0.469?deps=react@18.3.1",
  },
};

function injectImportMap(html: string): string {
  // Import maps MUST appear before the first <script type="module">. We
  // put it right after <base> at the top of <head>.
  const tag = `<script type="importmap">${JSON.stringify(IMPORT_MAP)}</script>`;
  if (/<script\s+type=["']importmap["']/i.test(html)) return html;
  if (/<head\b[^>]*>/i.test(html)) {
    return html.replace(/(<head\b[^>]*>)/i, `$1${tag}`);
  }
  return `<head>${tag}</head>${html}`;
}

/**
 * Replace the FIRST inline <script type="module"> (the one that imports
 * App.jsx + mounts React) with a single <script src="…/bundle?entry=…">
 * pointing at our server-side esbuild route. The route bundles every
 * local .jsx/.tsx file referenced from the inline entry into one self-
 * contained module — eliminating per-file path-mirror fragility,
 * relative-import resolution against about:srcdoc, and React duplication.
 *
 * Subsequent <script type="module"> tags (none typical, but the model
 * MIGHT add side-effect ones) are left as-is.
 */
function rewriteEntryToBundle(
  html: string,
  workspaceId: string,
  entryPath: string,
): string {
  const re =
    /<script\b[^>]*\btype\s*=\s*["']module["'][^>]*>[\s\S]*?<\/script>/i;
  const m = re.exec(html);
  if (!m) return html;
  // Skip if this <script> already has a src= (already external).
  if (/\bsrc\s*=/i.test(m[0].split(">")[0])) return html;
  const bundleUrl = `/api/workspace/${workspaceId}/bundle?entry=${encodeURIComponent(entryPath)}`;
  const replacement = `<script type="module" src="${bundleUrl}"></script>`;
  return html.replace(m[0], replacement);
}

function HtmlPreview({
  workspaceId,
  path,
  iframeKey,
  zoom,
  commentMode,
  onCommentModeOff,
  onComment,
  editMode = false,
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
  /** When true, the iframe-injected picker script makes leaf text elements
   *  contentEditable; commits flow back via the jarvis:design:edit:commit
   *  postMessage handled below. */
  editMode?: boolean;
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

  // Always inject:
  //   1. <base href> pointing at the path-mirroring file API, so relative
  //      imports inside the iframe (./App.jsx, ./components/Button.jsx, etc.)
  //      resolve to real URLs. Without this, srcDoc → about:srcdoc → broken.
  //   2. The picker script, so selection / tweak / edit messages work.
  const baseHref = `/api/workspace/${workspaceId}/files/`;
  const html = useMemo(
    () =>
      injectPickerScript(
        injectImportMap(
          injectBaseHref(
            rewriteEntryToBundle(content, workspaceId, path),
            baseHref,
          ),
        ),
      ),
    [content, baseHref],
  );

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

  // Tell the iframe when editMode flips. Same channel as commentMode but
  // with the edit-specific message types — the picker script's enableEdit()
  // adds contenteditable to leaf text elements; disableEdit() removes it.
  // Was previously missing entirely: the toolbar button toggled parent
  // state but never told the iframe, so clicking Edit appeared to do nothing.
  useEffect(() => {
    const win = iframeRef.current?.contentWindow;
    if (!win) return;
    win.postMessage(
      {
        type: editMode
          ? "jarvis:design:edit:enable"
          : "jarvis:design:edit:disable",
      },
      "*",
    );
  }, [editMode, html]);

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
            // `allow-same-origin` is REQUIRED — without it the iframe runs
            // in a unique opaque origin and `<script type="module">` imports
            // to `/api/workspace/.../files/App.jsx` are treated as cross-
            // origin without CORS, silently failing → black page. With both
            // flags the iframe shares origin with the parent so module
            // fetches work normally.
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
            className="h-full w-full border-0 bg-white"
            onLoad={() => {
              const win = iframeRef.current?.contentWindow;
              if (!win) return;
              if (commentMode) {
                win.postMessage({ type: "jarvis:design:enable" }, "*");
              }
              if (editMode) {
                win.postMessage(
                  { type: "jarvis:design:edit:enable" },
                  "*",
                );
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

// Map a file extension to a shiki language id. Designs are html/css/js plus
// the occasional jsx/ts for multi-file React mockups.
function langFromExt(e: string): string {
  switch (e) {
    case "html":
    case "htm":
      return "html";
    case "css":
      return "css";
    case "js":
    case "mjs":
    case "cjs":
      return "javascript";
    case "jsx":
      return "jsx";
    case "ts":
      return "typescript";
    case "tsx":
      return "tsx";
    case "json":
      return "json";
    case "md":
      return "markdown";
    case "svg":
    case "xml":
      return "xml";
    default:
      return "text";
  }
}

// Full-panel, syntax-highlighted source view (the "Code" side of the
// Preview/Code toggle). Uses the same shiki theme as the chat code blocks
// (github-dark-dimmed) so the Code tab reads like Claude's. Falls back to
// plain monospace if highlighting fails or is mid-load.
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
  const [html, setHtml] = useState("");
  const [copied, setCopied] = useState(false);
  const lang = useMemo(() => langFromExt(ext(path)), [path]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!content) {
        if (!cancelled) setHtml("");
        return;
      }
      try {
        const out = await codeToHtml(content, {
          lang,
          theme: "github-dark-dimmed",
        });
        if (!cancelled) setHtml(out);
      } catch {
        if (!cancelled) setHtml("");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [content, lang]);

  if (isLoading) return <PreviewLoading />;
  if (isError) return <PreviewError />;

  const copy = () => {
    void navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="relative h-full overflow-hidden bg-[#22272e]">
      <button
        type="button"
        onClick={copy}
        aria-label="Copy code"
        title="Copy code"
        className="absolute right-3 top-3 z-10 flex items-center gap-1 rounded-md border border-zinc-700 bg-zinc-900/90 px-2 py-1 text-[11px] text-zinc-300 transition-colors hover:text-white"
      >
        {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
        {copied ? "Copied" : "Copy"}
      </button>
      <div className="h-full overflow-auto text-[12.5px] leading-5 [&_code]:font-mono [&_pre]:m-0! [&_pre]:min-h-full [&_pre]:bg-transparent! [&_pre]:p-5">
        {html ? (
          <div dangerouslySetInnerHTML={{ __html: html }} />
        ) : (
          <pre className="m-0 whitespace-pre-wrap wrap-break-word p-5 font-mono text-zinc-200">
            {content}
          </pre>
        )}
      </div>
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
