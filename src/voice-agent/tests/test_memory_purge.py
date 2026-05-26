"""flag_garbage must catch ambient-audio hallucinations and LLM
narration while sparing real first-person facts. CONSERVATIVE — output
feeds a human-reviewed dry-run, never auto-deletion."""
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

# bin/jarvis-memory-purge has no .py extension, so spec_from_file_location
# can't infer a loader — use SourceFileLoader explicitly.
_path = Path(__file__).parent.parent.parent.parent / "bin" / "jarvis-memory-purge"
_loader = SourceFileLoader("jarvis_memory_purge", str(_path))
_spec = importlib.util.spec_from_loader("jarvis_memory_purge", _loader)
purge = importlib.util.module_from_spec(_spec)
_loader.exec_module(purge)


def test_flags_ambient_and_narration_spares_real_facts():
    mems = [
        {"memory_id": "g1", "content": "The Wimah is a fictional currency in this world."},
        {"memory_id": "g2", "content": "Gargis is being greeted."},
        {"memory_id": "g3", "content": "The user appears to be asking about something."},
        {"memory_id": "k1", "content": "Ulrich's wife is named Lizzy."},
        {"memory_id": "k2", "content": "User prefers terse replies."},
    ]
    flagged_ids = {m["memory_id"] for m in purge.flag_garbage(mems)}
    assert {"g1", "g2", "g3"} <= flagged_ids       # caught
    assert "k1" not in flagged_ids and "k2" not in flagged_ids  # spared
