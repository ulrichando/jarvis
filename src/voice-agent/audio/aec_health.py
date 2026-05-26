"""Runtime AEC health: measured echo-defense state + the hot-mic gate predicate.

The 2026-05-20 echo->STT regression came from deciding "is echo defense
active" off env flags instead of runtime reality, and from trusting ANY
layer rather than a soak-validated-SUFFICIENT set. This module is the single
source of truth both the mic-gate and the telemetry consume.

Platform note: the actual pw-dump probe lives in `audio.platform_audio`,
which dispatches to the Linux/Windows/macOS backend. On non-Linux the L1
layer is always reported off (no PipeWire-equivalent); the cascade falls
through to L2 (APM AEC) + L3 (DTLN residual), both cross-platform.

Spec: docs/superpowers/specs/2026-05-20-aec-cascade-completion-design.md
"""
from __future__ import annotations

import dataclasses
import functools
import logging
import os
import time

from audio.platform_audio import (
    _linux_default_source as _platform_default_source,
)
from audio.platform_audio import (
    l1_echo_cancel_active as _platform_l1_echo_cancel_active,
)

logger = logging.getLogger("jarvis.audio.aec_health")

_TTL_S = 5  # short — L1 can drop on hot-plug; the gate must see it fast.


def _default_source_name() -> str:
    """Back-compat shim: the PipeWire default.audio.source node name.

    Delegates to `audio.platform_audio._linux_default_source` (the lifted
    Linux probe). Preserved as a top-level symbol so existing tests can
    monkeypatch `aec_health._default_source_name` without knowing about
    the platform-dispatch layer.
    """
    return _platform_default_source()


@functools.lru_cache(maxsize=2)
def _l1_active_cached(_ttl_bucket: int) -> bool:
    # Read the module-level reference so monkeypatched _default_source_name
    # in tests is honored (preserves the pre-refactor test contract).
    return "echo-cancel" in _default_source_name().lower()


def _l1_cache_clear() -> None:
    _l1_active_cached.cache_clear()


def l1_echo_cancel_active() -> bool:
    """True iff the active default capture source is an echo-cancel source
    (i.e. the voice-client is genuinely getting L1-cancelled mic audio).
    `JARVIS_PIPEWIRE_AEC=0` is an operator ceiling that forces it off.

    On non-Linux this always returns False (no OS-level equivalent; the
    cascade falls through to L2 APM + L3 DTLN — both cross-platform).
    The platform check lives in `audio.platform_audio`; this function
    keeps the env-ceiling gate + TTL cache around the underlying probe.
    """
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


# The echo-defense set sufficient to keep the mic hot during TTS without
# garbling STT. "none" = deny (mic-drop). Normally promoted only after a
# passing `bin/jarvis-aec-soak` (spec §5.4).
# 2026-05-20: tried "l1" (hot-mic on tuned-L1) live — REVERTED to "none" because
# JARVIS stopped replying after enabling it (likely echo self-interrupt /
# garbled STT → tuned-L1 insufficient). Barge-in needs L3 (DTLN) — plan Phase B.
# Don't re-promote to "l1" without it.
# 2026-05-25: promoted to "l1_l3" — DTLN L3 now shipped (models +
# ai-edge-litert), smoke p95 1.6 ms / budget 15 ms. Tuned-L1 active too
# (NS/AGC OFF, extended_filter on — no double-DSP). Watch for echo
# self-interrupt regressions and demote to "none" if seen.
_HOT_MIC_SET = "l1_l3"   # one of: "none", "l1", "l1_l3"


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


# Re-exported for callers that want the bare platform-dispatched check
# without the env-ceiling / TTL wrap (currently unused; kept for symmetry).
__all__ = [
    "EchoDefense",
    "current_echo_defense",
    "l1_echo_cancel_active",
    "sufficient_for_hot_mic",
    "_default_source_name",
    "_l1_active_cached",
    "_l1_cache_clear",
    "_platform_l1_echo_cancel_active",
]
