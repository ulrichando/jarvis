"""JARVIS DeepSearch — multi-step deep research agent.

Inspired by Kimi-Researcher (RL-trained iterative search) and
multi-agent research systems.

Flow:
1. PLAN:      Decompose question into search queries targeting knowledge gaps
2. SEARCH:    Execute queries in parallel
3. READ:      Fetch and extract content from top results
4. ANALYZE:   Identify new facts + remaining gaps
5. REFINE:    Generate new queries for gaps → loop back to SEARCH
6. SYNTHESIZE: Merge all findings into structured report with sources

Gap detection drives the loop — each round explicitly identifies
what is still unknown rather than blindly re-searching.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("jarvis.deepsearch")


@dataclass
class SearchResult:
    query: str
    title: str
    url: str
    snippet: str


@dataclass
class Finding:
    fact: str
    source: str
    confidence: float = 0.8


@dataclass
class ResearchContext:
    question: str
    findings: list[Finding] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    sources_visited: set = field(default_factory=set)
    queries_used: list[str] = field(default_factory=list)
    round: int = 0


class DeepSearch:
    """Multi-step deep research agent."""

    def __init__(self, reasoner=None):
        self._reasoner = reasoner

    @property
    def reasoner(self):
        if self._reasoner is None:
            from src.reasoning.groq_client import GroqReasoner
            self._reasoner = GroqReasoner()
        return self._reasoner

    def set_reasoner(self, reasoner):
        self._reasoner = reasoner

    async def research(self, question: str, max_rounds: int = 3,
                        max_sources: int = 10, on_progress=None) -> str:
        """Run deep research on a question. Returns a structured report.

        Args:
            question: The research question
            max_rounds: Maximum search-analyze-refine cycles
            max_sources: Maximum total pages to fetch
            on_progress: Optional callback(message) for progress updates
        """
        ctx = ResearchContext(
            question=question,
            gaps=[question],  # Start with the full question as the gap
        )

        def progress(msg):
            if on_progress:
                on_progress(msg)
            log.info("DeepSearch: %s", msg)

        progress(f"Starting deep research: {question[:80]}")

        for round_num in range(max_rounds):
            ctx.round = round_num + 1
            progress(f"Round {ctx.round}/{max_rounds} — {len(ctx.gaps)} gaps to fill")

            if not ctx.gaps:
                progress("All gaps filled — moving to synthesis")
                break

            # Step 1: Generate search queries from gaps
            queries = await self._plan_queries(ctx)
            if not queries:
                progress("No more queries to try")
                break
            ctx.queries_used.extend(queries)

            # Step 2: Search in parallel
            progress(f"Searching {len(queries)} queries...")
            all_results = await self._parallel_search(queries)

            # Step 3: Read top results
            urls_to_read = []
            for r in all_results:
                if r.url and r.url not in ctx.sources_visited and len(urls_to_read) < max_sources - len(ctx.sources_visited):
                    urls_to_read.append(r)

            if urls_to_read:
                progress(f"Reading {len(urls_to_read)} sources...")
                pages = await self._parallel_fetch(urls_to_read)
                ctx.sources_visited.update(r.url for r in urls_to_read)
            else:
                pages = []

            # Step 4: Analyze — extract facts and identify remaining gaps
            progress("Analyzing findings...")
            analysis = await self._analyze(ctx, all_results, pages)
            ctx.findings.extend(analysis.get("new_facts", []))
            ctx.gaps = analysis.get("remaining_gaps", [])

            progress(f"Found {len(analysis.get('new_facts', []))} new facts, {len(ctx.gaps)} gaps remaining")

        # Step 5: Synthesize report
        progress(f"Synthesizing report from {len(ctx.findings)} findings across {len(ctx.sources_visited)} sources...")
        report = await self._synthesize(ctx)

        progress("Research complete")
        return report

    # ── Internal Steps ──

    async def _plan_queries(self, ctx: ResearchContext) -> list[str]:
        """Generate search queries targeting knowledge gaps."""
        gaps_text = "\n".join(f"- {g}" for g in ctx.gaps[:5])
        existing = "\n".join(f"- {f.fact[:100]}" for f in ctx.findings[-10:]) if ctx.findings else "None yet"
        used = ", ".join(ctx.queries_used[-10:]) if ctx.queries_used else "None"

        prompt = (
            f"Research question: {ctx.question}\n\n"
            f"What we already know:\n{existing}\n\n"
            f"Knowledge gaps to fill:\n{gaps_text}\n\n"
            f"Queries already tried: {used}\n\n"
            f"Generate 3-5 NEW search queries to fill the gaps. "
            f"Be specific and targeted. Avoid repeating previous queries.\n\n"
            f"Return ONLY a JSON array of strings: [\"query1\", \"query2\", ...]"
        )
        try:
            response, _ = await self.reasoner.query(
                prompt,
                system_prompt="You generate targeted web search queries for research. Return ONLY a JSON array of strings.",
                history=None,
            )
            response = response.strip()
            if "```" in response:
                lines = response.split("\n")
                response = "\n".join(l for l in lines if not l.strip().startswith("```"))
            start = response.find("[")
            end = response.rfind("]")
            if start != -1 and end != -1:
                queries = json.loads(response[start:end + 1])
                return [q for q in queries if isinstance(q, str)][:5]
        except Exception as e:
            log.debug("Query planning failed: %s", e)
        return []

    async def _parallel_search(self, queries: list[str]) -> list[SearchResult]:
        """Execute multiple search queries in parallel."""
        from src.internet.search import web_search

        async def search_one(query):
            try:
                results = await asyncio.to_thread(web_search, query, 5)
                return [
                    SearchResult(
                        query=query,
                        title=r.get("title", ""),
                        url=r.get("url", ""),
                        snippet=r.get("body", r.get("snippet", "")),
                    )
                    for r in (results or [])
                ]
            except Exception as e:
                log.debug("Search failed for '%s': %s", query, e)
                return []

        results = await asyncio.gather(*[search_one(q) for q in queries])
        flat = []
        for batch in results:
            flat.extend(batch)
        return flat

    async def _parallel_fetch(self, results: list[SearchResult]) -> list[dict]:
        """Fetch and extract content from URLs in parallel."""
        from src.internet.scraper import fetch_page

        async def fetch_one(r):
            try:
                content = await asyncio.to_thread(fetch_page, r.url)
                if content and len(content) > 100:
                    return {
                        "url": r.url,
                        "title": r.title,
                        "content": content[:3000],  # Cap per page
                    }
            except Exception as e:
                log.debug("Fetch failed for %s: %s", r.url, e)
            return None

        pages = await asyncio.gather(*[fetch_one(r) for r in results[:8]])
        return [p for p in pages if p]

    async def _analyze(self, ctx: ResearchContext, search_results: list[SearchResult],
                        pages: list[dict]) -> dict:
        """Analyze search results + pages. Extract new facts, identify remaining gaps."""
        # Build context from what we found
        snippets = "\n".join(
            f"[{r.title}] {r.snippet}" for r in search_results[:15]
        )
        page_content = "\n\n---\n".join(
            f"Source: {p['title']} ({p['url']})\n{p['content'][:1500]}"
            for p in pages[:5]
        )

        existing_facts = "\n".join(f"- {f.fact[:100]}" for f in ctx.findings) if ctx.findings else "None"

        prompt = (
            f"Research question: {ctx.question}\n\n"
            f"What we already know:\n{existing_facts}\n\n"
            f"New search snippets:\n{snippets[:2000]}\n\n"
            f"Page content:\n{page_content[:3000]}\n\n"
            f"Analyze this information. Return a JSON object:\n"
            f'{{"new_facts": ["fact 1 with source", "fact 2 with source"], '
            f'"remaining_gaps": ["what we still dont know 1", "question 2"]}}\n\n'
            f"Only include GENUINELY NEW facts not already in our findings. "
            f"Only include gaps that are RELEVANT to the original question."
        )

        try:
            response, _ = await self.reasoner.query(
                prompt,
                system_prompt="You analyze research findings. Extract new facts and identify remaining knowledge gaps. Return ONLY valid JSON.",
                history=None,
            )
            response = response.strip()
            if "```" in response:
                lines = response.split("\n")
                response = "\n".join(l for l in lines if not l.strip().startswith("```"))
            start = response.find("{")
            end = response.rfind("}")
            if start != -1 and end != -1:
                data = json.loads(response[start:end + 1])
                new_facts = [
                    Finding(fact=f, source="search", confidence=0.7)
                    for f in data.get("new_facts", []) if isinstance(f, str)
                ]
                gaps = [g for g in data.get("remaining_gaps", []) if isinstance(g, str)]
                return {"new_facts": new_facts, "remaining_gaps": gaps[:5]}
        except Exception as e:
            log.debug("Analysis failed: %s", e)

        return {"new_facts": [], "remaining_gaps": ctx.gaps}

    async def _synthesize(self, ctx: ResearchContext) -> str:
        """Synthesize all findings into a structured report."""
        facts_text = "\n".join(
            f"- {f.fact} (confidence: {f.confidence:.0%}, source: {f.source})"
            for f in ctx.findings
        )
        sources_text = "\n".join(f"- {url}" for url in list(ctx.sources_visited)[:20])

        prompt = (
            f"Write a comprehensive research report answering: {ctx.question}\n\n"
            f"Findings ({len(ctx.findings)} facts):\n{facts_text}\n\n"
            f"Sources consulted ({len(ctx.sources_visited)}):\n{sources_text}\n\n"
            f"Write a clear, structured report with:\n"
            f"1. Executive summary (2-3 sentences)\n"
            f"2. Key findings (organized by theme)\n"
            f"3. Sources\n\n"
            f"Be factual. Only include information from the findings above."
        )

        report, _ = await self.reasoner.query(
            prompt,
            system_prompt="You write clear, factual research reports. Cite sources. Be thorough but concise.",
            history=None,
        )

        # Add metadata
        header = (
            f"# Deep Research: {ctx.question}\n\n"
            f"*{len(ctx.findings)} facts from {len(ctx.sources_visited)} sources "
            f"across {ctx.round} search rounds, {len(ctx.queries_used)} queries*\n\n"
        )
        return header + report
