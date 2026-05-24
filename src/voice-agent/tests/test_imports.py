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
    "pipeline.prosody",
    "pipeline.turn_router",
    "pipeline.turn_telemetry",
    "pipeline.turn_graph",
    "pipeline.dispatching_llm",
    "pipeline.dispatching_tts",
    "providers.edge_tts",
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


