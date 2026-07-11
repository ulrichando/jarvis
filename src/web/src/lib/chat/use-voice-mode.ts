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
import { KOKORO_ID_RE } from "@/lib/chat/voices";

export type VoicePhase = "idle" | "connecting" | "listening" | "speaking";

// Endpointer tuning. SPEECH_RMS is on normalized RMS (0..1) of the time-domain
// signal; the rest are millisecond windows.
const SPEECH_RMS = 0.025; // above this a frame counts as voiced
const SILENCE_MS = 900; // trailing quiet that ends an utterance
const MIN_VOICE_MS = 250; // ignore sub-quarter-second blips (coughs, clicks)

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

  // Settings → General → Voice (a Kokoro voice id). Held in a ref because
  // the TTS fetch fires inside long-lived closures.
  const { data: settings } = useSettings();
  const ttsVoiceRef = useRef<string | null>(null);
  const prefVoice = settings?.user?.voice;
  ttsVoiceRef.current = prefVoice && KOKORO_ID_RE.test(prefVoice) ? prefVoice : null;

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
  const sttGenRef = useRef(0);

  // TTS machinery: read the reply aloud + drive the gray→white highlight.
  const intervalRef = useRef<number | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

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
    // Only feed a transcript while we're actually listening — not if the user
    // stopped voice mode or we've switched to speaking the reply meanwhile.
    if (text && activeRef.current && phaseRef.current === "listening") {
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
      if (text && activeRef.current && phaseRef.current === "listening") {
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
    activeRef.current = false;
    setActive(false);
    setPhase("idle");
    phaseRef.current = "idle";
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
  }, [clearTick, teardown]);

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
      const store = useVoiceRead.getState();
      if (messageId) store.startReading(messageId);

      const finish = () => {
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
          u.lang = navigator.language || "en-US";
          u.onboundary = (ev) => {
            const p = (ev.charIndex ?? 0) + (ev.charLength ?? 0);
            if (p > useVoiceRead.getState().readChar) store.setChar(p);
          };
          u.onend = finish;
          u.onerror = finish;
          window.speechSynthesis.cancel();
          window.speechSynthesis.speak(u);
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
          URL.revokeObjectURL(url);
          audioRef.current = null;
          speakBrowser();
        };
        try {
          await audio.play();
        } catch {
          URL.revokeObjectURL(url);
          audioRef.current = null;
          speakBrowser();
        }
      })();
    },
    [clearTick, pauseListening, startListening],
  );

  // Unmount cleanup. Touch refs directly (not the memoized teardown) so this
  // effect can keep an empty dep array and never re-run mid-session.
  useEffect(
    () => () => {
      activeRef.current = false;
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
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
