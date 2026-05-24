"""Output-device profile detection for AEC strategy gating.

Classifies the active PipeWire/PulseAudio sink (or sounddevice device on
Windows/macOS) as headphones, speakers, or unknown. The DTLN neural
residual (L3) only runs on speakers (headphones have no echo path).
Re-detects on hot-plug via a pw-mon subprocess watcher (Linux only;
a no-op on Windows/macOS).

The raw audio-system queries live in `audio.platform_audio` so this
module stays platform-agnostic — Linux uses pw-dump primary + pactl
fallback, Windows/macOS use sounddevice.query_devices.

Spec: docs/superpowers/specs/2026-05-19-echo-cancellation-cascade-design.md §5.4
"""
from __future__ import annotations

import functools
import logging
import os
import platform
import subprocess
import threading
import time
from typing import Callable, Literal

from audio.platform_audio import (
    _linux_pactl_active_sink_block,
    _linux_pwdump_active_sink_desc,
)
from audio.platform_audio import (
    default_output_sink_name as _platform_default_output_sink_name,
)

logger = logging.getLogger("jarvis.audio.output_profile")

Profile = Literal["headphones", "speakers", "unknown"]

_HEADPHONE_PORT_TOKENS = ("headphone", "headset", "hands-free", "handsfree")
_SPEAKER_PORT_TOKENS = ("speaker", "line", "hdmi")
_HEADPHONE_FORM = ("headset", "headphone")

# Broader token sets for free-text classification (node.description + props),
# used by both the pw-dump and pactl backends.
_HEADPHONE_TEXT_TOKENS = (
    "headphone", "headset", "hands-free", "handsfree", "bluez", "bluetooth",
)
_SPEAKER_TEXT_TOKENS = (
    "speaker", "analog", "built-in", "builtin", "hdmi", "line",
)
_SPEAKER_FORM = ("internal", "speaker")

# 30s TTL applied via a coarse time bucket arg to lru_cache.
_TTL_S = 30


def _pwdump_active_sink_desc() -> str:
    """Back-compat shim — delegates to `audio.platform_audio`. Preserved
    as a top-level symbol so tests can monkeypatch it without knowing
    about the platform-dispatch layer."""
    return _linux_pwdump_active_sink_desc()


def _active_sink_block() -> str:
    """Back-compat shim — delegates to `audio.platform_audio`. Preserved
    as a top-level symbol so tests can monkeypatch it without knowing
    about the platform-dispatch layer."""
    return _linux_pactl_active_sink_block()


def _classify_text(s: str) -> Profile:
    """Backend-agnostic classification of a free-text descriptor (sink
    description + device props). Headphones win over speakers on a tie."""
    low = s.lower()
    if any(t in low for t in _HEADPHONE_TEXT_TOKENS):
        return "headphones"
    if any(f in low for f in _HEADPHONE_FORM):
        return "headphones"
    if any(t in low for t in _SPEAKER_TEXT_TOKENS):
        return "speakers"
    if any(f in low for f in _SPEAKER_FORM):
        return "speakers"
    return "unknown"


def _classify_block(block: str) -> Profile:
    low = block.lower()
    # Active port line takes priority (pactl-specific).
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
    # Last resort: shared free-text core.
    return _classify_text(block)


@functools.lru_cache(maxsize=4)
def _classify_cached(_ttl_bucket: int) -> Profile:
    forced = os.environ.get("JARVIS_AEC_FORCE_PROFILE", "").strip().lower()
    if forced in ("headphones", "speakers", "unknown"):
        return forced  # type: ignore[return-value]
    # Primary: pw-dump (PipeWire-native — this box has no pactl).
    # Read via the module-level shim so tests' monkeypatch is honored.
    desc = _pwdump_active_sink_desc()
    if desc.strip():
        return _classify_text(desc)
    # Fallback: pactl (PulseAudio-native Linux hosts).
    block = _active_sink_block()
    if block.strip():
        return _classify_block(block)
    # Last resort on non-Linux: sounddevice default-output name.
    # On Linux this returns the pw-dump descriptor (already tried above)
    # or the pactl block — both have been seen empty here, so no extra
    # work. On Windows/macOS this is the only path that produces text.
    if platform.system() != "Linux":
        name = _platform_default_output_sink_name()
        if name.strip():
            return _classify_text(name)
    return "unknown"


def classify_output_device() -> Profile:
    """Classify the active output device. Cached for ~30s. Honors
    JARVIS_AEC_FORCE_PROFILE override."""
    return _classify_cached(int(time.time() // _TTL_S))


# `classify_output_device.cache_clear` shim for tests.
classify_output_device.cache_clear = _classify_cached.cache_clear  # type: ignore[attr-defined]


def watch_for_changes(callback: Callable[[Profile], None]) -> threading.Thread:
    """Spawn a daemon thread running `pw-mon` and invoke callback with
    the new profile on each node/port change. No-op thread if pw-mon
    is unavailable (e.g. Windows/macOS or a PulseAudio-only Linux host)."""
    def _run() -> None:
        if platform.system() != "Linux":
            logger.info(
                "[output_profile] pw-mon hot-plug watch disabled on %s",
                platform.system(),
            )
            return
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
