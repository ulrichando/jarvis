"""Tests for the SpecialistSpec registry pattern.

Pure Python — no LiveKit imports required. The generic specialist Agent
construction is covered separately by integration tests; this file only
exercises the data structure and lookup semantics.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from specialists.registry import (
    SpecialistSpec, register, all_specs, get, clear,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    """Ensure each test starts with an empty registry. The package's
    __init__ auto-registers `desktop`, so we clear before AND after."""
    clear()
    yield
    clear()


def _spec(name: str = "test", **kwargs) -> SpecialistSpec:
    """Build a minimal SpecialistSpec for tests."""
    defaults = dict(
        name=name,
        transfer_tool=f"transfer_to_{name}",
        when_to_use="testing only",
        instructions="be a test specialist",
        tool_factory=lambda: [],
    )
    defaults.update(kwargs)
    return SpecialistSpec(**defaults)


def test_register_and_lookup():
    register(_spec("alpha"))
    s = get("alpha")
    assert s is not None
    assert s.name == "alpha"
    assert s.transfer_tool == "transfer_to_alpha"


def test_get_returns_none_for_missing():
    assert get("nonexistent") is None


def test_get_returns_none_for_disabled():
    """A disabled spec should not surface via get() — callers shouldn't
    accidentally hand off to a turned-off specialist."""
    register(_spec("turned_off", enabled=False))
    assert get("turned_off") is None


def test_all_specs_skips_disabled():
    register(_spec("on1", enabled=True))
    register(_spec("off1", enabled=False))
    register(_spec("on2", enabled=True))

    names = [s.name for s in all_specs()]
    assert names == ["on1", "on2"]


def test_all_specs_empty_when_no_registrations():
    assert all_specs() == []


def test_register_overwrites_same_name():
    """Re-registering by name overwrites — useful for hot-reload AND
    for downstream code that wants to monkey-patch a built-in spec."""
    register(_spec("dup", instructions="first version"))
    register(_spec("dup", instructions="second version"))
    s = get("dup")
    assert s is not None
    assert s.instructions == "second version"


def test_register_rejects_empty_name():
    with pytest.raises(ValueError):
        register(_spec(name=""))


def test_register_rejects_empty_transfer_tool():
    with pytest.raises(ValueError):
        register(_spec("x", transfer_tool=""))


def test_clear_resets_registry():
    register(_spec("x"))
    register(_spec("y"))
    assert len(all_specs()) == 2
    clear()
    assert all_specs() == []


def test_default_ack_phrase_and_history_window():
    s = _spec("ack_test")
    assert s.ack_phrase == "Right away, sir."
    assert s.max_history_items == 12


def test_tool_factory_is_lazy():
    """Tool factory is only called when the spec is actually used —
    never at registration time. This keeps livekit imports out of the
    registry module's critical path."""
    calls = {"n": 0}

    def make_tools():
        calls["n"] += 1
        return ["a", "b"]

    register(_spec("lazy", tool_factory=make_tools))
    # No call yet
    assert calls["n"] == 0

    # Look up — still no call
    s = get("lazy")
    assert s is not None
    assert calls["n"] == 0

    # Only when the consumer invokes the factory
    assert s.tool_factory() == ["a", "b"]
    assert calls["n"] == 1


def test_package_autoregisters_desktop():
    """Phase 4: desktop is enabled in the registry. The legacy
    JarvisAgent.transfer_to_desktop method has been retired; the
    registry's RegistrySpecialist now owns the handoff for both
    desktop and planner."""
    from specialists import desktop as desktop_mod
    desktop_mod.register_desktop()

    spec = get("desktop")
    assert spec is not None
    assert spec.transfer_tool == "transfer_to_desktop"
    assert spec.enabled is True
    assert "DESKTOP" not in spec.instructions  # heading style differs; just sanity
    assert "task_done" in spec.instructions   # the back-handoff convention is documented


# ── Phase 3: planner specialist ────────────────────────────────────────


def test_planner_spec_is_registered_and_enabled():
    """Phase 3 added the planner specialist via the registry pattern.
    Unlike desktop (disabled, legacy method owns it), planner is the
    first registry-driven specialist that ships enabled=True."""
    from specialists import planner as planner_mod
    planner_mod.register_planner()

    spec = get("planner")
    assert spec is not None
    assert spec.transfer_tool == "transfer_to_planner"
    assert "run_jarvis_cli" in spec.instructions  # references the right primary tool
    assert spec.enabled is True


def test_planner_appears_in_all_specs():
    from specialists import planner as planner_mod
    planner_mod.register_planner()

    names = [s.name for s in all_specs()]
    assert "planner" in names
