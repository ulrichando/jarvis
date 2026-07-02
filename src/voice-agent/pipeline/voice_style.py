"""Runtime voice-style store — speaking speed (and pitch where the
engine supports it), adjustable mid-session.

Feature 2026-07-02: "Jarvis, speak slower" was a claim with no knob —
the user asked for slower speech live and nothing changed. Both TTS
engines already accept style parameters (Kokoro `speed` 0.25–4.0 on
/v1/audio/speech — schema-verified against the live container; EdgeTTS
`rate`/`pitch` signed strings) but froze them at construction.

Store = flat files under ~/.jarvis (the voice-model / tts-provider
convention):

  tts-speed — float multiplier, clamped SPEED_MIN..SPEED_MAX (1.0 = normal).
  tts-pitch — signed integer Hz offset (EdgeTTS only; Kokoro — the usual
              primary — has no pitch parameter, so pitch shapes the Edge
              fallback voice while speed shapes BOTH engines).

Providers read these PER SYNTHESIS (the Kokoro voice hot-swap pattern),
so a change lands on the very next utterance — no restart. Writers: the
`voice_style` supervisor tool ("speak slower") and the tray Speech-rate
submenu. Absent files mean "engine defaults" — reset() just deletes them.
"""
from __future__ import annotations

from pathlib import Path

# Engine allows 0.25–4.0 (Kokoro schema), but beyond this window speech
# stops sounding like conversation — clamp to the sane voice band.
SPEED_MIN: float = 0.5
SPEED_MAX: float = 2.0
SPEED_STEP: float = 0.1  # one "slower"/"faster" increment

PITCH_MIN_HZ: int = -50
PITCH_MAX_HZ: int = 50

SPEED_FILE: Path = Path.home() / ".jarvis" / "tts-speed"
PITCH_FILE: Path = Path.home() / ".jarvis" / "tts-pitch"


def clamp_speed(v: float) -> float:
    return max(SPEED_MIN, min(SPEED_MAX, float(v)))


def get_speed(default: float = 1.0) -> float:
    """Current speed multiplier, or `default` when unset/unreadable."""
    try:
        return clamp_speed(float(SPEED_FILE.read_text(encoding="utf-8").strip()))
    except Exception:
        return default


def set_speed(v: float) -> float:
    """Clamp + persist; returns the applied value. Never raises on write
    failure — returns the clamped value regardless (best-effort store)."""
    applied = round(clamp_speed(v), 2)
    try:
        SPEED_FILE.parent.mkdir(parents=True, exist_ok=True)
        SPEED_FILE.write_text(f"{applied}\n", encoding="utf-8")
    except Exception:
        pass
    return applied


def get_pitch_hz(default: int = 0) -> int:
    try:
        return _clamp_pitch(int(float(PITCH_FILE.read_text(encoding="utf-8").strip())))
    except Exception:
        return default


def set_pitch_hz(v: int) -> int:
    applied = _clamp_pitch(int(v))
    try:
        PITCH_FILE.parent.mkdir(parents=True, exist_ok=True)
        PITCH_FILE.write_text(f"{applied}\n", encoding="utf-8")
    except Exception:
        pass
    return applied


def _clamp_pitch(v: int) -> int:
    return max(PITCH_MIN_HZ, min(PITCH_MAX_HZ, v))


def reset() -> None:
    """Back to engine defaults — remove both override files."""
    for f in (SPEED_FILE, PITCH_FILE):
        try:
            f.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass


# ── EdgeTTS string mapping ─────────────────────────────────────────────
# Edge takes signed percent / Hz strings ("-20%", "+4Hz"). When no
# override file exists, hand back the construction-time fallback so the
# provider's configured defaults keep working untouched.

def edge_rate_string(fallback: str) -> str:
    if not SPEED_FILE.exists():
        return fallback
    return f"{round((get_speed() - 1.0) * 100):+d}%"


def edge_pitch_string(fallback: str) -> str:
    if not PITCH_FILE.exists():
        return fallback
    return f"{get_pitch_hz():+d}Hz"
