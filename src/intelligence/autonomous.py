"""JARVIS Autonomous Thinking — reason through problems, don't just react.

Old JARVIS: stimulus → response
New JARVIS: stimulus → understand → reason → evaluate → respond → reflect

The autonomous thinker is JARVIS's deepest level of reasoning.
It engages when:
- Questions are complex or ambiguous
- Confidence is low and thinking might help
- Multiple approaches exist and JARVIS needs to choose
- Something failed and JARVIS needs to figure out why

This is where JARVIS becomes more than a lookup table.
"""

import asyncio


THINKING_PROMPT = """You are JARVIS reasoning through a problem. This is your inner monologue.

You think in steps. Each step is ONE of:

UNDERSTAND: [Restate the problem in your own words. What is really being asked?]
DOUBT: [What am I unsure about? What could I be wrong about?]
HYPOTHESIZE: [I think the answer might be X because Y...]
VERIFY: [Let me check — search or run a command to confirm]
SEARCH: [search query to look up]
RUN: [command to execute and observe]
REALIZE: [I just understood something. Key insight.]
RECONSIDER: [My previous step was wrong. Here's why, and what I'll try instead.]
ANSWER: [Final answer for Ulrich — clear, honest, with confidence level]

Rules:
- ALWAYS start with UNDERSTAND. Make sure you know what's being asked.
- Use DOUBT when you're not sure. Honest uncertainty > false confidence.
- Use RECONSIDER when a step didn't work. Don't repeat the same thing.
- End with ANSWER. Keep it concise.
- Max 7 steps. Efficiency matters.
- If you realize you can't figure it out: ANSWER with honesty about what you tried.
- The ANSWER is what Ulrich hears. Everything else is internal.

Example — good reasoning:
UNDERSTAND: Ulrich wants to know why his Python script is slow.
HYPOTHESIZE: Could be I/O bound, CPU bound, or inefficient algorithm.
RUN: python3 -c "import cProfile; exec(open('/tmp/script.py').read())"
REALIZE: 80% of time is in the database query. Not the Python code itself.
ANSWER: Your script is slow because of the database query on line 42. The Python code is fine — it's waiting on the DB. Try adding an index or caching the results.

Example — honest uncertainty:
UNDERSTAND: Ulrich asks about a specific Kubernetes config he's using.
DOUBT: I don't know his exact cluster setup. I could guess wrong.
ANSWER: I'd need to see your cluster config to give a good answer. Can you share it, or want me to check?"""


class AutonomousThinker:
    """JARVIS reasons through problems — not just step-by-step, but with
    self-doubt, hypothesis testing, and the ability to change his mind."""

    def __init__(self, reasoner, executor):
        self.reasoner = reasoner
        self.executor = executor
        self._last_reasoning_trace: list[str] = []

    async def deep_think(self, question: str, context: str = "") -> str:
        """Reason through a complex problem.

        Returns the final answer, but internally goes through
        multiple steps of understanding, hypothesizing, and verifying.
        """
        accumulated_context = []
        if context:
            accumulated_context.append(f"Context: {context}")

        steps = 0
        max_steps = 7
        self._last_reasoning_trace = []

        # Initial reasoning
        prompt = f"Problem: {question}"
        if accumulated_context:
            prompt += f"\n\n{'|'.join(accumulated_context[:3])}"

        response = await self.reasoner.query(
            prompt,
            system_prompt=THINKING_PROMPT,
            history=None,
        )

        while steps < max_steps:
            steps += 1

            for line in response.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue

                # Track reasoning for self-reflection
                self._last_reasoning_trace.append(line)

                if line.upper().startswith("ANSWER:"):
                    answer = line.split(":", 1)[1].strip()
                    return answer

                elif line.upper().startswith("SEARCH:"):
                    query = line.split(":", 1)[1].strip()
                    try:
                        from src.internet.search import web_search
                        results = await asyncio.to_thread(web_search, query, 3)
                        snippets = " | ".join(
                            f"{r['title']}: {r['body'][:120]}" for r in results[:2]
                        )
                        accumulated_context.append(f"Searched '{query}': {snippets}")
                    except Exception as e:
                        accumulated_context.append(f"Search failed for '{query}': {e}")

                elif line.upper().startswith("RUN:"):
                    cmd = line.split(":", 1)[1].strip()
                    try:
                        result = self.executor.execute(cmd, timeout=15)
                        output = result["output"][:400] if result["output"] else "no output"
                        success = "✓" if result["success"] else "✗"
                        accumulated_context.append(f"Ran '{cmd}' {success}: {output}")
                    except Exception as e:
                        accumulated_context.append(f"Command failed '{cmd}': {e}")

                elif line.upper().startswith("VERIFY:"):
                    # Verification step — add to context for next iteration
                    accumulated_context.append(f"Verify: {line.split(':', 1)[1].strip()}")

                elif line.upper().startswith("UNDERSTAND:"):
                    accumulated_context.append(f"Understanding: {line.split(':', 1)[1].strip()}")

                elif line.upper().startswith("DOUBT:"):
                    accumulated_context.append(f"Doubt: {line.split(':', 1)[1].strip()}")

                elif line.upper().startswith("REALIZE:"):
                    accumulated_context.append(f"Insight: {line.split(':', 1)[1].strip()}")

                elif line.upper().startswith("RECONSIDER:"):
                    accumulated_context.append(f"Reconsidered: {line.split(':', 1)[1].strip()}")

                elif line.upper().startswith("HYPOTHESIZE:"):
                    accumulated_context.append(f"Hypothesis: {line.split(':', 1)[1].strip()}")

            # Feed accumulated context back for next iteration
            ctx_str = "\n".join(accumulated_context[-5:])
            response = await self.reasoner.query(
                f"Original problem: {question}\n\nReasoning so far:\n{ctx_str}\n\nContinue reasoning. If ready, give your ANSWER.",
                system_prompt=THINKING_PROMPT,
                history=None,
            )

        # Ran out of steps — extract best answer
        for line in response.strip().split("\n"):
            if line.strip().upper().startswith("ANSWER:"):
                return line.strip().split(":", 1)[1].strip()

        # Last resort — use the whole response
        return response.strip()

    def should_deep_think(self, query: str, confidence: float = 0.5) -> bool:
        """Decide if a question needs deep autonomous thinking.

        Now considers JARVIS's confidence level, not just keyword matching.
        Low confidence on ANY topic warrants thinking.
        """
        q = query.lower()

        # Always think deeply when confidence is low
        if confidence < 0.4:
            return True

        # Complex question indicators
        complexity_indicators = [
            "how would you", "what's the best way",
            "analyze", "compare", "evaluate",
            "plan", "strategy", "approach",
            "why does", "why is", "explain why",
            "figure out", "work out", "solve",
            "think about", "think through",
            "what do you think", "your opinion",
            "debug", "troubleshoot", "diagnose",
            "what went wrong", "why isn't", "why doesn't",
        ]
        if any(ind in q for ind in complexity_indicators):
            return True

        # Long or multi-part questions
        if len(query.split()) > 20:
            return True

        # Questions with multiple question marks (multi-part)
        if query.count("?") > 1:
            return True

        return False

    @property
    def last_trace(self) -> list[str]:
        """Get the reasoning trace from the last deep_think call.
        Useful for debugging and self-reflection."""
        return self._last_reasoning_trace
