"""Rearranges pipe commands for proper stdin redirect handling."""

from __future__ import annotations

import shlex


def rearrange_pipe_command(command: str) -> str:
    """Rearrange a piped command to place stdin redirect after the first command."""
    # Complex cases - use eval fallback
    if "`" in command or "$(" in command:
        return _quote_with_eval_stdin_redirect(command)

    if _contains_shell_var(command):
        return _quote_with_eval_stdin_redirect(command)

    if _contains_control_structure(command):
        return _quote_with_eval_stdin_redirect(command)

    return _quote_with_eval_stdin_redirect(command)


def _contains_shell_var(command: str) -> bool:
    import re
    return bool(re.search(r"\$[A-Za-z_{]", command))


def _contains_control_structure(command: str) -> bool:
    keywords = {"for", "while", "until", "if", "case", "select"}
    try:
        tokens = shlex.split(command)
        return any(t in keywords for t in tokens)
    except ValueError:
        return False


def _quote_with_eval_stdin_redirect(command: str) -> str:
    """Wrap command in eval with stdin redirect."""
    escaped = command.replace("'", "'\\''")
    return f"eval '{escaped}' < /dev/null"
