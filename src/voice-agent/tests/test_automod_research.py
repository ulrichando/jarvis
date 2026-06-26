"""Pre-build research stage (2026-06-26). Logic tested via dependency injection
— no live web, no LLM. Contract: best-effort, never raises, OFF by default,
and a brief is written for the offline build to read."""
from __future__ import annotations

import pytest

from pipeline.automod import research as r


@pytest.fixture
def armed(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_AUTOMOD_RESEARCH", "1")
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis-home"))


def test_disabled_by_default_skips(monkeypatch):
    monkeypatch.delenv("JARVIS_AUTOMOD_RESEARCH", raising=False)
    assert r.research_intent("add a retry to X")["skipped"] is True


def test_no_search_results_skips(armed):
    out = r.research_intent("add a retry", search=lambda q: "", synthesize=lambda i, f: "BRIEF")
    assert out["skipped"] is True
    assert out["reason"] == "no search results"


def test_synthesize_failure_skips_never_raises(armed):
    def boom(intent, findings):
        raise RuntimeError("model down")
    out = r.research_intent("add a retry", search=lambda q: "result text", synthesize=boom)
    assert out["skipped"] is True
    assert "synthesize failed" in out["reason"]


def test_empty_brief_skips(armed):
    out = r.research_intent("add a retry", search=lambda q: "result", synthesize=lambda i, f: "   ")
    assert out["skipped"] is True


def test_success_returns_and_writes_brief(armed):
    out = r.research_intent(
        "add exponential backoff to the HTTP client",
        automod_id="automod-test-1",
        search=lambda q: "Use tenacity. See https://example.com/backoff for details.",
        synthesize=lambda i, f: "Use exponential backoff with jitter; tenacity is the idiom.",
    )
    assert out["skipped"] is False
    assert "backoff" in out["brief"]
    assert "https://example.com/backoff" in out["sources"]
    # brief was persisted for the offline build to read
    assert r.brief_path("automod-test-1").exists()


def test_one_bad_query_does_not_abort(armed):
    calls = {"n": 0}
    def flaky(q):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first query failed")
        return "good result"
    out = r.research_intent("add a retry", search=flaky, synthesize=lambda i, f: "BRIEF")
    assert out["skipped"] is False  # later queries still produced findings


# --- pure units ---

def test_derive_queries_varies_angle():
    qs = r._derive_queries("add exponential backoff to the HTTP client")
    assert len(qs) >= 2
    assert any("best practices" in q for q in qs)


def test_extract_sources_dedupes_urls():
    findings = [("q", "see https://a.com and https://a.com and https://b.com")]
    assert r._extract_sources(findings) == ["https://a.com", "https://b.com"]
