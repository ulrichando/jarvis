"""Pre-build RESEARCH stage for self-evolution (2026-06-26).

The build agent runs OFFLINE (bin/jarvis-automod-impl sets
JARVIS_AUTOMOD_NO_NETWORK=1) — a deliberate security boundary: autonomous,
self-modifying code must not have network access (exfiltration / supply-chain
risk). So research can't happen *inside* the build. This stage runs BEFORE it,
WITH network but READ-ONLY (no code writing): it searches the web for relevant
docs / best-practices / similar implementations for the intent, synthesizes a
short brief, and writes it so the offline build agent reads it as grounding —
the same "research before you build" discipline a careful engineer uses.

Grounded in 2026 RAG guidance: retrieval grounding cuts hallucination but is
*not sufficient alone* (so it is PAIRED with the stress gate + tests, which
VERIFY), and retrieval quality is the bottleneck (prefer the credentialed web
backend over keyless search). Agentic-RAG-lite: a few derived queries, best
effort, never blocks the build.

OFF by default (JARVIS_AUTOMOD_RESEARCH=1). `search`/`synthesize` are injectable
so the logic is unit-tested without live web or an LLM. Never raises.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from pipeline.automod import _state

_SYNTH_SYSTEM = (
    "You are a senior engineer writing a SHORT research brief to ground a code "
    "change. From the search findings, extract only what's decision-relevant: "
    "the current best practice, the right API/idiom, known pitfalls, and any "
    "authoritative source. Be concise and concrete. If the findings are "
    "irrelevant or empty, say so in one line. No fluff."
)


def _derive_queries(intent: str, n: int = 3) -> list[str]:
    """A few focused search queries from the intent. Retrieval quality is the
    bottleneck, so we vary the angle: the change itself, best-practice, and docs."""
    base = " ".join(intent.split())[:120]
    short = base[:80]
    queries = [base, f"{short} python best practices", f"{short} documentation"]
    seen, out = set(), []
    for q in queries:
        q = q.strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out[:n]


def _extract_sources(findings: list[tuple[str, str]]) -> list[str]:
    import re
    urls: list[str] = []
    for _, text in findings:
        for u in re.findall(r"https?://[^\s)\]>\"']+", text or ""):
            if u not in urls:
                urls.append(u)
    return urls[:10]


def _web_search(query: str) -> str:
    """Reuse the voice-agent's web search (credentialed backend when available,
    else keyless). Sync wrapper over the async tool handler."""
    import asyncio
    from tools import web_tools
    return asyncio.run(web_tools._handle_web_search({"query": query}))


def _synthesize_brief(intent: str, findings: list[tuple[str, str]]) -> str:
    """Synthesize the search findings into a short grounding brief via the
    primary model. Raises on failure (caller turns that into a skip)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("no provider key")
    corpus = "\n\n".join(f"## query: {q}\n{(r or '')[:2000]}" for q, r in findings)
    prompt = f"INTENT:\n{intent}\n\nSEARCH FINDINGS:\n{corpus}\n\nWrite the research brief now."
    import anthropic
    client = anthropic.Anthropic(timeout=60.0, max_retries=1)
    resp = client.messages.create(
        model=os.environ.get("JARVIS_AUTOMOD_RESEARCH_MODEL", "claude-sonnet-4-6"),
        max_tokens=1200, system=_SYNTH_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(getattr(b, "text", "") for b in resp.content).strip()


def brief_path(automod_id: str) -> Path:
    # Sits next to the proposal artifact (<id>.json) so the offline build reads it.
    return _state.artifact_path(automod_id).with_name(f"{automod_id}.research.md")


def research_intent(
    intent: str,
    automod_id: str | None = None,
    *,
    search: Callable[[str], str] | None = None,
    synthesize: Callable[[str, list], str] | None = None,
    max_queries: int = 3,
) -> dict:
    """Gather online grounding for `intent`. Returns
    {"skipped": bool, "reason": str, "brief": str, "sources": [str]}.
    Writes the brief to brief_path(automod_id) when one is produced. Best-effort;
    never raises — a research failure must never block the build."""
    if os.environ.get("JARVIS_AUTOMOD_RESEARCH") != "1":
        return {"skipped": True, "reason": "research disabled", "brief": "", "sources": []}
    do_search = search or _web_search
    do_synth = synthesize or _synthesize_brief
    findings: list[tuple[str, str]] = []
    for q in _derive_queries(intent, max_queries):
        try:
            findings.append((q, do_search(q) or ""))
        except Exception:  # noqa: BLE001 — one bad query must not abort research
            continue
    if not any(text.strip() for _, text in findings):
        return {"skipped": True, "reason": "no search results", "brief": "", "sources": []}
    try:
        brief = do_synth(intent, findings)
    except Exception as e:  # noqa: BLE001
        return {"skipped": True, "reason": f"synthesize failed: {e}", "brief": "", "sources": []}
    if not brief.strip():
        return {"skipped": True, "reason": "empty brief", "brief": "", "sources": []}
    sources = _extract_sources(findings)
    if automod_id:
        try:
            p = brief_path(automod_id)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(brief + "\n\nSources:\n" + "\n".join(sources) + "\n", encoding="utf-8")
        except OSError:
            pass
    return {"skipped": False, "reason": "", "brief": brief, "sources": sources}
