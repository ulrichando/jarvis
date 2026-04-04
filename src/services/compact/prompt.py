"""
Compaction prompt templates.

Generates prompts for summarizing/compacting conversation history
to reduce token usage while preserving essential context.
"""

from __future__ import annotations

NO_TOOLS_PREAMBLE = """CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

"""


def build_compact_prompt(direction: str = "full") -> str:
    """Build the compaction prompt.

    Args:
        direction: 'full' for full conversation, 'partial' for recent messages only
    """
    analysis = _get_analysis_instruction(direction)
    return f"""{NO_TOOLS_PREAMBLE}{analysis}

After your analysis, provide your final summary inside <summary> tags. This summary should:

1. Capture ALL important context from the conversation including:
   - User goals and requirements
   - Technical decisions and approaches taken
   - File paths, code changes, and function signatures
   - Errors encountered and how they were resolved
   - User corrections and feedback
   - Current state and next steps

2. Be structured and organized with clear sections
3. Include specific details (file names, code snippets, commands)
4. Preserve the narrative flow and causal relationships
5. Be comprehensive enough that someone reading only this summary could continue the work

Important: Do NOT include tool calls, questions to the user, or commentary about the summarization process itself."""


def _get_analysis_instruction(direction: str) -> str:
    scope = "the conversation" if direction == "full" else "the recent messages"
    return f"""Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically analyze each message and section of {scope}. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly."""
