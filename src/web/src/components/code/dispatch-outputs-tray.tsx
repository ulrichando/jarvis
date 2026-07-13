"use client";

import { useEffect, useState } from "react";
import { FileText, FolderOpen } from "lucide-react";

// Files the dispatched session produced — derived read-only from the session's
// container diff (the same source the /code Diff panel reads). No new worker
// event: a true agent-initiated "share this file" would need a CLI change and
// is deferred. Renders the placeholder when there's no session or no change yet.
function filesFromDiff(diff: string): string[] {
  const out: string[] = [];
  const re = /^diff --git a\/.+ b\/(.+)$/gm;
  let m: RegExpExecArray | null;
  while ((m = re.exec(diff))) out.push(m[1]);
  return Array.from(new Set(out));
}

export function DispatchOutputsTray({
  sessionId,
  onOpen,
}: {
  sessionId: string | null;
  onOpen?: () => void;
}) {
  const [files, setFiles] = useState<string[]>([]);

  useEffect(() => {
    if (!sessionId) {
      setFiles([]);
      return;
    }
    let alive = true;
    fetch(`/api/bridge/v1/sessions/${sessionId}/diff`)
      .then((r) => (r.ok ? r.json() : { diff: "" }))
      .then((j: { diff?: string }) => {
        if (alive) setFiles(filesFromDiff(j.diff ?? ""));
      })
      .catch(() => {
        /* no diff yet — keep the placeholder */
      });
    return () => {
      alive = false;
    };
  }, [sessionId]);

  if (files.length === 0) {
    return (
      <p className="text-[12.5px] leading-relaxed text-muted-foreground/60">
        Files Jarvis shares will appear here.
      </p>
    );
  }

  return (
    <div className="space-y-0.5">
      {files.map((f) => (
        <button
          key={f}
          type="button"
          onClick={onOpen}
          title={f}
          className="flex w-full items-center gap-2 rounded-md py-1 text-left text-[12.5px] text-foreground/85 transition-colors hover:text-foreground"
        >
          <FileText className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate">{f.split("/").pop()}</span>
        </button>
      ))}
      <button
        type="button"
        onClick={onOpen}
        className="flex w-full items-center gap-2 rounded-md py-1 text-left text-[12.5px] text-muted-foreground/70 transition-colors hover:text-foreground"
      >
        <FolderOpen className="size-3.5 shrink-0" />
        <span>Show changes</span>
      </button>
    </div>
  );
}
