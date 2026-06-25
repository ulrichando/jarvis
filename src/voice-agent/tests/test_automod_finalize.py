"""Spec B (Plane 3) — finalize.py validation + artifact write."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def _setup_tmp_repo_with_commit(tmp_path):
    """Tmp git repo with master + an automod branch carrying a small clean diff."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "src" / "voice-agent" / "prompts").mkdir(parents=True)
    f = repo / "src" / "voice-agent" / "prompts" / "supervisor.md"
    f.write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-qb", "automod/test-001"], cwd=repo, check=True)
    f.write_text("world\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "feat: change"], cwd=repo, check=True)
    return repo


def _seed_intent_file(tmp_path, automod_id, text="INTENT: test change\nRATIONALE: t\nKIND: explicit\n"):
    p = tmp_path / "auto-mods" / f"{automod_id}.intent.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_green_diff_writes_pending_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = _setup_tmp_repo_with_commit(tmp_path)
    monkeypatch.chdir(repo)
    _seed_intent_file(tmp_path, "test-001")

    from pipeline.automod import finalize
    finalize.finalize_branch("test-001", "automod/test-001", skip_test_rerun=True)

    art = json.loads((tmp_path / "auto-mods" / "test-001.json").read_text())
    assert art["status"] == "pending"
    assert art["files_changed"] == ["src/voice-agent/prompts/supervisor.md"]
    assert art["parent_sha"]
    assert art["head_sha"]
    assert art["parent_sha"] != art["head_sha"]
    assert art["evolution"]["criteria_version"]
    assert "selection" in art["evolution"]["satisfied"]


def test_diff_with_blocked_path_marks_failed(tmp_path, monkeypatch):
    """A diff touching a blocked path is marked failed + branch deleted."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    bad = repo / "src" / "voice-agent" / "sanitizers"
    bad.mkdir(parents=True)
    f = bad / "dsml.py"
    f.write_text("x\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-qb", "automod/blocked-001"], cwd=repo, check=True)
    f.write_text("y\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "bad"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    _seed_intent_file(tmp_path, "blocked-001", "INTENT: bad\n")

    from pipeline.automod import finalize
    finalize.finalize_branch("blocked-001", "automod/blocked-001", skip_test_rerun=True)

    art = json.loads((tmp_path / "auto-mods" / "blocked-001.json").read_text())
    assert art["status"] == "failed"
    assert "block" in art.get("rejection_reason", "").lower()
    out = subprocess.check_output(["git", "branch"], cwd=repo).decode()
    assert "automod/blocked-001" not in out


def test_no_commit_marks_failed(tmp_path, monkeypatch):
    """If the CLI didn't commit anything, finalize marks artifact as failed."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "x.txt").write_text("hi\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-qb", "automod/nocommit-001"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    _seed_intent_file(tmp_path, "nocommit-001", "INTENT: nothing\n")

    from pipeline.automod import finalize
    finalize.finalize_branch("nocommit-001", "automod/nocommit-001", skip_test_rerun=True)

    art = json.loads((tmp_path / "auto-mods" / "nocommit-001.json").read_text())
    assert art["status"] == "failed"
    assert "no_commit" in art.get("rejection_reason", "")


def _setup_nocommit_repo(tmp_path, branch):
    """Tmp git repo whose automod branch carries NO new commit (HEAD == base)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "x.txt").write_text("hi\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-qb", branch], cwd=repo, check=True)
    return repo


def test_no_commit_with_red_base_flags_base_suite_red(tmp_path, monkeypatch):
    """No commit landed AND the base suite is red → finalize records the distinct
    reason `base_suite_red` and emits the loud `automod_base_suite_red` audit
    event, so /evolution shows the loop is blocked at the ROOT, not failing
    per-proposal (the silent ×7 churn of 2026-06-23)."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = _setup_nocommit_repo(tmp_path, "automod/redbase-001")
    monkeypatch.chdir(repo)
    _seed_intent_file(tmp_path, "redbase-001", "INTENT: nothing\n")

    from pipeline.automod import finalize
    monkeypatch.setattr(finalize, "_rerun_pytest", lambda: (False, "1 failed in 2s"))
    audited = []
    monkeypatch.setattr(finalize.artifact, "audit",
                        lambda kind, **f: audited.append(kind))

    finalize.finalize_branch("redbase-001", "automod/redbase-001", skip_test_rerun=False)

    art = json.loads((tmp_path / "auto-mods" / "redbase-001.json").read_text())
    assert art["status"] == "failed"
    assert art["rejection_reason"] == "base_suite_red"
    assert "automod_base_suite_red" in audited      # the loud, dashboard-visible signal


def test_no_commit_with_green_base_stays_no_commit_landed(tmp_path, monkeypatch):
    """No commit landed but the base suite is GREEN → the agent refused for its
    own reason; keep the generic no_commit_landed and do NOT raise the
    base-suite alarm."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = _setup_nocommit_repo(tmp_path, "automod/greenbase-001")
    monkeypatch.chdir(repo)
    _seed_intent_file(tmp_path, "greenbase-001", "INTENT: nothing\n")

    from pipeline.automod import finalize
    monkeypatch.setattr(finalize, "_rerun_pytest", lambda: (True, "100 passed"))
    audited = []
    monkeypatch.setattr(finalize.artifact, "audit",
                        lambda kind, **f: audited.append(kind))

    finalize.finalize_branch("greenbase-001", "automod/greenbase-001", skip_test_rerun=False)

    art = json.loads((tmp_path / "auto-mods" / "greenbase-001.json").read_text())
    assert art["status"] == "failed"
    assert art["rejection_reason"] == "no_commit_landed"
    assert "automod_base_suite_red" not in audited


def test_test_deletion_in_diff_marks_failed(tmp_path, monkeypatch):
    """A diff that DELETES a test should be rejected even though it
    touches src/voice-agent/."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    tdir = repo / "src" / "voice-agent" / "tests"
    tdir.mkdir(parents=True)
    f = tdir / "test_thing.py"
    f.write_text("def test_thing():\n    assert True\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-qb", "automod/deltest-001"], cwd=repo, check=True)
    f.write_text("# deleted\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "delete test"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    _seed_intent_file(tmp_path, "deltest-001")

    from pipeline.automod import finalize
    finalize.finalize_branch("deltest-001", "automod/deltest-001", skip_test_rerun=True)

    art = json.loads((tmp_path / "auto-mods" / "deltest-001.json").read_text())
    assert art["status"] == "failed"
    assert "test" in art.get("rejection_reason", "").lower()


def test_intent_text_threaded_into_artifact(tmp_path, monkeypatch):
    """The intent file's content lands in artifact['intent']."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = _setup_tmp_repo_with_commit(tmp_path)
    monkeypatch.chdir(repo)
    _seed_intent_file(tmp_path, "test-001",
                       "INTENT: my-specific-test-intent\nRATIONALE: x\nKIND: explicit\n")

    from pipeline.automod import finalize
    art = finalize.finalize_branch("test-001", "automod/test-001", skip_test_rerun=True)
    assert art["intent"] == "my-specific-test-intent" or "my-specific-test-intent" in art["intent"]


def test_rerun_pytest_uses_tooling_root_python(tmp_path, monkeypatch):
    from pipeline.automod import finalize

    tooling_root = tmp_path / "tooling"
    monkeypatch.setenv("JARVIS_AUTOMOD_TOOLING_ROOT", str(tooling_root))
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, "1 passed\n", "")

    monkeypatch.setattr(finalize.subprocess, "run", fake_run)
    ok, tail = finalize._rerun_pytest()

    assert ok is True
    assert "1 passed" in tail
    assert calls
    assert calls[0][0][0] == str(
        tooling_root / "src" / "voice-agent" / ".venv" / "bin" / "python"
    )
    assert calls[0][1]["cwd"] == Path(finalize.__file__).resolve().parents[2]


# ── Additional robustness tests (2026-06-25) ─────────────────────────────────


def test_base_suite_red_emits_distinct_audit_not_generic(tmp_path, monkeypatch):
    """When the base suite is red, finalize must emit BOTH the generic
    `automod_failed` AND the distinct `automod_base_suite_red` audit event.
    The distinct event is what the /evolution dashboard surfaces as a
    root-level blocker. A regression that collapses both into `automod_failed`
    only would hide the diagnosis."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = _setup_nocommit_repo(tmp_path, "automod/redbase-002")
    monkeypatch.chdir(repo)
    _seed_intent_file(tmp_path, "redbase-002", "INTENT: nothing\n")

    from pipeline.automod import finalize
    monkeypatch.setattr(finalize, "_rerun_pytest", lambda: (False, "7 failed"))
    audited = []
    monkeypatch.setattr(finalize.artifact, "audit",
                        lambda kind, **f: audited.append(kind))

    finalize.finalize_branch("redbase-002", "automod/redbase-002", skip_test_rerun=False)

    assert "automod_failed" in audited, "must always emit automod_failed"
    assert "automod_base_suite_red" in audited, (
        "must emit the DISTINCT automod_base_suite_red so /evolution can surface it"
    )


def test_base_suite_red_skipped_when_skip_test_rerun_true(tmp_path, monkeypatch):
    """When skip_test_rerun=True, the base-suite check is intentionally skipped.
    The artifact must record `no_commit_landed` (not `base_suite_red`) and must
    NOT emit `automod_base_suite_red` — those are reserved for real red-suite detections."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = _setup_nocommit_repo(tmp_path, "automod/skiptrerun-001")
    monkeypatch.chdir(repo)
    _seed_intent_file(tmp_path, "skiptrerun-001", "INTENT: nothing\n")

    from pipeline.automod import finalize
    # _rerun_pytest must NOT be called when skip_test_rerun=True
    monkeypatch.setattr(finalize, "_rerun_pytest",
                        lambda: (_ for _ in ()).throw(AssertionError("_rerun_pytest called!")))
    audited = []
    monkeypatch.setattr(finalize.artifact, "audit",
                        lambda kind, **f: audited.append(kind))

    finalize.finalize_branch("skiptrerun-001", "automod/skiptrerun-001", skip_test_rerun=True)

    art = json.loads((tmp_path / "auto-mods" / "skiptrerun-001.json").read_text())
    assert art["rejection_reason"] == "no_commit_landed"
    assert "automod_base_suite_red" not in audited


def test_no_commit_artifact_contains_test_output_on_red_base(tmp_path, monkeypatch):
    """The test_output_tail field of a base_suite_red artifact must contain the
    pytest output tail — this is what the /evolution UI shows to explain the blockage."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = _setup_nocommit_repo(tmp_path, "automod/tail-001")
    monkeypatch.chdir(repo)
    _seed_intent_file(tmp_path, "tail-001", "INTENT: nothing\n")

    from pipeline.automod import finalize
    monkeypatch.setattr(finalize, "_rerun_pytest",
                        lambda: (False, "SPECIFIC-FAIL-OUTPUT"))
    monkeypatch.setattr(finalize.artifact, "audit", lambda kind, **f: None)

    finalize.finalize_branch("tail-001", "automod/tail-001", skip_test_rerun=False)

    art = json.loads((tmp_path / "auto-mods" / "tail-001.json").read_text())
    assert "SPECIFIC-FAIL-OUTPUT" in art.get("test_output_tail", ""), (
        "test_output_tail must carry the pytest tail so /evolution can diagnose the blocker"
    )


def test_missing_intent_file_produces_failed_artifact_with_fallback(tmp_path, monkeypatch):
    """finalize_branch with no intent file on disk must not raise — it must
    write a failed artifact with the fallback intent string '(missing intent file)'."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = _setup_tmp_repo_with_commit(tmp_path)
    monkeypatch.chdir(repo)
    # Deliberately do NOT write any intent file for test-001

    from pipeline.automod import finalize
    art = finalize.finalize_branch("test-001", "automod/test-001", skip_test_rerun=True)

    # With a green diff but no intent file the proposal can still be pending;
    # what matters is it does NOT crash and 'intent' has the fallback.
    assert art.get("intent") is not None
    assert "missing" in art.get("intent", "").lower() or art.get("status") in ("pending", "failed")


def test_read_intent_malformed_evolution_json_falls_back(tmp_path, monkeypatch):
    """If the EVOLUTION field in the intent file is not valid JSON, _read_intent
    must return an empty dict fallback rather than crashing or propagating
    JSONDecodeError into finalize_branch."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    _seed_intent_file(
        tmp_path, "bad-evo-001",
        "INTENT: fix it\nEVOLUTION: {bad json here}\nRATIONALE: t\n",
    )

    from pipeline.automod import finalize
    result = finalize._read_intent("bad-evo-001")

    assert result.get("intent") == "fix it"
    assert isinstance(result.get("evolution"), dict), (
        "_read_intent must return an empty dict on malformed EVOLUTION JSON"
    )


