#!/usr/bin/env python3
"""One-shot — render JARVIS canned-phrase WAVs via the local Kokoro TTS
server (kokoro-fastapi, the same OpenAI-compatible /audio/speech endpoint
providers/kokoro_tts.py speaks to). Saves to ~/.jarvis/cache/voice/.
Re-run if voice config changes.

These WAVs are the breaker-open fallback: when _LLM_BREAKER is open
and JARVIS has nothing else to say, it speaks one of these instead
of going silent. See spec
docs/superpowers/specs/2026-05-04-jarvis-voice-resilience-design.md.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

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
    """Read ~/.jarvis/tts-provider; format is `kokoro:<voice>` /
    `edge:<voice>` or just a voice name. Default to af_heart when
    unset or when the spec picks a non-Kokoro provider."""
    p = Path.home() / ".jarvis" / "tts-provider"
    if not p.exists():
        return "af_heart"
    raw = p.read_text().strip()
    if ":" in raw:
        provider, voice = raw.split(":", 1)
        if provider.strip() != "kokoro":
            return "af_heart"
        return voice.strip() or "af_heart"
    return raw or "af_heart"


def _synthesize_wav(text: str, voice: str) -> bytes:
    """One OpenAI-compatible /audio/speech call to the local Kokoro
    server (same endpoint + payload shape as providers/kokoro_tts.py);
    response_format=wav so the body is a ready-to-save WAV."""
    base = os.environ.get("JARVIS_LOCAL_TTS_URL", "http://127.0.0.1:8880/v1").rstrip("/")
    req = urllib.request.Request(
        base + "/audio/speech",
        data=json.dumps(
            {"model": "kokoro", "input": text, "voice": voice, "response_format": "wav"}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def main() -> int:
    failures = 0
    _load_keys_env()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Match production: jarvis_agent.py exports JARVIS_LOCAL_TTS_VOICE
    # (kept in sync with the tray's Kokoro voice pick).
    voice = os.environ.get("JARVIS_LOCAL_TTS_VOICE") or _read_voice_setting()
    print(f"voice: {voice}")

    for filename, text in PHRASES.items():
        out_path = CACHE_DIR / filename
        tmp_path = out_path.with_suffix(".wav.tmp")
        print(f"rendering: {text!r} -> {out_path}")
        try:
            wav_bytes = _synthesize_wav(text, voice)
            if not wav_bytes:
                print("  ERROR: empty response from the local TTS server")
                failures += 1
                continue
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
        print(f"\n{failures}/{len(PHRASES)} phrases failed — re-run when the local Kokoro TTS server is reachable")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
