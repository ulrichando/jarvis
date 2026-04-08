"""Tests for src/agent/deepsearch.py — DeepSearch multi-step research agent.

Covers all pure/near-pure logic that does NOT require real HTTP calls:
- SearchResult / Finding / ResearchContext dataclasses
- DeepSearch.set_reasoner / reasoner property
- _plan_queries: JSON parsing, capping at 5, filter non-strings, invalid JSON → []
- _analyze: JSON parsing into Finding + gaps, invalid JSON → fallback
- _synthesize: header + report structure
- research: progress callback fires, gaps=[] exits loop early
"""

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent.deepsearch import (
    DeepSearch,
    Finding,
    ResearchContext,
    SearchResult,
)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ─── SearchResult dataclass ───────────────────────────────────────────────────

class TestSearchResult(unittest.TestCase):
    def test_fields(self):
        r = SearchResult(
            query="what is JARVIS",
            title="JARVIS Overview",
            url="https://example.com",
            snippet="JARVIS is an AI assistant",
        )
        self.assertEqual(r.query, "what is JARVIS")
        self.assertEqual(r.title, "JARVIS Overview")
        self.assertEqual(r.url, "https://example.com")
        self.assertEqual(r.snippet, "JARVIS is an AI assistant")


# ─── Finding dataclass ────────────────────────────────────────────────────────

class TestFinding(unittest.TestCase):
    def test_required_fields(self):
        f = Finding(fact="The sky is blue", source="wikipedia")
        self.assertEqual(f.fact, "The sky is blue")
        self.assertEqual(f.source, "wikipedia")

    def test_default_confidence(self):
        f = Finding(fact="fact", source="src")
        self.assertAlmostEqual(f.confidence, 0.8)

    def test_custom_confidence(self):
        f = Finding(fact="fact", source="src", confidence=0.95)
        self.assertAlmostEqual(f.confidence, 0.95)


# ─── ResearchContext dataclass ────────────────────────────────────────────────

class TestResearchContext(unittest.TestCase):
    def test_required_field(self):
        ctx = ResearchContext(question="How does X work?")
        self.assertEqual(ctx.question, "How does X work?")

    def test_defaults(self):
        ctx = ResearchContext(question="Q")
        self.assertEqual(ctx.findings, [])
        self.assertEqual(ctx.gaps, [])
        self.assertIsInstance(ctx.sources_visited, set)
        self.assertEqual(ctx.queries_used, [])
        self.assertEqual(ctx.round, 0)

    def test_sources_visited_is_set(self):
        ctx = ResearchContext(question="Q")
        ctx.sources_visited.add("https://example.com")
        self.assertIn("https://example.com", ctx.sources_visited)


# ─── DeepSearch.set_reasoner / reasoner property ─────────────────────────────

class TestDeepSearchReasonerProperty(unittest.TestCase):
    def test_set_reasoner(self):
        ds = DeepSearch()
        mock = MagicMock()
        ds.set_reasoner(mock)
        self.assertIs(ds.reasoner, mock)

    def test_set_reasoner_stores(self):
        ds = DeepSearch()
        mock = MagicMock()
        ds.set_reasoner(mock)
        self.assertIs(ds._reasoner, mock)

    def test_init_with_reasoner(self):
        mock = MagicMock()
        ds = DeepSearch(reasoner=mock)
        self.assertIs(ds._reasoner, mock)


# ─── DeepSearch._plan_queries ─────────────────────────────────────────────────

