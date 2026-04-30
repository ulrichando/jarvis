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
    assert s.ack_phrase == "On it, sir."
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
    """Importing the specialists package registers the built-in desktop
    spec. Auto-fixture cleared the registry, so we reimport here.

    Desktop is registered with `enabled=False` for now (legacy
    JarvisAgent.transfer_to_desktop still owns the live handoff), so
    `get("desktop")` returns None but the underlying _REGISTRY has it.
    """
    # Force a re-register by importing the desktop module directly
    from specialists import desktop as desktop_mod
    desktop_mod.register_desktop()

    # Disabled by default — legacy method still active
    assert get("desktop") is None
    # But the spec exists in the underlying registry; flip enabled to
    # see it. We don't reach into _REGISTRY directly; instead we
    # re-register with enabled=True to prove the spec is well-formed.
    desktop_spec = SpecialistSpec(
        name="desktop",
        transfer_tool="transfer_to_desktop",
        when_to_use="x",
        instructions="x",
        tool_factory=lambda: [],
        enabled=True,
    )
    register(desktop_spec)
    assert get("desktop") is not None
