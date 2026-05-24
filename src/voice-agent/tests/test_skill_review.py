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
    confab_check_state TEXT,
    tool_call_count INTEGER DEFAULT 0,
    had_tool_error INTEGER DEFAULT 0
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

    # Selected: subagent fired (post-2026-05-24 turns are written as
    # TASK_DESKTOP / TASK_BROWSER / TASK_OTHER — match by subagent column)
    _insert_turn(conn, tid=1, ts="2026-05-21T10:00:00Z",
                 user="open chrome and search", jarvis="Chrome opened.",
                 route="TASK_DESKTOP", subagent="desktop")
    # Selected: computer_use steps
    _insert_turn(conn, tid=2, ts="2026-05-21T10:01:00Z",
                 user="click the button", jarvis="Clicked.",
                 route="TASK_DESKTOP", subagent="computer_use", cu_steps=4)
    # Selected: long TASK_* reply, no subagent (matches LIKE 'TASK_%')
    _insert_turn(conn, tid=3, ts="2026-05-21T10:02:00Z",
                 user="how do I deploy", jarvis=long_reply, route="TASK_OTHER")
    # Selected: long REASONING reply
    _insert_turn(conn, tid=4, ts="2026-05-21T10:03:00Z",
                 user="think about it", jarvis=long_reply, route="REASONING")
    # NOT selected: banter
    _insert_turn(conn, tid=5, ts="2026-05-21T10:04:00Z",
                 user="haha nice", jarvis="Glad you liked it.", route="BANTER")
    # NOT selected: short TASK_* reply (< threshold, no subagent/steps)
    _insert_turn(conn, tid=6, ts="2026-05-21T10:05:00Z",
                 user="ok", jarvis="Done.", route="TASK_OTHER")
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

    def test_apply_memory_writes_to_file_store(self, telemetry_db, skills_env, monkeypatch):
        """memory proposals apply by writing to the file-backed store
        (pipeline.file_memory). A 'project' category lands in MEMORY.md
        ('memory' target); only 'user' goes to USER.md."""
        from pipeline import skill_review, file_memory
        monkeypatch.setenv("JARVIS_SKILL_REVIEW_APPLY", "1")
        monkeypatch.setenv("JARVIS_SKILL_REVIEW_LONG_REPLY_CHARS", "100000")
        # skills_env set JARVIS_HOME to an isolated tmp dir; start clean.
        file_memory.reload_store()

        llm = _fake_llm_factory(json.dumps({"proposals": [{
            "kind": "memory",
            "payload": {"category": "project",
                        "content": "Search flows open Chrome first."},
        }]}))
        res = asyncio.run(skill_review.run_review(limit=1, apply=True, llm_fn=llm))
        assert res.apply_enabled is True
        assert any(a.ok for a in res.applied)
        # The fact landed in the 'memory' store (non-'user' category).
        entries = file_memory.read("memory")["entries"]
        assert "Search flows open Chrome first." in entries


    def test_apply_memory_user_category_writes_to_user_store(
        self, telemetry_db, skills_env, monkeypatch
    ):
        """A 'user' category proposal lands in USER.md, not MEMORY.md."""
        from pipeline import skill_review, file_memory
        monkeypatch.setenv("JARVIS_SKILL_REVIEW_APPLY", "1")
        monkeypatch.setenv("JARVIS_SKILL_REVIEW_LONG_REPLY_CHARS", "100000")
        file_memory.reload_store()

        llm = _fake_llm_factory(json.dumps({"proposals": [{
            "kind": "memory",
            "payload": {"category": "user",
                        "content": "Ulrich prefers terse replies."},
        }]}))
        res = asyncio.run(skill_review.run_review(limit=1, apply=True, llm_fn=llm))
        assert any(a.ok for a in res.applied)
        assert "Ulrich prefers terse replies." in file_memory.read("user")["entries"]
        assert "Ulrich prefers terse replies." not in file_memory.read("memory")["entries"]

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


