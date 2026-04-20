// Tiny pub-sub event bus for the bridge. Subscribers get a Dispose fn.
// Events are JSON-serializable objects with a `type` discriminator.

export type PanelKind = "browser" | "video" | "image" | "text" | "file";
export type PanelSpec = {
  id: string;
  kind: PanelKind;
  title?: string;
  /** browser/video/image: URL. text: content. file: path (rendered as text or image based on extension). */
  src?: string;
  content?: string;
  x?: number; y?: number; width?: number; height?: number;
};

export type TurnPhase = "idle" | "listening" | "thinking" | "speaking";

export type Event =
  | { type: "voice.mode_changed"; mode: "off" | "ptt" | "wake"; changedAt: number }
  | { type: "voice.wake_triggered"; source?: string; at: number }
  | { type: "confirmation.opened"; id: string; tool: string }
  | { type: "confirmation.resolved"; id: string; decision: "allow" | "deny" }
  | { type: "panel.opened"; panel: PanelSpec }
  | { type: "panel.closed"; id: string }
  | { type: "panel.updated"; panel: PanelSpec }
  | { type: "turn.state"; phase: TurnPhase; audioLevel?: number; at: number };

export type Listener = (event: Event) => void;
export type Unsubscribe = () => void;

export class EventBus {
  private listeners = new Set<Listener>();

  subscribe(l: Listener): Unsubscribe {
    this.listeners.add(l);
    return () => { this.listeners.delete(l); };
  }

  emit(event: Event): void {
    // Snapshot listeners before iterating so a listener unsubscribing mid-emit
    // doesn't skip others.
    for (const l of [...this.listeners]) {
      try { l(event); } catch (err) {
        console.error("[eventbus] listener threw:", err);
      }
    }
  }

  get size(): number {
    return this.listeners.size;
  }
}