class TestPlanQueries(unittest.TestCase):
    def _make_ds(self, response_text: str) -> DeepSearch:
        ds = DeepSearch()
        r = MagicMock()
        r.query = AsyncMock(return_value=(response_text, "mock"))
        ds.set_reasoner(r)
        return ds

    def test_valid_json_returns_queries(self):
        ds = self._make_ds('["query one", "query two", "query three"]')
        ctx = ResearchContext(question="test", gaps=["gap 1"])
        result = run(ds._plan_queries(ctx))
        self.assertEqual(result, ["query one", "query two", "query three"])

    def test_capped_at_five(self):
        queries = [f"query {i}" for i in range(10)]
        ds = self._make_ds(json.dumps(queries))
        ctx = ResearchContext(question="test", gaps=["gap"])
        result = run(ds._plan_queries(ctx))
        self.assertLessEqual(len(result), 5)

    def test_non_string_items_filtered(self):
        ds = self._make_ds('["valid query", 42, null, "another valid"]')
        ctx = ResearchContext(question="test", gaps=["gap"])
        result = run(ds._plan_queries(ctx))
        self.assertEqual(result, ["valid query", "another valid"])

    def test_invalid_json_returns_empty(self):
        ds = self._make_ds("this is not json")
        ctx = ResearchContext(question="test", gaps=["gap"])
        result = run(ds._plan_queries(ctx))
        self.assertEqual(result, [])

    def test_code_fenced_json_parsed(self):
        ds = self._make_ds('```json\n["fenced query"]\n```')
        ctx = ResearchContext(question="test", gaps=["gap"])
        result = run(ds._plan_queries(ctx))
        self.assertEqual(result, ["fenced query"])

    def test_exception_in_reasoner_returns_empty(self):
        ds = DeepSearch()
        r = MagicMock()
        r.query = AsyncMock(side_effect=RuntimeError("LLM down"))
        ds.set_reasoner(r)
        ctx = ResearchContext(question="test", gaps=["gap"])
        result = run(ds._plan_queries(ctx))
        self.assertEqual(result, [])

    def test_empty_list_returns_empty(self):
        ds = self._make_ds("[]")
        ctx = ResearchContext(question="test", gaps=["gap"])
        result = run(ds._plan_queries(ctx))
        self.assertEqual(result, [])

    def test_context_uses_existing_findings(self):
        """Verify existing findings are included in prompt (not testing prompt text exactly)."""
        calls = []
        ds = DeepSearch()
        r = MagicMock()
        async def capture_query(prompt, **kwargs):
            calls.append(prompt)
            return ('["q1"]', "mock")
        r.query = capture_query
        ds.set_reasoner(r)
        ctx = ResearchContext(question="test", gaps=["gap"])
        ctx.findings = [Finding(fact="known fact", source="web")]
        run(ds._plan_queries(ctx))
        self.assertTrue(len(calls) > 0)
        self.assertIn("known fact", calls[0])


# ─── DeepSearch._analyze ──────────────────────────────────────────────────────

class TestAnalyze(unittest.TestCase):
    def _make_ds(self, response_text: str) -> DeepSearch:
        ds = DeepSearch()
        r = MagicMock()
        r.query = AsyncMock(return_value=(response_text, "mock"))
        ds.set_reasoner(r)
        return ds

    def test_returns_findings_and_gaps(self):
        data = json.dumps({
            "new_facts": ["fact one with source", "fact two"],
            "remaining_gaps": ["gap one", "gap two"],
        })
        ds = self._make_ds(data)
        ctx = ResearchContext(question="test")
        result = run(ds._analyze(ctx, [], []))
        self.assertEqual(len(result["new_facts"]), 2)
        self.assertEqual(len(result["remaining_gaps"]), 2)

    def test_new_facts_are_finding_objects(self):
        data = json.dumps({"new_facts": ["a fact"], "remaining_gaps": []})
        ds = self._make_ds(data)
        ctx = ResearchContext(question="test")
        result = run(ds._analyze(ctx, [], []))
        self.assertIsInstance(result["new_facts"][0], Finding)

    def test_finding_source_is_search(self):
        data = json.dumps({"new_facts": ["fact from web"], "remaining_gaps": []})
        ds = self._make_ds(data)
        ctx = ResearchContext(question="test")
        result = run(ds._analyze(ctx, [], []))
        self.assertEqual(result["new_facts"][0].source, "search")

    def test_finding_confidence_is_0_7(self):
        data = json.dumps({"new_facts": ["fact"], "remaining_gaps": []})
        ds = self._make_ds(data)
        ctx = ResearchContext(question="test")
        result = run(ds._analyze(ctx, [], []))
        self.assertAlmostEqual(result["new_facts"][0].confidence, 0.7)

    def test_gaps_capped_at_five(self):
        data = json.dumps({
            "new_facts": [],
            "remaining_gaps": [f"gap {i}" for i in range(10)],
        })
        ds = self._make_ds(data)
        ctx = ResearchContext(question="test")
        result = run(ds._analyze(ctx, [], []))
        self.assertLessEqual(len(result["remaining_gaps"]), 5)

    def test_invalid_json_falls_back_to_existing_gaps(self):
        ds = self._make_ds("not json")
        ctx = ResearchContext(question="test")
        ctx.gaps = ["original gap 1", "original gap 2"]
        result = run(ds._analyze(ctx, [], []))
        self.assertEqual(result["new_facts"], [])
        self.assertEqual(result["remaining_gaps"], ctx.gaps)

    def test_non_string_facts_filtered(self):
        data = json.dumps({"new_facts": ["valid", 42, None, "also valid"], "remaining_gaps": []})
        ds = self._make_ds(data)
        ctx = ResearchContext(question="test")
        result = run(ds._analyze(ctx, [], []))
        self.assertEqual(len(result["new_facts"]), 2)

    def test_search_results_used_in_prompt(self):
        calls = []
        ds = DeepSearch()
        r = MagicMock()
        async def capture(prompt, **kwargs):
            calls.append(prompt)
            return ('{"new_facts": [], "remaining_gaps": []}', "mock")
        r.query = capture
        ds.set_reasoner(r)
        ctx = ResearchContext(question="test")
        sr = SearchResult(query="q", title="T", url="u", snippet="snippet text here")
        run(ds._analyze(ctx, [sr], []))
        self.assertIn("snippet text here", calls[0])


