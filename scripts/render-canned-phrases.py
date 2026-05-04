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

# Allow imports from src/voice-agent so we can reuse the agent's
# Groq TTS construction conventions.
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "voice-agent"))

from livekit.plugins import groq

CACHE_DIR = Path.home() / ".jarvis" / "cache" / "voice"


def _load_keys_env() -> None:
    """Mirror jarvis_agent.py: load ~/.jarvis/keys.env into os.environ
    so GROQ_API_KEY is available without needing a full systemd env."""
    keys_env = Path.home() / ".jarvis" / "keys.env"
    if not keys_env.exists():
        return
    for line in keys_env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
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


async def main():
    _load_keys_env()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    voice = os.environ.get("JARVIS_VOICE") or _read_voice_setting()
    print(f"voice: {voice}")

    # Mirror jarvis_agent.py's groq.TTS construction. The agent uses
    # _LoggingGroqTTS(model="canopylabs/orpheus-v1-english", voice=voice),
    # but we use the vanilla groq.TTS here — no diagnostic shim needed
    # for one-shot rendering.
    tts = groq.TTS(model="canopylabs/orpheus-v1-english", voice=voice)

    for filename, text in PHRASES.items():
        out_path = CACHE_DIR / filename
        print(f"rendering: {text!r} -> {out_path}")
        try:
            stream = tts.synthesize(text)
            with open(out_path, "wb") as f:
                async for chunk in stream:
                    f.write(chunk.frame.data.tobytes())
            size = out_path.stat().st_size
            print(f"  wrote {size} bytes")
            if size == 0:
                print(f"  WARNING: zero bytes — render failed for {filename}")
        except Exception as e:
            print(f"  ERROR: {e}")
            print(f"  (run again when Groq TTS is reachable)")


if __name__ == "__main__":
    asyncio.run(main())
