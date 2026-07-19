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
import { normalizeForSpeech } from "@/lib/chat/tts-normalize";

export type VoicePhase = "idle" | "connecting" | "listening" | "speaking";

// Endpointer tuning. SPEECH_RMS is on normalized RMS (0..1) of the time-domain
// signal; the rest are millisecond windows.
// SPEECH_RMS 0.025→0.02 (2026-07-18, quiet-speech under-capture): 0.025
// (≈-32 dBFS) sat close enough to soft post-noise-suppression speech that a
// quiet talker could fail to accumulate MIN_VOICE_MS and never endpoint (the
// utterance was captured but never sent). 0.02 (≈-34 dBFS) still leaves wide
// headroom over a typical suppressed room-noise floor (< -50 dBFS).
// Tradeoff: a genuinely loud environment (TV/music) is ~2 dB more likely to
// count as voiced → spurious sends or late endpointing. NEEDS A REAL-MIC
// PASS — if background noise starts triggering segments, revert to 0.025.
const SPEECH_RMS = 0.02; // above this a frame counts as voiced
const SILENCE_MS = 900; // trailing quiet that ends an utterance
const MIN_VOICE_MS = 250; // ignore sub-quarter-second blips (coughs, clicks)

// Barge-in tuning (hot-mic monitor while JARVIS speaks). The mic stream runs
// with echoCancellation:true and the TTS plays through an HTMLAudioElement,
// so the browser AEC strips most of JARVIS's own voice from the mic — the
// monitor mostly sees the USER. Residual echo still leaks, hence a threshold
// well above the endpointer's SPEECH_RMS plus a sustain window before firing.
const BARGE_RMS = 0.045; // 2.25× SPEECH_RMS (absolute value unchanged by the
// 2026-07-18 SPEECH_RMS retune — barge sensitivity is deliberately untouched)
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
  // ALSO bumped on barge-in (interruptSpeech): once the user interrupts, a
  // pre-barge in-flight transcript is stale — delivering it alongside the
  // re-spoken interruption would double-submit.
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
    // Deliver if the voice session is still live and this STT generation
    // hasn't been superseded. Deliberately NOT gated on phase==="listening":
    // the /api/stt round-trip can land a moment after the previous reply's
    // TTS starts (phase already "speaking"), and that was real user speech —
    // the old phase gate silently ate those utterances. Guards:
    //   (a) double-delivery: each blob is transcribed exactly once (one
    //       recorder onstop → one transcribe call), so a passing gen check
    //       here can't deliver the same utterance twice;
    //   (b) empty text: `text` is trimmed + truthy-checked;
    //   (c) superseded generation: stop() AND barge-in (interruptSpeech)
    //       bump sttGenRef, so a pre-barge/pre-stop in-flight transcript
    //       can't double-submit alongside whatever the user re-speaks
    //       after taking the floor back.
    if (text && activeRef.current && gen === sttGenRef.current) {
      onUtteranceRef.current(text);
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
      // Gate on session liveness, not instantaneous phase: a final result
      // racing the listening→speaking flip is still real user speech. Stale
      // recognizer instances can't fire here — stopBrowserStt() (run by both
      // stop() and pauseListening()) detaches onresult before any phase flip.
      if (text && activeRef.current) {
        onUtteranceRef.current(text);
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
      // reopen while we're meant to be listening.
      recognitionRef.current = null;
      if (activeRef.current && phaseRef.current === "listening" && sttFallbackRef.current) {
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
      // we've since paused (speaking the reply) or stopped voice mode.
      if (activeRef.current && phaseRef.current === "listening") beginSegment();
      // Size floor 1200→600 (2026-07-18): this gate only exists to skip
      // header-only / degenerate blobs (a bare webm/opus container header is
      // ~200–700 B). At 1200 a short quiet utterance could be eaten — VBR
      // opus encodes soft speech small. Real-speech screening is hadSpeech
      // (≥MIN_VOICE_MS of voiced frames), not the byte count; a false
      // positive here just costs one cheap STT call that returns "".
      if (hadSpeech && blob.size > 600) void transcribe(blob);
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
    if (sttFallbackRef.current) {
      startBrowserStt(); // browser SpeechRecognition — its own capture loop
      return;
    }
    lastTickTsRef.current = 0;
    beginSegment();
    if (rafRef.current == null) rafRef.current = requestAnimationFrame(tick);
  }, [beginSegment, tick, startBrowserStt]);

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
    // The user is taking the floor back — invalidate any in-flight /api/stt
    // fetch from before the barge so its stale transcript can't double-submit
    // alongside what they're about to say.
    sttGenRef.current++;
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
    // KNOWN LIMITATION (deliberate): capture starts HERE, so the head of the
    // interruption — the ~BARGE_SUSTAIN_MS of speech that proved the barge,
    // plus anything inside BARGE_GUARD_MS — is not recorded. There is no
    // teardown delay to shave: everything above is synchronous, and the
    // pause-TTS-before-recorder order is what keeps JARVIS's own tail out of
    // the blob. Recovering the head means recording through playback with a
    // pre-roll ring buffer, rejected for now: any AEC leakage would put
    // JARVIS's own TTS into the transcript (self-interruption). Revisit only
    // with a real-mic AEC verification pass.
    startListening();
  }, [clearTick, startListening, stopBargeMonitor]);

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
        // echoCancellation: the barge-in monitor depends on the browser AEC
        // stripping JARVIS's TTS from the mic — keep ON. autoGainControl:
        // explicitly ON (was left to per-browser defaults) — it lifts quiet
        // speakers toward the SPEECH_RMS voiced threshold. noiseSuppression
        // stays ON: it can shave soft consonants, but turning it off raises
        // the noise floor into endpointer/barge false-trigger territory —
        // don't flip it without a real-mic pass.
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
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
      // Supersede any reply that's still being read aloud. Reachable when a
      // new turn lands mid-playback (e.g. a late STT transcript submitted
      // while the previous reply was speaking): the gen bump above turns the
      // old utterance's finish()/onended into no-ops, so without this the old
      // HTMLAudioElement would keep playing UNDER the new reply, and the old
      // highlight interval would corrupt the new message's reveal.
      stopBargeMonitor();
      clearTick();
      const prevAudio = audioRef.current;
      if (prevAudio) {
        audioRef.current = null;
        try {
          prevAudio.pause();
        } catch {
          /* gone */
        }
        try {
          if (prevAudio.src.startsWith("blob:")) URL.revokeObjectURL(prevAudio.src);
        } catch {
          /* detached */
        }
      }
      try {
        window.speechSynthesis?.cancel();
      } catch {
        /* no synth */
      }
      const store = useVoiceRead.getState();
      if (messageId) store.startReading(messageId);

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
          // Strip markdown / symbols so the browser voice doesn't read
          // "asterisk", "slash", etc. (the /api/tts server path normalizes
          // too; this covers the Kokoro/Edge-unreachable fallback). The
          // read-along estimate still tracks the original text length —
          // it's a rough time estimate on Linux where onboundary is silent.
          const u = new SpeechSynthesisUtterance(normalizeForSpeech(text) || text);
          // Voice mode is English-only — never navigator.language, which
          // makes browsers with a non-English locale read replies in that
          // locale's voice (mangled English at best).
          u.lang = "en-US";
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

  return { active, phase, toggle, start, stop, speak };
}
