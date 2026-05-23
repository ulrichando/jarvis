"""Tests for batch_runner — the offline text-mode parallel agent runner.

End-to-end (live LLM) tests are intentionally out of scope; those require
ANTHROPIC_API_KEY + network and would burn tokens on every CI run. The
tests below cover the pure-Python helpers (stats extraction, schema
conversion, dataset loading + batching, checkpointing) which are the
load-bearing parts of the port.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the voice-agent root is importable when pytest is run from the repo root.
_VA_ROOT = Path(__file__).resolve().parent.parent
if str(_VA_ROOT) not in sys.path:
    sys.path.insert(0, str(_VA_ROOT))

import batch_runner  # noqa: E402


# ── Tool-stats extraction ───────────────────────────────────────────


def test_extract_tool_stats_counts_call_and_success():
    """A single successful tool call: count=1, success=1, failure=0."""
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "what time is it?"}]},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_1", "name": "terminal",
             "input": {"command": "date"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "Fri 23 May 2026 13:42 UTC"},
        ]},
    ]
    stats = batch_runner._extract_tool_stats(messages)
    assert stats == {"terminal": {"count": 1, "success": 1, "failure": 0}}


def test_extract_tool_stats_counts_failure_on_error_prefix():
    """A tool_result whose content starts with 'Error:' counts as failure."""
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "tu_1", "name": "browser_task",
             "input": {"task": "open amazon"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1",
             "content": "Error: browser tool unavailable"},
        ]},
    ]
    stats = batch_runner._extract_tool_stats(messages)
    assert stats == {"browser_task": {"count": 1, "success": 0, "failure": 1}}


def test_extract_tool_stats_aggregates_across_multiple_calls():
    """Two calls to same tool, one success + one failure → count=2/s=1/f=1."""
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "a", "name": "web_fetch", "input": {"url": "u1"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a", "content": "page text"},
        ]},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "b", "name": "web_fetch", "input": {"url": "u2"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "b", "content": "Error: 404"},
        ]},
    ]
    stats = batch_runner._extract_tool_stats(messages)
    assert stats == {"web_fetch": {"count": 2, "success": 1, "failure": 1}}


def test_extract_tool_stats_handles_empty_messages():
    assert batch_runner._extract_tool_stats([]) == {}


# ── Schema conversion (JARVIS ToolEntry → Anthropic tool shape) ─────


def test_entries_to_anthropic_tools_remaps_parameters_to_input_schema():
    """ToolEntry.schema['parameters'] must become Anthropic's 'input_schema'."""

    class FakeEntry:
        name = "ping"
        description = "Echoes pong"
        schema = {
            "name": "ping",
            "description": "Echoes pong",
            "parameters": {
                "type": "object",
                "properties": {"msg": {"type": "string"}},
                "required": ["msg"],
            },
        }

    tools = batch_runner._entries_to_anthropic_tools([FakeEntry()])
    assert len(tools) == 1
    t = tools[0]
    assert t["name"] == "ping"
    assert t["description"] == "Echoes pong"
    assert "input_schema" in t and "parameters" not in t
    # additionalProperties:false must be set (Anthropic requirement)
    assert t["input_schema"].get("additionalProperties") is False


def test_entries_to_anthropic_tools_falls_back_to_empty_object_schema():
    """A tool with no schema params still gets a valid input_schema."""

    class FakeEntry:
        name = "noargs"
        description = "No-arg tool"
        schema = {"name": "noargs", "description": "No-arg tool"}

    tools = batch_runner._entries_to_anthropic_tools([FakeEntry()])
    assert tools[0]["input_schema"]["type"] == "object"


# ── Dataset loading + batching ──────────────────────────────────────


