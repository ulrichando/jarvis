"""Evolution Generator — JARVIS writes its own improvements.

Takes analysis results and generates Python code improvements.
Scoped ONLY to brain/ directory — never modifies core infrastructure.
"""

from brain.reasoning.groq_client import GroqReasoner


GENERATOR_PROMPT = """You are JARVIS's self-improvement engine. You analyze usage patterns and generate Python code improvements.

RULES:
- Only generate Python code that improves brain/ modules
- Code must be a complete, importable Python module
- Include docstrings explaining what the improvement does
- Focus on: shortcuts for common queries, fixes for failures, performance optimizations
- The code should be safe — no destructive operations, no file deletion
- Output ONLY the Python code, nothing else. No markdown, no explanation.

CURRENT ARCHITECTURE:
- brain/reasoning/groq_client.py — GroqReasoner with query() method
- brain/commands/executor.py — CommandExecutor with execute(), open_app(), open_url()
- brain/memory/store.py — MemoryStore with learn(), recall(), add_turn()
- brain/evolution/evolved_shortcuts.py — where shortcuts live (import and use)
"""


class EvolutionGenerator:
    """Generates code improvements from analysis results."""

    def __init__(self):
        self.reasoner = GroqReasoner()

    async def generate_shortcuts(self, opportunities: list[dict]) -> str | None:
        """Generate shortcut handlers for common queries."""
        shortcut_opps = [o for o in opportunities if o["type"] == "shortcut"]
        if not shortcut_opps:
            return None

        queries = "\n".join(f'- "{o["query"]}" (asked {o["count"]} times)' for o in shortcut_opps)

        prompt = f"""Generate a Python module with shortcut handlers for these frequently asked queries:

{queries}

The module should have a function `check_shortcut(query: str) -> str | None` that:
- Takes a user query string
- Returns a direct response if it matches a known shortcut (fuzzy matching)
- Returns None if no shortcut matches

Example:
```python
def check_shortcut(query: str) -> str | None:
    q = query.lower().strip()
    if "time" in q and ("what" in q or "current" in q):
        import datetime
        return datetime.datetime.now().strftime("[show:time]")
    return None
```

Generate the full module with shortcuts for ALL the queries listed above."""

        code = await self.reasoner.query(
            prompt,
            system_prompt=GENERATOR_PROMPT,
            history=None,
        )

        # Strip markdown code fences if present
        code = code.strip()
        if code.startswith("```"):
            lines = code.split("\n")
            code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        return code

    async def generate_fix(self, failure: dict) -> str | None:
        """Generate a fix for a failed interaction."""
        prompt = f"""A JARVIS interaction failed:
Query: "{failure['query']}"
Error: {failure['error']}

Generate a Python function `handle_error(query: str, error: str) -> str` that:
- Detects this specific error pattern
- Returns a helpful fallback response
- Returns None if it's not a known error pattern"""

        code = await self.reasoner.query(
            prompt,
            system_prompt=GENERATOR_PROMPT,
            history=None,
        )

        code = code.strip()
        if code.startswith("```"):
            lines = code.split("\n")
            code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        return code
