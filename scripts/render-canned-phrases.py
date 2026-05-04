#!/usr/bin/env python3
"""One-shot — render JARVIS canned-phrase WAVs using Groq TTS while
it's healthy. Saves to ~/.jarvis/cache/voice/. Re-run if voice
config changes.

These WAVs are the breaker-open fallback: when _LLM_BREAKER is open
and JARVIS has nothing else to say, it speaks one of these instead
of going silent. See spec
docs/superpowers/specs/2026-05-04-jarvis-voice-resilience-design.md.
"""
import asyncio
import os
import sys
from pathlib import Path

from livekit.plugins import groq

CACHE_DIR = Path.home() / ".jarvis" / "cache" / "voice"


def _load_keys_env() -> None:
    """Load ~/.jarvis/keys.env into os.environ. keys.env values WIN
    on collision so a stale shell-exported key doesn't beat the live
    one. Mirrors production behaviour in jarvis_agent.py."""
    p = Path.home() / ".jarvis" / "keys.env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and v:
            os.environ[k] = v


# NB: src/voice-agent/canned_phrases.py uses PHRASES as a tuple of
# stems without extension; this dict maps filename → text for
# rendering. The base names (without `.wav`) must match.
PHRASES = {
    "one_second.wav":          "One second, sir.",
    "connection_unstable.wav": "Connection unstable, sir.",
    "try_again.wav":           "Could you try that again, sir?",
}


def _read_voice_setting() -> str:
    """Read ~/.jarvis/tts-provider; format is `groq:<voice>` or just
    a voice name. Default to troy if not set."""
    p = Path.home() / ".jarvis" / "tts-provider"
    if not p.exists():
        return "troy"
    raw = p.read_text().strip()
    if ":" in raw:
        _, voice = raw.split(":", 1)
        return voice
    return raw or "troy"


async def main() -> int:
    failures = 0
    _load_keys_env()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Match production: jarvis_agent.py uses JARVIS_TTS_VOICE.
    voice = os.environ.get("JARVIS_TTS_VOICE") or _read_voice_setting()
    print(f"voice: {voice}")

    # Mirror jarvis_agent.py's groq.TTS construction. The agent uses
    # _LoggingGroqTTS(model="canopylabs/orpheus-v1-english", voice=voice),
    # but we use the vanilla groq.TTS here — no diagnostic shim needed
    # for one-shot rendering.
    tts = groq.TTS(model="canopylabs/orpheus-v1-english", voice=voice)

    for filename, text in PHRASES.items():
        out_path = CACHE_DIR / filename
        tmp_path = out_path.with_suffix(".wav.tmp")
        print(f"rendering: {text!r} -> {out_path}")
        try:
            stream = tts.synthesize(text)
            frames = []
            async for chunk in stream:
                frames.append(chunk.frame)
            if not frames:
                print(f"  ERROR: no frames received from Groq")
                failures += 1
                continue
            from livekit import rtc
            combined = rtc.combine_audio_frames(frames)
            wav_bytes = combined.to_wav_bytes()
            tmp_path.write_bytes(wav_bytes)
            tmp_path.replace(out_path)  # atomic on POSIX
            size = out_path.stat().st_size
            print(f"  wrote {size} bytes (WAV)")
        except Exception as e:
            print(f"  ERROR: {e}")
            failures += 1
            # Clean up the .tmp file if it exists
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass

    if failures:
        print(f"\n{failures}/{len(PHRASES)} phrases failed — re-run when Groq TTS is reachable")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
