// Voice mode state — coordination point for clients.
// The daemon doesn't change its behavior based on mode; it's a shared variable
// that the voice client, wake-word daemon, and HUD read/write.

export type VoiceMode = "off" | "ptt" | "wake";

export const VOICE_MODES: readonly VoiceMode[] = ["off", "ptt", "wake"] as const;

export function isVoiceMode(s: unknown): s is VoiceMode {
  return typeof s === "string" && (VOICE_MODES as readonly string[]).includes(s);
}

export class VoiceModeState {
  private current: VoiceMode = "off";
  private changedAt: number = Date.now();

  get(): { mode: VoiceMode; changedAt: number } {
    return { mode: this.current, changedAt: this.changedAt };
  }

  /** Set the mode. Returns the new state, or throws if value is invalid. */
  set(mode: VoiceMode): { mode: VoiceMode; changedAt: number } {
    if (!isVoiceMode(mode)) throw new Error(`invalid voice mode: ${String(mode)}`);
    if (mode !== this.current) {
      this.current = mode;
      this.changedAt = Date.now();
    }
    return this.get();
  }

  /** Advance to the next mode in cycle order (off → ptt → wake → off). */
  cycle(): { mode: VoiceMode; changedAt: number } {
    const order: VoiceMode[] = ["off", "ptt", "wake"];
    const idx = order.indexOf(this.current);
    const next = order[(idx + 1) % order.length]!;
    return this.set(next);
  }
}