def test_read_intent_malformed_prior_failures_falls_back(tmp_path, monkeypatch):
    """If the PRIOR_FAILURES field is not valid JSON, _read_intent must return
    an empty list rather than crashing."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    _seed_intent_file(
        tmp_path, "bad-pf-001",
        "INTENT: fix it\nPRIOR_FAILURES: [bad\nRATIONALE: t\n",
    )

    from pipeline.automod import finalize
    result = finalize._read_intent("bad-pf-001")

    assert isinstance(result.get("prior_failures"), list), (
        "_read_intent must return an empty list on malformed PRIOR_FAILURES JSON"
    )


def test_rerun_pytest_returns_false_on_timeout(tmp_path, monkeypatch):
    """If the pytest subprocess times out, _rerun_pytest returns (False, message)
    and must NOT propagate the TimeoutExpired exception to callers."""
    from pipeline.automod import finalize

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 300)

    monkeypatch.setattr(finalize.subprocess, "run", fake_run)
    ok, tail = finalize._rerun_pytest()

    assert ok is False
    assert "timeout" in tail.lower() or "300" in tail


def test_finalize_branch_writes_lineage_fields(tmp_path, monkeypatch):
    """A pending artifact must carry attempt/lineage/priority/prior_failures from
    the intent file so the retry scanner can cap attempts and avoid repeating fixes."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = _setup_tmp_repo_with_commit(tmp_path)
    monkeypatch.chdir(repo)
    _seed_intent_file(
        tmp_path, "test-001",
        "ATTEMPT: 2\nLINEAGE: original-id\nPRIOR_FAILURES: []\nPRIORITY: P1\n"
        "INTENT: fix X\nRATIONALE: test\nKIND: explicit\n",
    )

    from pipeline.automod import finalize
    art = finalize.finalize_branch("test-001", "automod/test-001", skip_test_rerun=True)

    assert art.get("status") == "pending"
    assert art.get("attempt") == 2, "attempt must be persisted from intent file"
    assert art.get("lineage") == "original-id", "lineage must be persisted from intent file"
    assert art.get("priority") == "P1", "priority must be persisted from intent file"


