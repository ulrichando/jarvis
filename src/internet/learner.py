"""JARVIS Internet Learner — search, read, understand, and remember.

This is the full pipeline: query → search → scrape → understand → store in lattice.
"""

import asyncio
from src.internet.search import web_search
from src.internet.scraper import fetch_page
from src.reasoning.groq_client import GroqReasoner
from src.memory.store import MemoryStore
from src.memory.lattice.node import NodeType


class InternetLearner:
    """Learns new information from the internet."""

    def __init__(self, reasoner: GroqReasoner, memory: MemoryStore):
        self.reasoner = reasoner
        self.memory = memory

    async def research(self, topic: str) -> str:
        """Research a topic: search → read top results → synthesize answer."""
        # Search
        results = await asyncio.to_thread(web_search, topic, 5)
        if not results:
            return "Couldn't find anything on that."

        # Read top 2 pages
        snippets = []
        for r in results[:2]:
            if r["url"]:
                content = await asyncio.to_thread(fetch_page, r["url"])
                if content and len(content) > 50:
                    snippets.append(f"Source: {r['title']}\n{content[:1500]}")

        if not snippets:
            # Fall back to search snippets
            snippets = [f"{r['title']}: {r['body']}" for r in results]

        # Synthesize
        context = "\n\n---\n\n".join(snippets)
        answer = await self.reasoner.query(
            f"Based on this info, answer: {topic}\n\n{context}",
            system_prompt="Summarize the info into a direct, simple answer. 2-4 sentences max. No filler.",
            history=None,
        )

        # Store what we learned
        self.memory.learn(f"{topic}: {answer[:200]}", NodeType.FACT, ["internet", "learned"])

        return answer

    async def quick_search(self, query: str) -> str:
        """Quick search — just return search snippets without reading full pages."""
        results = await asyncio.to_thread(web_search, query, 3)
        if not results:
            return "No results found."

        context = "\n".join(f"- {r['title']}: {r['body']}" for r in results)
        answer = await self.reasoner.query(
            f"Based on these search results, answer: {query}\n\n{context}",
            system_prompt="Give a direct answer from the search results. Simple language. 1-2 sentences.",
            history=None,
        )
        return answer