# ---------------------------------------------------------------------------
# 8. _REVIEW_PROMPT content — enrichment assertions
#    Verify the prompt carries the anti-garbage guard, signal list,
#    skill-vs-memory guidance, and still honours the conservative contract.
# ---------------------------------------------------------------------------


class TestReviewPromptContent:
    """Structural checks on _REVIEW_PROMPT without invoking the LLM."""

    def test_conservative_bias_present(self):
        from pipeline.skill_review import _REVIEW_PROMPT
        assert "conservative" in _REVIEW_PROMPT.lower(), (
            "_REVIEW_PROMPT must retain 'Be CONSERVATIVE' framing"
        )

    def test_json_contract_present(self):
        from pipeline.skill_review import _REVIEW_PROMPT
        assert '"proposals"' in _REVIEW_PROMPT, (
            "_REVIEW_PROMPT must still demand a JSON 'proposals' key"
        )

    def test_four_proposal_kinds_present(self):
        """All four proposal kinds appear in the prompt enumeration."""
        from pipeline.skill_review import _REVIEW_PROMPT
        for kind in ("skill_create", "skill_patch", "memory", "procedure"):
            assert kind in _REVIEW_PROMPT, (
                f"_REVIEW_PROMPT must still list proposal kind '{kind}'"
            )

    def test_anti_garbage_command_not_found(self):
        from pipeline.skill_review import _REVIEW_PROMPT
        prompt_lower = _REVIEW_PROMPT.lower()
        assert "command not found" in prompt_lower, (
            "_REVIEW_PROMPT must warn against capturing 'command not found' "
            "environment failures (anti-garbage block)"
        )

    def test_anti_garbage_negative_tool_claim(self):
        from pipeline.skill_review import _REVIEW_PROMPT
        prompt_lower = _REVIEW_PROMPT.lower()
        # Any form of "negative claim" about a tool (broken/doesn't work/etc.)
        has_negative_tool = (
            "negative claim" in prompt_lower
            or "broken" in prompt_lower
            or "does not work" in prompt_lower
            or "don't work" in prompt_lower
        )
        assert has_negative_tool, (
            "_REVIEW_PROMPT must warn against capturing negative tool claims "
            "that harden into self-cited refusals (anti-garbage block)"
        )

    def test_signal_list_style_correction(self):
        from pipeline.skill_review import _REVIEW_PROMPT
        prompt_lower = _REVIEW_PROMPT.lower()
        # Signal: style/tone/format/verbosity correction from user
        has_style_signal = (
            "verbose" in prompt_lower
            or "tone" in prompt_lower
            or "style" in prompt_lower
        )
        assert has_style_signal, (
            "_REVIEW_PROMPT must name style/tone/verbosity corrections as "
            "first-class skill signals"
        )

    def test_signal_list_workflow_correction(self):
        from pipeline.skill_review import _REVIEW_PROMPT
        prompt_lower = _REVIEW_PROMPT.lower()
        has_workflow = (
            "workflow" in prompt_lower
            or "approach" in prompt_lower
            or "pitfall" in prompt_lower
        )
        assert has_workflow, (
            "_REVIEW_PROMPT must name workflow/approach corrections as signals"
        )

    def test_skill_vs_memory_guidance(self):
        from pipeline.skill_review import _REVIEW_PROMPT
        prompt_lower = _REVIEW_PROMPT.lower()
        # Skill-vs-memory: memory=who, skills=how
        has_who = "who the user is" in prompt_lower
        has_how = "how to do" in prompt_lower or "how to handle" in prompt_lower
        assert has_who and has_how, (
            "_REVIEW_PROMPT must contain skill-vs-memory guidance: "
            "memory captures who the user is; skills capture how to do a task"
        )

    def test_no_hermes_tokens(self):
        from pipeline.skill_review import _REVIEW_PROMPT
        assert "hermes" not in _REVIEW_PROMPT.lower(), (
            "_REVIEW_PROMPT must not contain any 'hermes' token"
        )


