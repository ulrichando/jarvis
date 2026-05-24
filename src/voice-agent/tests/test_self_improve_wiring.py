"""Tests for the AUTONOMOUS self-improvement wiring.

The voice worker fires the self-improvement loop OFF the turn boundary
(fire-and-forget asyncio tasks, never awaited inline), mirroring the way
the memory-extractor already fires. This suite proves the wiring contract
WITHOUT a real LLM or real writes — the review/curator engines are mocked.

Proves:
  1. ``is_hard_turn`` mirrors ``select_review_candidates`` selection:
     a subagent fired / computer_use ran steps / a long TASK|REASONING
     reply is "hard"; banter / short / emotional is NOT.
  2. ``autonomous_review_turn`` reviews + AUTO-APPLIES validated proposals
     by default (the loop auto-writes) — apply runs without
     ``JARVIS_SKILL_REVIEW_APPLY``.
  3. The kill-switch ``JARVIS_SELF_IMPROVE_DISABLED=1`` makes the
     autonomous entrypoint a no-op (no review, no apply).
  4. A raised exception inside the review NEVER propagates out of
     ``autonomous_review_turn`` (the turn handler must never break).
  5. The turn-boundary fire helper ``fire_self_improvement`` schedules the
     review + curator as BACKGROUND tasks on a hard turn, does NOT fire on
     a banter turn, and is fully no-op'd by the kill-switch.

Isolation: the aux-LLM is injected via the ``llm_fn`` seam; ``apply_proposal``
is monkeypatched so NO skill/memory write touches disk. NO network.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


# ---------------------------------------------------------------------------
# 1. Hard-turn gate — mirrors select_review_candidates' criterion
# ---------------------------------------------------------------------------


class TestIsHardTurn:
    def test_subagent_is_hard(self):
        from pipeline.skill_review import TurnSnapshot, is_hard_turn
        snap = TurnSnapshot(
            turn_id=1, ts_utc="t", user_text="open chrome",
            jarvis_text="Done.", route="TASK_OTHER", subagent="desktop",
            computer_use_steps=0,
        )
        assert is_hard_turn(snap) is True

    def test_computer_use_steps_is_hard(self):
        from pipeline.skill_review import TurnSnapshot, is_hard_turn
        snap = TurnSnapshot(
            turn_id=2, ts_utc="t", user_text="click", jarvis_text="Clicked.",
            route="TASK_OTHER", subagent="", computer_use_steps=4,
        )
        assert is_hard_turn(snap) is True

    def test_long_task_reply_is_hard(self):
        from pipeline.skill_review import TurnSnapshot, is_hard_turn
        snap = TurnSnapshot(
            turn_id=3, ts_utc="t", user_text="how do I deploy",
            jarvis_text="step. " * 100, route="TASK_OTHER", subagent="",
            computer_use_steps=0,
        )
        assert is_hard_turn(snap) is True

    def test_long_reasoning_reply_is_hard(self):
        from pipeline.skill_review import TurnSnapshot, is_hard_turn
        snap = TurnSnapshot(
            turn_id=4, ts_utc="t", user_text="think", jarvis_text="x" * 600,
            route="REASONING", subagent="", computer_use_steps=0,
        )
        assert is_hard_turn(snap) is True

    def test_banter_not_hard(self):
        from pipeline.skill_review import TurnSnapshot, is_hard_turn
        snap = TurnSnapshot(
            turn_id=5, ts_utc="t", user_text="haha nice",
            jarvis_text="Glad you liked it.", route="BANTER", subagent="",
            computer_use_steps=0,
        )
        assert is_hard_turn(snap) is False

    def test_short_task_non_claim_not_hard(self):
        """Short TASK reply with no completion claim → not hard.

        Updated 2026-05-24: previously this used jarvis_text="Done." but
        is_hard_turn now flags short TASK/REASONING replies that match a
        strong completion claim (e.g. "Done.") with zero tool calls — that
        is the confab signature the new branch catches. Test now uses a
        neutral short reply that doesn't trip the claim regex."""
        from pipeline.skill_review import TurnSnapshot, is_hard_turn
        snap = TurnSnapshot(
            turn_id=6, ts_utc="t", user_text="ok",
            jarvis_text="Sure, let me know.",
            route="TASK_OTHER", subagent="", computer_use_steps=0,
        )
        assert is_hard_turn(snap) is False

    def test_emotional_not_hard(self):
        from pipeline.skill_review import TurnSnapshot, is_hard_turn
        snap = TurnSnapshot(
            turn_id=7, ts_utc="t", user_text="I love you",
            jarvis_text="That's kind.", route="EMOTIONAL", subagent="",
            computer_use_steps=0,
        )
        assert is_hard_turn(snap) is False


