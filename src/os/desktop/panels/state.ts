// In-memory panel state — tracks what's currently open on the HUD.
// Clients subscribe via the WS event bus; server is the source of truth.

import type { PanelKind, PanelSpec } from "../bridge/events.ts";

export class PanelState {
  private panels = new Map<string, PanelSpec>();
  private counter = 0;

  /** Open a new panel. Returns the full spec (with assigned id, position, size defaults). */
  open(opts: {
    kind: PanelKind;
    title?: string;
    src?: string;
    content?: string;
    x?: number; y?: number; width?: number; height?: number;
  }): PanelSpec {
    const id = `p_${++this.counter}_${Date.now().toString(36)}`;
    // Cascade default positions: each new panel offset down+right from the last.
    const n = this.panels.size;
    const spec: PanelSpec = {
      id,
      kind: opts.kind,
      title: opts.title ?? defaultTitle(opts.kind, opts.src),
      src: opts.src,
      content: opts.content,
      x: opts.x ?? (120 + n * 30),
      y: opts.y ?? (120 + n * 30),
      width: opts.width ?? defaultWidth(opts.kind),
      height: opts.height ?? defaultHeight(opts.kind),
    };
    this.panels.set(id, spec);
    return spec;
  }

  /** Close a panel. Returns true if it existed. */
  close(id: string): boolean {
    return this.panels.delete(id);
  }

  get(id: string): PanelSpec | undefined {
    return this.panels.get(id);
  }

  list(): PanelSpec[] {
    return Array.from(this.panels.values());
  }

  /** Update in place. Returns the new spec, or undefined if id unknown. */
  update(id: string, patch: Partial<Omit<PanelSpec, "id" | "kind">>): PanelSpec | undefined {
    const cur = this.panels.get(id);
    if (!cur) return undefined;
    const next: PanelSpec = { ...cur, ...patch };
    this.panels.set(id, next);
    return next;
  }

  clear(): void {
    this.panels.clear();
  }

  /**
   * Arrange all open panels into a layout. viewport is the screen size the
   * HUD is running at — arrangements target that rectangle. Returns the new
   * specs so the caller can emit panel.updated events for each.
   */
  arrange(layout: "grid" | "tile" | "cascade" | "side-by-side" | "stack",
          viewport: { width: number; height: number }): PanelSpec[] {
    const panels = this.list();
    const n = panels.length;
    if (n === 0) return [];
    const padding = 24;
    const headerSpace = 90;  // avoid the MISTY brand + top-right HUD
    const footerSpace = 90;  // avoid event stream + wake button
    const W = viewport.width - padding * 2;
    const H = viewport.height - headerSpace - footerSpace;
    const X0 = padding;
    const Y0 = headerSpace;

    const updated: PanelSpec[] = [];
    const apply = (i: number, x: number, y: number, w: number, h: number) => {
      const next = this.update(panels[i]!.id, {
        x: Math.round(x), y: Math.round(y),
        width: Math.round(w), height: Math.round(h),
      });
      if (next) updated.push(next);
    };

    if (layout === "stack") {
      for (let i = 0; i < n; i++) apply(i, X0, Y0, W, H);
    } else if (layout === "side-by-side") {
      const w = (W - (n - 1) * padding) / n;
      for (let i = 0; i < n; i++) apply(i, X0 + i * (w + padding), Y0, w, H);
    } else if (layout === "cascade") {
      const w = Math.min(W * 0.55, 720);
      const h = Math.min(H * 0.75, 520);
      const step = 40;
      for (let i = 0; i < n; i++) apply(i, X0 + i * step, Y0 + i * step, w, h);
    } else {
      // grid / tile: square-ish grid
      const cols = Math.ceil(Math.sqrt(n));
      const rows = Math.ceil(n / cols);
      const cellW = (W - (cols - 1) * padding) / cols;
      const cellH = (H - (rows - 1) * padding) / rows;
      for (let i = 0; i < n; i++) {
        const row = Math.floor(i / cols);
        const col = i % cols;
        apply(i, X0 + col * (cellW + padding), Y0 + row * (cellH + padding), cellW, cellH);
      }
    }
    return updated;
  }
}

function defaultTitle(kind: PanelKind, src?: string): string {
  switch (kind) {
    case "browser": return src ? `browser: ${truncate(src, 40)}` : "browser";
    case "video":   return src ? `video: ${truncate(src, 40)}` : "video";
    case "image":   return src ? `image: ${truncate(src, 40)}` : "image";
    case "text":    return "text";
    case "file":    return src ? `file: ${src.split("/").pop()}` : "file";
  }
}
function defaultWidth(kind: PanelKind): number { return kind === "video" ? 720 : 560; }
function defaultHeight(kind: PanelKind): number { return kind === "video" ? 420 : 420; }
function truncate(s: string, n: number): string { return s.length > n ? s.slice(0, n - 1) + "…" : s; }
