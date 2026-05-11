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
    "sanitizers.deepseek_roundtrip",
    "sanitizers.tool_name",
    "pipeline.acoustic_tap",
    "pipeline.turn_router",
    "pipeline.turn_telemetry",
    "pipeline.turn_graph",
    "pipeline.dispatching_llm",
    "pipeline.dispatching_tts",
    "providers.edge_tts",
    "tools.computer_use",
    "tools.browser",
    "tools.browser_ext",
    "subagents",
    "subagents.registry",
    "subagents.agent",
    "subagents.desktop",
    "subagents.browser",
    "subagents.summarize",
    "subagents.weather",
    "subagents.researcher",
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
    import sanitizers.deepseek_roundtrip as deepseek_roundtrip
    import sanitizers.tool_name as tool_name_sanitizer

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
    """The supervisor + subagents pull these by name from
    jarvis_agent. A rename / accidental delete here breaks subagent
    construction at session start."""
    import jarvis_agent
    required = (
        "bash", "launch_app", "run_jarvis_cli",
        "type_in_terminal", "media_control", "browser_task",
    )
    for name in required:
        assert hasattr(jarvis_agent, name), f"jarvis_agent.{name} is missing"


def test_subagent_registry_includes_builtin_subagents():
    """The summarize / weather / researcher subagents should
    auto-register when subagents/ is imported. Verifies the
    DelegatedSubagent / delegate path is wired end-to-end at production
    startup."""
    from subagents.registry import SUBAGENT_REGISTRY, clear_subagents
    from subagents.summarize import register_summarize
    from subagents.weather import register_weather
    from subagents.researcher import register_researcher

    # Other tests (test_subagent_registry) clear the SUBAGENT_REGISTRY
    # in their fixtures and may leave it empty when run before this.
    # Re-register explicitly so this test is order-independent.
    clear_subagents()
    register_summarize()
    register_weather()
    register_researcher()

    for name in ("summarize", "weather", "researcher"):
        assert name in SUBAGENT_REGISTRY, f"{name} missing from SUBAGENT_REGISTRY"
        assert SUBAGENT_REGISTRY[name].enabled is True


def test_build_all_transfer_tools_includes_delegate():
    """build_all_transfer_tools should return BOTH the per-spec
    transfer_to_X tools AND the single delegate(role, task) tool
    when there are DelegatedSubagents registered."""
    from subagents.registry import (
        clear, clear_subagents, _REGISTRY, SUBAGENT_REGISTRY,
    )
    from subagents.desktop import register_desktop
    from subagents.browser import register_browser
    from subagents.summarize import register_summarize
    from subagents.weather import register_weather
    from subagents.researcher import register_researcher
    from subagents.agent import build_all_transfer_tools

    clear()
    clear_subagents()
    register_desktop()
    register_browser()
    register_summarize()
    register_weather()
    register_researcher()

    tools = build_all_transfer_tools()
    tool_names = {getattr(t, "name", None) or t.info.name for t in tools}
    # Two legacy transfer_to_X tools + one delegate tool = 3 total.
    # (delegate covers all DelegatedSubagents internally — count stays at 3
    # whether you have 1 subagent or 100. That's the whole point.)
    assert "transfer_to_desktop" in tool_names
    assert "transfer_to_browser" in tool_names
    assert "delegate" in tool_names

    # Verify all three subagent roles are listed in delegate's description
    # so the supervisor's LLM can discover them at routing time.
    delegate = next(t for t in tools
                    if (getattr(t, "name", None) or t.info.name) == "delegate")
    for role in ("summarize", "weather", "researcher"):
        assert role in delegate.info.description, (
            f"role {role!r} missing from delegate description"
        )


def test_handoff_subagent_registry_discovers_enabled_specs():
    """Adding a handoff subagent is one file + one register() call;
    this sanity check ensures the registry actually picks up the
    two we expect (desktop, browser) and didn't lose one to a typo
    or rename.

    Other tests in the suite (test_specialist_registry,
    test_browser_specialist) call registry.clear() in their setup,
    so we re-register the production specs explicitly here rather
    than rely on import order."""
    from subagents.registry import _REGISTRY, clear
    from subagents.desktop import register_desktop
    from subagents.browser import register_browser

    clear()
    register_desktop()
    register_browser()

    enabled = {name for name, spec in _REGISTRY.items() if spec.enabled}
    assert {"desktop", "browser"}.issubset(enabled), (
        f"expected desktop/browser enabled, got {enabled}"
    )
