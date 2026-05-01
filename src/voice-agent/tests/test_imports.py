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
