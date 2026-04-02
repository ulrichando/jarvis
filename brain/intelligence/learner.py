"""JARVIS Self-Learning Loop — learn what you don't know, then do it.

When JARVIS doesn't know how to perform a task:
1. Search the internet for how to do it
2. Read and understand the results
3. Create a plan
4. Execute the plan
5. Store what it learned for next time
"""

import asyncio
from brain.internet.search import web_search
from brain.internet.scraper import fetch_page
from brain.reasoning.groq_client import GroqReasoner
from brain.memory.store import MemoryStore
from brain.memory.lattice.node import NodeType
from brain.commands.executor import CommandExecutor


class SelfLearner:
    """Learns new skills from the internet and executes them."""

    def __init__(self, reasoner: GroqReasoner, memory: MemoryStore, executor: CommandExecutor):
        self.reasoner = reasoner
        self.memory = memory
        self.executor = executor

    async def learn_and_do(self, task: str, update_fn=None) -> str:
        """Learn how to do something, then do it.

        update_fn: optional callback(message) for progress updates.
        """
        def update(msg):
            if update_fn:
                update_fn(msg)

        # Step 1: Search for how to do it
        update("Searching how to do this...")
        search_query = f"how to {task} linux command line tutorial"
        results = await asyncio.to_thread(web_search, search_query, 3)

        if not results:
            return "Couldn't find info on how to do this."

        # Step 2: Read the best result
        update("Reading and learning...")
        content = ""
        for r in results[:2]:
            if r["url"]:
                page = await asyncio.to_thread(fetch_page, r["url"])
                if page and len(page) > 100:
                    content += f"\n\nSource: {r['title']}\n{page[:2000]}"
                    break

        if not content:
            content = "\n".join(f"- {r['title']}: {r['body']}" for r in results)

        # Step 3: Ask AI to create an execution plan
        update("Creating execution plan...")
        plan = await self.reasoner.query(
            f"I need to: {task}\n\nHere's what I found online:\n{content[:3000]}\n\n"
            "Create a step-by-step plan with exact terminal commands I should run. "
            "Use [run:COMMAND] for each command. Be specific to Kali Linux.",
            system_prompt="You are a Kali Linux expert. Give exact commands with [run:COMMAND] tags. No explanations, just commands and brief labels.",
            history=None,
        )

        # Step 4: Store what we learned
        self.memory.learn(f"How to {task}: {plan[:200]}", NodeType.SKILL, ["learned", "self-taught"])
        update("Learned. Now executing...")

        return plan

    async def can_i_do(self, task: str) -> bool:
        """Check if JARVIS already knows how to do this from memory."""
        memories = self.memory.recall(f"how to {task}", top_k=3)
        return any(m.strength > 0.5 and m.node_type == NodeType.SKILL for m in memories)

    async def recall_skill(self, task: str) -> str | None:
        """Recall a previously learned skill."""
        memories = self.memory.recall(f"how to {task}", top_k=1)
        for m in memories:
            if m.node_type == NodeType.SKILL and m.strength > 0.3:
                return m.content
        return None