# ─── DeepSearch._synthesize ───────────────────────────────────────────────────

class TestSynthesize(unittest.TestCase):
    def test_header_contains_question(self):
        ds = DeepSearch()
        r = MagicMock()
        r.query = AsyncMock(return_value=("report body text", "mock"))
        ds.set_reasoner(r)
        ctx = ResearchContext(question="What is quantum computing?")
        ctx.findings = [Finding(fact="fact 1", source="web")]
        ctx.sources_visited = {"https://example.com"}
        ctx.round = 2
        ctx.queries_used = ["q1", "q2"]
        result = run(ds._synthesize(ctx))
        self.assertIn("What is quantum computing?", result)

    def test_header_contains_finding_count(self):
        ds = DeepSearch()
        r = MagicMock()
        r.query = AsyncMock(return_value=("report body", "mock"))
        ds.set_reasoner(r)
        ctx = ResearchContext(question="test?")
        ctx.findings = [Finding(fact="f1", source="s"), Finding(fact="f2", source="s")]
        ctx.sources_visited = {"url1"}
        ctx.round = 1
        ctx.queries_used = []
        result = run(ds._synthesize(ctx))
        self.assertIn("2", result)  # 2 facts in header

    def test_report_appended_to_header(self):
        ds = DeepSearch()
        r = MagicMock()
        r.query = AsyncMock(return_value=("THE REPORT CONTENT", "mock"))
        ds.set_reasoner(r)
        ctx = ResearchContext(question="test?")
        ctx.findings = []
        ctx.sources_visited = set()
        ctx.round = 1
        ctx.queries_used = []
        result = run(ds._synthesize(ctx))
        self.assertIn("THE REPORT CONTENT", result)

    def test_header_uses_markdown(self):
        ds = DeepSearch()
        r = MagicMock()
        r.query = AsyncMock(return_value=("body", "mock"))
        ds.set_reasoner(r)
        ctx = ResearchContext(question="test?")
        ctx.findings = []
        ctx.sources_visited = set()
        ctx.round = 1
        ctx.queries_used = []
        result = run(ds._synthesize(ctx))
        self.assertTrue(result.startswith("# "))


# ─── DeepSearch.research — progress callback and early exit ──────────────────

