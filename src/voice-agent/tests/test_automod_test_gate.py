"""Spec B (Plane 3) — test gate (diff validation)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def test_clean_diff_passes():
    from pipeline.automod.test_gate import validate_diff
    diff = (
        "diff --git a/src/voice-agent/prompts/supervisor.md b/src/voice-agent/prompts/supervisor.md\n"
        "index abc..def 100644\n"
        "--- a/src/voice-agent/prompts/supervisor.md\n"
        "+++ b/src/voice-agent/prompts/supervisor.md\n"
        "@@ -1,1 +1,1 @@\n"
        "-old line\n"
        "+new line\n"
    )
    ok, reason = validate_diff(diff)
    assert ok, reason


def test_rejects_empty_diff():
    from pipeline.automod.test_gate import validate_diff
    ok, reason = validate_diff("")
    assert not ok
    assert "empty" in reason.lower()


def test_rejects_no_diff_headers():
    from pipeline.automod.test_gate import validate_diff
    ok, reason = validate_diff("random text with no diff headers")
    assert not ok


def test_rejects_blocked_path_sanitizers():
    from pipeline.automod.test_gate import validate_diff
    diff = (
        "diff --git a/src/voice-agent/sanitizers/dsml.py b/src/voice-agent/sanitizers/dsml.py\n"
        "--- a/x\n+++ b/x\n@@\n-x\n+y\n"
    )
    ok, reason = validate_diff(diff)
    assert not ok
    assert "block" in reason.lower()


def test_rejects_path_outside_allowed_prefix():
    from pipeline.automod.test_gate import validate_diff
    diff = (
        "diff --git a/src/voice-agent/desktop-tauri/src/App.jsx b/src/voice-agent/desktop-tauri/src/App.jsx\n"
        "--- a/x\n+++ b/x\n@@\n-x\n+y\n"
    )
    ok, reason = validate_diff(diff)
    assert not ok


def test_rejects_blocked_path_confab_detector():
    from pipeline.automod.test_gate import validate_diff
    diff = (
        "diff --git a/src/voice-agent/confab_detector.py b/src/voice-agent/confab_detector.py\n"
        "--- a/x\n+++ b/x\n@@\n-x\n+y\n"
    )
    ok, reason = validate_diff(diff)
    assert not ok


def test_rejects_evolution_fitness_gate_edits():
    from pipeline.automod.test_gate import validate_diff
    diff = (
        "diff --git a/src/voice-agent/evolution/fitness.py b/src/voice-agent/evolution/fitness.py\n"
        "--- a/x\n+++ b/x\n@@\n-WEIGHTS = {}\n+WEIGHTS = {'latency': 1.0}\n"
    )
    ok, reason = validate_diff(diff)
    assert not ok
    assert "evolution" in reason


def test_rejects_test_deletion():
    from pipeline.automod.test_gate import validate_diff
    diff = (
        "diff --git a/src/voice-agent/tests/test_x.py b/src/voice-agent/tests/test_x.py\n"
        "--- a/src/voice-agent/tests/test_x.py\n"
        "+++ b/src/voice-agent/tests/test_x.py\n"
        "@@ -1,3 +1,1 @@\n"
        "-def test_thing():\n"
        "-    assert True\n"
        "+pass\n"
    )
    ok, reason = validate_diff(diff)
    assert not ok
    assert "test" in reason.lower()


def test_rejects_test_class_deletion():
    from pipeline.automod.test_gate import validate_diff
    diff = (
        "diff --git a/src/voice-agent/tests/test_x.py b/src/voice-agent/tests/test_x.py\n"
        "--- a/x\n+++ b/x\n@@\n-class TestThing:\n+pass\n"
    )
    ok, reason = validate_diff(diff)
    assert not ok


def test_rejects_new_pytest_skip():
    from pipeline.automod.test_gate import validate_diff
    diff = (
        "diff --git a/src/voice-agent/tests/test_x.py b/src/voice-agent/tests/test_x.py\n"
        "--- a/x\n+++ b/x\n@@\n+@pytest.mark.skipif(True, reason='broken')\n"
    )
    ok, reason = validate_diff(diff)
    assert not ok
    assert "skip" in reason.lower()


def test_rejects_new_xfail():
    from pipeline.automod.test_gate import validate_diff
    diff = (
        "diff --git a/src/voice-agent/tests/test_x.py b/src/voice-agent/tests/test_x.py\n"
        "--- a/x\n+++ b/x\n@@\n+@pytest.mark.xfail\n"
    )
    ok, reason = validate_diff(diff)
    assert not ok


def test_rejects_oversize_lines():
    from pipeline.automod.test_gate import validate_diff
    body = "+x\n" * 2500
    diff = (
        "diff --git a/src/voice-agent/x.py b/src/voice-agent/x.py\n"
        "--- a/x\n+++ b/x\n@@\n" + body
    )
    ok, reason = validate_diff(diff)
    assert not ok
    assert "size" in reason.lower() or "line" in reason.lower()


def test_rejects_too_many_files():
    from pipeline.automod.test_gate import validate_diff
    parts = []
    for i in range(6):
        parts.append(
            f"diff --git a/src/voice-agent/x{i}.py b/src/voice-agent/x{i}.py\n"
            f"--- a/src/voice-agent/x{i}.py\n+++ b/src/voice-agent/x{i}.py\n@@\n+x\n"
        )
    ok, reason = validate_diff("\n".join(parts))
    assert not ok
    assert "file" in reason.lower()


def test_parses_files_changed():
    from pipeline.automod.test_gate import files_changed
    diff = (
        "diff --git a/src/voice-agent/x.py b/src/voice-agent/x.py\n--- a/x\n+++ b/x\n@@\n+y\n"
        "diff --git a/src/voice-agent/y.py b/src/voice-agent/y.py\n--- a/x\n+++ b/x\n@@\n+y\n"
    )
    assert files_changed(diff) == ["src/voice-agent/x.py", "src/voice-agent/y.py"]


def test_files_changed_dedups():
    """If a file appears twice in the diff (rare but possible) — dedup."""
    from pipeline.automod.test_gate import files_changed
    diff = (
        "diff --git a/src/voice-agent/x.py b/src/voice-agent/x.py\n--- a/x\n+++ b/x\n@@\n+a\n"
        "diff --git a/src/voice-agent/x.py b/src/voice-agent/x.py\n--- a/x\n+++ b/x\n@@\n+b\n"
    )
    assert files_changed(diff) == ["src/voice-agent/x.py"]


def test_env_overrides_max_files(monkeypatch):
    monkeypatch.setenv("JARVIS_AUTOMOD_MAX_FILES", "2")
    from pipeline.automod.test_gate import validate_diff
    # Reload module to pick up env... wait, the env is read at call time per spec.
    parts = [
        f"diff --git a/src/voice-agent/x{i}.py b/src/voice-agent/x{i}.py\n--- a/x\n+++ b/x\n@@\n+a\n"
        for i in range(3)
    ]
    ok, reason = validate_diff("\n".join(parts))
    assert not ok
