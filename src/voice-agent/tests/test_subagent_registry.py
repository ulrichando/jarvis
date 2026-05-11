"""Tests for DelegatedSubagent + SUBAGENT_REGISTRY + build_delegate_tool.

The hybrid sub-agent architecture (one `delegate(role, task)` tool
covering N subagents) replaces the per-spec `transfer_to_X` tool path
for new specialists. Token cost in the supervisor's prompt becomes
constant in N instead of linear.

These tests cover:
  - Registry CRUD + idempotency + enabled gating
  - build_delegate_tool returns None on empty registry, a tool otherwise
  - The tool's description embeds the role list for LLM discovery
  - all_subagents / get_subagent semantics match the HandoffSubagent
    pattern (disabled → None on get; absent from all_*())
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from subagents.registry import (
    DelegatedSubagent,
    register_subagent, get_subagent, all_subagents, clear_subagents,
    SUBAGENT_REGISTRY,
)


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Each test starts with an empty SUBAGENT_REGISTRY and restores
    the production state on teardown so the live agent's registry
    isn't disturbed when these tests run alongside others."""
    saved = dict(SUBAGENT_REGISTRY)
    clear_subagents()
    yield
    clear_subagents()
    SUBAGENT_REGISTRY.update(saved)


def _spec(name: str, *, enabled: bool = True) -> DelegatedSubagent:
    return DelegatedSubagent(
        name=name,
        when_to_use=f"when the user wants {name}",
        instructions=f"You are the {name} subagent. Do {name}.",
        tool_factory=lambda: [],
        enabled=enabled,
    )


# ── Registry CRUD ────────────────────────────────────────────────────


def test_register_and_lookup_by_name():
    register_subagent(_spec("weather"))
    s = get_subagent("weather")
    assert s is not None
    assert s.name == "weather"


def test_register_overwrites_same_name():
    """Re-registering the same name replaces the prior spec —
    matches the HandoffSubagent convention so module reloads work."""
    register_subagent(_spec("weather"))
    register_subagent(DelegatedSubagent(
        name="weather",
        when_to_use="updated description",
        instructions="updated prompt",
        tool_factory=lambda: [],
    ))
    s = get_subagent("weather")
    assert s.when_to_use == "updated description"


def test_register_rejects_empty_name():
    with pytest.raises(ValueError):
        register_subagent(DelegatedSubagent(
            name="",
            when_to_use="x",
            instructions="x",
            tool_factory=lambda: [],
        ))


def test_get_subagent_returns_none_for_missing():
    assert get_subagent("nonexistent") is None


def test_get_subagent_returns_none_for_disabled():
    """Disabled spec is treated as if it doesn't exist on get() —
    callers shouldn't accidentally route to a disabled subagent."""
    register_subagent(_spec("dormant", enabled=False))
    assert get_subagent("dormant") is None


def test_all_subagents_skips_disabled():
    register_subagent(_spec("alpha"))
    register_subagent(_spec("beta", enabled=False))
    register_subagent(_spec("gamma"))
    names = {s.name for s in all_subagents()}
    assert names == {"alpha", "gamma"}


def test_all_subagents_preserves_registration_order():
    register_subagent(_spec("first"))
    register_subagent(_spec("second"))
    register_subagent(_spec("third"))
    assert [s.name for s in all_subagents()] == ["first", "second", "third"]


def test_clear_subagents_resets_registry():
    register_subagent(_spec("temp"))
    assert all_subagents()
    clear_subagents()
    assert all_subagents() == []


# ── build_delegate_tool ──────────────────────────────────────────────


def test_build_delegate_tool_returns_none_when_empty():
    """No registered subagents → no delegate tool → supervisor doesn't
    get a useless one-liner. The build_all_transfer_tools caller
    skips it on None."""
    from subagents.agent import build_delegate_tool
    assert build_delegate_tool() is None


def test_build_delegate_tool_returns_tool_when_populated():
    register_subagent(_spec("research"))
    register_subagent(_spec("calendar"))
    from subagents.agent import build_delegate_tool
    tool = build_delegate_tool()
    assert tool is not None
    # The function_tool decorator preserves the raw callable on `_func`.
    assert getattr(tool, "_func", None) is not None


def test_delegate_tool_description_lists_all_roles():
    """The LLM picks a role by reading the tool's description.
    Every registered subagent must show up there with its
    `when_to_use` line."""
    register_subagent(_spec("research"))
    register_subagent(_spec("calendar"))
    register_subagent(_spec("weather"))
    from subagents.agent import build_delegate_tool
    tool = build_delegate_tool()
    description = tool.info.description
    for name in ("research", "calendar", "weather"):
        assert name in description, f"role {name!r} missing from description"


def test_delegate_tool_description_skips_disabled_roles():
    register_subagent(_spec("active"))
    register_subagent(_spec("dormant", enabled=False))
    from subagents.agent import build_delegate_tool
    tool = build_delegate_tool()
    description = tool.info.description
    assert "active" in description
    assert "dormant" not in description
