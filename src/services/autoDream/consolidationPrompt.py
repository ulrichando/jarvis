"""
Consolidation prompt builder for auto-dream memory consolidation.
"""

from __future__ import annotations

ENTRYPOINT_NAME = "MEMORY.md"
MAX_ENTRYPOINT_LINES = 200
DIR_EXISTS_GUIDANCE = "If the directory doesn't exist yet, create it."


def build_consolidation_prompt(
    memory_root: str,
    transcript_dir: str,
    extra: str = "",
) -> str:
    """Build the consolidation prompt for memory dream sessions."""
    extra_section = f"\n\n## Additional context\n\n{extra}" if extra else ""

    return f"""# Dream: Memory Consolidation

You are performing a dream -- a reflective pass over your memory files. Synthesize what you've learned recently into durable, well-organized memories so that future sessions can orient quickly.

Memory directory: `{memory_root}`
{DIR_EXISTS_GUIDANCE}

Session transcripts: `{transcript_dir}` (large JSONL files -- grep narrowly, don't read whole files)

---

## Phase 1 -- Orient

- `ls` the memory directory to see what already exists
- Read `{ENTRYPOINT_NAME}` to understand the current index
- Skim existing topic files so you improve them rather than creating duplicates
- If `logs/` or `sessions/` subdirectories exist (assistant-mode layout), review recent entries there

## Phase 2 -- Gather recent signal

Look for new information worth persisting. Sources in rough priority order:

1. **Daily logs** (`logs/YYYY/MM/YYYY-MM-DD.md`) if present -- these are the append-only stream
2. **Existing memories that drifted** -- facts that contradict something you see in the codebase now
3. **Transcript search** -- if you need specific context, grep the JSONL transcripts for narrow terms:
   `grep -rn "<narrow term>" {transcript_dir}/ --include="*.jsonl" | tail -50`

Don't exhaustively read transcripts. Look only for things you already suspect matter.

## Phase 3 -- Consolidate

For each thing worth remembering, write or update a memory file at the top level of the memory directory.

Focus on:
- Merging new signal into existing topic files rather than creating near-duplicates
- Converting relative dates ("yesterday", "last week") to absolute dates
- Deleting contradicted facts -- if today's investigation disproves an old memory, fix it at the source

## Phase 4 -- Prune and index

Update `{ENTRYPOINT_NAME}` so it stays under {MAX_ENTRYPOINT_LINES} lines AND under ~25KB. It's an **index**, not a dump -- each entry should be one line under ~150 characters: `- [Title](file.md) -- one-line hook`. Never write memory content directly into it.

- Remove pointers to memories that are now stale, wrong, or superseded
- Demote verbose entries
- Add pointers to newly important memories
- Resolve contradictions

---

Return a brief summary of what you consolidated, updated, or pruned. If nothing changed, say so.{extra_section}"""