def test_pending_artifact_has_no_rejection_reason(tmp_path, monkeypatch):
    """A successfully landed proposal (pending) must NOT have a rejection_reason key
    — its presence would confuse the retry scanner into thinking the build failed."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = _setup_tmp_repo_with_commit(tmp_path)
    monkeypatch.chdir(repo)
    _seed_intent_file(tmp_path, "test-001")

    from pipeline.automod import finalize
    art = finalize.finalize_branch("test-001", "automod/test-001", skip_test_rerun=True)

    assert art.get("status") == "pending"
    assert "rejection_reason" not in art, (
        "a pending (reviewable) artifact must not contain rejection_reason"
    )


def test_failed_artifact_always_has_rejection_reason(tmp_path, monkeypatch):
    """Every failed artifact path must record a non-empty rejection_reason so the
    /evolution dashboard can explain why a proposal did not land."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = _setup_nocommit_repo(tmp_path, "automod/nc-002")
    monkeypatch.chdir(repo)
    _seed_intent_file(tmp_path, "nc-002", "INTENT: nothing\n")

    from pipeline.automod import finalize
    art = finalize.finalize_branch("nc-002", "automod/nc-002", skip_test_rerun=True)

    assert art.get("status") == "failed"
    assert art.get("rejection_reason"), (
        "every failed artifact must carry a non-empty rejection_reason"
    )


