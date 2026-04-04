"""Session ID tag translation helpers for the CCR v2 compat layer."""

from __future__ import annotations

from typing import Callable, Optional

_is_cse_shim_enabled: Optional[Callable[[], bool]] = None


def set_cse_shim_gate(gate: Callable[[], bool]) -> None:
    """Register the GrowthBook gate for the cse_ shim."""
    global _is_cse_shim_enabled
    _is_cse_shim_enabled = gate


def to_compat_session_id(id_val: str) -> str:
    """Re-tag a cse_* session ID to session_* for the v1 compat API."""
    if not id_val.startswith("cse_"):
        return id_val
    if _is_cse_shim_enabled and not _is_cse_shim_enabled():
        return id_val
    return "session_" + id_val[len("cse_"):]


def to_infra_session_id(id_val: str) -> str:
    """Re-tag a session_* session ID to cse_* for infrastructure-layer calls."""
    if not id_val.startswith("session_"):
        return id_val
    return "cse_" + id_val[len("session_"):]
