import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_recall_tool_registered():
    import tools.memory_providers  # noqa: F401
    from tools.registry import registry
    assert "recall" in set(registry.all_names())


def test_recall_tool_inert_without_provider(monkeypatch):
    monkeypatch.delenv("JARVIS_MEMORY_PROVIDER", raising=False)
    from tools.memory_providers import check_recall_available
    assert check_recall_available() is False
