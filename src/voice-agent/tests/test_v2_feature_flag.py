"""V2 feature flag — JARVIS_BLACKBOARD interacts cleanly with v1 flag."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")


def test_v2_flag_off_with_v1_off_uses_legacy():
    import jarvis_agent
    with patch.dict(os.environ, {
        "JARVIS_LANGGRAPH_SUPERVISOR": "0",
        "JARVIS_BLACKBOARD": "0",
    }):
        chosen = jarvis_agent._pick_supervisor_llm(
            specialist_tools=[],
            legacy_llm="LEGACY-SENTINEL",
        )
    assert chosen == "LEGACY-SENTINEL"


def test_v2_flag_off_with_v1_on_uses_v1_supervisor():
    import jarvis_agent
    from supervisor_graph.llm_adapter import JarvisSupervisorGraphLLM
    with patch.dict(os.environ, {
        "JARVIS_LANGGRAPH_SUPERVISOR": "1",
        "JARVIS_BLACKBOARD": "0",
    }):
        chosen = jarvis_agent._pick_supervisor_llm(
            specialist_tools=[],
            legacy_llm="LEGACY-SENTINEL",
        )
    assert isinstance(chosen, JarvisSupervisorGraphLLM)


def test_v2_flag_on_with_v1_off_still_uses_legacy():
    """V2 layers on top of v1; if v1 is off, v2 has nothing to wrap.
    Falls back to legacy."""
    import jarvis_agent
    with patch.dict(os.environ, {
        "JARVIS_LANGGRAPH_SUPERVISOR": "0",
        "JARVIS_BLACKBOARD": "1",
    }):
        chosen = jarvis_agent._pick_supervisor_llm(
            specialist_tools=[],
            legacy_llm="LEGACY-SENTINEL",
        )
    assert chosen == "LEGACY-SENTINEL"


def test_v2_flag_on_with_v1_on_uses_v1_with_v2_layered():
    """Both flags on: v1 supervisor in use, v2 grounding_gate baked
    into the same compiled graph (the graph's build_graph reads
    JARVIS_BLACKBOARD at compile time)."""
    import jarvis_agent
    from supervisor_graph.llm_adapter import JarvisSupervisorGraphLLM
    with patch.dict(os.environ, {
        "JARVIS_LANGGRAPH_SUPERVISOR": "1",
        "JARVIS_BLACKBOARD": "1",
    }):
        chosen = jarvis_agent._pick_supervisor_llm(
            specialist_tools=[],
            legacy_llm="LEGACY-SENTINEL",
        )
    assert isinstance(chosen, JarvisSupervisorGraphLLM)
