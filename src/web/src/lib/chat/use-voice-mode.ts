"use client";

// Voice mode — the /chat live-conversation loop (#53).
//   STT: RESTORED 2026-07-09. /api/stt proxies each captured utterance to an
//        OpenAI-compatible transcription endpoint (default: OpenAI
//        gpt-4o-mini-transcribe; JARVIS_STT_URL swaps providers). It was dead
//        2026-06-29 → 2026-07-09 after the provider-eradication pass removed
//        the old route's only backend. 503 = no key configured server-side —
//        `transcribe` toasts that once instead of failing silently. When no
//        server key is configured, falls back to the browser's
//        SpeechRecognition (Web Speech API) where available (Chrome/Edge).
//   TTS: Kokoro via /api/tts (local, natural voice — same engine the voice
//        agent uses), with a browser speechSynthesis fallback. The gray→white
//        highlight rides the real audio.currentTime (exact) or a time estimate
//        for the fallback.
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { useVoiceRead } from "@/stores/voice-read";
import { useSettings } from "@/hooks/use-settings";
import { KOKORO_ID_RE, isEdgeVoice } from "@/lib/chat/voices";

export type VoicePhase =
  | "idle"
  | "connecting"
  | "listening"
  | "thinking"
  | "speaking";

/** Input style: hands-free = continuous listen with silence endpointing;
 *  ptt = push-to-talk, the mic captures only while the talk button is held. */
export type VoiceInputMode = "handsfree" | "ptt";

// Persisted voice preferences (mirrors the mobile app's pushToTalk +
// voiceRatePct settings). localStorage — per-browser, like theme.
const MODE_KEY = "jarvis.voice.mode";
const RATE_KEY = "jarvis.voice.ratePct";

function readStoredMode(): VoiceInputMode {
  if (typeof window === "undefined") return "handsfree";
  try {
    return localStorage.getItem(MODE_KEY) === "ptt" ? "ptt" : "handsfree";
  } catch {
    return "handsfree";
  }
}

function clampRatePct(n: number): number {
  if (!Number.isFinite(n)) return 0;
  return Math.max(-50, Math.min(50, Math.round(n)));
}

function readStoredRate(): number {
  if (typeof window === "undefined") return 0;
  try {
    return clampRatePct(Number(localStorage.getItem(RATE_KEY) ?? 0));
  } catch {
    return 0;
  }
}

// Endpointer tuning. SPEECH_RMS is on normalized RMS (0..1) of the time-domain
// signal; the rest are millisecond windows.
const SPEECH_RMS = 0.025; // above this a frame counts as voiced
const SILENCE_MS = 900; // trailing quiet that ends an utterance
const MIN_VOICE_MS = 250; // ignore sub-quarter-second blips (coughs, clicks)

// Barge-in tuning (hot-mic monitor while JARVIS speaks). The mic stream runs
// with echoCancellation:true and the TTS plays through an HTMLAudioElement,
// so the browser AEC strips most of JARVIS's own voice from the mic — the
// monitor mostly sees the USER. Residual echo still leaks, hence a threshold
// ~1.8× the endpointer's SPEECH_RMS plus a sustain window before firing.
const BARGE_RMS = 0.045; // ≈1.8× SPEECH_RMS — clears residual-echo floor
const BARGE_SUSTAIN_MS = 280; // voiced time required before interrupting
const BARGE_GAP_RESET_MS = 150; // quiet gap that resets the sustain counter
const BARGE_GUARD_MS = 300; // refractory after playback start (onset transient)

function pickMimeType(): string {
  if (typeof MediaRecorder === "undefined") return "";
  for (const c of [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/mp4",
  ]) {
    if (MediaRecorder.isTypeSupported(c)) return c;
  }
  return "";
}

// Minimal Web Speech API shape (lib.dom omits SpeechRecognition types).
// Enough to run a continuous recognizer and read final transcripts.
type SpeechRecognitionLike = {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  start: () => void;
  stop: () => void;
  onresult:
    | ((e: {
        resultIndex: number;
        results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }>;
      }) => void)
    | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
};

