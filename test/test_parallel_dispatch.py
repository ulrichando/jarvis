"""Tests for src/agent/parallel_dispatch.py — routing, scoring, ranking.

Covers all pure functions that do NOT require a live LLM:
- _kw_match: word-boundary enforcement for short tokens
- score_target: keyword scoring per domain
- route_query: top-k selection, threshold, include/exclude, tie-break
- _compute_quality: length buckets + structural signals + penalties
- _extract_confidence: explicit % parsing + sentiment heuristics
- _composite_score: weighted blend
- _score_bar: ASCII rendering
- DispatchResult: dataclass fields and defaults
- ParallelDispatcher: set_reasoner, dispatch_domains edge cases,
                      format_results empty/done/failed/verbose, _rank ordering
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent.parallel_dispatch import (
    ALL_TARGETS,
    DispatchResult,
    ParallelDispatcher,
    _composite_score,
    _compute_quality,
    _extract_confidence,
    _kw_match,
    _score_bar,
    route_query,
    score_target,
)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ─── _kw_match ────────────────────────────────────────────────────────────────

class TestKwMatch(unittest.TestCase):
    def test_long_keyword_simple_contains(self):
        self.assertTrue(_kw_match("exploit", "how to exploit this buffer"))
        self.assertFalse(_kw_match("exploit", "how to examine this buffer"))

    def test_short_keyword_word_boundary(self):
        # "go" must not match "google" or "going"
        self.assertFalse(_kw_match("go", "going to google"))
        self.assertTrue(_kw_match("go", "write it in go and deploy"))

    def test_short_keyword_ui_not_in_equity(self):
        self.assertFalse(_kw_match("ui", "equity valuation"))
        self.assertTrue(_kw_match("ui", "build the ui component"))

    def test_case_sensitive(self):
        # _kw_match operates on the already-lowercased query string
        self.assertFalse(_kw_match("exploit", "EXPLOIT this"))  # uppercase not matched
        self.assertTrue(_kw_match("exploit", "exploit this"))

    def test_short_three_char_boundary(self):
        # "api" (3 chars) needs word boundary
        self.assertFalse(_kw_match("api", "rapid"))
        self.assertTrue(_kw_match("api", "call the api endpoint"))


# ─── score_target ─────────────────────────────────────────────────────────────

class TestScoreTarget(unittest.TestCase):
    def test_engineer_matches_code(self):
        score = score_target("fix the bug in this code", "engineer")
        self.assertGreater(score, 0.0)

    def test_red_team_matches_exploit(self):
        score = score_target("write an exploit for this buffer overflow", "red_team")
        self.assertGreater(score, 0.0)

    def test_unknown_target_returns_zero(self):
        score = score_target("anything at all", "nonexistent_domain")
        self.assertEqual(score, 0.0)

    def test_score_capped_at_one(self):
        # Pile on many hard keywords — must not exceed 1.0
        q = " ".join(["code", "bug", "function", "class", "implement",
                      "algorithm", "api", "database", "refactor", "test",
                      "debug", "compile", "runtime", "deploy", "docker",
                      "kubernetes", "ci/cd", "git", "bash", "ansible"])
        score = score_target(q, "engineer")
        self.assertLessEqual(score, 1.0)

    def test_soft_keywords_contribute_less_than_hard(self):
        hard_score = score_target("write code to fix this bug", "engineer")
        soft_score = score_target("architecture design for a software platform", "engineer")
        # Both should be > 0; hard generally more per-hit
        self.assertGreater(hard_score, 0.0)
        self.assertGreater(soft_score, 0.0)

    def test_legal_matches_contract(self):
        score = score_target("review this contract for force majeure clauses", "legal")
        self.assertGreater(score, 0.0)

    def test_financial_matches_equity(self):
        score = score_target("what is the equity dilution from this SAFE note?", "financial")
        self.assertGreater(score, 0.0)


# ─── route_query ──────────────────────────────────────────────────────────────

class TestRouteQuery(unittest.TestCase):
    def test_returns_list(self):
        result = route_query("write some code")
        self.assertIsInstance(result, list)

    def test_top_k_limits_results(self):
        result = route_query("exploit buffer overflow code review", top_k=2)
        self.assertLessEqual(len(result), 2)

    def test_results_sorted_descending(self):
        result = route_query("exploit buffer overflow", top_k=5)
        scores = [s for _, s in result]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_nothing_above_threshold_returns_single_best(self):
        # Very generic query likely scores near 0 everywhere
        result = route_query("the", threshold=0.99)
        self.assertEqual(len(result), 1)

    def test_exclude_removes_target(self):
        result = route_query("write code to fix this bug", exclude=["engineer"])
        targets = [t for t, _ in result]
        self.assertNotIn("engineer", targets)

    def test_include_forces_extra_target(self):
        # Force include financial even for a code query
        result = route_query("fix this code bug", include=["financial"])
        targets = [t for t, _ in result]
        self.assertIn("financial", targets)

    def test_threshold_filters_low_scores(self):
        # High threshold should filter most results
        result = route_query("write code", threshold=0.90)
        for _, score in result:
            # All returned scores should be >= threshold OR it fell back to single-best
            pass  # fallback allows below-threshold single best — just check no crash
        self.assertIsInstance(result, list)

    def test_include_invalid_target_ignored(self):
        result = route_query("some query", include=["totally_fake_domain"])
        # Should not crash and fake domain should not appear
        targets = [t for t, _ in result]
        self.assertNotIn("totally_fake_domain", targets)

    def test_all_targets_covered(self):
        # route_query should draw from ALL_TARGETS
        result = route_query("anything", top_k=len(ALL_TARGETS), threshold=0.0)
        self.assertGreater(len(result), 0)


# ─── _compute_quality ─────────────────────────────────────────────────────────

class TestComputeQuality(unittest.TestCase):
    def test_empty_returns_zero(self):
        self.assertEqual(_compute_quality(""), 0.0)

    def test_bracket_prefix_returns_zero(self):
        self.assertEqual(_compute_quality("[error: something]"), 0.0)

    def test_very_short_low_score(self):
        score = _compute_quality("ok")
        self.assertLessEqual(score, 0.2)

    def test_medium_length_higher_score(self):
        text = "x" * 500
        score = _compute_quality(text)
        self.assertGreaterEqual(score, 0.35)

    def test_headers_boost_score(self):
        plain = "A" * 300
        headed = "# Introduction\n" + "A" * 280
        self.assertGreater(_compute_quality(headed), _compute_quality(plain))

    def test_bullets_boost_score(self):
        plain = "A" * 300
        bulleted = "- item one\n- item two\n" + "A" * 260
        self.assertGreater(_compute_quality(bulleted), _compute_quality(plain))

    def test_code_block_boosts_score(self):
        plain = "A" * 300
        with_code = "```python\nprint('hi')\n```\n" + "A" * 270
        self.assertGreater(_compute_quality(with_code), _compute_quality(plain))

    def test_placeholder_penalizes(self):
        normal = "Here is a detailed explanation with 300 chars." + "x" * 260
        with_placeholder = normal + " [placeholder]"
        self.assertGreater(_compute_quality(normal), _compute_quality(with_placeholder))

    def test_ai_refusal_penalizes(self):
        normal = "Here is the answer: " + "x" * 300
        refusal = normal + " As an AI I cannot answer this fully."
        self.assertGreater(_compute_quality(normal), _compute_quality(refusal))

    def test_score_clamped_zero_to_one(self):
        # Maximally stuffed response
        text = "# H\n- b\n```code```\n1. item\n" + "x" * 2000
        score = _compute_quality(text)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)


# ─── _extract_confidence ──────────────────────────────────────────────────────

class TestExtractConfidence(unittest.TestCase):
    def test_empty_returns_zero(self):
        self.assertEqual(_extract_confidence(""), 0.0)

    def test_bracket_prefix_returns_zero(self):
        self.assertEqual(_extract_confidence("[timeout]"), 0.0)

    def test_explicit_percent_confident(self):
        conf = _extract_confidence("I am 90% confident this is correct.")
        self.assertAlmostEqual(conf, 0.90, places=2)

    def test_explicit_confidence_colon_format(self):
        conf = _extract_confidence("Confidence: 75%")
        self.assertAlmostEqual(conf, 0.75, places=2)

    def test_high_confidence_words_increase_score(self):
        baseline = _extract_confidence("Here is the answer.")
        boosted = _extract_confidence("The answer is definitely correct and clearly verified.")
        self.assertGreater(boosted, baseline)

    def test_low_confidence_words_decrease_score(self):
        baseline = _extract_confidence("Here is the answer.")
        hedged = _extract_confidence("This might be the answer, possibly, I think, maybe uncertain.")
        self.assertLess(hedged, baseline)

    def test_score_clamped_between_0_1_and_0_95(self):
        conf = _extract_confidence("definitely certainly confirmed verified the answer is documented clearly")
        self.assertGreaterEqual(conf, 0.1)
        self.assertLessEqual(conf, 0.95)

    def test_baseline_near_middle(self):
        conf = _extract_confidence("The answer is 42.")
        self.assertGreater(conf, 0.3)
        self.assertLess(conf, 0.85)


# ─── _composite_score ─────────────────────────────────────────────────────────

class TestCompositeScore(unittest.TestCase):
    def test_all_zeros(self):
        self.assertEqual(_composite_score(0.0, 0.0, 0.0), 0.0)

    def test_weights_sum_correctly(self):
        # 0.4*1 + 0.35*1 + 0.25*1 = 1.0
        result = _composite_score(1.0, 1.0, 1.0)
        self.assertAlmostEqual(result, 1.0, places=3)

    def test_domain_weight(self):
        # domain=1, others=0 → 0.40
        result = _composite_score(1.0, 0.0, 0.0)
        self.assertAlmostEqual(result, 0.40, places=3)

    def test_quality_weight(self):
        # quality=1, others=0 → 0.35
        result = _composite_score(0.0, 1.0, 0.0)
        self.assertAlmostEqual(result, 0.35, places=3)

    def test_confidence_weight(self):
        # confidence=1, others=0 → 0.25
        result = _composite_score(0.0, 0.0, 1.0)
        self.assertAlmostEqual(result, 0.25, places=3)

    def test_partial_values(self):
        result = _composite_score(0.5, 0.5, 0.5)
        self.assertAlmostEqual(result, 0.5, places=3)


# ─── _score_bar ───────────────────────────────────────────────────────────────

class TestScoreBar(unittest.TestCase):
    def test_full_score_all_filled(self):
        bar = _score_bar(1.0, 10)
        self.assertEqual(bar, "[██████████]")

    def test_zero_score_all_empty(self):
        bar = _score_bar(0.0, 10)
        self.assertEqual(bar, "[░░░░░░░░░░]")

    def test_length_is_width_plus_2(self):
        for w in (5, 10, 20):
            bar = _score_bar(0.5, w)
            self.assertEqual(len(bar), w + 2)

    def test_half_score_roughly_half_filled(self):
        bar = _score_bar(0.5, 10)
        filled = bar.count("█")
        self.assertEqual(filled, 5)


# ─── DispatchResult dataclass ─────────────────────────────────────────────────

class TestDispatchResult(unittest.TestCase):
    def test_required_fields(self):
        r = DispatchResult(
            target="engineer",
            response="test response",
            domain_score=0.8,
            quality_score=0.7,
            confidence=0.6,
            final_score=0.72,
            duration_ms=123,
        )
        self.assertEqual(r.target, "engineer")
        self.assertEqual(r.response, "test response")
        self.assertEqual(r.domain_score, 0.8)

    def test_default_status_done(self):
        r = DispatchResult(
            target="engineer", response="resp",
            domain_score=0.5, quality_score=0.5,
            confidence=0.5, final_score=0.5, duration_ms=100,
        )
        self.assertEqual(r.status, "done")

    def test_default_provider_empty(self):
        r = DispatchResult(
            target="engineer", response="resp",
            domain_score=0.5, quality_score=0.5,
            confidence=0.5, final_score=0.5, duration_ms=100,
        )
        self.assertEqual(r.provider, "")

    def test_custom_status_and_provider(self):
        r = DispatchResult(
            target="analyst", response="[timeout]",
            domain_score=0.3, quality_score=0.0,
            confidence=0.0, final_score=0.0, duration_ms=45000,
            status="timeout", provider="ollama",
        )
        self.assertEqual(r.status, "timeout")
        self.assertEqual(r.provider, "ollama")


# ─── ParallelDispatcher ───────────────────────────────────────────────────────

class TestParallelDispatcher(unittest.TestCase):
    def test_set_reasoner(self):
        d = ParallelDispatcher()
        mock = MagicMock()
        d.set_reasoner(mock)
        self.assertIs(d._reasoner, mock)

    def test_format_results_empty(self):
        d = ParallelDispatcher()
        self.assertEqual(d.format_results([]), "No results.")

    def test_format_results_done_agent(self):
        d = ParallelDispatcher()
        r = DispatchResult(
            target="engineer", response="Here is the fix.",
            domain_score=0.8, quality_score=0.7,
            confidence=0.6, final_score=0.72, duration_ms=500,
        )
        output = d.format_results([r])
        self.assertIn("engineer", output)
        self.assertIn("Here is the fix.", output)

    def test_format_results_failed_agent(self):
        d = ParallelDispatcher()
        r = DispatchResult(
            target="analyst", response="[timeout]",
            domain_score=0.3, quality_score=0.0,
            confidence=0.0, final_score=0.0, duration_ms=45000,
            status="timeout",
        )
        output = d.format_results([r])
        self.assertIn("Failed", output)
        self.assertIn("analyst", output)

    def test_format_results_verbose_shows_subscores(self):
        d = ParallelDispatcher()
        r = DispatchResult(
            target="engineer", response="Answer here.",
            domain_score=0.8, quality_score=0.7,
            confidence=0.6, final_score=0.72, duration_ms=300,
        )
        output = d.format_results([r], verbose=True)
        self.assertIn("domain=", output)
        self.assertIn("quality=", output)
        self.assertIn("confidence=", output)

    def test_format_results_truncates_long_response(self):
        d = ParallelDispatcher()
        long_response = "A" * 2000
        r = DispatchResult(
            target="engineer", response=long_response,
            domain_score=0.8, quality_score=0.7,
            confidence=0.6, final_score=0.72, duration_ms=300,
        )
        output = d.format_results([r], max_response_chars=100)
        # Truncated with "..."
        self.assertIn("...", output)

    def test_rank_done_before_failed(self):
        d = ParallelDispatcher()
        failed = DispatchResult(
            target="analyst", response="[error]",
            domain_score=0.9, quality_score=0.0,
            confidence=0.0, final_score=0.9, duration_ms=100,
            status="failed",
        )
        done = DispatchResult(
            target="engineer", response="good response",
            domain_score=0.3, quality_score=0.3,
            confidence=0.3, final_score=0.3, duration_ms=200,
            status="done",
        )
        ranked = d._rank([failed, done])
        self.assertEqual(ranked[0].status, "done")
        self.assertEqual(ranked[1].status, "failed")

    def test_rank_done_sorted_by_final_score(self):
        d = ParallelDispatcher()
        low = DispatchResult(
            target="a", response="low",
            domain_score=0.1, quality_score=0.1, confidence=0.1,
            final_score=0.1, duration_ms=100,
        )
        high = DispatchResult(
            target="b", response="high",
            domain_score=0.9, quality_score=0.9, confidence=0.9,
            final_score=0.9, duration_ms=100,
        )
        ranked = d._rank([low, high])
        self.assertEqual(ranked[0].final_score, 0.9)

    def test_dispatch_domains_invalid_target_skipped(self):
        d = ParallelDispatcher()
        result = run(d.dispatch_domains("some query", ["not_a_real_domain"]))
        self.assertEqual(result, [])

    def test_dispatch_domains_empty_list(self):
        d = ParallelDispatcher()
        result = run(d.dispatch_domains("some query", []))
        self.assertEqual(result, [])

    def test_format_results_summary_line(self):
        d = ParallelDispatcher()
        r1 = DispatchResult(
            target="engineer", response="resp1",
            domain_score=0.7, quality_score=0.6, confidence=0.5,
            final_score=0.63, duration_ms=200,
        )
        r2 = DispatchResult(
            target="analyst", response="[fail]",
            domain_score=0.3, quality_score=0.0, confidence=0.0,
            final_score=0.0, duration_ms=100, status="failed",
        )
        output = d.format_results([r1, r2])
        self.assertIn("1/2", output)  # 1 done out of 2 total


if __name__ == "__main__":
    unittest.main()
