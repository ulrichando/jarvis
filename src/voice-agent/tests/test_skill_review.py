"""Tests for pipeline/skill_review.py — the background-review engine.

Proves:
  1. select_review_candidates() picks complex/hard turns from a tmp
     turn_telemetry.db (subagent fired / computer_use steps / long
     TASK|REASONING reply) and excludes banter/short turns. Newest-first,
     capped at `limit`.
  2. parse_review_output() turns a structured JSON payload into validated
     Proposal objects (skill_create / skill_patch / memory).
  3. Junk / narration proposals ("The user is X-ing", "It seems to be Y")
     are filtered out (shared meta-paraphrase regex with the extractor).
  4. run_review(apply=False) writes NOTHING to the skill store or memory
     (asserts no new skill files) but DOES log proposals (run.json +
     SUMMARY.md under JARVIS_HOME/logs/skill_review/).
  5. apply=True + env JARVIS_SKILL_REVIEW_APPLY=1 applies a skill_create
     via skills_authoring into the tmp JARVIS_SKILLS_PATHS root.
  6. apply=True WITHOUT the env flag stays propose-only (double-gate).

Isolation: JARVIS_TURN_TELEMETRY_DB -> tmp db; JARVIS_SKILLS_PATHS -> tmp
skills root; JARVIS_HOME -> tmp/home for the report dir. The aux-LLM is
ALWAYS mocked via the `llm_fn` seam — NO network, NO live LLM.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


# ---------------------------------------------------------------------------
# Tiny telemetry DB matching the REAL `turns` schema (verified 2026-05-21).
# Only the columns skill_review reads are populated; the rest match the
# live shape so the SELECT compiles against a faithful schema.
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE turns (
    id INTEGER PRIMARY KEY,
    ts_utc TEXT NOT NULL,
    user_text TEXT NOT NULL,
    jarvis_text TEXT NOT NULL,
    emotion TEXT,
    route TEXT,
    llm_used TEXT,
    voice_used TEXT,
    ttfw_ms INTEGER,
    total_audio_ms INTEGER,
    user_followup_30s INTEGER,
    route_fallback INTEGER,
    notes TEXT,
    memory_auto_extracted INTEGER DEFAULT 0,
    subagent TEXT,
    interrupted INTEGER DEFAULT 0,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    context_pressure TEXT,
    prompt_cached_tokens INTEGER DEFAULT 0,
    browser_backend TEXT,
    computer_use_steps INTEGER,
    computer_use_cost_usd REAL,
    confab_check_state TEXT
);
"""


def _insert_turn(conn, *, tid, ts, user, jarvis, route="TASK",
                 subagent="", cu_steps=None):
    conn.execute(
        "INSERT INTO turns (id, ts_utc, user_text, jarvis_text, route, "
        "subagent, computer_use_steps) VALUES (?,?,?,?,?,?,?)",
        (tid, ts, user, jarvis, route, subagent or None, cu_steps),
    )


@pytest.fixture
def telemetry_db(tmp_path, monkeypatch):
    """Build a tmp turn_telemetry.db with a known mix of turns and wire
    JARVIS_TURN_TELEMETRY_DB to it. Returns the db Path."""
    db = tmp_path / "turn_telemetry.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_SCHEMA)
    long_reply = "step one. " * 60  # ~600 chars > 400 default threshold

    # Selected: subagent fired
    _insert_turn(conn, tid=1, ts="2026-05-21T10:00:00Z",
                 user="open chrome and search", jarvis="Chrome opened.",
                 route="TASK", subagent="desktop")
    # Selected: computer_use steps
    _insert_turn(conn, tid=2, ts="2026-05-21T10:01:00Z",
                 user="click the button", jarvis="Clicked.",
                 route="TASK", subagent="computer_use", cu_steps=4)
    # Selected: long TASK reply, no subagent
    _insert_turn(conn, tid=3, ts="2026-05-21T10:02:00Z",
                 user="how do I deploy", jarvis=long_reply, route="TASK")
    # Selected: long REASONING reply
    _insert_turn(conn, tid=4, ts="2026-05-21T10:03:00Z",
                 user="think about it", jarvis=long_reply, route="REASONING")
    # NOT selected: banter
    _insert_turn(conn, tid=5, ts="2026-05-21T10:04:00Z",
                 user="haha nice", jarvis="Glad you liked it.", route="BANTER")
    # NOT selected: short TASK reply (< threshold, no subagent/steps)
    _insert_turn(conn, tid=6, ts="2026-05-21T10:05:00Z",
                 user="ok", jarvis="Done.", route="TASK")
    # NOT selected: emotional
    _insert_turn(conn, tid=7, ts="2026-05-21T10:06:00Z",
                 user="I love you", jarvis="That's kind.", route="EMOTIONAL")
    conn.commit()
    conn.close()

    monkeypatch.setenv("JARVIS_TURN_TELEMETRY_DB", str(db))
    return db