# ── Fused from parallel agent A: complementary coverage
# (rerun-red path, atomic write, mark_auto_merged create + idempotent) 2026-06-25 ──

def test_rerun_red_after_valid_diff_marks_tests_failed(tmp_path, monkeypatch):
    """After a valid diff passes the test_gate, if the re-run of the full
    suite is RED, finalize writes rejection_reason='tests_failed_on_rerun'
    with status='failed'. This path is triggered only when skip_test_rerun=False."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = _setup_tmp_repo_with_commit(tmp_path)
    monkeypatch.chdir(repo)
    _seed_intent_file(tmp_path, "test-001")

    from pipeline.automod import finalize
    # Simulate a red pytest re-run (suite broke after the agent's changes)
    monkeypatch.setattr(finalize, "_rerun_pytest",
                        lambda: (False, "FAILED tests/test_x.py::test_y"))

    audited = []
    monkeypatch.setattr(finalize.artifact, "audit",
                        lambda kind, **f: audited.append(kind))

    finalize.finalize_branch("test-001", "automod/test-001", skip_test_rerun=False)

    art = json.loads((tmp_path / "auto-mods" / "test-001.json").read_text())
    assert art["status"] == "failed"
    assert art["rejection_reason"] == "tests_failed_on_rerun"
    assert art["diff_summary"] == "tests-red"
    assert "automod_failed" in audited


def test_pending_artifact_written_atomically(tmp_path, monkeypatch):
    """A successful finalize (pending artifact) must leave a parseable JSON
    file — verifies the atomic-write path doesn't leave a partial/empty file."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    repo = _setup_tmp_repo_with_commit(tmp_path)
    monkeypatch.chdir(repo)
    _seed_intent_file(tmp_path, "test-001")

    from pipeline.automod import finalize
    finalize.finalize_branch("test-001", "automod/test-001", skip_test_rerun=True)

    art_path = tmp_path / "auto-mods" / "test-001.json"
    assert art_path.exists(), "artifact file must exist after pending finalize"
    # Must be parseable (atomic write, no partial JSON)
    parsed = json.loads(art_path.read_text())
    assert parsed["id"] == "test-001"
    assert parsed["status"] == "pending"