# ---------------------------------------------------------------------------
# 2. autonomous_review_turn — reviews + AUTO-APPLIES by default
# ---------------------------------------------------------------------------


def _hard_snapshot():
    from pipeline.skill_review import TurnSnapshot
    return TurnSnapshot(
        turn_id=42, ts_utc="2026-05-21T10:00:00Z",
        user_text="run the deploy sequence",
        jarvis_text="Ran make build, make test, make deploy.",
        route="TASK_OTHER", subagent="desktop", computer_use_steps=0,
    )


def _one_skill_llm_payload():
    return (
        '{"proposals": [{"kind": "skill_create", "payload": '
        '{"name": "deploy-sequence", "description": "Deploy the app", '
        '"when_to_use": "when asked to deploy", '
        '"body": "## Steps\\n1. make build\\n2. make test\\n3. make deploy"}, '
        '"rationale": "repeatable deploy procedure"}]}'
    )


class TestAutonomousReviewTurn:
    def test_auto_applies_by_default_without_env_flag(self, monkeypatch):
        """Validated proposals are APPLIED on the live autonomous path
        WITHOUT JARVIS_SKILL_REVIEW_APPLY=1 (auto-write by default)."""
        import pipeline.skill_review as sr

        monkeypatch.delenv("JARVIS_SKILL_REVIEW_APPLY", raising=False)
        monkeypatch.delenv("JARVIS_SELF_IMPROVE_DISABLED", raising=False)

        applied = []
        monkeypatch.setattr(
            sr, "apply_proposal",
            lambda p: applied.append(p) or sr.ApplyResult(proposal=p, ok=True, detail="ok"),
        )

        async def fake_llm(snapshot):
            return _one_skill_llm_payload()

        results = asyncio.run(
            sr.autonomous_review_turn(_hard_snapshot(), llm_fn=fake_llm)
        )
        assert len(applied) == 1
        assert applied[0].kind == "skill_create"
        assert results and results[0].ok

    def test_killswitch_makes_it_a_noop(self, monkeypatch):
        import pipeline.skill_review as sr

        monkeypatch.setenv("JARVIS_SELF_IMPROVE_DISABLED", "1")
        applied = []
        monkeypatch.setattr(
            sr, "apply_proposal",
            lambda p: applied.append(p) or sr.ApplyResult(proposal=p, ok=True),
        )
        llm_calls = []

        async def fake_llm(snapshot):
            llm_calls.append(snapshot)
            return _one_skill_llm_payload()

        results = asyncio.run(
            sr.autonomous_review_turn(_hard_snapshot(), llm_fn=fake_llm)
        )
        assert results == []
        assert applied == []     # nothing applied
        assert llm_calls == []   # LLM never even called

    def test_review_exception_does_not_propagate(self, monkeypatch):
        """A raised exception inside the review must be swallowed so it can
        never break the turn handler."""
        import pipeline.skill_review as sr

        monkeypatch.delenv("JARVIS_SELF_IMPROVE_DISABLED", raising=False)

        async def boom(snapshot):
            raise RuntimeError("LLM exploded")

        # Must NOT raise.
        results = asyncio.run(
            sr.autonomous_review_turn(_hard_snapshot(), llm_fn=boom)
        )
        assert results == []

    def test_junk_proposal_filtered_before_apply(self, monkeypatch):
        """The meta-paraphrase junk filter still gates the autonomous path:
        a narration-shaped memory proposal is dropped, never applied."""
        import pipeline.skill_review as sr

        monkeypatch.delenv("JARVIS_SKILL_REVIEW_APPLY", raising=False)
        monkeypatch.delenv("JARVIS_SELF_IMPROVE_DISABLED", raising=False)

        applied = []
        monkeypatch.setattr(
            sr, "apply_proposal",
            lambda p: applied.append(p) or sr.ApplyResult(proposal=p, ok=True),
        )

        async def junk_llm(snapshot):
            return (
                '{"proposals": [{"kind": "memory", "payload": '
                '{"category": "user", "content": "The user is asking about deploys"}, '
                '"rationale": "narration"}]}'
            )

        results = asyncio.run(
            sr.autonomous_review_turn(_hard_snapshot(), llm_fn=junk_llm)
        )
        assert results == []
        assert applied == []