@pytest.fixture
def skills_env(tmp_path, monkeypatch):
    """Isolate skill store + report dir at tmp paths. Returns the user
    skills root."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("JARVIS_SKILLS_PATHS", str(skills))
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("JARVIS_SKILL_REVIEW_APPLY", raising=False)
    from pipeline.skills_loader import load_skills
    load_skills()
    return skills


# ---------------------------------------------------------------------------
# 1. Candidate selection
# ---------------------------------------------------------------------------


class TestSelectCandidates:
    def test_picks_complex_turns_only(self, telemetry_db):
        from pipeline.skill_review import select_review_candidates
        cands = select_review_candidates(limit=50)
        ids = {c.turn_id for c in cands}
        # subagent / computer_use / long TASK / long REASONING in; rest out.
        assert ids == {1, 2, 3, 4}
        assert 5 not in ids and 6 not in ids and 7 not in ids

    def test_newest_first_and_limit(self, telemetry_db):
        from pipeline.skill_review import select_review_candidates
        cands = select_review_candidates(limit=2)
        assert len(cands) == 2
        # ts_utc DESC -> turns 4 then 3 (the two newest qualifying turns)
        assert [c.turn_id for c in cands] == [4, 3]

    def test_reason_strings(self, telemetry_db):
        from pipeline.skill_review import select_review_candidates
        by_id = {c.turn_id: c for c in select_review_candidates(limit=50)}
        assert by_id[1].reason == "subagent=desktop"
        assert by_id[2].reason.startswith("subagent=computer_use")
        assert by_id[3].reason.startswith("long_TASK")

    def test_missing_db_returns_empty(self, tmp_path, monkeypatch):
        from pipeline.skill_review import select_review_candidates
        monkeypatch.setenv(
            "JARVIS_TURN_TELEMETRY_DB", str(tmp_path / "nope.db")
        )
        assert select_review_candidates(limit=10) == []

    def test_zero_limit(self, telemetry_db):
        from pipeline.skill_review import select_review_candidates
        assert select_review_candidates(limit=0) == []


# ---------------------------------------------------------------------------
# 2. Parsing
# ---------------------------------------------------------------------------


class TestParse:
    def test_parses_skill_create(self):
        from pipeline.skill_review import parse_review_output
        raw = json.dumps({"proposals": [{
            "kind": "skill_create",
            "payload": {
                "name": "deploy-app",
                "description": "deploy the app to prod",
                "when_to_use": "when shipping a release",
                "body": "## Steps\n1. build\n2. push",
            },
            "rationale": "repeatable deploy flow",
        }]})
        props = parse_review_output(raw, source_turn_id=3)
        assert len(props) == 1
        assert props[0].kind == "skill_create"
        assert props[0].payload["name"] == "deploy-app"
        assert props[0].source_turn_id == 3

    def test_parses_memory_and_patch(self):
        from pipeline.skill_review import parse_review_output
        raw = json.dumps({"proposals": [
            {"kind": "memory",
             "payload": {"category": "user", "content": "Ulrich prefers terse replies."},
             "rationale": "stated preference"},
            {"kind": "skill_patch",
             "payload": {"name": "deploy-app", "old_string": "push",
                         "new_string": "push && verify"},
             "rationale": "add verify step"},
        ]})
        props = parse_review_output(raw)
        kinds = {p.kind for p in props}
        assert kinds == {"memory", "skill_patch"}

    def test_empty_and_nothing(self):
        from pipeline.skill_review import parse_review_output
        assert parse_review_output('{"proposals": []}') == []
        assert parse_review_output("NOTHING") == []
        assert parse_review_output("") == []
        assert parse_review_output(None) == []
        assert parse_review_output("not json{{{") == []

    def test_unknown_kind_dropped(self):
        from pipeline.skill_review import parse_review_output
        raw = json.dumps({"proposals": [
            {"kind": "delete_everything", "payload": {"x": 1}},
            {"kind": "memory", "payload": {"category": "user", "content": "real fact."}},
        ]})
        props = parse_review_output(raw)
        assert len(props) == 1
        assert props[0].kind == "memory"

    def test_invalid_memory_category_dropped(self):
        from pipeline.skill_review import parse_review_output
        raw = json.dumps({"proposals": [
            {"kind": "memory", "payload": {"category": "bogus", "content": "x"}},
        ]})
        assert parse_review_output(raw) == []

    def test_invalid_skill_name_dropped(self):
        from pipeline.skill_review import parse_review_output
        raw = json.dumps({"proposals": [
            {"kind": "skill_create",
             "payload": {"name": "Bad Name!", "description": "d", "body": "b"}},
        ]})
        assert parse_review_output(raw) == []


# ---------------------------------------------------------------------------
# 3. Junk / narration filter
# ---------------------------------------------------------------------------


class TestJunkFilter:
    def test_narration_memory_rejected(self):
        from pipeline.skill_review import parse_review_output
        raw = json.dumps({"proposals": [
            {"kind": "memory",
             "payload": {"category": "user",
                         "content": "The user is asking about the weather."}},
            {"kind": "memory",
             "payload": {"category": "user",
                         "content": "It seems to be a casual conversation."}},
        ]})
        assert parse_review_output(raw) == []

    def test_narration_skill_rejected(self):
        from pipeline.skill_review import parse_review_output
        raw = json.dumps({"proposals": [
            {"kind": "skill_create",
             "payload": {"name": "weather-thing",
                         "description": "The user appears to want weather info.",
                         "body": "do stuff"}},
        ]})
        assert parse_review_output(raw) == []

    def test_real_fact_passes(self):
        from pipeline.skill_review import parse_review_output
        raw = json.dumps({"proposals": [
            {"kind": "memory",
             "payload": {"category": "project",
                         "content": "Deploys go through the staging cluster first."}},
        ]})
        assert len(parse_review_output(raw)) == 1


# ---------------------------------------------------------------------------
# 4. run_review(apply=False) — propose-only, writes nothing, logs proposals
# ---------------------------------------------------------------------------


def _fake_llm_factory(per_turn_payload):
    """Return an async llm_fn that emits `per_turn_payload` (a JSON string)
    for every turn, ignoring the snapshot."""
    async def _fn(_snapshot):
        return per_turn_payload
    return _fn


class TestRunReviewProposeOnly:
    def test_propose_only_writes_nothing(self, telemetry_db, skills_env):
        from pipeline import skill_review
        # LLM proposes a skill on every reviewed turn.
        llm = _fake_llm_factory(json.dumps({"proposals": [{
            "kind": "skill_create",
            "payload": {"name": "deploy-app", "description": "deploy to prod",
                        "when_to_use": "shipping", "body": "## Steps\n1. go"},
            "rationale": "repeatable",
        }]}))

        res = asyncio.run(skill_review.run_review(limit=10, apply=False, llm_fn=llm))

        # Proposals were produced + logged, NONE applied.
        assert len(res.proposals) >= 1
        assert res.applied == []
        assert res.apply_enabled is False

        # NO skill files created in the user skills root.
        assert list(skills_env.glob("**/SKILL.md")) == []

        # Report written: run.json + SUMMARY.md exist under the run dir.
        assert res.run_dir is not None
        run_dir = Path(res.run_dir)
        assert (run_dir / "run.json").exists()
        assert (run_dir / "SUMMARY.md").exists()
        payload = json.loads((run_dir / "run.json").read_text())
        assert payload["apply_enabled"] is False
        assert payload["counts"]["proposals"] >= 1
        assert payload["counts"]["applied_ok"] == 0

    def test_apply_arg_without_env_flag_is_propose_only(self, telemetry_db, skills_env, monkeypatch):
        """Double-gate: apply=True alone (env flag unset) writes nothing."""
        from pipeline import skill_review
        monkeypatch.delenv("JARVIS_SKILL_REVIEW_APPLY", raising=False)
        llm = _fake_llm_factory(json.dumps({"proposals": [{
            "kind": "skill_create",
            "payload": {"name": "deploy-app", "description": "deploy",
                        "body": "## go"},
        }]}))
        res = asyncio.run(skill_review.run_review(limit=10, apply=True, llm_fn=llm))
        assert res.apply_enabled is False
        assert res.applied == []
        assert list(skills_env.glob("**/SKILL.md")) == []

    def test_no_proposals_still_writes_report(self, telemetry_db, skills_env):
        from pipeline import skill_review
        llm = _fake_llm_factory('{"proposals": []}')
        res = asyncio.run(skill_review.run_review(limit=10, apply=False, llm_fn=llm))
        assert res.proposals == []
        assert res.run_dir is not None
        assert (Path(res.run_dir) / "run.json").exists()


# ---------------------------------------------------------------------------
# 5–6. apply=True + env flag — applies via skills_authoring
# ---------------------------------------------------------------------------


class TestRunReviewApply:
    def test_apply_with_flag_creates_skill(self, telemetry_db, skills_env, monkeypatch):
        from pipeline import skill_review
        monkeypatch.setenv("JARVIS_SKILL_REVIEW_APPLY", "1")
        # Single qualifying turn so we get a deterministic single skill.
        monkeypatch.setenv("JARVIS_SKILL_REVIEW_LONG_REPLY_CHARS", "100000")
        llm = _fake_llm_factory(json.dumps({"proposals": [{
            "kind": "skill_create",
            "payload": {"name": "open-and-search",
                        "description": "open chrome and run a search",
                        "when_to_use": "when asked to search the web",
                        "body": "## Steps\n1. open chrome\n2. type query"},
            "rationale": "repeatable browse flow",
        }]}))

        res = asyncio.run(skill_review.run_review(limit=2, apply=True, llm_fn=llm))

        assert res.apply_enabled is True
        assert len(res.applied) >= 1
        assert any(a.ok for a in res.applied)
        # Skill file actually written into the tmp user skills root.
        assert (skills_env / "open-and-search" / "SKILL.md").exists()
        # Report records the applied result.
        payload = json.loads((Path(res.run_dir) / "run.json").read_text())
        assert payload["apply_enabled"] is True
        assert payload["counts"]["applied_ok"] >= 1

    def test_apply_memory_uses_publish_path(self, telemetry_db, skills_env, monkeypatch):
        """memory proposals apply via tools.memory publish path (mocked)."""
        from pipeline import skill_review
        monkeypatch.setenv("JARVIS_SKILL_REVIEW_APPLY", "1")
        monkeypatch.setenv("JARVIS_SKILL_REVIEW_LONG_REPLY_CHARS", "100000")

        published = []

        async def _fake_publish(event_type, payload):
            published.append((event_type, payload))

        monkeypatch.setattr(
            "tools.memory._publish_event_async", _fake_publish
        )

        llm = _fake_llm_factory(json.dumps({"proposals": [{
            "kind": "memory",
            "payload": {"category": "project",
                        "content": "Search flows open Chrome first."},
        }]}))
        res = asyncio.run(skill_review.run_review(limit=1, apply=True, llm_fn=llm))
        assert res.apply_enabled is True
        assert any(a.ok for a in res.applied)
        assert len(published) >= 1
        assert published[0][0] == "memory.value.upserted"
        assert published[0][1]["category"] == "project"

    def test_apply_proposal_unknown_kind(self):
        from pipeline.skill_review import Proposal, apply_proposal
        res = apply_proposal(Proposal(kind="bogus", payload={}))
        assert res.ok is False
        assert "unknown kind" in res.detail


# ---------------------------------------------------------------------------
# 7. review_turn with mocked llm_fn
# ---------------------------------------------------------------------------


class TestReviewTurn:
    def test_review_turn_parses_mocked_llm(self):
        from pipeline.skill_review import TurnSnapshot, review_turn
        snap = TurnSnapshot(
            turn_id=42, ts_utc="2026-05-21T00:00:00Z",
            user_text="deploy", jarvis_text="deployed",
            route="TASK", subagent="desktop", computer_use_steps=0,
        )
        llm = _fake_llm_factory(json.dumps({"proposals": [{
            "kind": "memory",
            "payload": {"category": "user", "content": "Ulrich deploys on Fridays."},
        }]}))
        props = asyncio.run(review_turn(snap, llm_fn=llm))
        assert len(props) == 1
        assert props[0].source_turn_id == 42

    def test_review_turn_llm_raises_returns_empty(self):
        from pipeline.skill_review import TurnSnapshot, review_turn
        snap = TurnSnapshot(
            turn_id=1, ts_utc="t", user_text="x", jarvis_text="y",
            route="TASK", subagent="", computer_use_steps=0,
        )

        async def _boom(_s):
            raise RuntimeError("LLM down")

        assert asyncio.run(review_turn(snap, llm_fn=_boom)) == []
