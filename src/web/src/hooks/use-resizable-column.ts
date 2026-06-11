"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type UseResizableColumnOptions = {
  /** localStorage key. If unset, width is not persisted. */
  storageKey?: string;
  /** Initial width in pixels (used when nothing is in storage). */
  defaultWidth: number;
  /** Hard min/max so the panel never collapses to nothing. */
  min: number;
  max: number;
};

export function useResizableColumn(opts: UseResizableColumnOptions) {
  const { storageKey, defaultWidth, min, max } = opts;
  const [width, setWidth] = useState<number>(defaultWidth);
  const [dragging, setDragging] = useState(false);

  // Load persisted width on mount.
  useEffect(() => {
    if (!storageKey) return;
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (!raw) return;
      const n = Number(raw);
      // SSR-safe localStorage hydration: lazy-initializing useState from
      // localStorage mismatches SSR (window is undefined server-side), so the
      // mount-then-upgrade setState is intentional — a single post-mount
      // update, not a cascade.
      // eslint-disable-next-line react-hooks/set-state-in-effect -- SSR-safe localStorage load
      if (Number.isFinite(n) && n >= min && n <= max) setWidth(n);
    } catch {}
  }, [storageKey, min, max]);

  // Persist on change (debounced via rAF to avoid thrashing during drag).
  const persistRaf = useRef<number | null>(null);
  useEffect(() => {
    if (!storageKey) return;
    if (persistRaf.current != null) cancelAnimationFrame(persistRaf.current);
    persistRaf.current = requestAnimationFrame(() => {
      try {
        window.localStorage.setItem(storageKey, String(width));
      } catch {}
    });
    return () => {
      if (persistRaf.current != null) cancelAnimationFrame(persistRaf.current);
    };
  }, [width, storageKey]);

  const startDrag = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      const startX = e.clientX;
      const startWidth = width;
      setDragging(true);

      const onMove = (ev: MouseEvent) => {
        const next = Math.min(max, Math.max(min, startWidth + (ev.clientX - startX)));
        setWidth(next);
      };
      const onUp = () => {
        setDragging(false);
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    },
    [width, min, max],
  );

  return { width, dragging, startDrag };
}
