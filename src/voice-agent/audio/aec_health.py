"""Runtime AEC health: measured echo-defense state + the hot-mic gate predicate.

The 2026-05-20 echo->STT regression came from deciding "is echo defense
active" off env flags instead of runtime reality, and from trusting ANY
layer rather than a soak-validated-SUFFICIENT set. This module is the single
source of truth both the mic-gate and the telemetry consume.

This box is PipeWire-native (pw-dump/wpctl; NO pactl). Spec:
docs/superpowers/specs/2026-05-20-aec-cascade-completion-design.md
"""
from __future__ import annotations

import dataclasses
import functools
import json
import logging
import os
import subprocess
import time

logger = logging.getLogger("jarvis.audio.aec_health")

_TTL_S = 5  # short — L1 can drop on hot-plug; the gate must see it fast.


def _default_source_name() -> str:
    """The PipeWire default.audio.source node name via pw-dump. Empty on any
    failure. Split out for test mocking."""
    try:
        raw = subprocess.run(
            ["pw-dump"], capture_output=True, text=True, timeout=2
        ).stdout
        nodes = json.loads(raw)
    except Exception:
        return ""
    if not isinstance(nodes, list):
        return ""
    for n in nodes:
        if isinstance(n, dict) and n.get("type") == "PipeWire:Interface:Metadata":
            for entry in n.get("metadata") or []:
                if entry.get("key") == "default.audio.source":
                    val = entry.get("value")
                    if isinstance(val, dict):
                        return (val.get("name") or "").strip()
                    if isinstance(val, str):
                        return val.strip()
    return ""


@functools.lru_cache(maxsize=2)
def _l1_active_cached(_ttl_bucket: int) -> bool:
    return "echo-cancel" in _default_source_name().lower()


def _l1_cache_clear() -> None:
    _l1_active_cached.cache_clear()


def l1_echo_cancel_active() -> bool:
    """True iff the active default capture source is an echo-cancel source
    (i.e. the voice-client is genuinely getting L1-cancelled mic audio).
    `JARVIS_PIPEWIRE_AEC=0` is an operator ceiling that forces it off."""
    if os.environ.get("JARVIS_PIPEWIRE_AEC", "1") != "1":
        return False
    return _l1_active_cached(int(time.time() // _TTL_S))


@dataclasses.dataclass(frozen=True)
class EchoDefense:
    l1: bool
    l2_aec: bool
    l3: bool


def current_echo_defense(*, apm_aec: bool, dtln_healthy: bool) -> EchoDefense:
    """Snapshot the MEASURED echo-defense layers. Fail-closed: any probe
    error -> that layer reads False (never raises into the audio callback)."""
    try:
        l1 = l1_echo_cancel_active()
    except Exception as e:
        logger.warning(f"[aec_health] l1 probe failed ({e}); treating as off")
        l1 = False
    return EchoDefense(l1=l1, l2_aec=bool(apm_aec), l3=bool(dtln_healthy))


# The echo-defense set proven (by `bin/jarvis-aec-soak`) sufficient to keep
# the mic hot during TTS without garbling STT. "none" until a soak promotes
# it. PROMOTE by editing this after a passing soak (spec §5.4). NEVER widen
# it on a hunch — the 2026-05-20 regression was exactly an unvalidated hot mic.
_HOT_MIC_SET = "none"   # one of: "none", "l1", "l1_l3"


def sufficient_for_hot_mic(d: EchoDefense, profile: str) -> bool:
    """True iff the validated-sufficient echo-defense set is measured active.
    Deny-by-default on speakers; headphones never have an echo path."""
    if profile == "headphones":
        return True
    if _HOT_MIC_SET == "l1":
        return d.l1
    if _HOT_MIC_SET == "l1_l3":
        return d.l1 and d.l3
    return False  # "none" -> deny -> mic-drop during speak
