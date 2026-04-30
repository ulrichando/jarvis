"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
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
  const [editMode, setEditMode] = useState(false);

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
          commentMode={isHtml ? commentMode : undefined}
          onCommentModeChange={isHtml ? setCommentModeExclusive : undefined}
          editMode={isHtml ? editMode : undefined}
          onEditModeChange={isHtml ? setEditModeExclusive : undefined}
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
            editMode={editMode}
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
  commentMode?: boolean;
  onCommentModeChange?: (next: boolean) => void;
  editMode?: boolean;
  onEditModeChange?: (next: boolean) => void;
}) {
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

  // ── Edit mode: contentEditable on text-leaf elements, postMessage on commit.
  let editEnabled = false;
  let editFocused = null;
  let editOriginal = '';
  const EDIT_TARGETS = ['p','h1','h2','h3','h4','h5','h6','li','span','figcaption','blockquote','dt','dd','td','th','caption','summary'];

  function enableEdit() {
    editEnabled = true;
    document.body.setAttribute('data-jarvis-edit', '1');
    EDIT_TARGETS.forEach(function(tag){
      const nodes = document.getElementsByTagName(tag);
      for (let i = 0; i < nodes.length; i++) {
        const el = nodes[i];
        // Only mark leaves so a heading containing an icon/span doesn't become a single editable block.
        if (el.children.length === 0 && (el.textContent || '').trim().length > 0) {
          el.setAttribute('contenteditable', 'plaintext-only');
          el.classList.add(STYLE + '_edit');
        }
      }
    });
    if (!document.getElementById(STYLE + '-editstyle')) {
      const s = document.createElement('style');
      s.id = STYLE + '-editstyle';
      s.textContent = '.' + STYLE + '_edit{cursor:text;}'
        + '.' + STYLE + '_edit:hover{outline:1px dashed ' + ACCENT + ';outline-offset:2px;}'
        + '.' + STYLE + '_edit:focus{outline:2px solid ' + ACCENT + ';outline-offset:2px;background:rgba(255,170,0,0.06);}';
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