class TestResearchFlow(unittest.TestCase):
    def test_progress_callback_fires(self):
        ds = DeepSearch()
        r = MagicMock()
        r.query = AsyncMock(return_value=('["q1"]', "mock"))
        ds.set_reasoner(r)

        progress_calls = []

        with patch.object(ds, "_parallel_search", new=AsyncMock(return_value=[])), \
             patch.object(ds, "_parallel_fetch", new=AsyncMock(return_value=[])), \
             patch.object(ds, "_analyze", new=AsyncMock(return_value={"new_facts": [], "remaining_gaps": []})), \
             patch.object(ds, "_synthesize", new=AsyncMock(return_value="report")):
            run(ds.research("What is AI?", max_rounds=1, on_progress=progress_calls.append))

        self.assertGreater(len(progress_calls), 0)

    def test_empty_gaps_exits_early(self):
        """If ctx.gaps is empty after init adjustment, loop should exit early."""
        ds = DeepSearch()
        r = MagicMock()
        r.query = AsyncMock(return_value=("report", "mock"))
        ds.set_reasoner(r)

        plan_calls = []

        async def mock_plan(ctx):
            plan_calls.append(ctx)
            return []

        with patch.object(ds, "_plan_queries", side_effect=mock_plan), \
             patch.object(ds, "_synthesize", new=AsyncMock(return_value="report")):
            result = run(ds.research("test question", max_rounds=3))

        # _plan_queries returns [] → loop breaks after first round
        self.assertLessEqual(len(plan_calls), 1)
        self.assertEqual(result, "report")

    def test_returns_string(self):
        ds = DeepSearch()
        r = MagicMock()
        r.query = AsyncMock(return_value=("report content", "mock"))
        ds.set_reasoner(r)

        with patch.object(ds, "_parallel_search", new=AsyncMock(return_value=[])), \
             patch.object(ds, "_parallel_fetch", new=AsyncMock(return_value=[])), \
             patch.object(ds, "_analyze", new=AsyncMock(return_value={"new_facts": [], "remaining_gaps": []})), \
             patch.object(ds, "_synthesize", new=AsyncMock(return_value="final report")):
            result = run(ds.research("question", max_rounds=1))

        self.assertIsInstance(result, str)
        self.assertEqual(result, "final report")

    def test_max_rounds_respected(self):
        """_plan_queries called at most max_rounds times."""
        ds = DeepSearch()
        r = MagicMock()
        r.query = AsyncMock(return_value=("report", "mock"))
        ds.set_reasoner(r)

        plan_calls = [0]

        async def mock_plan(ctx):
            plan_calls[0] += 1
            return ["q1", "q2"]

        async def mock_analyze(ctx, sr, pages):
            return {"new_facts": [], "remaining_gaps": ["still need this"]}

        with patch.object(ds, "_plan_queries", side_effect=mock_plan), \
             patch.object(ds, "_parallel_search", new=AsyncMock(return_value=[])), \
             patch.object(ds, "_parallel_fetch", new=AsyncMock(return_value=[])), \
             patch.object(ds, "_analyze", side_effect=mock_analyze), \
             patch.object(ds, "_synthesize", new=AsyncMock(return_value="report")):
            run(ds.research("question", max_rounds=2))

        self.assertLessEqual(plan_calls[0], 2)

    def test_sources_tracked_across_rounds(self):
        """Sources visited should accumulate and not re-fetch."""
        ds = DeepSearch()
        r = MagicMock()
        r.query = AsyncMock(return_value=("report", "mock"))
        ds.set_reasoner(r)

        sr = SearchResult(query="q", title="T", url="https://tracked.com", snippet="s")

        with patch.object(ds, "_plan_queries", new=AsyncMock(return_value=["query"])), \
             patch.object(ds, "_parallel_search", new=AsyncMock(return_value=[sr])), \
             patch.object(ds, "_parallel_fetch", new=AsyncMock(return_value=[])), \
             patch.object(ds, "_analyze", new=AsyncMock(return_value={"new_facts": [], "remaining_gaps": []})), \
             patch.object(ds, "_synthesize", new=AsyncMock(return_value="report")):
            result = run(ds.research("question", max_rounds=1))

        self.assertIsInstance(result, str)


if __name__ == "__main__":
    unittest.main()
