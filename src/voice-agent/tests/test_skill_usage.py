"""Tests for pipeline/skill_usage.py — usage telemetry + lifecycle state.

Proves:
  1. record_use / bump_view / bump_patch increment the right counters and set
     timestamps, persisted to .usage.json.
  2. get_record backfills missing keys; load/save round-trips.
  3. pin / unpin / is_pinned toggle the pinned flag.
  4. set_state validates state names; archived sets archived_at.
  5. is_curatable / list_curatable_skill_names only see user-root skills.
  6. archive_skill moves the dir to .archived/ (recoverable) + flips state;
     restore_skill moves it back. No hard delete.
  7. record_use on a stale skill reactivates it immediately.
  8. curatable_report rows carry derived last_activity_at + activity_count.

Isolation: every test points JARVIS_SKILLS_PATHS at a tmp_path so the user
skills root is the tmp dir — exactly like test_skills_tool.py. No live LLM,
no network, nothing escapes to ~/.jarvis/skills/.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_skill(root: Path, name: str, description: str = "does a thing") -> None:
    """Write a minimal valid SKILL.md under root/<name>/SKILL.md."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nwhen_to_use: {description}\n---\n"
        f"# {name}\nBody.\n"
    )


@pytest.fixture
def skills_root(tmp_path, monkeypatch):
    """A tmp user-skills root wired via JARVIS_SKILLS_PATHS. Reloads the
    loader so _default_roots()[-1] resolves to tmp_path."""
    monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(tmp_path))
    from pipeline.skills_loader import load_skills
    load_skills()
    return tmp_path


# ---------------------------------------------------------------------------
# 1–2. Counter bumps + persistence
# ---------------------------------------------------------------------------


class TestCounterBumps:
    def test_record_use_increments_and_persists(self, skills_root):
        from pipeline import skill_usage
        _make_skill(skills_root, "alpha")

        skill_usage.record_use("alpha")
        skill_usage.record_use("alpha")

        rec = skill_usage.get_record("alpha")
        assert rec["use_count"] == 2
        assert rec["last_used_at"] is not None

        # Persisted on disk under the user root.
        usage_path = skills_root / ".usage.json"
        assert usage_path.exists()
        data = json.loads(usage_path.read_text())
        assert data["alpha"]["use_count"] == 2

    def test_bump_view_and_patch(self, skills_root):
        from pipeline import skill_usage
        _make_skill(skills_root, "beta")

        skill_usage.bump_view("beta")
        skill_usage.bump_view("beta")
        skill_usage.bump_view("beta")
        skill_usage.bump_patch("beta")

        rec = skill_usage.get_record("beta")
        assert rec["view_count"] == 3
        assert rec["patch_count"] == 1
        assert rec["last_viewed_at"] is not None
        assert rec["last_patched_at"] is not None

    def test_get_record_backfills_missing_keys(self, skills_root):
        from pipeline import skill_usage
        _make_skill(skills_root, "gamma")
        # Write a partial record directly (old-format file).
        (skills_root / ".usage.json").write_text(json.dumps({"gamma": {"use_count": 5}}))

        rec = skill_usage.get_record("gamma")
        assert rec["use_count"] == 5
        # Backfilled defaults present.
        assert rec["state"] == skill_usage.STATE_ACTIVE
        assert rec["pinned"] is False
        assert "view_count" in rec

    def test_forget_drops_entry(self, skills_root):
        from pipeline import skill_usage
        _make_skill(skills_root, "delta")
        skill_usage.record_use("delta")
        assert "delta" in skill_usage.load_usage()

        skill_usage.forget("delta")
        assert "delta" not in skill_usage.load_usage()


# ---------------------------------------------------------------------------
# 3. Pinning
# ---------------------------------------------------------------------------


class TestPinning:
    def test_pin_unpin_roundtrip(self, skills_root):
        from pipeline import skill_usage
        _make_skill(skills_root, "pinme")

        assert skill_usage.is_pinned("pinme") is False
        skill_usage.pin("pinme")
        assert skill_usage.is_pinned("pinme") is True
        skill_usage.unpin("pinme")
        assert skill_usage.is_pinned("pinme") is False

    def test_set_pinned_explicit(self, skills_root):
        from pipeline import skill_usage
        _make_skill(skills_root, "p2")
        skill_usage.set_pinned("p2", True)
        assert skill_usage.get_record("p2")["pinned"] is True


# ---------------------------------------------------------------------------
# 4. State
# ---------------------------------------------------------------------------