# ---------------------------------------------------------------------------
# 3. fire_self_improvement — the turn-boundary fire helper
# ---------------------------------------------------------------------------


class TestFireSelfImprovement:
    def test_fires_review_and_curator_as_tasks_on_hard_turn(self, monkeypatch):
        import pipeline.skill_review as sr

        monkeypatch.delenv("JARVIS_SELF_IMPROVE_DISABLED", raising=False)

        review_calls = []
        curator_calls = []

        async def fake_review(snapshot):
            review_calls.append(snapshot)
            return []

        def fake_maybe_curate():
            curator_calls.append(True)
            return None

        monkeypatch.setattr(sr, "autonomous_review_turn", fake_review)
        monkeypatch.setattr(
            "pipeline.curator.maybe_run_curation", fake_maybe_curate
        )

        async def drive():
            tasks = sr.fire_self_improvement(_hard_snapshot())
            # Deterministically await the scheduled fire-and-forget tasks.
            await asyncio.gather(*tasks)

        asyncio.run(drive())
        assert len(review_calls) == 1
        assert len(curator_calls) == 1

    def test_does_not_fire_review_on_banter_turn(self, monkeypatch):
        import pipeline.skill_review as sr

        monkeypatch.delenv("JARVIS_SELF_IMPROVE_DISABLED", raising=False)

        review_calls = []
        curator_calls = []

        async def fake_review(snapshot):
            review_calls.append(snapshot)
            return []

        def fake_maybe_curate():
            curator_calls.append(True)
            return None

        monkeypatch.setattr(sr, "autonomous_review_turn", fake_review)
        monkeypatch.setattr(
            "pipeline.curator.maybe_run_curation", fake_maybe_curate
        )

        from pipeline.skill_review import TurnSnapshot
        banter = TurnSnapshot(
            turn_id=9, ts_utc="t", user_text="haha", jarvis_text="Heh.",
            route="BANTER", subagent="", computer_use_steps=0,
        )

        async def drive():
            tasks = sr.fire_self_improvement(banter)
            await asyncio.gather(*tasks)

        asyncio.run(drive())
        assert review_calls == []          # banter → no review
        # Curator is interval-gated and turn-content-agnostic; it still gets
        # a chance to self-gate (it returns None unless due). It must NOT be
        # suppressed just because the turn was banter.
        assert len(curator_calls) == 1

    def test_killswitch_suppresses_both_fires(self, monkeypatch):
        import pipeline.skill_review as sr

        monkeypatch.setenv("JARVIS_SELF_IMPROVE_DISABLED", "1")

        review_calls = []
        curator_calls = []

        async def fake_review(snapshot):
            review_calls.append(snapshot)
            return []

        def fake_maybe_curate():
            curator_calls.append(True)
            return None

        monkeypatch.setattr(sr, "autonomous_review_turn", fake_review)
        monkeypatch.setattr(
            "pipeline.curator.maybe_run_curation", fake_maybe_curate
        )

        async def drive():
            tasks = sr.fire_self_improvement(_hard_snapshot())
            await asyncio.gather(*tasks)

        asyncio.run(drive())
        assert review_calls == []
        assert curator_calls == []

    def test_fire_never_raises_with_no_running_loop(self, monkeypatch):
        """Called outside an event loop (defensive), the fire helper must
        swallow the 'no running loop' error and not raise."""
        import pipeline.skill_review as sr

        monkeypatch.delenv("JARVIS_SELF_IMPROVE_DISABLED", raising=False)
        monkeypatch.setattr(
            "pipeline.curator.maybe_run_curation", lambda: None
        )
        # No asyncio.run wrapper → no running loop. Must not raise.
        sr.fire_self_improvement(_hard_snapshot())
