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


def test_recall_tool_schema_has_search_mode():
    """recall exposes a 'mode' param with 'answer' (default) and 'search'."""
    from tools.memory_providers import _RECALL_SCHEMA
    mode = _RECALL_SCHEMA["parameters"]["properties"].get("mode")
    assert mode is not None
    assert set(mode["enum"]) == {"answer", "search"}


def test_recall_tool_routes_mode(monkeypatch):
    """mode='search' → search_for_query; default/absent → recall_for_query."""
    import asyncio
    from pipeline import memory_provider
    calls = []
    monkeypatch.setattr(memory_provider, "search_for_query",
                        lambda q, *a, **k: calls.append(("search", q)) or "SEARCH-RESULT")
    monkeypatch.setattr(memory_provider, "recall_for_query",
                        lambda q, *a, **k: calls.append(("answer", q)) or "ANSWER-RESULT")
    from tools.memory_providers import _handle_recall

    out_search = asyncio.run(_handle_recall({"query": "cars", "mode": "search"}))
    out_answer = asyncio.run(_handle_recall({"query": "cars"}))
    assert out_search == "SEARCH-RESULT" and ("search", "cars") in calls
    assert out_answer == "ANSWER-RESULT" and ("answer", "cars") in calls


def test_recall_search_empty_is_non_authoritative(monkeypatch):
    """An empty search result must NOT read as 'never discussed' (denial risk)."""
    import asyncio
    from pipeline import memory_provider
    monkeypatch.setattr(memory_provider, "search_for_query", lambda q, *a, **k: "")
    from tools.memory_providers import _handle_recall
    out = asyncio.run(_handle_recall({"query": "x", "mode": "search"}))
    assert "does not mean" in out.lower()


def test_recall_mode_coerces_non_string(monkeypatch):
    """A non-string mode must not crash — coerced via str(), routes to answer."""
    import asyncio
    from pipeline import memory_provider
    monkeypatch.setattr(memory_provider, "recall_for_query", lambda q, *a, **k: "ANSWER")
    monkeypatch.setattr(memory_provider, "search_for_query", lambda q, *a, **k: "SEARCH")
    from tools.memory_providers import _handle_recall
    out = asyncio.run(_handle_recall({"query": "x", "mode": 123}))
    assert out == "ANSWER"     # unknown/non-string mode → safe default (answer)


def test_memory_tool_schema_has_procedure_target():
    """Track 2c: tool schema's target enum includes 'procedure'."""
    from tools.memory import MEMORY_SCHEMA
    target_prop = MEMORY_SCHEMA["parameters"]["properties"]["target"]
    assert "procedure" in target_prop["enum"]


def test_memory_tool_schema_has_name_param():
    """Track 2c: tool schema has 'name' param for procedure target."""
    from tools.memory import MEMORY_SCHEMA
    props = MEMORY_SCHEMA["parameters"]["properties"]
    assert "name" in props
    assert "kebab-case" in props["name"]["description"].lower() \
        or "procedure" in props["name"]["description"].lower()


def test_memory_tool_rejects_procedure_add_without_name(tmp_path, monkeypatch):
    """Track 2c: action=add target=procedure without 'name' returns an error."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline import file_memory
    file_memory.reload_store()

    from tools.memory import _handle_memory
    import json
    res_str = _handle_memory({"action": "add", "target": "procedure",
                              "content": "1. step one"})
    res = json.loads(res_str)
    assert not res.get("success", True)
    assert "name" in res.get("error", "").lower()


def test_memory_tool_accepts_procedure_add_with_name(tmp_path, monkeypatch):
    """Track 2c: action=add target=procedure with 'name' writes to PROCEDURES.md."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline import file_memory
    file_memory.reload_store()

    from tools.memory import _handle_memory
    import json
    res_str = _handle_memory({
        "action": "add", "target": "procedure",
        "name": "morning-routine",
        "content": "1. coffee\n2. shower\n3. code",
    })
    res = json.loads(res_str)
    assert res.get("success"), res

    procedures_md = tmp_path / "memories" / "PROCEDURES.md"
    assert procedures_md.exists()
    assert "morning-routine" in procedures_md.read_text(encoding="utf-8")
