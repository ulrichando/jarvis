"""Magic Docs prompt templates."""

from __future__ import annotations


def get_update_prompt_template() -> str:
    """Get the Magic Docs update prompt template."""
    return """Based on the user conversation above, update the Magic Doc file to incorporate any NEW learnings.

The file {{docPath}} has already been read. Current contents:
<current_doc_content>
{{docContents}}
</current_doc_content>

Document title: {{docTitle}}
{{customInstructions}}

Your ONLY task is to use the Edit tool to update the documentation file if there is substantial new information.

CRITICAL RULES:
- Preserve the Magic Doc header exactly as-is
- Keep the document CURRENT with the latest state
- Update information IN-PLACE -- do NOT append historical notes
- Remove or replace outdated information
- Fix obvious errors
- Keep the document well organized

DOCUMENTATION PHILOSOPHY:
- BE TERSE. High signal only.
- Focus on OVERVIEWS, ARCHITECTURE, and ENTRY POINTS
- Do NOT duplicate information obvious from reading source code

What TO document:
- High-level architecture and system design
- Non-obvious patterns, conventions, or gotchas
- Key entry points and where to start reading code
- Important design decisions and their rationale
- Critical dependencies or integration points

What NOT to document:
- Anything obvious from reading the code itself
- Detailed implementation steps
- Exhaustive API docs"""
