"""voice_style tool — let the supervisor actually change how it sounds.

Live gap 2026-07-02: user said "count from one to ten slowly" / "speak
slower" and JARVIS replied "Got it — I'll speak slower" with NO
mechanism behind the claim. This tool writes the runtime voice-style
store (pipeline/voice_style.py); both TTS engines read it per
synthesis, so the change is audible from the very next sentence.

Speed applies to BOTH engines (Kokoro primary + EdgeTTS fallback).
Pitch applies only when EdgeTTS is speaking — Kokoro has no pitch
parameter; a "deeper/higher voice" request while on Kokoro is honestly
reported as speed-only + a suggestion to switch the TTS voice.
"""
from __future__ import annotations

import json

from .registry import registry, tool_error

from pipeline import voice_style as vs

# Word → action. Relative words step from the CURRENT value so repeated
# "slower" keeps slowing down; absolute presets jump.
_RELATIVE = {"slower": -vs.SPEED_STEP, "faster": +vs.SPEED_STEP}
_PRESETS = {"slow": 0.8, "normal": 1.0, "fast": 1.2}


def _state(note: str = "") -> str:
    out = {
        "speed": vs.get_speed(),
        "pitch_hz": vs.get_pitch_hz(),
        "speed_range": [vs.SPEED_MIN, vs.SPEED_MAX],
        "applies": "from the next sentence — no restart",
    }
    if note:
        out["note"] = note
    return json.dumps(out, ensure_ascii=False)


def _handle_voice_style(args: dict) -> str:
    action = (args.get("action") or "get").strip().lower()

    if action == "get":
        return _state()

    if action == "reset":
        vs.reset()
        return _state("voice style reset to defaults")

    if action == "pronounce":
        from pipeline import pronunciation
        word = (args.get("word") or "").strip()
        if not word:
            return tool_error("voice_style: pronounce needs a word")
        sounds_like = (args.get("sounds_like") or "").strip()
        if not sounds_like:
            # empty sounds_like = forget the entry
            existed = pronunciation.forget_word(word)
            return json.dumps({
                "pronunciations": pronunciation.entries(),
                "note": f"forgot {word!r}" if existed else f"{word!r} had no entry",
            }, ensure_ascii=False)
        pronunciation.set_word(word, sounds_like)
        return json.dumps({
            "pronunciations": pronunciation.entries(),
            "note": f"{word!r} will be pronounced like {sounds_like!r} "
                    "from the next sentence",
        }, ensure_ascii=False)

    if action != "set":
        return tool_error(
            f"voice_style: unknown action {action!r} (set/get/reset/pronounce)"
        )

    note_bits = []
    speed = args.get("speed")
    if speed is not None:
        if isinstance(speed, str):
            word = speed.strip().lower()
            if word in _RELATIVE:
                vs.set_speed(vs.get_speed() + _RELATIVE[word])
            elif word in _PRESETS:
                vs.set_speed(_PRESETS[word])
            else:
                try:
                    vs.set_speed(float(word))
                except ValueError:
                    return tool_error(
                        f"voice_style: speed {speed!r} — use a number "
                        f"{vs.SPEED_MIN}-{vs.SPEED_MAX} or "
                        "slower/faster/slow/normal/fast"
                    )
        else:
            try:
                vs.set_speed(float(speed))
            except (TypeError, ValueError):
                return tool_error(f"voice_style: speed {speed!r} is not a number")
        note_bits.append(f"speed set to {vs.get_speed()}")

    pitch = args.get("pitch_hz")
    if pitch is not None:
        try:
            vs.set_pitch_hz(int(pitch))
        except (TypeError, ValueError):
            return tool_error(f"voice_style: pitch_hz {pitch!r} is not an integer")
        note_bits.append(
            f"pitch offset {vs.get_pitch_hz():+d}Hz (shapes the Edge fallback "
            "voice only — the primary Kokoro engine has no pitch knob)"
        )

    if not note_bits:
        return tool_error("voice_style: set needs speed and/or pitch_hz")
    return _state("; ".join(note_bits))


_SCHEMA = {
    "name": "voice_style",
    "description": (
        "Adjust how you SOUND — your real speaking speed (and pitch on the "
        "fallback voice). Call this whenever the user asks you to speak "
        "slower/faster, slow down, calm your delivery, talk at normal speed, "
        "or complains you talk too fast — do NOT just claim you'll speak "
        "slower; this tool is the actual knob, and it takes effect from your "
        "next sentence.\n\n"
        "speed: a number (0.5–2.0, 1.0 = normal) or a word — 'slower'/'faster' "
        "step by 0.1 from the current value (repeatable), 'slow'/'normal'/"
        "'fast' jump to presets. For 'count slowly'-style requests, also "
        "write the reply with pauses (ellipses between items) in addition to "
        "lowering speed.\n"
        "pitch_hz: optional integer offset (-50..50); only audible when the "
        "Edge fallback voice is speaking — the primary on-device voice has "
        "no pitch control (offer a TTS voice switch for deeper/higher voice "
        "requests instead).\n\n"
        "action='pronounce' fixes ENUNCIATION of a specific word the user "
        "says you mispronounce ('you say Pretva wrong', 'pronounce my name "
        "OOL-rik'): pass word + sounds_like. sounds_like is either a plain "
        "respelling ('OOL-rik' — works on every voice) or IPA phonemes "
        "wrapped in slashes ('/pɹˈɛtvə/' — most precise). Empty sounds_like "
        "forgets the entry. Applies from the next sentence, persists forever."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["set", "get", "reset", "pronounce"],
                "description": "set = apply speed/pitch, get = report current, "
                               "reset = defaults, pronounce = per-word "
                               "enunciation fix.",
            },
            "speed": {
                "description": "Number 0.5–2.0, or slower/faster/slow/normal/fast.",
            },
            "pitch_hz": {
                "type": "integer",
                "description": "Pitch offset in Hz (-50..50), Edge fallback only.",
            },
            "word": {
                "type": "string",
                "description": "pronounce: the word being mispronounced.",
            },
            "sounds_like": {
                "type": "string",
                "description": "pronounce: respelling ('OOL-rik') or "
                               "/IPA phonemes/ ('/pɹˈɛtvə/'). Empty = forget.",
            },
        },
        "required": ["action"],
    },
}

registry.register(
    name="voice_style",
    schema=_SCHEMA,
    handler=_handle_voice_style,
    toolset="voice_style",
    check_fn=None,   # always available — writes a local file
    is_async=False,
    emoji="🎚",
)
