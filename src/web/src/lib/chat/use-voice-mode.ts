"use client";

// Voice mode — the /chat live-conversation loop (#53).
//   STT: we capture mic audio with the standard browser stack
//        (getUserMedia + MediaRecorder) and transcribe each utterance
//        server-side via /api/stt (Groq Whisper Large v3 Turbo). We do NOT use
//        the Web Speech API: it leans on Google's speech servers that aren't
//        bundled in Chromium builds, so it fails there with "not-allowed". The
//        standard stack works on every Chromium/Firefox/Safari with no model
//        assets to load — important because this is verified blind (no browser
//        introspection on our side). End-of-utterance is detected with an
//        energy-based endpointer (Web Audio AnalyserNode RMS) — the same
//        technique neural-VAD libraries wrap; swap in Silero (@ricky0123/vad)
//        later if ambient noise demands it.
//   TTS: Orpheus via /api/tts (natural voice, like the voice agent), with a
//        browser speechSynthesis fallback. The gray→white highlight rides the
//        real audio.currentTime (exact) or a time estimate for the fallback.
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { useVoiceRead } from "@/stores/voice-read";

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

export function useVoiceMode(opts: {
  onUtterance: (text: string) => void;
  onUnsupported?: () => void;
}) {
  const { onUtterance, onUnsupported } = opts;
  const [active, setActive] = useState(false);
  const [phase, setPhase] = useState<VoicePhase>("idle");

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
    const fd = new FormData();
    fd.append("file", blob, "utterance.webm");
    let res: Response;
    try {
      res = await fetch("/api/stt", { method: "POST", body: fd });
    } catch {
      return; // network hiccup — the next utterance will try again
    }
    if (!res.ok) {
      if (res.status === 503) {
        toast.error("Speech-to-text isn't configured on the server.");
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
    lastTickTsRef.current = 0;
    beginSegment();
    if (rafRef.current == null) rafRef.current = requestAnimationFrame(tick);
  }, [beginSegment, tick]);

  const pauseListening = useCallback(() => {
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
  }, []);

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
    const md = navigator.mediaDevices;
    if (!md?.getUserMedia || typeof MediaRecorder === "undefined") {
      toast.error("This browser can't capture microphone audio.");
      onUnsupported?.();
      return;
    }
    activeRef.current = true;
    setActive(true);
    setPhase("connecting");
    phaseRef.current = "connecting";
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
            body: JSON.stringify({ text }),
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
