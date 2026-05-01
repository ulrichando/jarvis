"""Import-smoke tests for voice-agent modules.

Catches the failure mode where a module-level statement breaks
(syntax error, missing dependency, monkey-patch target moved in a
livekit-agents bump, etc.) and the agent silently fails to start a
session — historically these only surface from the live systemd unit's
log, hours after the broken commit.

Each module here is imported by the agent at startup; if any one of
them raises, the agent dies before joining a room. Cheap insurance.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.mark.parametrize("mod", [
    "jarvis_agent",
    "deepseek_roundtrip",
    "tool_name_sanitizer",
    "acoustic_tap",
    "turn_router",
    "turn_telemetry",
    "turn_graph",
    "dispatching_llm",
    "dispatching_tts",
    "edge_tts_plugin",
    "jarvis_computer_use",
    "jarvis_browser",
    "jarvis_browser_ext",
    "specialists",
    "specialists.registry",
    "specialists.agent",
    "specialists.desktop",
    "specialists.planner",
    "specialists.browser",
    "specialists.summarize",
])
def test_module_imports_clean(mod):
    """Each must import without raising. Module-level patches like
    `deepseek_roundtrip.install()` (called from jarvis_agent on import)
    are part of the test surface — if they raise on import, the agent
    won't start."""
    __import__(mod)


def test_critical_patches_install_idempotently():
    """install() functions must be re-callable. The agent's entrypoint
    runs in a forked worker subprocess and re-imports modules; the
    second install() must not double-wrap or otherwise misbehave."""
    import deepseek_roundtrip
    import tool_name_sanitizer

    deepseek_roundtrip.install()
    deepseek_roundtrip.install()  # second call must be no-op
    tool_name_sanitizer.install()
    tool_name_sanitizer.install()

    from livekit.agents.inference import llm as inf_llm
    from livekit.agents.llm._provider_format import openai as oai_fmt

    assert getattr(inf_llm.LLMStream, "_jarvis_deepseek_patched", False)
    assert getattr(inf_llm.LLMStream, "_jarvis_sanitizer_patched", False)
    assert getattr(oai_fmt, "_jarvis_deepseek_patched", False)


def test_jarvis_agent_exposes_required_tools():
    """The supervisor + specialists pull these by name from
    jarvis_agent. A rename / accidental delete here breaks specialist
    construction at session start."""
    import jarvis_agent
    required = (
        "bash", "launch_app", "run_jarvis_cli",
        "type_in_terminal", "media_control", "browser_task",
    )
    for name in required:
        assert hasattr(jarvis_agent, name), f"jarvis_agent.{name} is missing"


def test_subagent_registry_includes_summarize():
    """The summarize subagent should auto-register when specialists/
    is imported. Verifies the SubagentSpec / delegate path is wired
    end-to-end at production startup."""
    from specialists.registry import SUBAGENT_REGISTRY, clear_subagents
    from specialists.summarize import register_summarize

    # Other tests (test_subagent_registry) clear the SUBAGENT_REGISTRY
    # in their fixtures and may leave it empty when run before this.
    # Re-register explicitly so this test is order-independent.
    clear_subagents()
    register_summarize()

    assert "summarize" in SUBAGENT_REGISTRY
    assert SUBAGENT_REGISTRY["summarize"].enabled is True


def test_build_all_transfer_tools_includes_delegate():
    """build_all_transfer_tools should return BOTH the per-spec
    transfer_to_X tools AND the single delegate(role, task) tool
    when there are SubagentSpecs registered."""
    from specialists.registry import (
        clear, clear_subagents, _REGISTRY, SUBAGENT_REGISTRY,
    )
    from specialists.planner import register_planner
    from specialists.desktop import register_desktop
    from specialists.browser import register_browser
    from specialists.summarize import register_summarize
    from specialists.agent import build_all_transfer_tools

    clear()
    clear_subagents()
    register_planner()
    register_desktop()
    register_browser()
    register_summarize()

    tools = build_all_transfer_tools()
    tool_names = {getattr(t, "name", None) or t.info.name for t in tools}
    # Three legacy transfer_to_X tools + one delegate tool = 4
    assert "transfer_to_planner" in tool_names
    assert "transfer_to_desktop" in tool_names
    assert "transfer_to_browser" in tool_names
    assert "delegate" in tool_names


def test_specialist_registry_discovers_three_enabled_specs():
    """Adding a specialist is one file + one register() call; this
    sanity check ensures the registry actually picks up the three
    we expect (planner, desktop, browser) and didn't lose one to a
    typo or rename.

    Other tests in the suite (test_specialist_registry,
    test_browser_specialist) call registry.clear() in their setup,
    so we re-register the production specs explicitly here rather
    than rely on import order."""
    from specialists.registry import _REGISTRY, clear
    from specialists.planner import register_planner
    from specialists.desktop import register_desktop
    from specialists.browser import register_browser

    clear()
    register_planner()
    register_desktop()
    register_browser()

    enabled = {name for name, spec in _REGISTRY.items() if spec.enabled}
    assert {"planner", "desktop", "browser"}.issubset(enabled), (
        f"expected planner/desktop/browser enabled, got {enabled}"
    )
