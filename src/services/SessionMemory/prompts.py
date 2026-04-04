"""Session memory prompt templates."""

from __future__ import annotations

MAX_SECTION_LENGTH = 2000
MAX_TOTAL_SESSION_MEMORY_TOKENS = 12000

DEFAULT_SESSION_MEMORY_TEMPLATE = """
# Session Title
_A short and distinctive 5-10 word descriptive title for the session._

# Current State
_What is actively being worked on right now? Pending tasks not yet completed._

# Task specification
_What did the user ask to build? Design decisions or explanatory context._

# Files and Functions
_Important files, what they contain and why they are relevant._

# Workflow
_Bash commands usually run and in what order._

# Errors & Corrections
_Errors encountered and how they were fixed._

# Codebase and System Documentation
_Important system components. How they work/fit together._

# Learnings
_What has worked well? What has not? What to avoid?_

# Key results
_If the user asked for specific output, repeat the exact result here._

# Worklog
_Step by step, what was attempted, done? Very terse summary._
"""


def get_default_update_prompt() -> str:
    """Get the default session memory update prompt template."""
    return (
        "Based on the user conversation above, update the session notes file.\n\n"
        "The file {{notesPath}} has already been read for you. Current contents:\n"
        "<current_notes_content>\n{{currentNotes}}\n</current_notes_content>\n\n"
        "Update the notes to reflect the current state of the conversation.\n"
        "Focus on what changed since the last update."
    )
