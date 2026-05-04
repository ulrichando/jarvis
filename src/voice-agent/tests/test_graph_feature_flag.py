"""When JARVIS_LANGGRAPH_SUPERVISOR=1 is set, entrypoint() must build
the JarvisSupervisorGraphLLM and pass it to AgentSession in place of
the dispatcher. Test the construction path without standing up a real
LiveKit session."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")


def test_feature_flag_off_uses_legacy_supervisor():
    """Default behaviour: env var unset → existing dispatcher path.
    The flag is opt-in for the soak window."""
    import jarvis_agent
    with patch.dict(os.environ, {"JARVIS_LANGGRAPH_SUPERVISOR": "0"}):
        # Calling the helper that picks the supervisor LLM:
        chosen = jarvis_agent._pick_supervisor_llm(
            specialist_tools=[],
            legacy_llm="LEGACY-SENTINEL",
        )
    assert chosen == "LEGACY-SENTINEL"


def test_feature_flag_on_uses_graph_supervisor():
    import jarvis_agent
    from supervisor_graph.llm_adapter import JarvisSupervisorGraphLLM
    with patch.dict(os.environ, {"JARVIS_LANGGRAPH_SUPERVISOR": "1"}):
        chosen = jarvis_agent._pick_supervisor_llm(
            specialist_tools=[],
            legacy_llm="LEGACY-SENTINEL",
        )
    assert isinstance(chosen, JarvisSupervisorGraphLLM)


def test_feature_flag_on_silences_dispatcher_listeners():
    """When the graph flag is on, the legacy dispatcher's per-turn
    LLM-swap listeners must not fire — those would silently overwrite
    the graph supervisor's LLM after the first BANTER turn."""
    import os
    import re
    from pathlib import Path

    src = Path(
        "/home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py"
    ).read_text()
    # The fix appears in entrypoint(): when the env flag is on,
    # _dispatch_llm and friends are nuked so the existing
    # `if _dispatch_llm is not None:` listeners short-circuit.
    pattern = (
        r"JARVIS_LANGGRAPH_SUPERVISOR.*?==.*?[\"']1[\"']"
        r".*?_dispatch_llm\s*=\s*None"
    )
    assert re.search(pattern, src, re.DOTALL), (
        "expected entrypoint() to clear _dispatch_llm when "
        "JARVIS_LANGGRAPH_SUPERVISOR=1"
    )
