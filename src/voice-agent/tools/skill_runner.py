"""Skill-runner tools for the supervisor.

Exposes the skills registry from `pipeline.skills_loader` as two
@function_tool entry points the voice LLM can call:

  - `list_skills()` — enumerate available skills with when-to-use
    blurbs. Voice-discoverable: "Jarvis, what skills do you have?"
    triggers this.

  - `run_skill(name)` — load a skill's body as a tool result. The
    supervisor reads that as guidance for the current turn and
    executes the recipe using its existing tool surface (bash,
    edit, write, screenshot, etc.). The skill body is just markdown
    with examples — it does NOT execute arbitrary code on its own.

Claude-Code-parity design: skills are docs, not sandboxes. They tell
the LLM how to combine existing tools to achieve a named outcome.
The supervisor is still on the hook for actually calling those tools.

Added 2026-05-11 evening — see pipeline/skills_loader.py for the
file format and discovery rules.
"""
from __future__ import annotations

import logging

from livekit.agents.llm import function_tool


logger = logging.getLogger("jarvis.skill_runner")


@function_tool
async def list_skills() -> str:
    """List all skills currently available to JARVIS.

    Voice-trigger phrases that should call this tool:
      - "what skills do you have?"
      - "list your skills"
      - "what can you do?"  (informational variant)

    Returns a multi-line summary of `name — when_to_use`. The user
    can then ask you to "run the X skill" or just describe the task
    and you decide whether one of the skills fits — if so, call
    `run_skill(name)`.

    Skills live as markdown files under `~/.jarvis/skills/<name>/SKILL.md`
    (user) or `src/voice-agent/skills/<name>/SKILL.md` (shipped). Each
    file's YAML frontmatter declares the name + description + when-to-use;
    the body is the recipe you follow when invoked.
    """
    # Lazy import to dodge module-load-order issues with the registry's
    # auto-load (skills_loader runs at import; this tool module is
    # imported by jarvis_agent which is loaded later).
    from pipeline.skills_loader import SKILLS

    skills = SKILLS.all()
    if not skills:
        return "(no skills installed yet)"

    lines = [f"{len(skills)} skill(s) available:"]
    for s in skills:
        # Compact one-line summary, voice-friendly.
        # Strip newlines from when_to_use so the output is single-paragraph.
        wtu = " ".join(s.when_to_use.split())
        lines.append(f"  • {s.name} — {wtu}")
    return "\n".join(lines)


@function_tool
async def run_skill(name: str) -> str:
    """Load a skill's recipe so you can follow it for the current turn.

    Call this when one of the skills from `list_skills()` matches what
    the user is asking for. The skill's markdown body is returned as
    the tool result — read it as guidance and execute the recipe
    using your existing tools (bash, screenshot, web_fetch, etc.).

    Args:
        name: The skill's `name` field (e.g. 'spotify-control').

    Returns:
        The skill's markdown body on success — treat the contents as
        a turn-scoped instruction. If the skill is unknown, returns
        a `(unknown skill: ...)` message listing available names.

    Skills don't run any code on their own. They are recipes — text
    instructions that combine your existing tools. So calling
    `run_skill` doesn't trigger any side effects; you still have to
    call the actual tools the recipe describes.
    """
    from pipeline.skills_loader import SKILLS

    sk = SKILLS.get(name.strip())
    if sk is None:
        available = ", ".join(SKILLS.names()) or "(none)"
        return f"(unknown skill: {name!r}; available: {available})"
    logger.info(f"[skill-runner] loaded skill {name!r} ({len(sk.body)} chars)")
    # The body is a markdown recipe. Prefix with a brief header so the
    # supervisor LLM knows what it's reading and can act accordingly.
    return (
        f"=== SKILL: {sk.name} ===\n"
        f"{sk.description}\n\n"
        f"--- Recipe ---\n"
        f"{sk.body}\n\n"
        f"Now execute the recipe above using your tools, then respond "
        f"to the user with the outcome (one short sentence)."
    )