class TestState:
    def test_set_state_archived_sets_timestamp(self, skills_root):
        from pipeline import skill_usage
        _make_skill(skills_root, "s1")
        skill_usage.set_state("s1", skill_usage.STATE_ARCHIVED)
        rec = skill_usage.get_record("s1")
        assert rec["state"] == skill_usage.STATE_ARCHIVED
        assert rec["archived_at"] is not None

    def test_set_state_active_clears_archived_at(self, skills_root):
        from pipeline import skill_usage
        _make_skill(skills_root, "s2")
        skill_usage.set_state("s2", skill_usage.STATE_ARCHIVED)
        skill_usage.set_state("s2", skill_usage.STATE_ACTIVE)
        assert skill_usage.get_record("s2")["archived_at"] is None

    def test_invalid_state_is_noop(self, skills_root):
        from pipeline import skill_usage
        _make_skill(skills_root, "s3")
        skill_usage.set_state("s3", "bogus")
        assert skill_usage.get_record("s3")["state"] == skill_usage.STATE_ACTIVE

    def test_record_use_reactivates_stale(self, skills_root):
        from pipeline import skill_usage
        _make_skill(skills_root, "s4")
        skill_usage.set_state("s4", skill_usage.STATE_STALE)
        assert skill_usage.get_record("s4")["state"] == skill_usage.STATE_STALE
        skill_usage.record_use("s4")
        assert skill_usage.get_record("s4")["state"] == skill_usage.STATE_ACTIVE


# ---------------------------------------------------------------------------
# 5. Provenance — only user-root skills are curatable
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_is_curatable_for_user_skill(self, skills_root):
        from pipeline import skill_usage
        _make_skill(skills_root, "user-skill")
        assert skill_usage.is_curatable("user-skill") is True

    def test_unknown_skill_not_curatable(self, skills_root):
        from pipeline import skill_usage
        assert skill_usage.is_curatable("nope") is False

    def test_list_curatable_skips_archived(self, skills_root):
        from pipeline import skill_usage
        _make_skill(skills_root, "live-one")
        # A dir under .archived/ must not be enumerated as live.
        archived = skills_root / ".archived" / "old-one"
        archived.mkdir(parents=True)
        (archived / "SKILL.md").write_text(
            "---\nname: old-one\ndescription: x\n---\n# old\nbody\n"
        )
        names = skill_usage.list_curatable_skill_names()
        assert "live-one" in names
        assert "old-one" not in names

    def test_mutate_noop_for_non_curatable(self, skills_root):
        from pipeline import skill_usage
        # No SKILL.md on disk → not curatable → record_use is a silent no-op.
        skill_usage.record_use("ghost")
        assert "ghost" not in skill_usage.load_usage()


# ---------------------------------------------------------------------------
# 6. Archive / restore — recoverable, never hard-delete
# ---------------------------------------------------------------------------


class TestArchiveRestore:
    def test_archive_moves_to_archived_dir(self, skills_root):
        from pipeline import skill_usage
        _make_skill(skills_root, "archiveme")
        skill_dir = skills_root / "archiveme"
        assert skill_dir.exists()

        ok, msg = skill_usage.archive_skill("archiveme")
        assert ok, msg
        # Original gone, archived copy present (recoverable).
        assert not skill_dir.exists()
        assert (skills_root / ".archived" / "archiveme" / "SKILL.md").exists()
        # State flipped.
        assert skill_usage.get_record("archiveme")["state"] == skill_usage.STATE_ARCHIVED
        assert "archiveme" in skill_usage.list_archived_skill_names()

    def test_archive_unknown_returns_error(self, skills_root):
        from pipeline import skill_usage
        ok, msg = skill_usage.archive_skill("does-not-exist")
        assert ok is False
        assert "not found" in msg

    def test_restore_moves_back(self, skills_root):
        from pipeline import skill_usage
        _make_skill(skills_root, "roundtrip")
        skill_usage.archive_skill("roundtrip")
        assert not (skills_root / "roundtrip").exists()

        ok, msg = skill_usage.restore_skill("roundtrip")
        assert ok, msg
        assert (skills_root / "roundtrip" / "SKILL.md").exists()
        assert skill_usage.get_record("roundtrip")["state"] == skill_usage.STATE_ACTIVE

    def test_restore_refuses_to_clobber_live(self, skills_root):
        from pipeline import skill_usage
        _make_skill(skills_root, "dup")
        skill_usage.archive_skill("dup")
        # Recreate a live skill of the same name.
        _make_skill(skills_root, "dup")
        ok, msg = skill_usage.restore_skill("dup")
        assert ok is False
        assert "already exists" in msg


# ---------------------------------------------------------------------------
# 8. Reporting
# ---------------------------------------------------------------------------


class TestReport:
    def test_curatable_report_carries_derived_fields(self, skills_root):
        from pipeline import skill_usage
        _make_skill(skills_root, "rep")
        skill_usage.record_use("rep")
        skill_usage.bump_view("rep")

        rows = skill_usage.curatable_report()
        row = next(r for r in rows if r["name"] == "rep")
        assert row["activity_count"] == 2  # 1 use + 1 view
        assert row["last_activity_at"] is not None
        assert row["state"] == skill_usage.STATE_ACTIVE

    def test_latest_activity_excludes_created_at(self):
        from pipeline import skill_usage
        # No activity timestamps → latest_activity_at is None even with created_at.
        rec = {"created_at": "2020-01-01T00:00:00+00:00"}
        assert skill_usage.latest_activity_at(rec) is None
