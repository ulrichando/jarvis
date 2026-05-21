"""One-shot bulk-integration script.

Usage (run from src/voice-agent/):
    python skills/_bulk_integrate.py

Reads every hermes/skills/**/SKILL.md, flattens to skills/<name>/SKILL.md,
scrubs frontmatter (author, metadata.hermes → metadata.jarvis, keeps
name/description/version/license/platforms/dependencies), and does a
conservative body scrub of standalone "Hermes Agent"/"Hermes" prose
→ "JARVIS" where clearly a name, not a CLI/tool symbol.

Prints a summary of:
  - Skills copied
  - Collisions (first wins)
  - Oversized skills (>100 000 chars) — truncated at frontmatter boundary
  - Skills whose bodies still contain hermes-tool refs (deferred rewrite)
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent.parent.parent  # jarvis/
HERMES_SKILLS = REPO_ROOT / "hermes" / "skills"
TARGET_ROOT = Path(__file__).parent  # src/voice-agent/skills/

MAX_SKILL_CONTENT_CHARS = 100_000  # from skills_authoring.py

# ── Frontmatter scrub ──────────────────────────────────────────────────────────
# Keys we unconditionally KEEP (and will emit back out).
KEEP_KEYS = {"name", "description", "version", "license", "platforms",
             "dependencies", "when_to_use", "title"}

# Regex for the top-level YAML frontmatter block.
_FM_RE = re.compile(r"\A(---\s*\n)(.*?)(\n---\s*\n)(.*)", re.DOTALL)


def _scrub_frontmatter(text: str, skill_name: str) -> str:
    """Return the text with a sanitised frontmatter block.

    Strategy: re-parse the raw block line-by-line (no PyYAML), drop
    `metadata.hermes.*`, rename `metadata.hermes` → `metadata.jarvis`,
    and rewrite `author: Hermes Agent*` → `author: JARVIS`.  Other keys
    are emitted as-is.
    """
    m = _FM_RE.match(text)
    if not m:
        return text  # no frontmatter — loader will warn and skip

    open_dashes, raw_fm, close_dashes, body = (
        m.group(1), m.group(2), m.group(3), m.group(4)
    )

    out_lines: list[str] = []
    in_metadata = False      # inside 'metadata:' block
    in_hermes_block = False  # inside 'metadata.hermes:' sub-block
    skip_current = False     # skip an in-progress block
    pending_indent: int | None = None

    for raw_line in raw_fm.splitlines():
        stripped = raw_line.strip()

        # ── author rewrite ────────────────────────────────────────────
        if re.match(r"^author\s*:", raw_line):
            val = raw_line.split(":", 1)[1].strip()
            if re.search(r"Hermes Agent", val, re.IGNORECASE):
                # rewrite author entirely
                out_lines.append("author: JARVIS")
            else:
                out_lines.append(raw_line)
            continue

        # ── metadata block detection ──────────────────────────────────
        if re.match(r"^metadata\s*:", raw_line):
            in_metadata = True
            in_hermes_block = False
            pending_indent = None
            out_lines.append(raw_line)
            continue

        if in_metadata:
            indent = len(raw_line) - len(raw_line.lstrip())
            if indent == 0 and stripped:
                # Exited the metadata block
                in_metadata = False
                in_hermes_block = False
            elif re.match(r"\s+hermes\s*:", raw_line):
                # Replace `  hermes:` with `  jarvis:`
                out_lines.append(raw_line.replace("hermes:", "jarvis:", 1))
                in_hermes_block = True
                pending_indent = indent
                continue
            elif in_hermes_block:
                # sub-keys of the hermes block — keep as-is (just renamed)
                if stripped and indent <= pending_indent:
                    in_hermes_block = False
                else:
                    out_lines.append(raw_line)
                    continue
            # Fall-through: emit metadata sub-keys verbatim

        out_lines.append(raw_line)

    new_fm = "\n".join(out_lines)
    return open_dashes + new_fm + close_dashes + body


# ── Body scrub ─────────────────────────────────────────────────────────────────
# Conservative: only replace standalone "Hermes Agent" or "Hermes" when used
# as a proper name / product name — NOT inside code blocks, tool names,
# import paths, or shell commands.
#
# Safe replacements:
#   "Hermes Agent" → "JARVIS"        (standalone prose)
#   "the Hermes" / "Hermes's" / "via Hermes" → "JARVIS" / "JARVIS's" / "via JARVIS"
#
# NOT replaced (left for per-skill recipe rewrite):
#   delegate_task(, hermes_tool, from hermes, hermes/tools, hermes-cli,
#   hermes.py, HERMES_*, kanban_, yb_query, web_extract, etc.

_HERMES_AGENT_RE = re.compile(r"\bHermes Agent\b")
_HERMES_STANDALONE_RE = re.compile(
    r"(?<![`/\-])(?<!\w)\bHermes\b(?!\s*[-_./]|\s+Tool|\s+CLI|\s+TUI|\s+tool)",
    re.IGNORECASE,
)

# Hermes-tool references that flag a skill for deferred recipe rewrite.
_HERMES_TOOL_RE = re.compile(
    r"(hermes_tool|hermes-cli|hermes\.tool|hermes\.py|from hermes\b|import hermes\b"
    r"|hermes/tools|hermes/agent|delegate_task\(|web_extract\(|kanban_|yb_query|yb_send"
    r"|KANBAN_GUIDANCE|agent/prompt_builder)",
    re.IGNORECASE,
)


def _scrub_body(body: str) -> tuple[str, bool]:
    """Return (scrubbed_body, needs_recipe_rewrite).

    Replaces standalone Hermes-as-name uses with JARVIS.
    Sets needs_recipe_rewrite=True if hermes-tool symbols remain.
    """
    # Replace "Hermes Agent" first (more specific)
    body = _HERMES_AGENT_RE.sub("JARVIS", body)
    # Replace standalone "Hermes" as a name (conservative)
    body = _HERMES_STANDALONE_RE.sub("JARVIS", body)
    needs_rewrite = bool(_HERMES_TOOL_RE.search(body))
    return body, needs_rewrite


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    # Enumerate all hermes skills
    hermes_skills = sorted(HERMES_SKILLS.rglob("SKILL.md"))

    copied: list[str] = []
    collisions: list[tuple[str, Path, Path]] = []  # (name, kept, dropped)
    deferred: list[str] = []  # skills needing per-skill recipe rewrite
    oversize_truncated: list[str] = []
    seen: dict[str, Path] = {}  # name → source path (first wins)

    for src_path in hermes_skills:
        skill_name = src_path.parent.name  # leaf folder name

        # Collision check — first wins
        if skill_name in seen:
            collisions.append((skill_name, seen[skill_name], src_path))
            print(f"  COLLISION DROPPED: {skill_name} ({src_path}) kept {seen[skill_name]}")
            continue
        seen[skill_name] = src_path

        text = src_path.read_text(encoding="utf-8")

        # ── Frontmatter scrub ─────────────────────────────────────────
        text = _scrub_frontmatter(text, skill_name)

        # ── Body scrub ────────────────────────────────────────────────
        # Split at second ---
        fm_match = _FM_RE.match(text)
        if fm_match:
            fm_part = fm_match.group(1) + fm_match.group(2) + fm_match.group(3)
            body_part = fm_match.group(4)
            body_part, needs_rewrite = _scrub_body(body_part)
            text = fm_part + body_part
        else:
            _, needs_rewrite = _scrub_body(text)

        if needs_rewrite:
            deferred.append(skill_name)

        # ── Oversize guard ────────────────────────────────────────────
        if len(text) > MAX_SKILL_CONTENT_CHARS:
            # Truncate body to fit; keep frontmatter intact.
            fm_match2 = _FM_RE.match(text)
            if fm_match2:
                fm_section = fm_match2.group(1) + fm_match2.group(2) + fm_match2.group(3)
                body_section = fm_match2.group(4)
                allowed_body = MAX_SKILL_CONTENT_CHARS - len(fm_section) - 200
                # Truncate at a line boundary
                truncated_body = body_section[:allowed_body]
                last_nl = truncated_body.rfind("\n")
                if last_nl > 0:
                    truncated_body = truncated_body[:last_nl]
                truncated_body += (
                    "\n\n---\n\n"
                    "_[Skill body truncated during JARVIS import — "
                    "see hermes/skills original for full content.]_\n"
                )
                text = fm_section + truncated_body
                oversize_truncated.append(skill_name)
                print(f"  OVERSIZE TRUNCATED: {skill_name} ({len(src_path.read_bytes())} bytes → {len(text)})")

        # ── Write to target ───────────────────────────────────────────
        dest_dir = TARGET_ROOT / skill_name
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / "SKILL.md"
        dest_path.write_text(text, encoding="utf-8")
        copied.append(skill_name)

    # Summary
    print(f"\n{'='*60}")
    print(f"Copied: {len(copied)} skills")
    print(f"Collisions dropped: {len(collisions)}")
    print(f"Oversize truncated: {len(oversize_truncated)}: {oversize_truncated}")
    print(f"Deferred (body recipe rewrite needed): {len(deferred)}")
    print(f"  {sorted(deferred)}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
