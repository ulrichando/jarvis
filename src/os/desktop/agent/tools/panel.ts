// Panel tool — misty opens/closes UI panels on the HUD.
// The tool mutates a shared PanelState and emits events via the EventBus.
// HUD clients (web or Tauri) subscribe and render the panel shelf.

import type { ToolRunner } from "../types.ts";
import type { EventBus } from "../../bridge/events.ts";
import type { PanelState } from "../../panels/state.ts";
import type { PanelKind } from "../../bridge/events.ts";

type ArrangeLayout = "grid" | "tile" | "cascade" | "side-by-side" | "stack";

type PanelInput =
  | { action: "open"; kind: PanelKind; title?: string; src?: string; content?: string;
      x?: number; y?: number; width?: number; height?: number }
  | { action: "close"; id: string }
  | { action: "list" }
  | { action: "clear" }
  | { action: "move"; id: string; x: number; y: number }
  | { action: "resize"; id: string; width: number; height: number }
  | { action: "arrange"; layout: ArrangeLayout; viewport?: { width: number; height: number } };

export function createPanelTool(state: PanelState, events: EventBus): ToolRunner {
  return {
    def: {
      name: "panel",
      description:
        "Open/close/arrange UI panels on the user's HUD (Tony Stark workshop style). " +
        "Actions: open (kind: browser|video|image|text|file, src/content), close (id), list, clear, " +
        "move (id + x,y), resize (id + width,height), arrange (layout: grid|tile|cascade|side-by-side|stack — auto-lays out every open panel).",
      input_schema: {
        type: "object",
        properties: {
          action: { type: "string", enum: ["open", "close", "list", "clear", "move", "resize", "arrange"] },
          kind:   { type: "string", enum: ["browser", "video", "image", "text", "file"],
                    description: "Required when action=open" },
          title:  { type: "string", description: "Window title (optional)" },
          src:    { type: "string", description: "URL (browser/video/image) or path (file)" },
          content:{ type: "string", description: "Inline text content (only for kind=text)" },
          x:      { type: "number" }, y: { type: "number" },
          width:  { type: "number" }, height: { type: "number" },
          id:     { type: "string", description: "Required when action=close/move/resize" },
          layout: { type: "string", enum: ["grid", "tile", "cascade", "side-by-side", "stack"],
                    description: "Required when action=arrange" },
        },
        required: ["action"],
      },
    },
    async run(input: unknown): Promise<{ output: string; is_error?: boolean }> {
      try {
        const inp = input as PanelInput;
        switch (inp.action) {
          case "open": {
            if (!inp.kind) return { output: "panel.open requires a 'kind'", is_error: true };
            if (inp.kind !== "text" && !inp.src) {
              return { output: `panel.open with kind=${inp.kind} requires 'src' (URL or path)`, is_error: true };
            }
            if (inp.kind === "text" && !inp.content) {
              return { output: "panel.open with kind=text requires 'content'", is_error: true };
            }
            const spec = state.open(inp);
            events.emit({ type: "panel.opened", panel: spec });
            return { output: `opened ${inp.kind} panel ${spec.id}: ${spec.title}` };
          }
          case "close": {
            if (!inp.id) return { output: "panel.close requires 'id'", is_error: true };
            const ok = state.close(inp.id);
            if (!ok) return { output: `no panel with id ${inp.id}`, is_error: true };
            events.emit({ type: "panel.closed", id: inp.id });
            return { output: `closed panel ${inp.id}` };
          }
          case "list": {
            return { output: JSON.stringify(state.list(), null, 2) };
          }
          case "clear": {
            const ids = state.list().map((p) => p.id);
            state.clear();
            for (const id of ids) events.emit({ type: "panel.closed", id });
            return { output: `cleared ${ids.length} panels` };
          }
          case "move": {
            if (!inp.id) return { output: "panel.move requires 'id'", is_error: true };
            const next = state.update(inp.id, { x: inp.x, y: inp.y });
            if (!next) return { output: `no panel with id ${inp.id}`, is_error: true };
            events.emit({ type: "panel.updated", panel: next });
            return { output: `moved ${inp.id} to (${inp.x}, ${inp.y})` };
          }
          case "resize": {
            if (!inp.id) return { output: "panel.resize requires 'id'", is_error: true };
            const next = state.update(inp.id, { width: inp.width, height: inp.height });
            if (!next) return { output: `no panel with id ${inp.id}`, is_error: true };
            events.emit({ type: "panel.updated", panel: next });
            return { output: `resized ${inp.id} to ${inp.width}x${inp.height}` };
          }
          case "arrange": {
            if (!inp.layout) return { output: "panel.arrange requires 'layout'", is_error: true };
            const vp = inp.viewport ?? { width: 1920, height: 1080 };
            const updated = state.arrange(inp.layout, vp);
            for (const panel of updated) events.emit({ type: "panel.updated", panel });
            return { output: `arranged ${updated.length} panels as ${inp.layout}` };
          }
          default: {
            const exhaustive: never = inp;
            return { output: `unknown action: ${JSON.stringify(exhaustive)}`, is_error: true };
          }
        }
      } catch (err) {
        return { output: String(err), is_error: true };
      }
    },
  };
}
