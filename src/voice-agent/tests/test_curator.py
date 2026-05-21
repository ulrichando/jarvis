"""Tests for pipeline/curator.py — the skill-maintenance engine.

Proves:
  1. Deterministic age transitions: active -> stale -> archived by the
     stale/archive day thresholds, driven by each skill's last-activity
     timestamp written into .usage.json.
  2. Pinned skills are NEVER auto-transitioned.
  3. Fresh (never-used) skills anchor on created_at and are NOT immediately
     archived.
  4. A used-again stale skill reactivates.
  5. tar.gz snapshot is created; rollback restores a deleted/archived skill.
  6. run_curation() end-to-end: snapshots, applies transitions, writes a
     report, bumps state; dry_run is report-only (no snapshot, no transitions).
  7. should_run_now() interval gate: first observation seeds + defers.
  8. Consolidation review is gated OFF by default, suggestion-only when on,
     and parses a mocked LLM cluster payload (no network).

Isolation: JARVIS_SKILLS_PATHS -> tmp_path makes the user skills root the tmp
dir. JARVIS_HOME -> tmp_path/home isolates the report dir. Day thresholds are
set tiny via env so we control transitions by stamping old timestamps into
.usage.json directly. No live LLM, no network.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _make_skill(root: Path, name: str, description: str = "does a thing") -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nwhen_to_use: {description}\n---\n"
        f"# {name}\nBody.\n"
    )


def _stamp_usage(root: Path, name: str, *, days_ago: float, state: str = "active",
                 pinned: bool = False) -> None:
    """Write a .usage.json record for *name* whose last activity is *days_ago*
    days in the past. Lets transition tests run without waiting real time."""
    from pipeline import skill_usage
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    data = skill_usage.load_usage()
    rec = data.get(name) or {}
    rec.update({
        "use_count": rec.get("use_count", 1),
        "last_used_at": ts,
        "created_at": ts,
        "state": state,
        "pinned": pinned,
        "archived_at": None,
    })
    data[name] = rec
    skill_usage.save_usage(data)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Wire skills root + JARVIS_HOME at tmp paths and set tight day
    thresholds (stale > 10d, archive > 30d) so transitions are testable."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(skills))
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("JARVIS_CURATOR_STALE_AFTER_DAYS", "10")
    monkeypatch.setenv("JARVIS_CURATOR_ARCHIVE_AFTER_DAYS", "30")
    # Consolidation off unless a test opts in.
    monkeypatch.delenv("JARVIS_CURATOR_CONSOLIDATION", raising=False)
    from pipeline.skills_loader import load_skills
    load_skills()
    return skills


# ---------------------------------------------------------------------------
# 1–4. Deterministic age transitions
# ---------------------------------------------------------------------------


class TestAgeTransitions:
    def test_active_to_stale(self, env):
        from pipeline import curator, skill_usage
        _make_skill(env, "agingskill")
        _stamp_usage(env, "agingskill", days_ago=15)  # > stale(10), < archive(30)

        counts = curator.apply_automatic_transitions()
        assert counts["marked_stale"] == 1
        assert counts["archived"] == 0
        assert skill_usage.get_record("agingskill")["state"] == skill_usage.STATE_STALE

    def test_to_archived(self, env):
        from pipeline import curator, skill_usage
        _make_skill(env, "oldskill")
        _stamp_usage(env, "oldskill", days_ago=45)  # > archive(30)

        counts = curator.apply_automatic_transitions()
        assert counts["archived"] == 1
        # Dir moved to .archived/ (recoverable), not deleted.
        assert not (env / "oldskill").exists()
        assert (env / ".archived" / "oldskill" / "SKILL.md").exists()
        assert skill_usage.get_record("oldskill")["state"] == skill_usage.STATE_ARCHIVED

    def test_pinned_never_transitions(self, env):
        from pipeline import curator, skill_usage
        _make_skill(env, "pinnedskill")
        _stamp_usage(env, "pinnedskill", days_ago=999, pinned=True)

        counts = curator.apply_automatic_transitions()
        assert counts["marked_stale"] == 0
        assert counts["archived"] == 0
        assert (env / "pinnedskill").exists()
        assert skill_usage.get_record("pinnedskill")["state"] == skill_usage.STATE_ACTIVE

    def test_fresh_skill_not_archived(self, env):
        from pipeline import curator, skill_usage
        _make_skill(env, "freshskill")
        _stamp_usage(env, "freshskill", days_ago=1)  # young

        counts = curator.apply_automatic_transitions()
        assert counts["archived"] == 0
        assert counts["marked_stale"] == 0
        assert skill_usage.get_record("freshskill")["state"] == skill_usage.STATE_ACTIVE

    def test_never_used_skill_anchors_on_created_at(self, env):
        # A skill present on disk with NO usage record at all must anchor on its
        # created_at (now) and not get archived on the first pass.
        from pipeline import curator
        _make_skill(env, "brandnew")
        counts = curator.apply_automatic_transitions()
        assert counts["archived"] == 0
        assert (env / "brandnew").exists()

    def test_stale_reactivates_when_used(self, env):
        from pipeline import curator, skill_usage
        _make_skill(env, "comeback")
        _stamp_usage(env, "comeback", days_ago=3, state="stale")  # recent + stale

        counts = curator.apply_automatic_transitions()
        assert counts["reactivated"] == 1
        assert skill_usage.get_record("comeback")["state"] == skill_usage.STATE_ACTIVE


# ---------------------------------------------------------------------------
# 5. Backup + rollback
# ---------------------------------------------------------------------------


class TestBackupRollback:
    def test_snapshot_creates_tarball(self, env):
        from pipeline import curator
        _make_skill(env, "s1")
        snap = curator.snapshot_skills(reason="test")
        assert snap is not None
        assert (snap / "skills.tar.gz").exists()
        assert (snap / "manifest.json").exists()
        mf = json.loads((snap / "manifest.json").read_text())
        assert mf["reason"] == "test"
        assert mf["skill_files"] >= 1

    def test_list_backups(self, env):
        from pipeline import curator
        _make_skill(env, "s1")
        curator.snapshot_skills(reason="one")
        rows = curator.list_backups()
        assert len(rows) == 1
        assert rows[0]["reason"] == "one"

    def test_rollback_restores_deleted_skill(self, env):
        from pipeline import curator
        _make_skill(env, "keepme")
        _make_skill(env, "deleteme")
        snap = curator.snapshot_skills(reason="before-delete")
        assert snap is not None

        # Remove a skill after the snapshot.
        import shutil
        shutil.rmtree(env / "deleteme")
        assert not (env / "deleteme").exists()

        ok, msg, restored_from = curator.rollback(snap.name)
        assert ok, msg
        # The deleted skill is back from the snapshot.
        assert (env / "deleteme" / "SKILL.md").exists()
        assert (env / "keepme" / "SKILL.md").exists()

    def test_rollback_no_backups(self, env):
        from pipeline import curator
        ok, msg, _ = curator.rollback()
        assert ok is False
        assert "no matching backup" in msg

    def test_backup_prune_keeps_only_n(self, env):
        from pipeline import curator
        # Build 4 snapshot dirs by hand with deterministic, ordered ids so the
        # prune is testable without depending on wall-clock second resolution.
        backups = env / ".curator_backups"
        backups.mkdir(parents=True, exist_ok=True)
        ids = [
            "2026-01-01T00-00-01Z",
            "2026-01-01T00-00-02Z",
            "2026-01-01T00-00-03Z",
            "2026-01-01T00-00-04Z",
        ]
        for sid in ids:
            d = backups / sid
            d.mkdir()
            (d / "skills.tar.gz").write_bytes(b"\x1f\x8b")  # gzip magic; content irrelevant

        deleted = curator._prune_old_backups(keep=2)
        # The two oldest got pruned.
        assert set(deleted) == {"2026-01-01T00-00-01Z", "2026-01-01T00-00-02Z"}
        remaining = {p.name for p in backups.iterdir() if p.is_dir()}
        assert remaining == {"2026-01-01T00-00-03Z", "2026-01-01T00-00-04Z"}


# ---------------------------------------------------------------------------
# 6. run_curation end-to-end
# ---------------------------------------------------------------------------


class TestRunCuration:
    def test_live_run_applies_and_reports(self, env, monkeypatch):
        from pipeline import curator, skill_usage
        monkeypatch.setenv("JARVIS_HOME", str(env.parent / "home"))
        _make_skill(env, "fresh")
        _make_skill(env, "old")
        _stamp_usage(env, "fresh", days_ago=1)
        _stamp_usage(env, "old", days_ago=45)

        result = curator.run_curation(dry_run=False)
        assert result["dry_run"] is False
        assert result["auto_transitions"]["archived"] == 1
        # A pre-run snapshot was taken.
        assert result["backup_id"] is not None
        # Report written.
        assert result["report_path"] is not None
        assert (Path(result["report_path"]) / "run.json").exists()
        assert (Path(result["report_path"]) / "REPORT.md").exists()
        # State bumped.
        state = curator.load_state()
        assert state["run_count"] == 1
        assert state["last_run_at"] is not None
        # The old skill is archived.
        assert skill_usage.get_record("old")["state"] == skill_usage.STATE_ARCHIVED

    def test_dry_run_is_report_only(self, env):
        from pipeline import curator, skill_usage
        _make_skill(env, "old")
        _stamp_usage(env, "old", days_ago=45)

        result = curator.run_curation(dry_run=True)
        assert result["dry_run"] is True
        # No transitions applied.
        assert result["auto_transitions"]["archived"] == 0
        assert (env / "old").exists()
        assert skill_usage.get_record("old")["state"] == skill_usage.STATE_ACTIVE
        # No snapshot on dry-run.
        assert result["backup_id"] is None
        # run_count NOT bumped on dry-run.
        assert curator.load_state()["run_count"] == 0
        # But a report is still written.
        assert result["report_path"] is not None


# ---------------------------------------------------------------------------
# 7. Interval gate
# ---------------------------------------------------------------------------


class TestShouldRunNow:
    def test_first_observation_seeds_and_defers(self, env):
        from pipeline import curator
        # No prior state → should NOT run, but seeds last_run_at.
        assert curator.should_run_now() is False
        assert curator.load_state()["last_run_at"] is not None

    def test_runs_after_interval(self, env, monkeypatch):
        from pipeline import curator
        monkeypatch.setenv("JARVIS_CURATOR_INTERVAL_HOURS", "24")
        # Seed last_run_at well in the past.
        state = curator.load_state()
        state["last_run_at"] = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        curator.save_state(state)
        assert curator.should_run_now() is True

    def test_not_run_within_interval(self, env, monkeypatch):
        from pipeline import curator
        monkeypatch.setenv("JARVIS_CURATOR_INTERVAL_HOURS", "24")
        state = curator.load_state()
        state["last_run_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        curator.save_state(state)
        assert curator.should_run_now() is False

    def test_paused_blocks_run(self, env, monkeypatch):
        from pipeline import curator
        monkeypatch.setenv("JARVIS_CURATOR_INTERVAL_HOURS", "1")
        state = curator.load_state()
        state["last_run_at"] = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        state["paused"] = True
        curator.save_state(state)
        assert curator.should_run_now() is False

    def test_disabled_blocks_run(self, env, monkeypatch):
        from pipeline import curator
        monkeypatch.setenv("JARVIS_CURATOR_DISABLED", "1")
        assert curator.should_run_now() is False


# ---------------------------------------------------------------------------
# 8. Consolidation review (gated, suggestion-only, mocked LLM)
# ---------------------------------------------------------------------------


class TestConsolidationReview:
    def test_off_by_default(self, env):
        from pipeline import curator
        _make_skill(env, "a")
        _make_skill(env, "b")
        # No JARVIS_CURATOR_CONSOLIDATION → returns [] regardless of llm.
        called = {"n": 0}

        def _fake(_cands):
            called["n"] += 1
            return '{"clusters": []}'

        out = curator.run_consolidation_review(llm_fn=_fake)
        assert out == []
        assert called["n"] == 0  # LLM not even called when gated off

    def test_enabled_returns_suggestions(self, env, monkeypatch):
        from pipeline import curator
        monkeypatch.setenv("JARVIS_CURATOR_CONSOLIDATION", "1")
        _make_skill(env, "pdf-extract", "extract text from pdf")
        _make_skill(env, "docx-extract", "extract text from docx")

        def _fake(cands):
            names = [c["name"] for c in cands]
            assert "pdf-extract" in names and "docx-extract" in names
            return json.dumps({
                "clusters": [{
                    "members": ["pdf-extract", "docx-extract"],
                    "umbrella": "document-extract",
                    "reason": "both extract text from documents",
                }]
            })

        out = curator.run_consolidation_review(llm_fn=_fake)
        assert len(out) == 1
        assert set(out[0]["members"]) == {"pdf-extract", "docx-extract"}
        assert out[0]["umbrella"] == "document-extract"
        # Suggestion-only — skills still present, none archived.
        assert (env / "pdf-extract").exists()
        assert (env / "docx-extract").exists()

    def test_parse_rejects_hallucinated_members(self, env):
        from pipeline import curator
        valid = {"a", "b"}
        # Member "ghost" isn't a candidate → whole cluster dropped.
        raw = json.dumps({"clusters": [{"members": ["a", "ghost"], "umbrella": "x"}]})
        assert curator.parse_consolidation_output(raw, valid) == []

    def test_parse_rejects_singletons(self, env):
        from pipeline import curator
        valid = {"a", "b"}
        raw = json.dumps({"clusters": [{"members": ["a"], "umbrella": "a"}]})
        assert curator.parse_consolidation_output(raw, valid) == []

    def test_parse_bad_json_returns_empty(self, env):
        from pipeline import curator
        assert curator.parse_consolidation_output("not json", {"a"}) == []
        assert curator.parse_consolidation_output(None, {"a"}) == []
