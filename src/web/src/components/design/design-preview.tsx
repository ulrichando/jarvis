"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ExternalLink,
  FileText,
  Maximize2,
  Minus,
  Plus,
  RefreshCw,
} from "lucide-react";
import { apiReadFile, type TreeEntry } from "@/lib/workspace/client";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { ext, IMAGE_EXT } from "./file-classify";

export function DesignPreview({
  workspaceId,
  selected,
  showToolbar = false,
  streaming = null,
}: {
  workspaceId: string;
  selected: TreeEntry | null;
  /** When true, render the preview toolbar (refresh / zoom / present)
   *  above the content. Used when a file tab is the active center view. */
  showToolbar?: boolean;
  /** Live partial HTML being streamed by the LLM. When present, render this
   *  over the selected-file view so the canvas updates as Claude generates.
   *  Cleared by the parent when the file action closes. */
  streaming?: { filePath: string; content: string } | null;
}) {
  // Streaming wins over selection: the user is watching their design build
  // right now, that's more interesting than whatever was previously selected.
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
    />
  );
}

function StreamingPreview({ content, filePath }: { content: string; filePath: string }) {
  // The model is mid-stream so the HTML may be incomplete. Append the
  // closing tags an iframe needs so the partial content still renders.
  // (If the closing tags are already present they're harmless.)
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
}: {
  workspaceId: string;
  selected: TreeEntry;
  showToolbar: boolean;
}) {
  const qc = useQueryClient();
  const [iframeKey, setIframeKey] = useState(0);
  const [zoom, setZoom] = useState(100);

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
        />
      )}
      <div className="flex-1 min-h-0">
        {isHtml ? (
          <HtmlPreview
            workspaceId={workspaceId}
            path={selected.path}
            iframeKey={iframeKey}
            zoom={zoom}
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
}: {
  onRefresh?: () => void;
  presentHref?: string | null;
  zoom?: number;
  onZoomIn?: () => void;
  onZoomOut?: () => void;
  onZoomReset?: () => void;
  disabled?: boolean;
}) {
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

function HtmlPreview({
  workspaceId,
  path,
  iframeKey,
  zoom,
}: {
  workspaceId: string;
  path: string;
  iframeKey: number;
  zoom: number;
}) {
  const { data: content = "", isLoading, isError } = useQuery({
    queryKey: ["design-file", workspaceId, path],
    queryFn: () => apiReadFile(workspaceId, path),
  });
  if (isLoading) return <PreviewLoading />;
  if (isError) return <PreviewError />;
  // Width-compensated zoom: scale the iframe and grow its layout box by
  // the inverse ratio so 50% doesn't leave half the surface blank.
  const scale = zoom / 100;
  return (
    <div className="h-full w-full overflow-hidden bg-white">
      <div
        className="origin-top-left"
        style={{
          width: `${100 / scale}%`,
          height: `${100 / scale}%`,
          transform: `scale(${scale})`,
        }}
      >
        <iframe
          key={iframeKey}
          title={path}
          srcDoc={content}
          sandbox="allow-scripts"
          className="h-full w-full border-0 bg-white"
        />
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
      {/* Workspace-served bytes — next/image can't optimize a private
          local API route, so plain <img> is the right tool here. */}
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
