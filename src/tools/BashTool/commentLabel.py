"""
Extract comment labels from bash commands for UI display.
"""
from __future__ import annotations

import re
from typing import Optional


def extract_bash_comment_label(command: str) -> Optional[str]:
    """If the first line of a bash command is a `# comment` (not a `#!` shebang),
    return the comment text stripped of the `#` prefix. Otherwise None.

    Under fullscreen mode this is the non-verbose tool-use label AND the
    collapse-group hint -- it's what JARVIS wrote for the human to read.
    """
    nl = command.find("\n")
    first_line = (command[:nl] if nl != -1 else command).strip()
    if not first_line.startswith("#") or first_line.startswith("#!"):
        return None
    result = re.sub(r"^#+\s*", "", first_line)
    return result or None
