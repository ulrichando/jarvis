"""Skills discovery + parsing — Claude-Code-parity.

A "skill" is a named recipe the supervisor LLM can invoke at runtime
to handle a specific class of request. Skills live as markdown files
with YAML frontmatter, exactly like Claude Code's `.claude/skills/`
convention.

Discovery order (later wins on name collision — user overrides ship):

  1. `src/voice-agent/skills/<name>/SKILL.md`  — shipped defaults
  2. `~/.jarvis/skills/<name>/SKILL.md`         — user-managed

Skill file format::

    ---
    name: spotify-control
    description: Play, pause, skip tracks on Spotify
    when_to_use: |
      User wants to control music playback (play / pause / next /
      previous / current song info).
    ---

    # Spotify Control Skill

    The user wants to control their Spotify music. Use these patterns:

    **To play/pause:**
    `bash("dbus-send --print-reply --dest=org.mpris.MediaPlayer2.spotify ...")`

    ... (markdown body — gets returned as the tool result when the
    skill is invoked, becoming a system-style instruction the
    supervisor reads as guidance.)

Hot-reload: discovery runs once at module load. Add new skills
without a restart by calling `reload_skills()`. Skill changes to
existing files require a worker subprocess restart since the registry
is cached per-worker.

Designed 2026-05-11 evening at user request — parity with Claude
Code's skills system, adapted for the voice-agent's single-process
LiveKit Agents runtime.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


__all__ = [
    "Skill",
    "SkillsRegistry",
    "discover_skills",
    "load_skills",
    "reload_skills",
    "SKILLS",
]


log = logging.getLogger("jarvis.skills_loader")


@dataclass(frozen=True)
class Skill:
    """A parsed skill file. Immutable so registry consumers can hold
    references without coordination."""
    name: str
    description: str
    when_to_use: str
    body: str
    path: Path
    raw_frontmatter: dict = field(default_factory=dict)


# ── Discovery roots ─────────────────────────────────────────────────


def _default_roots() -> list[Path]:
    """User skills override shipped skills on name collision; we keep
    that order so the user's `~/.jarvis/skills/<X>/SKILL.md` always
    wins over the bundled `src/voice-agent/skills/<X>/SKILL.md`.

    Overridable via `JARVIS_SKILLS_PATHS` (colon-separated paths,
    same shape as Unix PATH). Useful for test isolation.
    """
    env_paths = os.environ.get("JARVIS_SKILLS_PATHS")
    if env_paths:
        return [Path(p) for p in env_paths.split(":") if p]
    return [
        Path(__file__).parent.parent / "skills",        # bundled defaults
        Path.home() / ".jarvis" / "skills",              # user-managed
    ]


# ── Frontmatter parser ─────────────────────────────────────────────


# Minimal YAML frontmatter parser. Skills use a tiny subset — flat
# key/value pairs with optional block-scalar values (the `|` style for
# multi-line strings). Importing PyYAML would be overkill and adds a
# transitive dep; we hand-parse the three documented fields.
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body). Empty dict + full text if no
    frontmatter block is present at the top of the file."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw, body = m.group(1), m.group(2)

    fm: dict = {}
    current_key: Optional[str] = None
    current_block_lines: list[str] = []
    block_indent: Optional[int] = None

    def _flush_block() -> None:
        nonlocal current_key, current_block_lines, block_indent
        if current_key is not None:
            # Strip the consistent indent off block-scalar lines so
            # the value is the raw multi-line string the author wrote.
            if block_indent is None:
                block_indent = 0
            text = "\n".join(
                ln[block_indent:] if len(ln) >= block_indent else ln
                for ln in current_block_lines
            ).rstrip()
            fm[current_key] = text
        current_key = None
        current_block_lines = []
        block_indent = None

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            if current_key is not None:
                current_block_lines.append(line)
            continue

        # Continuation of a block scalar?
        if current_key is not None and (line.startswith(" ") or line.startswith("\t")):
            if block_indent is None:
                block_indent = len(line) - len(line.lstrip())
            current_block_lines.append(line)
            continue

        # New key — flush any in-progress block first.
        _flush_block()

        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()
        if val == "|" or val == ">":
            current_key = key
            current_block_lines = []
            block_indent = None
            continue
        # Single-line value. Strip optional surrounding quotes.
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        fm[key] = val

    _flush_block()
    return fm, body


# ── Skill file parser ───────────────────────────────────────────────


def _parse_skill_file(path: Path) -> Optional[Skill]:
    """Parse one SKILL.md. Returns None on any failure — log + skip
    rather than crash the whole registry on a single bad file."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        log.warning(f"[skills] cannot read {path}: {e}")
        return None

    fm, body = _parse_frontmatter(text)
    if not fm:
        log.warning(f"[skills] skipping {path}: no YAML frontmatter")
        return None

    name = fm.get("name", "").strip()
    if not name:
        log.warning(f"[skills] skipping {path}: missing 'name' in frontmatter")
        return None
    description = fm.get("description", "").strip()
    when_to_use = fm.get("when_to_use", description).strip() or description
    if not description:
        log.warning(f"[skills] skipping {path}: missing 'description'")
        return None

    return Skill(
        name=name,
        description=description,
        when_to_use=when_to_use,
        body=body.strip(),
        path=path,
        raw_frontmatter=fm,
    )