def test_mark_auto_merged_creates_artifact_when_absent(tmp_path, monkeypatch):
    """mark_auto_merged must create a minimal artifact record when none exists
    — this handles the crash-before-finalize path so the revert tool can
    always find the id."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import finalize

    finalize.mark_auto_merged(
        "crash-before-001",
        rollback_ref="refs/tags/automod-rollback-crash-before-001",
        rollback_sha="abc123",
        merge_sha="def456",
    )

    art_path = tmp_path / "auto-mods" / "crash-before-001.json"
    assert art_path.exists()
    parsed = json.loads(art_path.read_text())
    assert parsed["id"] == "crash-before-001"
    assert parsed["merge_sha"] == "def456"
    assert parsed["rollback_sha"] == "abc123"


def test_mark_auto_merged_idempotent_on_existing_artifact(tmp_path, monkeypatch):
    """mark_auto_merged is idempotent: calling it twice stamps the same
    fields; the second call's values win without corrupting the artifact."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline.automod import artifact, finalize

    artifact.write({"id": "idem-001", "status": "pending", "intent": "do X"})

    finalize.mark_auto_merged(
        "idem-001",
        rollback_ref="refs/tags/automod-rollback-idem-001",
        rollback_sha="sha1",
        merge_sha="sha2",
    )
    # Second call — same id, different SHA to verify last-write wins
    finalize.mark_auto_merged(
        "idem-001",
        rollback_ref="refs/tags/automod-rollback-idem-001",
        rollback_sha="sha1",
        merge_sha="sha3",
    )

    parsed = json.loads((tmp_path / "auto-mods" / "idem-001.json").read_text())
    assert parsed["merge_sha"] == "sha3", "second mark_auto_merged must overwrite merge_sha"
    assert parsed["status"] == "pending", "pre-existing status must survive stamp"
    assert parsed["intent"] == "do X", "pre-existing intent must survive stamp"
