"""Cross-process AEC state bridge.

AEC runs in the voice-client process; per-turn telemetry is written by
the agent process. This module is the bridge: the voice-client writes
a small JSON state file (atomic), the agent reads it at turn-write time
with a staleness guard.

Mirrors JARVIS's existing flat-file IPC convention (~/.jarvis/cli-model,
voice-model, tool-busy flags). Spec: 2026-05-19 §5.5.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("jarvis.audio.aec_state")

DEFAULT_PATH = Path.home() / ".jarvis" / "aec-state.json"

_NULL_STATE = {
    "output_profile": None,
    "aec_layer1_active": None,
    "aec_layer2_aec_active": None,
    "aec_layer3_active": None,
    "apm_delay_ms_p50": None,
    "dtln_latency_ms_p95": None,
}


def write_aec_state(
    path: Path = DEFAULT_PATH, *,
    output_profile: str,
    l1_active: bool,
    l2_aec_active: bool,
    l3_active: bool,
    apm_delay_ms_p50: Optional[int],
    dtln_latency_ms_p95: Optional[float],
) -> None:
    """Atomically write the current AEC state (voice-client side)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "output_profile": output_profile,
        "l1_active": bool(l1_active),
        "l2_aec_active": bool(l2_aec_active),
        "l3_active": bool(l3_active),
        "apm_delay_ms_p50": apm_delay_ms_p50,
        "dtln_latency_ms_p95": dtln_latency_ms_p95,
        "updated_utc": datetime.datetime.now(datetime.timezone.utc)
            .isoformat().replace("+00:00", "Z"),
    }
    try:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)   # atomic
    except Exception as e:
        logger.warning(f"[aec_state] write failed: {e}")


def read_aec_state(path: Path = DEFAULT_PATH, *, max_age_s: int = 60) -> dict:
    """Read the AEC state (agent side), mapping JSON keys to the
    turns-table column names. Returns all-None if the file is missing,
    malformed, or older than max_age_s (voice-client may have died)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(_NULL_STATE)
    ts = raw.get("updated_utc", "")
    try:
        t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        age = (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds()
        if age > max_age_s:
            return dict(_NULL_STATE)
    except Exception:
        return dict(_NULL_STATE)
    return {
        "output_profile": raw.get("output_profile"),
        "aec_layer1_active": int(bool(raw.get("l1_active"))) if "l1_active" in raw else None,
        "aec_layer2_aec_active": int(bool(raw.get("l2_aec_active"))) if "l2_aec_active" in raw else None,
        "aec_layer3_active": int(bool(raw.get("l3_active"))) if "l3_active" in raw else None,
        "apm_delay_ms_p50": raw.get("apm_delay_ms_p50"),
        "dtln_latency_ms_p95": raw.get("dtln_latency_ms_p95"),
    }