function browserSpeechRecognition(): (new () => SpeechRecognitionLike) | null {
  const w = window as unknown as {
    SpeechRecognition?: new () => SpeechRecognitionLike;
    webkitSpeechRecognition?: new () => SpeechRecognitionLike;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

// Is server-side STT (/api/stt) unavailable? A key-less server answers its key
// check with 503 before it parses a body, so a bodyless POST is a safe probe:
// 503 = no server STT, anything else = it's there.
// ponytail: relies on the route checking the key before the body; if that
// order changes the probe over-reports "available" and we keep the server path
// (whose own 503 toast still fires). Safe failure direction.
async function serverSttUnavailable(): Promise<boolean> {
  try {
    const r = await fetch("/api/stt", { method: "POST" });
    return r.status === 503;
  } catch {
    return false; // network hiccup — don't preempt the server path
  }
}

export function useVoiceMode(opts: {
  onUtterance: (text: string) => void;
  onUnsupported?: () => void;
}) {
  const { onUtterance, onUnsupported } = opts;
  const [active, setActive] = useState(false);
  const [phase, setPhase] = useState<VoicePhase>("idle");

  // Settings → General → Voice (a Kokoro or Edge voice id — /api/tts routes
  // by id). Held in a ref because the TTS fetch fires inside long-lived
  // closures.
  const { data: settings } = useSettings();
  const ttsVoiceRef = useRef<string | null>(null);
  const prefVoice = settings?.user?.voice;
  ttsVoiceRef.current =
    prefVoice && (KOKORO_ID_RE.test(prefVoice) || isEdgeVoice(prefVoice))
      ? prefVoice
      : null;

  const activeRef = useRef(false);
  const phaseRef = useRef<VoicePhase>("idle");
  phaseRef.current = phase;

  // Input mode (hands-free ⇄ push-to-talk) + speech rate — persisted.
  const [mode, setModeState] = useState<VoiceInputMode>(readStoredMode);
  const modeRef = useRef(mode);
  const [ratePct, setRatePctState] = useState<number>(readStoredRate);
  const rateRef = useRef(ratePct);
  // PTT hold state (true while the talk button is pressed).
  const [pttHeld, setPttHeldState] = useState(false);
  const pttHeldRef = useRef(false);
  // Hands-free mic mute (session-scoped, not persisted).
  const [micMuted, setMicMutedState] = useState(false);
  const micMutedRef = useRef(false);
  // Overlay transcript: the user's latest transcribed utterance and the
  // assistant text currently being spoken. Discrete updates only (per
  // utterance / per reply) — never per audio frame.
  const [lastUtterance, setLastUtterance] = useState("");
  const [speakingText, setSpeakingText] = useState("");

  // STT machinery: mic stream → AnalyserNode endpointer → per-utterance
  // MediaRecorder → /api/stt.
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const rafRef = useRef<number | null>(null);
  const voicedMsRef = useRef(0);
  const lastVoiceTsRef = useRef(0);
  const lastTickTsRef = useRef(0);
  const endpointingRef = useRef(false);
  // Warn once (not per-utterance) when STT fails — 503 means the server has
  // no transcription key configured; anything else is an upstream failure.
  // Reset on each start() so a new voice session gets fresh feedback.
  const sttWarnedRef = useRef(false);
  // Voice-session generation: bumped on stop() so an in-flight /api/stt fetch
  // from BEFORE a stop can't deliver its transcript into a restarted session
  // (active+listening are true again after stop→start, so those guards alone
  // don't survive a restart — a discarded utterance would submit a real turn).
  const sttGenRef = useRef(0);

  // TTS machinery: read the reply aloud + drive the gray→white highlight.
  const intervalRef = useRef<number | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // Barge-in machinery: a lightweight VAD raf loop over the SAME analyser
  // (which survives pauseListening — only the recorder + endpointer raf stop)
  // that runs while phase === "speaking". No MediaRecorder during speaking —
  // we never record JARVIS's own audio, we only watch RMS for the user.
  const bargeRafRef = useRef<number | null>(null);
  const bargeVoicedMsRef = useRef(0);
  const bargeLastVoiceTsRef = useRef(0);
  const bargeLastTickTsRef = useRef(0);
  const bargeStartedAtRef = useRef(0);
  // Speak generation: bumped by barge-in and stop() so the normal finish()
  // path of a superseded utterance becomes a no-op — the barge path and the
  // finish path can never both fire (double startListening / stale cleanup).
  const speakGenRef = useRef(0);

  const onUtteranceRef = useRef(onUtterance);
  useEffect(() => {
    onUtteranceRef.current = onUtterance;
  }, [onUtterance]);

  const clearTick = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  // Enable/disable the raw mic tracks. Disabled tracks feed silence to BOTH
  // the analyser (orb goes flat) and the recorder — the honest "not
  // listening" state for mute + PTT-idle without tearing the pipeline down.
  const setTrackEnabled = useCallback((on: boolean) => {
    const s = streamRef.current;
    if (s) for (const t of s.getAudioTracks()) t.enabled = on;
  }, []);

  // Set after beginThinking is defined (it depends on pauseListening, which
  // is defined below transcribe) — called via ref from the STT paths.
  const beginThinkingRef = useRef<() => void>(() => {});

  // --- STT: transcribe a settled utterance ---------------------------------
  const transcribe = useCallback(async (blob: Blob) => {
    const gen = sttGenRef.current;
    const fd = new FormData();
    fd.append("file", blob, "utterance.webm");
    let res: Response;
    try {
      res = await fetch("/api/stt", { method: "POST", body: fd });
    } catch {
      return; // network hiccup — the next utterance will try again
    }
    if (gen !== sttGenRef.current) return; // voice mode stopped meanwhile
    if (!res.ok) {
      // Warn once so the mic toggle isn't a silent dead end. 503 = the server
      // has no transcription key; other statuses = upstream/transient failure.
      if (!sttWarnedRef.current) {
        sttWarnedRef.current = true;
        toast.error(
          res.status === 503
            ? "Voice input isn't set up on this server — configure a transcription key (OPENAI_API_KEY or JARVIS_STT_API_KEY)."
            : "Speech-to-text failed — try again or check the server logs.",
        );
      }
      return;
    }
    let text = "";
    try {
      const data = (await res.json()) as { text?: string };
      text = (data?.text ?? "").trim();
    } catch {
      return;
    }
    // Only feed a transcript while we're actually listening — not if the user
    // stopped voice mode or we've switched to speaking the reply meanwhile.
    if (text && activeRef.current && phaseRef.current === "listening") {
      setLastUtterance(text);
      onUtteranceRef.current(text);
      // The utterance is now with the model — show "thinking" until the
      // reply lands (speak()) or the caller resumes listening (error path).
      beginThinkingRef.current();
    }
  }, []);

  // --- STT fallback: browser SpeechRecognition -----------------------------
  // Used when /api/stt is unavailable (no server transcription key). Runs its
  // own capture + endpointing, so it REPLACES the getUserMedia/MediaRecorder
  // path rather than layering on it. Real on Chrome/Edge; unavailable
  // elsewhere (we probe support before choosing this path).
  const sttFallbackRef = useRef(false);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const startBrowserSttRef = useRef<() => void>(() => {});

  const startBrowserStt = useCallback(() => {
    if (recognitionRef.current || !activeRef.current) return;
    const SR = browserSpeechRecognition();
    if (!SR) return;
    let rec: SpeechRecognitionLike;
    try {
      rec = new SR();
    } catch {
      return;
    }
    rec.lang = navigator.language || "en-US";
    rec.interimResults = false;
    rec.continuous = true;
    rec.onresult = (e) => {
      let text = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) text += r[0]?.transcript ?? "";
      }
      text = text.trim();
      if (text && activeRef.current && phaseRef.current === "listening") {
        setLastUtterance(text);
        onUtteranceRef.current(text);
        beginThinkingRef.current();
      }
    };
    rec.onerror = (e) => {
      // aborted (our stop) + no-speech are normal; anything else (e.g.
      // "network"/"not-allowed" on de-Googled Chromium) means Web Speech can't
      // serve here — say so once.
      if (e.error !== "aborted" && e.error !== "no-speech" && !sttWarnedRef.current) {
        sttWarnedRef.current = true;
        toast.error("Voice input isn't available in this browser.");
      }
    };
    rec.onend = () => {
      // Continuous recognition still ends on long silence / transient errors —
      // reopen while we're meant to be listening. In PTT the recognizer only
      // runs while the talk button is held; in hands-free, not while muted.
      recognitionRef.current = null;
      if (
        activeRef.current &&
        phaseRef.current === "listening" &&
        sttFallbackRef.current &&
        (modeRef.current === "ptt" ? pttHeldRef.current : !micMutedRef.current)
      ) {
        startBrowserSttRef.current();
      }
    };
    recognitionRef.current = rec;
    try {
      rec.start();
    } catch {
      recognitionRef.current = null;
    }
  }, []);
  useEffect(() => {
    startBrowserSttRef.current = startBrowserStt;
  }, [startBrowserStt]);

  const stopBrowserStt = useCallback(() => {
    const rec = recognitionRef.current;
    recognitionRef.current = null;
    if (rec) {
      rec.onend = null; // don't auto-restart
      rec.onresult = null;
      try {
        rec.stop();
      } catch {
        /* already stopped */
      }
    }
  }, []);

  // Open a fresh recording segment. We stop+restart the recorder per utterance
  // so each posted blob is a self-contained file with a valid header.
  const beginSegment = useCallback(() => {
    const stream = streamRef.current;
    if (!stream || !activeRef.current) return;
    chunksRef.current = [];
    voicedMsRef.current = 0;
    lastVoiceTsRef.current = performance.now();
    endpointingRef.current = false;
    let rec: MediaRecorder;
    try {
      const mt = pickMimeType();
      rec = mt ? new MediaRecorder(stream, { mimeType: mt }) : new MediaRecorder(stream);
    } catch {
      return;
    }
    rec.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
    };
    rec.onstop = () => {
      const hadSpeech = voicedMsRef.current >= MIN_VOICE_MS;
      const blob = new Blob(chunksRef.current, { type: rec.mimeType || "audio/webm" });
      // Reopen the mic immediately so we don't clip the next utterance — unless
      // we've since paused (speaking the reply), stopped voice mode, muted, or
      // released the PTT button (a PTT segment is one hold, not continuous).
      if (
        activeRef.current &&
        phaseRef.current === "listening" &&
        (modeRef.current === "ptt" ? pttHeldRef.current : !micMutedRef.current)
      ) {
        beginSegment();
      }
      if (hadSpeech && blob.size > 1200) void transcribe(blob);
    };
    recorderRef.current = rec;
    rec.start();
  }, [transcribe]);

  // Energy endpointer: sample the analyser each frame, accumulate voiced time,
  // and when a voiced segment is trailed by SILENCE_MS of quiet, close the
  // recorder (its onstop sends the blob + reopens the mic).
  const tick = useCallback(() => {
    const analyser = analyserRef.current;
    if (!analyser || !activeRef.current) return;
    const now = performance.now();
    const dt = lastTickTsRef.current ? now - lastTickTsRef.current : 0;
    lastTickTsRef.current = now;

    const buf = new Uint8Array(analyser.fftSize);
    analyser.getByteTimeDomainData(buf);
    let sumSq = 0;
    for (let i = 0; i < buf.length; i++) {
      const v = (buf[i] - 128) / 128;
      sumSq += v * v;
    }
    const rms = Math.sqrt(sumSq / buf.length);

    if (phaseRef.current === "listening" && !endpointingRef.current) {
      if (rms > SPEECH_RMS) {
        voicedMsRef.current += dt;
        lastVoiceTsRef.current = now;
      } else if (
        modeRef.current !== "ptt" && // PTT: the segment ends on release, never on silence
        voicedMsRef.current >= MIN_VOICE_MS &&
        now - lastVoiceTsRef.current > SILENCE_MS
      ) {
        endpointingRef.current = true;
        try {
          recorderRef.current?.stop(); // → onstop sends + reopens
        } catch {
          /* already stopped */
        }
      }
    }
    rafRef.current = requestAnimationFrame(tick);
  }, []);

  const startListening = useCallback(() => {
    setPhase("listening");
    phaseRef.current = "listening";
    if (modeRef.current === "ptt") {
      // PTT: "listening" is the session's resting state; actual capture only
      // runs while the talk button is held (pttHold opens/closes segments).
      setTrackEnabled(pttHeldRef.current);
      if (!pttHeldRef.current) return;
    } else {
      if (micMutedRef.current) {
        setTrackEnabled(false);
        return; // muted — phase shows listening, capture stays closed
      }
      setTrackEnabled(true);
    }
    if (sttFallbackRef.current) {
      startBrowserStt(); // browser SpeechRecognition — its own capture loop
      return;
    }
    lastTickTsRef.current = 0;
    beginSegment();
    if (rafRef.current == null) rafRef.current = requestAnimationFrame(tick);
  }, [beginSegment, tick, startBrowserStt, setTrackEnabled]);

  const pauseListening = useCallback(() => {
    stopBrowserStt();
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    const rec = recorderRef.current;
    recorderRef.current = null;
    if (rec && rec.state !== "inactive") {
      rec.onstop = null; // don't reopen / don't transcribe the partial
      try {
        rec.stop();
      } catch {
        /* already stopped */
      }
    }
  }, [stopBrowserStt]);

  // --- Thinking window: utterance sent, reply not yet spoken ----------------
  // Entered automatically when an utterance is delivered (see transcribe /
  // the browser-STT onresult). Left via speak() (reply arrived) or
  // resumeListening() (chat.tsx calls it when a turn ends with nothing to
  // say — error, empty reply, deduped reply — so the mic can't stay dead).
  const beginThinking = useCallback(() => {
    if (!activeRef.current || phaseRef.current !== "listening") return;
    pauseListening();
    setPhase("thinking");
    phaseRef.current = "thinking";
  }, [pauseListening]);
  useEffect(() => {
    beginThinkingRef.current = beginThinking;
  }, [beginThinking]);

  const resumeListening = useCallback(() => {
    if (!activeRef.current || phaseRef.current !== "thinking") return;
    startListening();
  }, [startListening]);

  // --- Barge-in: hot-mic interrupt while JARVIS speaks ----------------------
  const stopBargeMonitor = useCallback(() => {
    if (bargeRafRef.current != null) {
      cancelAnimationFrame(bargeRafRef.current);
      bargeRafRef.current = null;
    }
  }, []);

  // Fired when the monitor detects sustained user speech: kill the TTS
  // (neural audio AND the speechSynthesis fallback), clear the highlight,
  // and go straight back to listening so the interrupting utterance is
  // captured. Bumping speakGenRef makes the superseded utterance's finish()
  // a no-op, so this path and the normal end-of-playback path can't both run.
  const interruptSpeech = useCallback(() => {
    if (!activeRef.current || phaseRef.current !== "speaking") return;
    speakGenRef.current++;
    stopBargeMonitor();
    clearTick();
    const audio = audioRef.current;
    audioRef.current = null;
    if (audio) {
      try {
        audio.pause();
      } catch {
        /* gone */
      }
      try {
        if (audio.src.startsWith("blob:")) URL.revokeObjectURL(audio.src);
      } catch {
        /* detached */
      }
    }
    try {
      window.speechSynthesis?.cancel();
    } catch {
      /* no synth */
    }
    useVoiceRead.getState().stopReading();
    setSpeakingText("");
    startListening();
  }, [clearTick, startListening, stopBargeMonitor]);

  // --- Push-to-talk: capture only while the talk button is held -------------
  // Down → open a capture segment (interrupting JARVIS if he's mid-reply);
  // up → close the segment, which transcribes + sends it (mobile pttHold).
  const pttHold = useCallback(
    (down: boolean) => {
      if (!activeRef.current || modeRef.current !== "ptt") return;
      if (down) {
        if (pttHeldRef.current) return;
        pttHeldRef.current = true;
        setPttHeldState(true);
        if (phaseRef.current === "connecting") return; // start() arms capture once live
        if (phaseRef.current === "speaking") {
          interruptSpeech(); // → startListening() sees the held flag and captures
          return;
        }
        startListening();
        return;
      }
      if (!pttHeldRef.current) return;
      pttHeldRef.current = false;
      setPttHeldState(false);
      setTrackEnabled(false);
      if (sttFallbackRef.current) {
        stopBrowserStt(); // finals already delivered by the recognizer
        return;
      }
      // Close the held segment. Unlike pauseListening we KEEP rec.onstop —
      // that's what transcribes + sends the utterance.
      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      const rec = recorderRef.current;
      recorderRef.current = null;
      if (rec && rec.state !== "inactive") {
        try {
          rec.stop();
        } catch {
          /* already stopped */
        }
      }
    },
    [interruptSpeech, startListening, stopBrowserStt, setTrackEnabled],
  );

  // Switch hands-free ⇄ push-to-talk (persisted). Mid-session the capture
  // pipeline is re-armed under the new mode's rules.
  const setMode = useCallback(
    (m: VoiceInputMode) => {
      if (modeRef.current === m) return;
      modeRef.current = m;
      setModeState(m);
      try {
        localStorage.setItem(MODE_KEY, m);
      } catch {
        /* private mode */
      }
      pttHeldRef.current = false;
      setPttHeldState(false);
      if (!activeRef.current) return;
      if (phaseRef.current === "listening") {
        pauseListening();
        startListening();
      }
    },
    [pauseListening, startListening],
  );

  // Hands-free mic mute toggle (tap the mic in the overlay). Tracks are
  // disabled so the analyser flatlines — the orb honestly shows "not heard".
  const toggleMic = useCallback(() => {
    if (!activeRef.current || modeRef.current === "ptt") return;
    const next = !micMutedRef.current;
    micMutedRef.current = next;
    setMicMutedState(next);
    if (phaseRef.current !== "listening") {
      // speaking/thinking — just flip the tracks; startListening() applies
      // the mute when the turn returns to listening.
      setTrackEnabled(!next);
      return;
    }
    if (next) {
      pauseListening();
      setTrackEnabled(false);
    } else {
      startListening();
    }
  }, [pauseListening, startListening, setTrackEnabled]);

  // Speech rate (percent delta, -50..+50 — the mobile app's voiceRatePct).
  const setRatePct = useCallback((n: number) => {
    const v = clampRatePct(n);
    rateRef.current = v;
    setRatePctState(v);
    try {
      localStorage.setItem(RATE_KEY, String(v));
    } catch {
      /* private mode */
    }
  }, []);

  // Stable analyser getter for the overlay's canvas loop — the orb reads
  // audio energy straight off the AnalyserNode inside its own rAF loop, so
  // no React state ever updates per audio frame. Null on the
  // browser-SpeechRecognition fallback path (it owns capture — no pipeline).
  const getAnalyser = useCallback(() => analyserRef.current, []);

  // Per-frame RMS sampler (same math as tick()) with three false-trigger
  // guards: a refractory window after playback start (TTS onset transient,
  // before AEC converges), a higher-than-endpointer RMS threshold (residual
  // echo), and a sustain requirement (~BARGE_SUSTAIN_MS of voiced time, with
  // short quiet gaps tolerated) before firing.
  const bargeTick = useCallback(() => {
    const analyser = analyserRef.current;
    if (!analyser || !activeRef.current || phaseRef.current !== "speaking") {
      bargeRafRef.current = null;
      return;
    }
    const now = performance.now();
    const dt = bargeLastTickTsRef.current ? now - bargeLastTickTsRef.current : 0;
    bargeLastTickTsRef.current = now;

    if (now - bargeStartedAtRef.current >= BARGE_GUARD_MS) {
      const buf = new Uint8Array(analyser.fftSize);
      analyser.getByteTimeDomainData(buf);
      let sumSq = 0;
      for (let i = 0; i < buf.length; i++) {
        const v = (buf[i] - 128) / 128;
        sumSq += v * v;
      }
      const rms = Math.sqrt(sumSq / buf.length);
      if (rms > BARGE_RMS) {
        bargeVoicedMsRef.current += dt;
        bargeLastVoiceTsRef.current = now;
      } else if (now - bargeLastVoiceTsRef.current > BARGE_GAP_RESET_MS) {
        bargeVoicedMsRef.current = 0; // brief blip, not sustained speech
      }
      if (bargeVoicedMsRef.current >= BARGE_SUSTAIN_MS) {
        interruptSpeech();
        return;
      }
    }
    bargeRafRef.current = requestAnimationFrame(bargeTick);
  }, [interruptSpeech]);

  // Started once TTS playback actually begins (not at speak() entry — the
  // /api/tts fetch can take a while and the guard window is relative to
  // audible onset). No-op on the browser-SpeechRecognition STT path, where
  // we own no mic pipeline (no analyser) — barge-in needs the analyser.
  const startBargeMonitor = useCallback(() => {
    if (!analyserRef.current || bargeRafRef.current != null) return;
    bargeVoicedMsRef.current = 0;
    bargeLastVoiceTsRef.current = 0;
    bargeLastTickTsRef.current = 0;
    bargeStartedAtRef.current = performance.now();
    bargeRafRef.current = requestAnimationFrame(bargeTick);
  }, [bargeTick]);

  const teardown = useCallback(() => {
    pauseListening();
    if (audioCtxRef.current) {
      void audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    analyserRef.current = null;
    if (streamRef.current) {
      for (const t of streamRef.current.getTracks()) t.stop();
      streamRef.current = null;
    }
  }, [pauseListening]);

  const start = useCallback(async () => {
    if (activeRef.current) return;
    activeRef.current = true;
    setActive(true);
    setPhase("connecting");
    phaseRef.current = "connecting";
    sttWarnedRef.current = false; // fresh session → fresh failure feedback

    // Pick the capture path once, up front: server STT if configured, else the
    // browser's SpeechRecognition where it exists — so we never open a mic
    // pipeline we won't use.
    sttFallbackRef.current =
      (await serverSttUnavailable()) && !!browserSpeechRecognition();
    if (!activeRef.current) return; // stopped during the probe

    if (sttFallbackRef.current) {
      startListening(); // browser SpeechRecognition owns capture + permission
      return;
    }

    const md = navigator.mediaDevices;
    if (!md?.getUserMedia || typeof MediaRecorder === "undefined") {
      activeRef.current = false;
      setActive(false);
      setPhase("idle");
      phaseRef.current = "idle";
      toast.error("This browser can't capture microphone audio.");
      onUnsupported?.();
      return;
    }
    let stream: MediaStream;
    try {
      stream = await md.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
    } catch {
      activeRef.current = false;
      setActive(false);
      setPhase("idle");
      phaseRef.current = "idle";
      toast.error(
        "Microphone access was blocked — allow it in your browser to use voice mode.",
      );
      onUnsupported?.();
      return;
    }
    if (!activeRef.current) {
      // user stopped voice mode during the permission prompt
      for (const t of stream.getTracks()) t.stop();
      return;
    }
    streamRef.current = stream;
    try {
      const Ctx =
        window.AudioContext ??
        (window as unknown as { webkitAudioContext: typeof AudioContext })
          .webkitAudioContext;
      const ctx = new Ctx();
      audioCtxRef.current = ctx;
      const src = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      src.connect(analyser);
      analyserRef.current = analyser;
    } catch {
      teardown();
      activeRef.current = false;
      setActive(false);
      setPhase("idle");
      phaseRef.current = "idle";
      toast.error("Couldn't start audio processing for voice mode.");
      onUnsupported?.();
      return;
    }
    startListening();
  }, [onUnsupported, startListening, teardown]);

  const stop = useCallback(() => {
    sttGenRef.current++; // invalidate in-flight transcriptions from this session
    speakGenRef.current++; // any pending finish() becomes a no-op
    activeRef.current = false;
    setActive(false);
    setPhase("idle");
    phaseRef.current = "idle";
    pttHeldRef.current = false;
    setPttHeldState(false);
    micMutedRef.current = false;
    setMicMutedState(false);
    setLastUtterance("");
    setSpeakingText("");
    stopBargeMonitor();
    teardown();
    clearTick();
    if (audioRef.current) {
      try {
        audioRef.current.pause();
      } catch {
        /* gone */
      }
      audioRef.current = null;
    }
    useVoiceRead.getState().stopReading();
    try {
      window.speechSynthesis?.cancel();
    } catch {
      /* no synth */
    }
  }, [clearTick, stopBargeMonitor, teardown]);

  const toggle = useCallback(() => {
    if (activeRef.current) stop();
    else void start();
  }, [start, stop]);

  // --- TTS: read an assistant reply aloud, pausing STT meanwhile -----------
  const speak = useCallback(
    (text: string, messageId?: string) => {
      if (!activeRef.current || !text) return;
      pauseListening();
      setPhase("speaking");
      phaseRef.current = "speaking";
      // This utterance's generation. Barge-in and stop() bump the counter,
      // turning every callback below (finish, onerror, late speakBrowser)
      // into a no-op — so the normal end-of-playback path and the barge-in
      // path can never both fire.
      const gen = ++speakGenRef.current;
      const store = useVoiceRead.getState();
      if (messageId) store.startReading(messageId);
      setSpeakingText(text);

      const finish = () => {
        if (gen !== speakGenRef.current) return; // barged-in or superseded
        stopBargeMonitor();
        clearTick();
        if (audioRef.current) {
          try {
            audioRef.current.pause();
          } catch {
            /* gone */
          }
          audioRef.current = null;
        }
        store.stopReading();
        setSpeakingText("");
        if (activeRef.current) startListening();
      };

      // Fallback: browser TTS (robotic on Linux). onboundary doesn't fire on
      // Linux/Android, so a time estimate drives the reveal there.
      const speakBrowser = () => {
        if (gen !== speakGenRef.current) return; // barged-in or superseded
        if (!("speechSynthesis" in window)) {
          finish();
          return;
        }
        const startedAt = Date.now();
        clearTick();
        intervalRef.current = window.setInterval(() => {
          const est = Math.min(text.length, Math.floor(((Date.now() - startedAt) / 1000) * 15));
          if (est > useVoiceRead.getState().readChar) store.setChar(est);
        }, 80);
        try {
          const u = new SpeechSynthesisUtterance(text);
          // Voice mode is English-only — never navigator.language, which
          // makes browsers with a non-English locale read replies in that
          // locale's voice (mangled English at best).
          u.lang = "en-US";
          // Honor the speech-rate preference on the fallback voice too.
          if (rateRef.current !== 0) {
            u.rate = Math.max(0.5, Math.min(2, 1 + rateRef.current / 100));
          }
          u.onboundary = (ev) => {
            const p = (ev.charIndex ?? 0) + (ev.charLength ?? 0);
            if (p > useVoiceRead.getState().readChar) store.setChar(p);
          };
          u.onend = finish;
          u.onerror = finish;
          window.speechSynthesis.cancel();
          window.speechSynthesis.speak(u);
          // Hot-mic barge-in (no-op without an analyser, i.e. on the
          // browser-SpeechRecognition STT path). AEC coverage of synth
          // output varies by platform — the higher threshold + sustain
          // window carry more of the load here than on the audio-element
          // path.
          startBargeMonitor();
        } catch {
          finish();
        }
      };

      // Preferred: neural TTS (Orpheus) — highlight rides real audio.currentTime.
      void (async () => {
        let res: Response;
        try {
          res = await fetch("/api/tts", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              text,
              ...(ttsVoiceRef.current ? { voice: ttsVoiceRef.current } : {}),
              // Speech-rate preference (percent delta). Omitted at 0 so the
              // request shape is unchanged for the default speed.
              ...(rateRef.current !== 0 ? { ratePct: rateRef.current } : {}),
            }),
          });
        } catch {
          speakBrowser();
          return;
        }
        if (!activeRef.current) {
          finish();
          return;
        }
        if (!res.ok) {
          speakBrowser();
          return;
        }
        let url: string;
        try {
          url = URL.createObjectURL(await res.blob());
        } catch {
          speakBrowser();
          return;
        }
        const audio = new Audio(url);
        audioRef.current = audio;
        clearTick();
        intervalRef.current = window.setInterval(() => {
          const d = audio.duration;
          if (d && isFinite(d) && d > 0) {
            const est = Math.min(text.length, Math.floor((audio.currentTime / d) * text.length));
            if (est > useVoiceRead.getState().readChar) store.setChar(est);
          }
        }, 80);
        audio.onended = () => {
          URL.revokeObjectURL(url);
          finish();
        };
        audio.onerror = () => {
          URL.revokeObjectURL(url); // no-op if barge-in already revoked it
          if (gen !== speakGenRef.current) return; // don't touch a newer turn
          audioRef.current = null;
          speakBrowser();
        };
        try {
          await audio.play();
        } catch {
          URL.revokeObjectURL(url);
          if (gen !== speakGenRef.current) return;
          audioRef.current = null;
          speakBrowser();
          return;
        }
        // Playback is audibly running — arm the hot-mic barge-in monitor
        // (its BARGE_GUARD_MS refractory is relative to this moment).
        if (gen === speakGenRef.current) startBargeMonitor();
      })();
    },
    [clearTick, pauseListening, startBargeMonitor, startListening, stopBargeMonitor],
  );

  // Unmount cleanup. Touch refs directly (not the memoized teardown) so this
  // effect can keep an empty dep array and never re-run mid-session.
  useEffect(
    () => () => {
      activeRef.current = false;
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      if (bargeRafRef.current != null) cancelAnimationFrame(bargeRafRef.current);
      const rec = recorderRef.current;
      if (rec && rec.state !== "inactive") {
        rec.onstop = null;
        try {
          rec.stop();
        } catch {
          /* already stopped */
        }
      }
      if (recognitionRef.current) {
        recognitionRef.current.onend = null;
        try {
          recognitionRef.current.stop();
        } catch {
          /* already stopped */
        }
      }
      if (audioCtxRef.current) void audioCtxRef.current.close().catch(() => {});
      if (streamRef.current) {
        for (const t of streamRef.current.getTracks()) t.stop();
      }
      if (intervalRef.current) clearInterval(intervalRef.current);
      if (audioRef.current) {
        try {
          audioRef.current.pause();
        } catch {
          /* gone */
        }
      }
      try {
        window.speechSynthesis?.cancel();
      } catch {
        /* no synth */
      }
    },
    [],
  );

  return {
    active,
    phase,
    toggle,
    start,
    stop,
    speak,
    // Thinking window (utterance sent, reply pending).
    beginThinking,
    resumeListening,
    // Input mode + push-to-talk.
    mode,
    setMode,
    pttHold,
    pttHeld,
    // Hands-free mic mute.
    micMuted,
    toggleMic,
    // Speech rate (percent delta, -50..+50).
    ratePct,
    setRatePct,
    // Overlay feed: analyser for the canvas orb (read inside its own rAF
    // loop — never per-frame React state) + the live transcript pieces.
    getAnalyser,
    lastUtterance,
    speakingText,
  };
}