def test_review_prompt_routes_explicit_save_to_memory():
    """Track 5: explicit save phrases must route to kind=memory, not kind=skill_create."""
    from pipeline.skill_review import _REVIEW_PROMPT
    prompt_lower = _REVIEW_PROMPT.lower()
    # Explicit save phrases must steer to memory
    assert "explicit save phrases" in prompt_lower
    assert "remember this" in prompt_lower
    assert "save that" in prompt_lower
    assert "kind=memory" in prompt_lower or '"kind": "memory"' in _REVIEW_PROMPT
    # And procedure routing for named multi-step processes
    assert "kind=procedure" in prompt_lower or '"kind": "procedure"' in _REVIEW_PROMPT
    # Style/tone corrections still route to skills (preserved Hermes guidance)
    assert "style" in prompt_lower and "tone" in prompt_lower
    assert "skill_create" in prompt_lower or "skill_patch" in prompt_lower
    # Anti-garbage block preserved
    assert "command not found" in prompt_lower
    assert "negative claims" in prompt_lower
    # FORBIDDEN narration block preserved
    assert "the user is" in prompt_lower
    assert "the conversation has shifted" in prompt_lower
    # JSON-only contract intact
    assert "Output JSON ONLY" in _REVIEW_PROMPT


def test_review_prompt_no_hermes_tokens():
    """JARVIS-native — no hermes references in the prompt."""
    from pipeline.skill_review import _REVIEW_PROMPT
    assert "hermes" not in _REVIEW_PROMPT.lower()


# ---------------------------------------------------------------------------
# Track 2.5 prereq — TurnSnapshot tool_call_count + had_tool_error fields
# ---------------------------------------------------------------------------


def test_turn_snapshot_has_tool_call_fields():
    """Track 2.5 prereq: TurnSnapshot exposes tool_call_count + had_tool_error."""
    from pipeline.skill_review import TurnSnapshot
    snap = TurnSnapshot(
        turn_id=1, ts_utc="2026-05-24T00:00:00Z",
        user_text="deploy", jarvis_text="done",
        route="TASK", subagent="", computer_use_steps=0,
        tool_call_count=3, had_tool_error=False,
    )
    assert snap.tool_call_count == 3
    assert snap.had_tool_error is False


def test_turn_snapshot_defaults_back_compat():
    """Existing constructors (without the new fields) keep working."""
    from pipeline.skill_review import TurnSnapshot
    snap = TurnSnapshot(
        turn_id=1, ts_utc="2026-05-24T00:00:00Z",
        user_text="hi", jarvis_text="hello",
        route="BANTER", subagent="", computer_use_steps=0,
    )
    assert snap.tool_call_count == 0
    assert snap.had_tool_error is False


def test_select_review_candidates_populates_tool_call_fields(tmp_path, monkeypatch):
    """Track 2.5: select_review_candidates pulls tool_call_count + had_tool_error
    from the turn_telemetry.turns table."""
    import sqlite3
    db_path = tmp_path / "turn_telemetry.db"
    monkeypatch.setenv("JARVIS_TURN_TELEMETRY_DB", str(db_path))

    from pipeline import turn_telemetry
    turn_telemetry.init_db(db_path)

    # Insert a row that qualifies via the subagent criterion so the WHERE
    # clause picks it up regardless of reply length or env overrides.
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO turns (ts_utc, user_text, jarvis_text, route,
                              subagent, computer_use_steps,
                              tool_call_count, had_tool_error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("2026-05-24T00:00:00Z", "deploy the app", "Done — deployed.",
         "TASK", "terminal", 0, 3, 0),
    )
    conn.commit()
    conn.close()

    from pipeline.skill_review import select_review_candidates
    candidates = select_review_candidates(limit=10)
    assert len(candidates) >= 1
    # Find our row (jarvis_text matches "Done — deployed.")
    snap = next(c for c in candidates if c.jarvis_text.startswith("Done"))
    assert snap.tool_call_count == 3
    assert snap.had_tool_error is False
