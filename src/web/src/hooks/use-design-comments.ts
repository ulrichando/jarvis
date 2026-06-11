"use client";

import { useCallback, useEffect, useState } from "react";

export type DesignCommentRecord = {
  id: string;
  workspaceId: string;
  filePath: string;
  selector: string;
  tag: string;
  text: string;
  comment: string;
  createdAt: number;
};

const KEY = (workspaceId: string) => `design.comments.${workspaceId}`;

function read(workspaceId: string): DesignCommentRecord[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(KEY(workspaceId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as DesignCommentRecord[]) : [];
  } catch {
    return [];
  }
}

function write(workspaceId: string, list: DesignCommentRecord[]) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(KEY(workspaceId), JSON.stringify(list));
  } catch {}
}

/**
 * Per-workspace store of inline comments left via the canvas selection layer.
 * Persisted in localStorage so the Comments tab survives page reloads but is
 * fully client-side — comments are personal annotations, not shared with the
 * workspace owner over the wire.
 */
export function useDesignComments(workspaceId: string) {
  const [items, setItems] = useState<DesignCommentRecord[]>([]);

  useEffect(() => {
    // SSR-safe: localStorage is client-only, so load this workspace's comments
    // after mount rather than via a hydration-mismatching lazy initializer.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- SSR-safe localStorage load
    setItems(read(workspaceId));
  }, [workspaceId]);

  // Sync across tabs/windows — lightweight: respond to storage events on our
  // own key only.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== KEY(workspaceId)) return;
      setItems(read(workspaceId));
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [workspaceId]);

  const add = useCallback(
    (rec: Omit<DesignCommentRecord, "id" | "workspaceId" | "createdAt">) => {
      const next: DesignCommentRecord = {
        ...rec,
        id: `c_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
        workspaceId,
        createdAt: Date.now(),
      };
      setItems((prev) => {
        const updated = [next, ...prev].slice(0, 200); // cap so storage never balloons
        write(workspaceId, updated);
        return updated;
      });
      return next;
    },
    [workspaceId],
  );

  const remove = useCallback(
    (id: string) => {
      setItems((prev) => {
        const updated = prev.filter((x) => x.id !== id);
        write(workspaceId, updated);
        return updated;
      });
    },
    [workspaceId],
  );

  const clear = useCallback(() => {
    setItems([]);
    write(workspaceId, []);
  }, [workspaceId]);

  return { items, add, remove, clear };
}
