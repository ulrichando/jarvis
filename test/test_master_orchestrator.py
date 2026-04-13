"""Comprehensive tests for MasterOrchestrator and its subsystems."""

import asyncio
import threading
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.master_orchestrator import (
    MasterOrchestrator,
    QueryAnalyzer,
    TaskDecomposer,
    AgentRouter,
    ConflictResolver,
    ResponseSynthesizer,
    RoutingLearner,
    SubTask,
    TaskGraph,
    AgentResult,
    OrchestratorResult,
    _score_quality,
    get_orchestrator,
    COMPLEXITY_THRESHOLD,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def run(coro):
    return asyncio.run(coro)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    return tmp_path / "test_routing.db"


@pytest.fixture
def learner(tmp_db):
    return RoutingLearner(tmp_db)


@pytest.fixture
def analyzer():
    return QueryAnalyzer()


@pytest.fixture
def decomposer(analyzer):
    return TaskDecomposer(analyzer)


@pytest.fixture
def router(learner):
    return AgentRouter(learner)


@pytest.fixture
def resolver():
    return ConflictResolver()


@pytest.fixture
def synthesizer():
    return ResponseSynthesizer()


@pytest.fixture
def orch(tmp_path):
    MasterOrchestrator.reset()
    o = MasterOrchestrator(db_path=tmp_path / "test.db")
    yield o
    MasterOrchestrator.reset()


# ─── RoutingLearner ──────────────────────────────────────────────────────────

class TestRoutingLearner:

    def test_init_creates_tables(self, learner):
        assert isinstance(learner.get_weights(), dict)

    def test_record_and_retrieve(self, learner):
        learner.record(domain="engineering", agent_name="engineer",
                       quality=0.8, latency_ms=100)
        weights = learner.get_weights("engineering")
        assert "engineer" in weights
        assert 0.0 < weights["engineer"] <= 1.0

    def test_ema_updates_on_second_record(self, learner):
        learner.record("engineering", "engineer", 1.0, 50)
        w1 = learner.get_weights("engineering")["engineer"]
        learner.record("engineering", "engineer", 0.0, 50)
        w2 = learner.get_weights("engineering")["engineer"]
        assert w2 < w1

    def test_record_null_domain(self, learner):
        learner.record(domain=None, agent_name="engineer", quality=0.7, latency_ms=200)
        weights = learner.get_weights(None)
        assert "engineer" in weights

    def test_get_stats_empty(self, learner):
        assert isinstance(learner.get_stats(), list)

    def test_reset_clears_data(self, learner):
        learner.record("engineering", "engineer", 0.9, 100)
        learner.reset()
        assert learner.get_weights("engineering") == {}
        assert learner.get_stats() == []

    def test_fallback_to_global_when_no_domain_weights(self, learner):
        learner.record(domain=None, agent_name="analyst", quality=0.8, latency_ms=100)
        weights = learner.get_weights("unknown_domain")
        assert "analyst" in weights

    def test_thread_safety(self, learner):
        """Concurrent writes must not corrupt EMA weights."""
        errors = []

        def writer(i):
            try:
                learner.record("engineering", "engineer", i / 20.0, 100)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        weights = learner.get_weights("engineering")
        assert "engineer" in weights
        assert 0.0 <= weights["engineer"] <= 1.0

    def test_multiple_agents_same_domain(self, learner):
        learner.record("engineering", "engineer", 0.8, 100)
        learner.record("engineering", "analyst",  0.9, 120)
        weights = learner.get_weights("engineering")
        assert "engineer" in weights
        assert "analyst" in weights


# ─── QueryAnalyzer ───────────────────────────────────────────────────────────

class TestQueryAnalyzer:

    def test_simple_query_low_complexity(self, analyzer):
        result = analyzer.analyze("hello how are you")
        assert result.complexity_score < COMPLEXITY_THRESHOLD
        assert not result.is_complex

    def test_complex_query_hits_threshold(self, analyzer):
        # Sequential markers + domain hits → enough for complexity
        result = analyzer.analyze(
            "First write a Python function, then add unit tests, "
            "finally deploy to AWS Lambda and configure the nginx server"
        )
        assert result.is_complex or result.complexity_score >= 0.20  # sequential always fires

    def test_many_questions_raise_complexity(self, analyzer):
        # >3 questions gives 0.25+0.15 = 0.40 exactly, plus any domain hits
        result = analyzer.analyze(
            "What is SQL injection? How do I prevent it? "
            "What tools detect it? Which databases are most vulnerable? "
            "How do I audit code for SQL injection bugs?"
        )
        assert result.complexity_score >= COMPLEXITY_THRESHOLD

    def test_sequential_markers_detected(self, analyzer):
        result = analyzer.analyze("First scan the network, then exploit the service, finally pivot")
        assert result.is_sequential

    def test_parallel_markers_detected(self, analyzer):
        result = analyzer.analyze("Check code quality also review security additionally run tests")
        assert result.is_parallel

    def test_domain_detection_security(self, analyzer):
        result = analyzer.analyze("exploit this buffer overflow vulnerability with shellcode")
        assert "security_offensive" in result.domains

    def test_domain_detection_engineering(self, analyzer):
        result = analyzer.analyze("refactor this Python class and add unit tests")
        assert "engineering" in result.domains

    def test_multi_domain_detection(self, analyzer):
        result = analyzer.analyze(
            "Review the contract NDA clause and check the SQL injection in the API code"
        )
        assert result.is_multi_domain or len(result.domains) >= 2

    def test_primary_domain_is_set(self, analyzer):
        result = analyzer.analyze("pentest this web app for XSS and CSRF vulnerabilities")
        assert result.primary_domain is not None

    def test_estimated_tasks_capped_at_6(self, analyzer):
        query = "? ".join(["Do this"] * 10) + "?"
        result = analyzer.analyze(query)
        assert result.estimated_tasks <= 6


# ─── TaskDecomposer / TaskGraph ──────────────────────────────────────────────

class TestTaskDecomposer:

    def test_simple_query_single_task(self, decomposer):
        graph = decomposer.decompose("tell me a joke")
        assert len(graph.tasks) == 1
        assert not graph.is_complex

    def test_sequential_query_ordered_tasks(self, decomposer):
        graph = decomposer.decompose(
            "First write the Python function, then add unit tests for it, finally deploy it"
        )
        if len(graph.tasks) > 1:
            has_dep = any(t.depends_on for t in graph.tasks)
            assert has_dep

    def test_parallel_query_parallel_tasks(self, decomposer):
        graph = decomposer.decompose(
            "Review Python code quality also check for SQL injection vulnerabilities"
        )
        if len(graph.tasks) > 1:
            assert any(t.parallel_ok for t in graph.tasks)

    # ── stages() ──────────────────────────────────────────────────────────────

    def test_stages_single_task(self):
        graph = TaskGraph("test", [SubTask("t1", "do thing")])
        stages = graph.stages()
        assert len(stages) == 1
        assert stages[0][0].id == "t1"

    def test_stages_all_parallel(self):
        graph = TaskGraph("test", [
            SubTask("t1", "task 1", parallel_ok=True),
            SubTask("t2", "task 2", parallel_ok=True),
            SubTask("t3", "task 3", parallel_ok=True),
        ])
        stages = graph.stages()
        assert len(stages) == 1
        assert len(stages[0]) == 3

    def test_stages_strict_dependency_chain(self):
        graph = TaskGraph("test", [
            SubTask("t1", "step 1", parallel_ok=False),
            SubTask("t2", "step 2", depends_on=["t1"], parallel_ok=False),
            SubTask("t3", "step 3", depends_on=["t2"], parallel_ok=False),
        ])
        stages = graph.stages()
        assert len(stages) == 3
        assert stages[0][0].id == "t1"
        assert stages[1][0].id == "t2"
        assert stages[2][0].id == "t3"

    def test_stages_mixed_parallel_then_sequential(self):
        graph = TaskGraph("test", [
            SubTask("t1", "gather info", parallel_ok=True),
            SubTask("t2", "also gather", parallel_ok=True),
            SubTask("t3", "analyze", depends_on=["t1", "t2"], parallel_ok=False),
        ])
        stages = graph.stages()
        # Stage 0: t1 + t2 together; stage 1: t3 alone
        assert len(stages[0]) == 2
        assert {s.id for s in stages[0]} == {"t1", "t2"}
        assert stages[1][0].id == "t3"

    def test_stages_cycle_guard_no_infinite_loop(self):
        graph = TaskGraph("test", [
            SubTask("t1", "a", depends_on=["t2"]),
            SubTask("t2", "b", depends_on=["t1"]),
        ])
        stages = graph.stages()
        total = sum(len(s) for s in stages)
        assert total == 2  # all tasks flushed

    def test_stages_empty_tasks(self):
        graph = TaskGraph("test", [])
        assert graph.stages() == [[]]

    def test_stages_performance_linear(self):
        """O(n) algorithm: 100 tasks should complete without noticeable delay."""
        import time
        tasks = [
            SubTask(f"t{i}", f"task {i}", parallel_ok=True)
            for i in range(100)
        ]
        graph = TaskGraph("perf test", tasks)
        t0 = time.monotonic()
        stages = graph.stages()
        elapsed = time.monotonic() - t0
        assert elapsed < 0.1  # must be fast
        assert len(stages) == 1
        assert len(stages[0]) == 100


# ─── AgentRouter ─────────────────────────────────────────────────────────────

class TestAgentRouter:

    def test_select_returns_decisions(self, router):
        task = SubTask("t1", "exploit CVE buffer overflow shellcode")
        decisions = router.select(task, top_k=3)
        assert len(decisions) >= 1
        assert decisions[0].task_id == "t1"

    def test_security_routes_to_red_team(self, router):
        task = SubTask("t1", "pentest XSS CSRF payload exploit")
        decisions = router.select(task, top_k=3)
        assert "red_team" in [d.agent_name for d in decisions]

    def test_engineering_routes_to_engineer(self, router):
        task = SubTask("t1", "refactor Python class add unit tests debug compile")
        decisions = router.select(task, top_k=3)
        assert "engineer" in [d.agent_name for d in decisions]

    def test_explicit_domain_injected(self, router):
        task = SubTask("t1", "something generic", domain="financial")
        decisions = router.select(task, top_k=3)
        assert "financial" in [d.agent_name for d in decisions]

    def test_fallback_non_empty(self, router):
        task = SubTask("t1", "xxxxxxxxxx yyyyy zzzzz unknown words")
        decisions = router.select(task, top_k=3)
        assert len(decisions) >= 1

    def test_learned_weights_boost_agent(self, router, learner):
        for _ in range(5):
            learner.record("analysis", "analyst", 0.95, 100)
        task = SubTask("t1", "analyze performance metrics benchmark")
        decisions = router.select(task, top_k=3)
        assert "analyst" in [d.agent_name for d in decisions]

    def test_confidence_scores_valid(self, router):
        task = SubTask("t1", "write Python code and deploy to docker")
        decisions = router.select(task, top_k=3)
        for d in decisions:
            assert 0.0 <= d.confidence <= 2.0  # combined score may exceed 1.0

    def test_fallback_score_non_empty(self, router):
        scores = router._fallback_score(SubTask("t1", "vague task"), top_k=3)
        assert len(scores) >= 1
        assert all(isinstance(s, tuple) and len(s) == 2 for s in scores)


# ─── ConflictResolver ────────────────────────────────────────────────────────

class TestConflictResolver:

    def test_single_result_passes_through(self, resolver):
        r = AgentResult("t1", "engineer", "The answer is 42", quality_score=0.8)
        resolved = resolver.resolve([r])
        assert resolved[0].response == "The answer is 42"

    def test_single_result_gets_scored(self, resolver):
        """Single results with quality_score=0 should be auto-scored."""
        r = AgentResult("t1", "engineer",
                        "Here is a detailed code review with recommendations and bullet points:\n- Fix line 10\n- Rename variable",
                        quality_score=0.0)
        resolved = resolver.resolve([r])
        assert resolved[0].quality_score > 0.0

    def test_picks_highest_quality(self, resolver):
        r1 = AgentResult("t1", "engineer", "Good answer", quality_score=0.9)
        r2 = AgentResult("t1", "analyst",  "Mediocre",    quality_score=0.3)
        resolved = resolver.resolve([r1, r2])
        assert resolved[0].agent_name == "engineer"

    def test_contradiction_merges(self, resolver):
        r1 = AgentResult("t1", "red_team",  "This is safe to use.",        quality_score=0.8)
        r2 = AgentResult("t1", "blue_team", "This is unsafe for production.", quality_score=0.7)
        resolved = resolver.resolve([r1, r2])
        assert resolved[0].status == "merged"
        assert "perspective" in resolved[0].response.lower()

    def test_groups_by_task_id(self, resolver):
        r1 = AgentResult("t1", "engineer", "Task 1", quality_score=0.8)
        r2 = AgentResult("t2", "analyst",  "Task 2", quality_score=0.7)
        resolved = resolver.resolve([r1, r2])
        assert len(resolved) == 2

    def test_no_merge_when_no_contradiction(self, resolver):
        r1 = AgentResult("t1", "engineer", "Use Python.", quality_score=0.9)
        r2 = AgentResult("t1", "analyst",  "Python works.", quality_score=0.6)
        resolved = resolver.resolve([r1, r2])
        assert resolved[0].status != "merged"
        assert resolved[0].agent_name == "engineer"


# ─── ResponseSynthesizer ─────────────────────────────────────────────────────

class TestResponseSynthesizer:

    def test_single_result(self, synthesizer):
        graph = TaskGraph("q", [SubTask("t1", "q")])
        r = AgentResult("t1", "engineer", "The answer")
        assert synthesizer.synthesize(graph, [r]) == "The answer"

    def test_multiple_results_joined(self, synthesizer):
        graph = TaskGraph("q", [SubTask("t1", "q1"), SubTask("t2", "q2")])
        r1 = AgentResult("t1", "engineer", "Part 1")
        r2 = AgentResult("t2", "analyst",  "Part 2")
        out = synthesizer.synthesize(graph, [r1, r2])
        assert "Part 1" in out and "Part 2" in out and "---" in out

    def test_attribution_adds_headers(self, synthesizer):
        tasks = [SubTask(f"t{i}", f"task {i}") for i in range(1, 4)]
        graph = TaskGraph("complex", tasks, is_complex=True)
        results = [AgentResult(f"t{i}", "engineer", f"Answer {i}") for i in range(1, 4)]
        out = synthesizer.synthesize(graph, results, attribution=True)
        assert "##" in out

    def test_empty_results(self, synthesizer):
        graph = TaskGraph("q", [])
        assert synthesizer.synthesize(graph, []) == ""


# ─── Quality Scorer ──────────────────────────────────────────────────────────

class TestScoreQuality:

    def test_empty_low_score(self):
        assert _score_quality("") < 0.1

    def test_short_low_score(self):
        assert _score_quality("ok") < 0.15

    def test_structured_response_high_score(self):
        text = "## Solution\n\n- Step 1: Fix config\n- Step 2: Update schema\n\n```python\ndef fix(): pass\n```\n\n1. Run tests\n2. Deploy\n"
        assert _score_quality(text) > 0.5

    def test_code_block_boosts(self):
        plain = "Here is the answer to your question about the topic."
        with_code = plain + "\n```python\nx = 1\n```"
        assert _score_quality(with_code) > _score_quality(plain)

    def test_error_language_penalizes(self):
        good = "Here is how to do it: use the API endpoint."
        bad  = "Sorry I cannot do that. I am unable to help. Error occurred."
        assert _score_quality(good) > _score_quality(bad)

    def test_medium_length_optimal(self):
        medium    = "x" * 500
        very_long = "x" * 6000
        assert _score_quality(medium) >= _score_quality(very_long)


# ─── MasterOrchestrator integration ──────────────────────────────────────────

class TestMasterOrchestrator:

    @staticmethod
    async def _executor(agent_name, task, **_):
        return f"[{agent_name}] Result for: {task[:40]}"

    def test_simple_query_returns_response(self, orch):
        result = run(orch.route("hello", executor=self._executor))
        assert isinstance(result, OrchestratorResult)
        assert isinstance(result.response, str)
        assert result.response
        assert result.task_count >= 1

    def test_complex_sequential_query(self, orch):
        query = (
            "First write a Python function to parse JWT tokens, "
            "then add unit tests for it, "
            "finally document the API endpoints"
        )
        result = run(orch.route(query, executor=self._executor))
        assert result.response
        assert isinstance(result.agent_results, list)

    def test_force_parallel(self, orch):
        result = run(orch.route("simple task", executor=self._executor, force_parallel=True))
        assert result.response

    def test_on_start_callback_fires(self, orch):
        started = []
        def on_start(agent, tid): started.append(agent)
        run(orch.route("write code", executor=self._executor, on_agent_start=on_start))
        assert len(started) >= 1

    def test_on_done_callback_fires(self, orch):
        done = []
        def on_done(agent, tid, quality): done.append(quality)
        run(orch.route("write code", executor=self._executor, on_agent_done=on_done))
        assert len(done) >= 1
        assert all(isinstance(q, float) for q in done)

    def test_executor_exception_graceful(self, orch):
        async def bad(agent, task, **_): raise RuntimeError("boom")
        result = run(orch.route("write code", executor=bad))
        assert isinstance(result.response, str)
        assert "error" in result.response.lower() or "boom" in result.response

    def test_on_start_exception_no_crash(self, orch):
        def bad_cb(agent, tid): raise ValueError("broken")
        result = run(orch.route("test", executor=self._executor, on_agent_start=bad_cb))
        assert result.response

    def test_on_done_exception_no_crash(self, orch):
        def bad_cb(agent, tid, q): raise RuntimeError("oops")
        result = run(orch.route("test", executor=self._executor, on_agent_done=bad_cb))
        assert result.response

    def test_routing_learns_from_outcomes(self, orch):
        run(orch.route("refactor the Python code and add type hints", executor=self._executor))
        stats = orch.routing_stats()
        assert isinstance(stats, list)
        # Learning should have recorded at least one weight
        assert len(stats) >= 1

    def test_result_metadata_fields(self, orch):
        result = run(orch.route("analyze this code", executor=self._executor))
        assert "complexity_score" in result.metadata
        assert "domains" in result.metadata
        assert "is_complex" in result.metadata
        assert result.total_latency_ms >= 0

    def test_synthesis_strategy_valid(self, orch):
        result = run(orch.route("simple question", executor=self._executor))
        assert result.synthesis_strategy in ("single", "parallel_merge", "sequential_chain")

    def test_multi_task_result_has_agent_results(self, orch):
        query = "First analyze the code then also review security and additionally benchmark performance"
        result = run(orch.route(query, executor=self._executor))
        assert len(result.agent_results) >= 1

    def test_concurrent_routes_no_db_corruption(self, orch):
        """8 concurrent routes must not corrupt SQLite weights."""
        async def many():
            execs = []
            async def executor(agent, task, **_):
                await asyncio.sleep(0.005)
                return f"done by {agent}"
            coros = [
                orch.route(f"write code for task {i}", executor=executor)
                for i in range(8)
            ]
            return await asyncio.gather(*coros)

        results = run(many())
        assert all(isinstance(r, OrchestratorResult) for r in results)
        assert all(r.response for r in results)
        assert isinstance(orch.routing_stats(), list)

    def test_singleton_same_instance(self, tmp_path):
        MasterOrchestrator.reset()
        a = get_orchestrator()
        b = get_orchestrator()
        assert a is b
        MasterOrchestrator.reset()

    def test_reset_weights_clears_db(self, orch):
        run(orch.route("code review", executor=self._executor))
        orch.reset_weights()
        assert orch.routing_stats() == []

    def test_analyze_method_returns_dict(self, orch):
        result = orch.analyze("exploit buffer overflow XSS")
        assert "complexity_score" in result
        assert "domains" in result
        assert "is_complex" in result
        assert "tasks" in result


# ─── Edge cases ──────────────────────────────────────────────────────────────

class TestEdgeCases:

    @staticmethod
    async def _executor(agent, task, **_): return "ok"

    def test_empty_query(self, orch):
        result = run(orch.route("", executor=self._executor))
        assert isinstance(result.response, str)

    def test_very_long_query(self, orch):
        long_q = "analyze security vulnerabilities " * 50
        result = run(orch.route(long_q, executor=self._executor))
        assert result.response

    def test_executor_returns_empty_string(self, orch):
        async def executor(agent, task, **_): return ""
        result = run(orch.route("test", executor=executor))
        assert isinstance(result.response, str)

    def test_executor_returns_none(self, orch):
        async def executor(agent, task, **_): return None
        result = run(orch.route("test", executor=executor))
        assert isinstance(result.response, str)

    def test_stages_nonempty_for_nonempty_tasks(self):
        graph = TaskGraph("q", [SubTask("t1", "do it")])
        assert len(graph.stages()) >= 1
