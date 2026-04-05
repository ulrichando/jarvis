"""Evolved shortcuts — DISABLED.

All queries go through the LLM directly, just like Claude.
The LLM decides when to use bash, web_search, read_file, etc.
No hardcoded responses.
"""


def check_shortcut(query: str) -> str | None:
    """No shortcuts — let the LLM handle everything."""
    return None
