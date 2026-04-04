"""JARVIS Agent Planner — breaks complex tasks into steps and executes them.

JARVIS can now handle multi-step tasks autonomously:
1. Break task into steps
2. Execute each step
3. Use output of one step as input to the next
4. Report final result

This is what makes JARVIS truly agentic.
"""

import json
from src.reasoning.groq_client import GroqReasoner
from src.commands_brain.executor import CommandExecutor


PLANNER_PROMPT = """You are a task planner. Break the user's request into a list of steps.

Return a JSON array of steps. Each step has:
- "action": one of "think", "command", "search", "answer"
- "detail": what to do in this step
- "depends_on": step index this depends on (null if independent)

Example for "find all python files and count them":
[
  {"action": "command", "detail": "find . -name '*.py' -type f", "depends_on": null},
  {"action": "command", "detail": "find . -name '*.py' -type f | wc -l", "depends_on": null},
  {"action": "answer", "detail": "Report the count of Python files found", "depends_on": 1}
]

Return ONLY valid JSON. No markdown, no explanation."""


class AgentPlanner:
    """Plans and executes multi-step tasks."""

    def __init__(self):
        self.reasoner = GroqReasoner()
        self.executor = CommandExecutor(safety_mode=False)

    async def plan(self, task: str) -> list[dict]:
        """Break a task into executable steps."""
        response = await self.reasoner.query(
            task,
            system_prompt=PLANNER_PROMPT,
            history=None,
        )

        # Parse JSON
        response = response.strip()
        if response.startswith("```"):
            lines = response.split("\n")
            response = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            steps = json.loads(response)
            if isinstance(steps, list) and steps:
                return steps
        except json.JSONDecodeError:
            pass

        # Fallback — treat as a simple answer task
        return [{"action": "answer", "detail": task, "depends_on": None}]

    async def execute(self, task: str) -> str:
        """Plan and execute a multi-step task. Returns final result."""
        steps = await self.plan(task)

        if len(steps) == 1 and steps[0]["action"] == "answer":
            # Simple task — just answer directly
            return await self.reasoner.query(
                task,
                system_prompt="Answer directly in simple language.",
                history=None,
            )

        results = {}
        for i, step in enumerate(steps):
            action = step.get("action", "think")
            detail = step.get("detail", "")

            # Inject previous results if this step depends on another
            dep = step.get("depends_on")
            if dep is not None and dep in results:
                detail += f"\n\nPrevious result: {results[dep]}"

            if action == "command":
                result = self.executor.execute(detail)
                results[i] = result["output"] if result["success"] else f"Error: {result['output']}"

            elif action == "think":
                results[i] = await self.reasoner.query(
                    detail,
                    system_prompt="Think through this step. Be brief.",
                    history=None,
                )

            elif action == "search":
                # For now, use a command to search
                result = self.executor.execute(f"grep -r '{detail}' . 2>/dev/null | head -20")
                results[i] = result["output"]

            elif action == "answer":
                # Final synthesis
                context = "\n".join(f"Step {k}: {v[:200]}" for k, v in results.items())
                results[i] = await self.reasoner.query(
                    f"Based on these results, answer the original question: {task}\n\n{context}",
                    system_prompt="Give a direct, simple answer. No filler.",
                    history=None,
                )

        # Return the last result
        if results:
            return results[max(results.keys())]
        return "Done."

    def is_complex_task(self, query: str) -> bool:
        """Heuristic: does this query need multi-step planning?"""
        indicators = [
            "and then", "after that", "step by step",
            "find and", "search and", "install",
            "create a", "build a", "set up",
            "scan", "enumerate", "exploit",
            "download", "compile", "deploy",
        ]
        q = query.lower()
        return any(ind in q for ind in indicators) or len(query.split()) > 15
