"use client";

// Immersive full-screen voice-mode overlay — web parity with the mobile app's
// VoiceOverlay (orb + transcript + [gear][mic][stop] cluster) and claude.ai's
// voice mode.
//
// PERFORMANCE CONTRACT (repo rule: "voice reactor sphere was removed for
// latency; never re-add per-frame React state to voice UI"): the orb is a
// <canvas> driven by ONE requestAnimationFrame loop that reads the Web Audio
// AnalyserNode (and the voice-read store, imperatively via getState())
// directly. React re-renders happen only on discrete changes — phase, mode,
// transcript text, rate — never per audio frame.
import { AudioLines, Hand, Mic, MicOff, Settings2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { useVoiceRead } from "@/stores/voice-read";
import type { VoiceInputMode, VoicePhase } from "@/lib/chat/use-voice-mode";

const ORB_SIZE = 300; // CSS px; canvas backing store is DPR-scaled
const TAU = Math.PI * 2;

// Phase palette (RGB triplets — lerped per frame on the canvas, echoed as
// rgb() strings in the label/glow). Cyan tracks the app primary
// (oklch(0.80 0.155 198)); the speaking coral matches the mobile VoiceGlow
// "talking" hue; thinking is a calm violet.
const COLOR: Record<"listening" | "thinking" | "speaking" | "connecting" | "quiet", [number, number, number]> = {
  listening: [96, 218, 232],
  thinking: [167, 139, 250],
  speaking: [235, 122, 102],
  connecting: [148, 163, 184],
  quiet: [110, 121, 138],
};

type VisualKey = keyof typeof COLOR;

function rgb([r, g, b]: [number, number, number], a?: number): string {
  return a == null ? `rgb(${r},${g},${b})` : `rgba(${r},${g},${b},${a})`;
}

/** The reply being narrated, with the app's gray→white read-along riding the
 *  voice-read store. Isolated so the ~12 Hz readChar updates re-render ONLY
 *  this paragraph (same pattern message.tsx uses), never the overlay. */
function SpokenReply({ text, live }: { text: string; live: boolean }) {
  const readChar = useVoiceRead((s) => (live ? s.readChar : 0));
  if (!text) return null;
  if (!live) {
    return (
      <p className="font-serif text-[15.5px] leading-7 text-muted-foreground/80">
        {text}
      </p>
    );
  }
  const n = Math.min(readChar, text.length);
  return (
    <p className="font-serif text-[15.5px] leading-7">
      <span className="text-foreground">{text.slice(0, n)}</span>
      <span className="text-muted-foreground/50">{text.slice(n)}</span>
    </p>
  );
}

type VoiceOverlayProps = {
  phase: VoicePhase;
  /** Stable getter for the mic AnalyserNode — read inside the canvas rAF
   *  loop only. Null on the browser-SpeechRecognition STT fallback path. */
  getAnalyser: () => AnalyserNode | null;
  lastUtterance: string;
  speakingText: string;
  micMuted: boolean;
  onToggleMic: () => void;
  mode: VoiceInputMode;
  onModeChange: (m: VoiceInputMode) => void;
  pttHeld: boolean;
  onPttHold: (down: boolean) => void;
  ratePct: number;
  onRateChange: (pct: number) => void;
  onClose: () => void;
};

export function VoiceOverlay({
  phase,
  getAnalyser,
  lastUtterance,
  speakingText,
  micMuted,
  onToggleMic,
  mode,
  onModeChange,
  pttHeld,
  onPttHold,
  ratePct,
  onRateChange,
  onClose,
}: VoiceOverlayProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [rateOpen, setRateOpen] = useState(false);

  // Mirror the discrete props into a ref so the canvas loop reads them
  // without being a dependency of anything — the loop mounts once.
  const stateRef = useRef({ phase, mode, pttHeld, micMuted });
  stateRef.current = { phase, mode, pttHeld, micMuted };
  const getAnalyserRef = useRef(getAnalyser);
  getAnalyserRef.current = getAnalyser;
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  // prefers-reduced-motion → static orb (one frame per discrete change).
  const [reducedMotion, setReducedMotion] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(mq.matches);
    const on = (e: MediaQueryListEvent) => setReducedMotion(e.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  // What the orb/label should look like right now (discrete).
  const micLive =
    phase === "listening" && (mode === "ptt" ? pttHeld : !micMuted);
  const visual: VisualKey =
    phase === "listening"
      ? micLive
        ? "listening"
        : "quiet"
      : phase === "speaking"
        ? "speaking"
        : phase === "thinking"
          ? "thinking"
          : "connecting";

  // ── Canvas orb — one rAF loop, zero React state ──────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = ORB_SIZE * dpr;
    canvas.height = ORB_SIZE * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // Closure-scoped animation state — never touches React.
    let raf: number | null = null;
    let energy = 0.08;
    let burst = 0;
    let lastReadChar = 0;
    let arcAngle = 0;
    const color: [number, number, number] = [...COLOR.connecting];
    let timeBuf: Uint8Array<ArrayBuffer> | null = null;
    let freqBuf: Uint8Array<ArrayBuffer> | null = null;

    const draw = () => {
      const s = stateRef.current;
      const live =
        s.phase === "listening" &&
        (s.mode === "ptt" ? s.pttHeld : !s.micMuted);
      const key: VisualKey =
        s.phase === "listening"
          ? live
            ? "listening"
            : "quiet"
          : s.phase === "speaking"
            ? "speaking"
            : s.phase === "thinking"
              ? "thinking"
              : "connecting";
      const target = COLOR[key];
      for (let i = 0; i < 3; i++) color[i] += (target[i] - color[i]) * 0.08;

      const now = performance.now();
      const breath = 0.5 + 0.5 * Math.sin(now / 1400);
      const analyser = getAnalyserRef.current();

      // Energy source per phase:
      //  - listening: real mic RMS off the AnalyserNode
      //  - speaking:  bursts on read-along progress (the mic analyser is
      //    AEC-stripped of JARVIS's own voice, so — like the mobile app's
      //    per-word speechTick — playback progress carries the rhythm)
      //  - thinking/connecting/quiet: gentle synthetic breathing
      let targetEnergy = 0.05 + 0.05 * breath;
      if (live && analyser) {
        if (!timeBuf || timeBuf.length !== analyser.fftSize) {
          timeBuf = new Uint8Array(analyser.fftSize);
        }
        analyser.getByteTimeDomainData(timeBuf);
        let sumSq = 0;
        for (let i = 0; i < timeBuf.length; i++) {
          const v = (timeBuf[i] - 128) / 128;
          sumSq += v * v;
        }
        const rms = Math.sqrt(sumSq / timeBuf.length);
        targetEnergy = Math.min(1, rms * 10) + 0.04 * breath;
      } else if (s.phase === "speaking") {
        const rc = useVoiceRead.getState().readChar;
        if (rc > lastReadChar) burst = 1;
        lastReadChar = rc;
        burst *= 0.92;
        targetEnergy = 0.12 + 0.1 * breath + 0.55 * burst;
      } else if (s.phase === "thinking") {
        targetEnergy = 0.1 + 0.08 * (0.5 + 0.5 * Math.sin(now / 900));
      }
      // Fast attack, slow release — speech pops, silence settles.
      energy += (targetEnergy - energy) * (targetEnergy > energy ? 0.35 : 0.07);

      // ── Render ──
      ctx.clearRect(0, 0, ORB_SIZE, ORB_SIZE);
      const cx = ORB_SIZE / 2;
      const cy = ORB_SIZE / 2;
      const base = 62;
      const coreR = base * (1 + 0.18 * energy);
      const haloR = base * (2.0 + 1.05 * energy);
      const r = Math.round(color[0]);
      const g = Math.round(color[1]);
      const b = Math.round(color[2]);

      // Soft halo.
      const halo = ctx.createRadialGradient(cx, cy, coreR * 0.35, cx, cy, haloR);
      halo.addColorStop(0, `rgba(${r},${g},${b},${(0.15 + 0.22 * energy).toFixed(3)})`);
      halo.addColorStop(1, `rgba(${r},${g},${b},0)`);
      ctx.fillStyle = halo;
      ctx.beginPath();
      ctx.arc(cx, cy, haloR, 0, TAU);
      ctx.fill();

      // Radial waveform ring — real frequency data while the mic is live.
      if (live && analyser && !reducedMotion) {
        if (!freqBuf || freqBuf.length !== analyser.frequencyBinCount) {
          freqBuf = new Uint8Array(analyser.frequencyBinCount);
        }
        analyser.getByteFrequencyData(freqBuf);
        const bins = 72;
        const span = Math.min(56, freqBuf.length - 2); // voice band, skip DC
        ctx.strokeStyle = `rgba(${r},${g},${b},0.28)`;
        ctx.lineWidth = 1.5;
        ctx.lineCap = "round";
        ctx.beginPath();
        for (let i = 0; i < bins; i++) {
          const v = freqBuf[2 + Math.floor((i * span) / bins)] / 255;
          const a = (i / bins) * TAU - Math.PI / 2;
          const r0 = coreR + 10;
          const r1 = r0 + 2 + v * 22 * (0.35 + energy);
          ctx.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0);
          ctx.lineTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1);
        }
        ctx.stroke();
      }

      // Thinking — two slow counter-rotating arcs.
      if (s.phase === "thinking" && !reducedMotion) {
        arcAngle += 0.022;
        ctx.lineWidth = 2;
        ctx.lineCap = "round";
        ctx.strokeStyle = `rgba(${r},${g},${b},0.40)`;
        ctx.beginPath();
        ctx.arc(cx, cy, coreR + 14, arcAngle, arcAngle + 1.15);
        ctx.stroke();
        ctx.strokeStyle = `rgba(${r},${g},${b},0.16)`;
        ctx.beginPath();
        ctx.arc(cx, cy, coreR + 22, -arcAngle * 0.7, -arcAngle * 0.7 + 0.75);
        ctx.stroke();
      }

      // Luminous core — soft, not a hard ball.
      const core = ctx.createRadialGradient(
        cx - coreR * 0.22,
        cy - coreR * 0.28,
        coreR * 0.08,
        cx,
        cy,
        coreR,
      );
      const hr = Math.round(r + (255 - r) * 0.55);
      const hg = Math.round(g + (255 - g) * 0.55);
      const hb = Math.round(b + (255 - b) * 0.55);
      core.addColorStop(0, `rgba(${hr},${hg},${hb},0.85)`);
      core.addColorStop(0.55, `rgba(${r},${g},${b},0.38)`);
      core.addColorStop(1, `rgba(${r},${g},${b},0.07)`);
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(cx, cy, coreR, 0, TAU);
      ctx.fill();

      // Hairline rim.
      ctx.strokeStyle = "rgba(255,255,255,0.07)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(cx, cy, coreR, 0, TAU);
      ctx.stroke();
    };

    if (reducedMotion) {
      // Static orb: one frame now; the effect re-runs on discrete visual
      // changes (see deps) so the color still tracks the phase.
      draw();
      return;
    }
    const loop = () => {
      draw();
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => {
      if (raf != null) cancelAnimationFrame(raf);
    };
    // `visual` only matters when reduced motion needs a static redraw — the
    // animated loop reads live values from refs.
  }, [reducedMotion, reducedMotion ? visual : null]); // eslint-disable-line react-hooks/exhaustive-deps

  // Esc closes; lock body scroll; take focus so Esc lands here.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCloseRef.current();
    };
    window.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    rootRef.current?.focus();
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, []);

  // Hold the finished reply on screen between turns so the transcript
  // doesn't blank while JARVIS waits for the next utterance.
  const [heldReply, setHeldReply] = useState("");
  useEffect(() => {
    if (speakingText) setHeldReply(speakingText);
  }, [speakingText]);
  const replyText = speakingText || heldReply;
  const replyLive = !!speakingText;

  // Keep the transcript pinned to its latest content (discrete changes only).
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lastUtterance, replyText]);

  const label =
    phase === "connecting"
      ? "Connecting…"
      : phase === "thinking"
        ? "Thinking…"
        : phase === "speaking"
          ? "Speaking"
          : micLive
            ? "Listening…"
            : mode === "ptt"
              ? "Hold to talk"
              : "Muted";
  const tint = COLOR[visual];

  const circleBtn =
    "flex size-12 items-center justify-center rounded-full border border-border/70 bg-card/80 text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground focus-visible:outline-2 focus-visible:outline-primary/60";

  return (
    <div
      ref={rootRef}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-label="Voice mode"
      className="fixed inset-0 z-50 flex flex-col bg-background/95 outline-none backdrop-blur-2xl"
    >
      {/* Ambient bottom glow (mobile VoiceGlow parity) — discrete color per
          phase, CSS-transitioned. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 bottom-0 h-56 transition-opacity duration-500"
        style={{
          background: `linear-gradient(to top, ${rgb(tint, 0.14)}, transparent)`,
        }}
      />

      {/* Orb + state label */}
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center">
        <canvas
          ref={canvasRef}
          aria-hidden
          style={{ width: ORB_SIZE, height: ORB_SIZE }}
        />
        <div className="mt-2 flex items-center gap-2" aria-live="polite">
          <span
            aria-hidden
            className="size-1.5 rounded-full"
            style={{ backgroundColor: rgb(tint, 0.9) }}
          />
          <span
            className="text-[13px] font-medium tracking-wide"
            style={{ color: rgb(tint, 0.92) }}
          >
            {label}
          </span>
        </div>
      </div>

      {/* Live transcript — latest utterance + the reply being narrated */}
      <div
        ref={scrollRef}
        className="mx-auto mb-8 max-h-[26vh] w-full max-w-xl flex-none overflow-y-auto px-6 text-center"
      >
        {lastUtterance && (
          <p className="text-[13px] italic leading-6 text-muted-foreground/80">
            &ldquo;{lastUtterance}&rdquo;
          </p>
        )}
        {replyText && (
          <div className="mt-3">
            <SpokenReply text={replyText} live={replyLive} />
          </div>
        )}
      </div>

      {/* Control cluster — [rate] [mode] [mic] [end] */}
      <div
        className="flex items-center justify-center gap-3"
        style={{ paddingBottom: "max(2.5rem, env(safe-area-inset-bottom))" }}
      >
        <div className="relative">
          <button
            type="button"
            onClick={() => setRateOpen((o) => !o)}
            aria-label="Voice settings"
            aria-expanded={rateOpen}
            title="Speech rate"
            className={cn(circleBtn, rateOpen && "bg-accent/60 text-foreground")}
          >
            <Settings2 className="size-5" />
          </button>
          {rateOpen && (
            <>
              <button
                type="button"
                aria-hidden
                tabIndex={-1}
                onClick={() => setRateOpen(false)}
                className="fixed inset-0 cursor-default"
              />
              <div className="absolute bottom-14 left-1/2 z-10 w-60 -translate-x-1/2 rounded-xl border border-border/70 bg-card p-4 shadow-xl">
                <div className="flex items-center justify-between">
                  <span className="text-[12px] font-medium text-muted-foreground">
                    Speech rate
                  </span>
                  <span className="text-[12px] tabular-nums text-foreground">
                    {ratePct > 0 ? `+${ratePct}%` : `${ratePct}%`}
                  </span>
                </div>
                <input
                  type="range"
                  min={-50}
                  max={50}
                  step={5}
                  value={ratePct}
                  onChange={(e) => onRateChange(Number(e.target.value))}
                  aria-label="Speech rate"
                  className="mt-3 w-full accent-primary"
                />
                <div className="mt-1.5 flex items-center justify-between text-[10.5px] text-muted-foreground/70">
                  <span>Slower</span>
                  <button
                    type="button"
                    onClick={() => onRateChange(0)}
                    className="rounded px-1 transition-colors hover:text-foreground"
                  >
                    Reset
                  </button>
                  <span>Faster</span>
                </div>
              </div>
            </>
          )}
        </div>

        <button
          type="button"
          onClick={() => onModeChange(mode === "ptt" ? "handsfree" : "ptt")}
          aria-label={
            mode === "ptt" ? "Switch to hands-free" : "Switch to push-to-talk"
          }
          title={
            mode === "ptt" ? "Switch to hands-free" : "Switch to push-to-talk"
          }
          className={circleBtn}
        >
          {mode === "ptt" ? (
            <Hand className="size-5" />
          ) : (
            <AudioLines className="size-5" />
          )}
        </button>

        {mode === "ptt" ? (
          <button
            type="button"
            onPointerDown={(e) => {
              e.preventDefault();
              e.currentTarget.setPointerCapture?.(e.pointerId);
              onPttHold(true);
            }}
            onPointerUp={() => onPttHold(false)}
            onPointerCancel={() => onPttHold(false)}
            onKeyDown={(e) => {
              if ((e.key === " " || e.key === "Enter") && !e.repeat) {
                e.preventDefault();
                onPttHold(true);
              }
            }}
            onKeyUp={(e) => {
              if (e.key === " " || e.key === "Enter") {
                e.preventDefault();
                onPttHold(false);
              }
            }}
            onBlur={() => onPttHold(false)}
            onContextMenu={(e) => e.preventDefault()}
            aria-label="Hold to talk"
            aria-pressed={pttHeld}
            className={cn(
              "flex h-12 touch-none select-none items-center gap-2 rounded-full px-6 text-[13.5px] font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-primary/60",
              pttHeld
                ? "bg-primary text-primary-foreground"
                : "border border-border/70 bg-card text-foreground hover:bg-accent/60",
            )}
          >
            <Mic className="size-[18px]" />
            {pttHeld ? "Listening…" : "Hold to talk"}
          </button>
        ) : (
          <button
            type="button"
            onClick={onToggleMic}
            aria-label={micMuted ? "Unmute microphone" : "Mute microphone"}
            aria-pressed={micMuted}
            title={micMuted ? "Unmute microphone" : "Mute microphone"}
            className={cn(
              "flex size-12 items-center justify-center rounded-full border transition-colors focus-visible:outline-2 focus-visible:outline-primary/60",
              micMuted
                ? "border-red-500/40 bg-red-500/15 text-red-300 hover:bg-red-500/25"
                : "border-border/70 bg-card text-foreground hover:bg-accent/60",
            )}
          >
            {micMuted ? (
              <MicOff className="size-5" />
            ) : (
              <Mic className="size-5" />
            )}
          </button>
        )}

        <button
          type="button"
          onClick={onClose}
          aria-label="End voice mode"
          title="End voice mode (Esc)"
          className="flex size-12 items-center justify-center rounded-full bg-foreground text-background transition-transform hover:scale-105 focus-visible:outline-2 focus-visible:outline-primary/60"
        >
          <X className="size-5" />
        </button>
      </div>
    </div>
  );
}
