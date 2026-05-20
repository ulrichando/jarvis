"""Output-device profile detection for AEC strategy gating.

Classifies the active PipeWire/PulseAudio sink as headphones,
speakers, or unknown. The DTLN neural residual (L3) only runs on
speakers (headphones have no echo path). Re-detects on hot-plug via
a pw-mon subprocess watcher.

Spec: docs/superpowers/specs/2026-05-19-echo-cancellation-cascade-design.md §5.4
"""
from __future__ import annotations

import functools
import logging
import os
import subprocess
import threading
import time
from typing import Callable, Literal

logger = logging.getLogger("jarvis.audio.output_profile")

Profile = Literal["headphones", "speakers", "unknown"]

_HEADPHONE_PORT_TOKENS = ("headphone", "headset", "hands-free", "handsfree")
_SPEAKER_PORT_TOKENS = ("speaker", "line", "hdmi")
_HEADPHONE_FORM = ("headset", "headphone")

# 30s TTL applied via a coarse time bucket arg to lru_cache.
_TTL_S = 30


def _active_sink_block() -> str:
    """Return the full `pactl list sinks` block for the default sink.
    Empty string if pactl is unavailable. Split out for test mocking."""
    try:
        default = subprocess.run(
            ["pactl", "get-default-sink"], capture_output=True, text=True, timeout=2
        ).stdout.strip()
        full = subprocess.run(
            ["pactl", "list", "sinks"], capture_output=True, text=True, timeout=2
        ).stdout
    except Exception:
        return ""
    # Slice out the block for the default-named sink.
    blocks = full.split("Sink #")
    for b in blocks:
        if default and default in b:
            return b
    return blocks[1] if len(blocks) > 1 else ""


def _classify_block(block: str) -> Profile:
    low = block.lower()
    # Active port line takes priority.
    for line in low.splitlines():
        if line.strip().startswith("active port:"):
            if any(t in line for t in _HEADPHONE_PORT_TOKENS):
                return "headphones"
            if any(t in line for t in _SPEAKER_PORT_TOKENS):
                return "speakers"
    # Fall back to form factor.
    if any(f'form_factor = "{f}"' in low for f in _HEADPHONE_FORM):
        return "headphones"
    if "form_factor" in low or "speaker" in low or "analog-output" in low:
        return "speakers"
    return "unknown"


@functools.lru_cache(maxsize=4)
def _classify_cached(_ttl_bucket: int) -> Profile:
    forced = os.environ.get("JARVIS_AEC_FORCE_PROFILE", "").strip().lower()
    if forced in ("headphones", "speakers", "unknown"):
        return forced  # type: ignore[return-value]
    block = _active_sink_block()
    if not block.strip():
        return "unknown"
    return _classify_block(block)


def classify_output_device() -> Profile:
    """Classify the active output device. Cached for ~30s. Honors
    JARVIS_AEC_FORCE_PROFILE override."""
    return _classify_cached(int(time.time() // _TTL_S))


# `classify_output_device.cache_clear` shim for tests.
classify_output_device.cache_clear = _classify_cached.cache_clear  # type: ignore[attr-defined]


def watch_for_changes(callback: Callable[[Profile], None]) -> threading.Thread:
    """Spawn a daemon thread running `pw-mon` and invoke callback with
    the new profile on each node/port change. No-op thread if pw-mon
    is unavailable."""
    def _run() -> None:
        try:
            proc = subprocess.Popen(
                ["pw-mon"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
            )
        except FileNotFoundError:
            logger.warning("[output_profile] pw-mon unavailable; hot-plug detection off")
            return
        last: Profile = classify_output_device()
        for line in proc.stdout:  # type: ignore[union-attr]
            if any(k in line for k in ("changed", "Port", "Node")):
                classify_output_device.cache_clear()  # type: ignore[attr-defined]
                cur = classify_output_device()
                if cur != last:
                    last = cur
                    try:
                        callback(cur)
                    except Exception as e:
                        logger.warning(f"[output_profile] callback raised: {e}")

    t = threading.Thread(target=_run, name="aec-profile-watch", daemon=True)
    t.start()
    return t
