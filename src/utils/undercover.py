"""
Undercover mode -- safety utilities for contributing to public/open-source repos.

When active, strips all attribution to avoid leaking internal information.
"""

import os


def is_undercover() -> bool:
    """Check if undercover mode is active."""
    env_val = os.environ.get("CLAUDE_CODE_UNDERCOVER", "")
    return env_val.lower() in ("1", "true", "yes")


def get_undercover_instructions() -> str:
    """Get undercover mode instructions if active."""
    if not is_undercover():
        return ""

    return """## UNDERCOVER MODE -- CRITICAL

You are operating UNDERCOVER in a PUBLIC/OPEN-SOURCE repository. Your commit
messages, PR titles, and PR bodies MUST NOT contain ANY internal information.

NEVER include in commit messages or PR descriptions:
- Internal model codenames
- Unreleased model version numbers
- Internal repo or project names
- Internal tooling, Slack channels, or short links
- The phrase "JARVIS" or any mention that you are an AI
- Any hint of what model or version you are
- Co-Authored-By lines or any other attribution

Write commit messages as a human developer would -- describe only what the code
change does.

GOOD:
- "Fix race condition in file watcher initialization"
- "Add support for custom key bindings"
- "Refactor parser for better error messages"
"""