# ── Registry ────────────────────────────────────────────────────────


class SkillsRegistry:
    """Holds the parsed skills. Module-level singleton `SKILLS` is
    populated by `load_skills()` at module import; consumers can
    `reload_skills()` to pick up newly-added files without a restart.
    """

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def __len__(self) -> int:
        return len(self._skills)

    def __iter__(self):
        return iter(self._skills.values())

    def names(self) -> list[str]:
        return sorted(self._skills.keys())

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def all(self) -> list[Skill]:
        return list(self._skills.values())

    def _replace(self, items: dict[str, Skill]) -> None:
        self._skills = items


# ── Public API ──────────────────────────────────────────────────────


def discover_skills(roots: Optional[list[Path]] = None) -> dict[str, Skill]:
    """Scan the discovery roots and return name → Skill. Later roots
    win on collision (user > shipped). Skips any file that can't be
    parsed; logs at WARNING."""
    out: dict[str, Skill] = {}
    if roots is None:
        roots = _default_roots()
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        # Two layouts supported:
        #   - Claude-Code-style:  <root>/<name>/SKILL.md
        #   - Flat:               <root>/<name>.md
        # The Claude-Code-style is preferred; flat is allowed for
        # quick one-file skills without a directory.
        for sub in root.iterdir():
            if sub.is_dir():
                f = sub / "SKILL.md"
                if not f.exists():
                    continue
            elif sub.is_file() and sub.suffix == ".md":
                f = sub
            else:
                continue
            sk = _parse_skill_file(f)
            if sk is None:
                continue
            out[sk.name] = sk  # user-root overrides shipped-root
    return out


def load_skills(roots: Optional[list[Path]] = None) -> SkillsRegistry:
    """Discover + populate the module-level SKILLS singleton.
    Returns the registry for convenience."""
    SKILLS._replace(discover_skills(roots))
    n = len(SKILLS)
    if n:
        log.info(f"[skills] loaded {n} skill(s): {', '.join(SKILLS.names())}")
    else:
        log.info("[skills] no skills found in discovery roots")
    return SKILLS


def reload_skills() -> SkillsRegistry:
    """Public re-scan trigger — for the `reload_skills` voice tool or
    a SIGHUP handler."""
    return load_skills()


# Module-level singleton. Auto-populates on first import; consumers
# can `from pipeline.skills_loader import SKILLS` and treat it as
# read-only data.
SKILLS = SkillsRegistry()
load_skills()