def _write_jsonl(path: Path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def test_load_dataset_reads_jsonl_and_filters_invalid(tmp_path: Path):
    p = tmp_path / "data.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"prompt": "first"}) + "\n")
        f.write("not valid json\n")
        f.write(json.dumps({"no_prompt_field": True}) + "\n")
        f.write(json.dumps({"prompt": "second"}) + "\n")

    r = batch_runner.BatchRunner(
        dataset_file=p, run_name="t", output_root=tmp_path / "out",
    )
    assert len(r.dataset) == 2
    assert r.dataset[0]["prompt"] == "first"
    assert r.dataset[1]["prompt"] == "second"


def test_load_dataset_raises_when_no_valid_entries(tmp_path: Path):
    p = tmp_path / "empty.jsonl"
    p.write_text("not json\n{\"no_prompt\":1}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        batch_runner.BatchRunner(
            dataset_file=p, run_name="t", output_root=tmp_path / "out",
        )


def test_load_dataset_raises_when_file_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        batch_runner.BatchRunner(
            dataset_file=tmp_path / "nope.jsonl", run_name="t",
            output_root=tmp_path / "out",
        )


def test_create_batches_splits_evenly_with_remainder(tmp_path: Path):
    p = tmp_path / "data.jsonl"
    _write_jsonl(p, [{"prompt": f"p{i}"} for i in range(7)])
    r = batch_runner.BatchRunner(
        dataset_file=p, run_name="t", batch_size=3, output_root=tmp_path / "out",
    )
    assert len(r.batches) == 3  # 7 / 3 = 3 batches (3, 3, 1)
    assert len(r.batches[0]) == 3
    assert len(r.batches[1]) == 3
    assert len(r.batches[2]) == 1
    # Indices are absolute across the dataset, not local to the batch.
    assert [idx for idx, _ in r.batches[0]] == [0, 1, 2]
    assert [idx for idx, _ in r.batches[2]] == [6]


def test_max_samples_truncates_dataset(tmp_path: Path):
    p = tmp_path / "data.jsonl"
    _write_jsonl(p, [{"prompt": f"p{i}"} for i in range(10)])
    r = batch_runner.BatchRunner(
        dataset_file=p, run_name="t", batch_size=3,
        max_samples=4, output_root=tmp_path / "out",
    )
    assert len(r.dataset) == 4


# ── Checkpointing ────────────────────────────────────────────────────


def test_save_then_load_checkpoint_roundtrips(tmp_path: Path):
    p = tmp_path / "data.jsonl"
    _write_jsonl(p, [{"prompt": "x"}])
    r = batch_runner.BatchRunner(
        dataset_file=p, run_name="t", output_root=tmp_path / "out",
    )
    payload = {"run_name": "t", "completed_prompts": [0, 2, 5], "batch_stats": {}}
    r._save_checkpoint(payload)
    loaded = r._load_checkpoint()
    assert loaded["completed_prompts"] == [0, 2, 5]
    assert "last_updated" in loaded  # _save_checkpoint stamps it


def test_load_checkpoint_returns_empty_when_missing(tmp_path: Path):
    p = tmp_path / "data.jsonl"
    _write_jsonl(p, [{"prompt": "x"}])
    r = batch_runner.BatchRunner(
        dataset_file=p, run_name="freshrun", output_root=tmp_path / "out",
    )
    loaded = r._load_checkpoint()
    assert loaded["completed_prompts"] == []
    assert loaded["run_name"] == "freshrun"


# ── CLI surface (smoke) ──────────────────────────────────────────────


def test_argparser_requires_dataset_and_run_name():
    parser = batch_runner._build_argparser()
    # Both required → SystemExit if missing.
    with pytest.raises(SystemExit):
        parser.parse_args([])
    with pytest.raises(SystemExit):
        parser.parse_args(["--dataset_file=/tmp/x.jsonl"])
    # With both, parsing succeeds.
    ns = parser.parse_args(["--dataset_file=/tmp/x.jsonl", "--run_name=r"])
    assert ns.dataset_file == Path("/tmp/x.jsonl")
    assert ns.run_name == "r"
    assert ns.batch_size == batch_runner.DEFAULT_BATCH_SIZE


def test_argparser_parses_optional_flags():
    parser = batch_runner._build_argparser()
    ns = parser.parse_args([
        "--dataset_file=/tmp/x.jsonl", "--run_name=r",
        "--batch_size=20", "--num_workers=8",
        "--model=claude-sonnet-4-6", "--max_turns=15",
        "--max_samples=100", "--resume", "--verbose",
    ])
    assert ns.batch_size == 20
    assert ns.num_workers == 8
    assert ns.model == "claude-sonnet-4-6"
    assert ns.max_turns == 15
    assert ns.max_samples == 100
    assert ns.resume is True
    assert ns.verbose is True
