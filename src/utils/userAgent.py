"""
User-Agent string helpers.

Kept dependency-free so SDK-bundled code can import without pulling in
auth and its transitive dependency tree.
"""

# Version should be set from project config
VERSION = "0.1.0"


def get_claude_code_user_agent() -> str:
    return f"claude-code/{VERSION}"
