"""Output-device profile detection for AEC strategy gating.

Classifies the active PipeWire/PulseAudio sink as headphones,
speakers, or unknown. The DTLN neural residual (L3) only runs on
speakers (headphones have no echo path). Re-detects on hot-plug via
a pw-mon subprocess watcher.

Spec: docs/superpowers/specs/2026-05-19-echo-cancellation-cascade-design.md §5.4
"""
from __future__ import annotations

import functools
import json
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
    """Return a free-text descriptor of the active output sink via pw-dump
    (PipeWire-native). Empty string if pw-dump is absent or anything fails.

    Resolves the default sink from the Metadata node's `default.audio.sink`
    entry, then returns that sink's `node.description` plus (when available)
    the linked device's form-factor token so the classifier can see it.
    If the default sink is the echo-cancel virtual sink (or no default is
    set), prefer the first real (non-echo-cancel) Audio/Sink node.
    """
    try:
        raw = subprocess.run(
            ["pw-dump"], capture_output=True, text=True, timeout=2
        ).stdout
        nodes = json.loads(raw)
    except Exception:
        return ""
    if not isinstance(nodes, list):
        return ""

    # 1. Default sink name from the Metadata node.
    default_name = ""
    for n in nodes:
        if n.get("type") == "PipeWire:Interface:Metadata":
            for entry in n.get("metadata") or []:
                if entry.get("key") == "default.audio.sink":
                    val = entry.get("value")
                    if isinstance(val, dict):
                        default_name = (val.get("name") or "").strip()
                    elif isinstance(val, str):
                        default_name = val.strip()

    # 2. Collect Audio/Sink nodes keyed by node.name.
    sinks = {}
    for n in nodes:
        props = (n.get("info") or {}).get("props") or {}
        if props.get("media.class") == "Audio/Sink":
            name = props.get("node.name") or ""
            sinks[name] = props

    if not sinks:
        return ""

    def _is_echo_cancel(name: str) -> bool:
        return "echo-cancel" in (name or "").lower()

    # 3. Pick the target sink: the metadata default unless it's the
    #    echo-cancel virtual sink; otherwise the first real device; else
    #    the first sink of any kind.
    target = ""
    if default_name and default_name in sinks and not _is_echo_cancel(default_name):
        target = default_name
    else:
        real = [nm for nm in sinks if not _is_echo_cancel(nm)]
        target = real[0] if real else next(iter(sinks))

    props = sinks.get(target, {})
    parts = [props.get("node.description") or "", props.get("node.name") or ""]

    # 4. Append the linked device's form-factor so the classifier sees it.
    #    pw-dump uses the hyphenated `device.form-factor`; pactl uses the
    #    underscored `device.form_factor` — accept either.
    dev_id = props.get("device.id")
    if dev_id is not None:
        for n in nodes:
            if n.get("id") == dev_id:
                dprops = (n.get("info") or {}).get("props") or {}
                ff = dprops.get("device.form-factor") or dprops.get("device.form_factor")
                if ff:
                    parts.append(str(ff))
                desc = dprops.get("device.description")
                if desc:
                    parts.append(str(desc))
                break

    return " ".join(p for p in parts if p).strip()


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
    desc = _pwdump_active_sink_desc()
    if desc.strip():
        return _classify_text(desc)
    # Fallback: pactl (PulseAudio-native systems).
    block = _active_sink_block()
    if block.strip():
        return _classify_block(block)
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
