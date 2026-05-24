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
