"""AGENTS.md — consolidated structured memory for session injection.

Inspired by DeepAgents' AGENTS.md pattern: a single human-readable file that
summarises all learned patterns, behavioral rules, user preferences, and project
context.  This file is:

  1. Auto-generated  from individual memory/*.md files via generate()
  2. Auto-loaded     at session start and injected into the system prompt
  3. Manually editable — edits survive regeneration (generation is additive)

Loading priority (later overrides earlier):
  ~/.jarvis/AGENTS.md       — user-level patterns (global)
  .jarvis/AGENTS.md         — project-level overrides
  .jarvis/agents/*.md       — per-agent overrides (if any)

Sections produced:
  ## Behavioral Rules      ← from feedback_*.md memories
  ## User Profile          ← from user_*.md memories
  ## Project Context       ← from project_*.md memories
  ## References            ← from reference_*.md memories
  ## Learned Patterns      ← from lattice SKILLs (optional, high-strength only)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.config import JARVIS_HOME

log = logging.getLogger("jarvis.memory.agents")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AGENTS_MD_FILENAME = "AGENTS.md"
_SECTION_MAP = {
    "feedback": "Behavioral Rules",
    "user":     "User Profile",
    "project":  "Project Context",
    "reference": "References",
}
_SECTION_ORDER = ["Behavioral Rules", "User Profile", "Project Context", "References"]

MAX_SECTION_CHARS = 4000   # per-section cap to keep prompt lean
MAX_TOTAL_CHARS   = 12000  # total cap for the injected block


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class AgentsSection:
    title: str
    entries: list[str] = field(default_factory=list)

    def render(self, max_chars: int = MAX_SECTION_CHARS) -> str:
        if not self.entries:
            return ""
        header = f"## {self.title}"
        lines = [header]
        char_count = len(header)
        for entry in self.entries:
            entry = entry.strip()
            if not entry:
                continue
            # Wrap each entry as a bullet unless it already has markdown structure
            if entry.startswith("#") or entry.startswith("-") or entry.startswith("*"):
                item = entry
            else:
                item = f"- {entry}"
            if char_count + len(item) + 1 > max_chars:
                lines.append("  *(truncated — see ~/.jarvis/memory/ for full content)*")
                break
            lines.append(item)
            char_count += len(item) + 1
        return "\n".join(lines)


@dataclass
class AgentsMemoryDoc:
    """In-memory representation of an AGENTS.md file."""
    role: str = "JARVIS autonomous agent"
    version: int = 1
    updated: str = ""
    sections: dict[str, AgentsSection] = field(default_factory=dict)
    raw_extra: str = ""   # preserves manually written sections we don't own

    def __post_init__(self):
        if not self.updated:
            self.updated = time.strftime("%Y-%m-%d")

    def get_or_create_section(self, title: str) -> AgentsSection:
        if title not in self.sections:
            self.sections[title] = AgentsSection(title=title)
        return self.sections[title]

    def to_markdown(self) -> str:
        """Serialize to AGENTS.md content."""
        lines = [
            "---",
            f"role: \"{self.role}\"",
            f"version: {self.version}",
            f"updated: {self.updated}",
            "---",
            "",
        ]
        for section_title in _SECTION_ORDER:
            sec = self.sections.get(section_title)
            if sec and sec.entries:
                lines.append(sec.render(max_chars=999999))  # no cap when writing to disk
                lines.append("")
        if self.raw_extra:
            lines.append(self.raw_extra.strip())
            lines.append("")
        return "\n".join(lines)

    def to_system_prompt(self, max_chars: int = MAX_TOTAL_CHARS) -> str:
        """Return a compact block suitable for injection into the system prompt."""
        parts = ["[Learned agent context — follow these patterns:]"]
        char_used = len(parts[0])
        per_section = max_chars // max(len(self.sections), 1)

        for section_title in _SECTION_ORDER:
            sec = self.sections.get(section_title)
            if sec and sec.entries:
                rendered = sec.render(max_chars=min(per_section, MAX_SECTION_CHARS))
                if char_used + len(rendered) > max_chars:
                    break
                parts.append(rendered)
                char_used += len(rendered)

        if self.raw_extra.strip():
            extra = self.raw_extra.strip()
            if char_used + len(extra) < max_chars:
                parts.append(extra)

        return "\n\n".join(parts)

    def is_empty(self) -> bool:
        return all(not sec.entries for sec in self.sections.values()) and not self.raw_extra


# ---------------------------------------------------------------------------
# Parser — reads an existing AGENTS.md file
# ---------------------------------------------------------------------------


def _parse_agents_md(text: str) -> AgentsMemoryDoc:
    """Parse an AGENTS.md file back into an AgentsMemoryDoc."""
    doc = AgentsMemoryDoc()

    # Parse frontmatter
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            fm_block = text[3:end].strip()
            for line in fm_block.splitlines():
                if ":" in line:
                    key, _, val = line.partition(":")
                    key = key.strip()
                    val = val.strip().strip('"')
                    if key == "role":
                        doc.role = val
                    elif key == "version":
                        try:
                            doc.version = int(val)
                        except ValueError:
                            pass
                    elif key == "updated":
                        doc.updated = val
            text = text[end + 3:].strip()

    # Parse sections
    current_section: Optional[AgentsSection] = None
    extra_lines: list[str] = []
    owned_titles = set(_SECTION_ORDER)

    for line in text.splitlines():
        if line.startswith("## "):
            title = line[3:].strip()
            if title in owned_titles:
                current_section = doc.get_or_create_section(title)
                extra_lines = []   # owned section — stop accumulating extra
            else:
                current_section = None
                extra_lines.append(line)
        elif current_section is not None:
            stripped = line.strip()
            if stripped.startswith("- "):
                current_section.entries.append(stripped[2:])
            elif stripped.startswith("* "):
                current_section.entries.append(stripped[2:])
            elif stripped and not stripped.startswith("*(truncated"):
                current_section.entries.append(stripped)
        else:
            extra_lines.append(line)

    doc.raw_extra = "\n".join(extra_lines).strip()
    return doc


# ---------------------------------------------------------------------------
# AgentsMemory — main class
# ---------------------------------------------------------------------------


class AgentsMemory:
    """Manages the AGENTS.md lifecycle: generate, load, merge, inject.

    Typical usage::

        am = AgentsMemory()
        doc = am.load()                        # load from disk
        system_block = doc.to_system_prompt()  # inject into system prompt
        am.regenerate()                        # rebuild from memory/*.md files
    """

    def __init__(
        self,
        memory_dir: Optional[Path] = None,
        global_agents_path: Optional[Path] = None,
        project_agents_path: Optional[Path] = None,
    ):
        self._memory_dir = memory_dir or (JARVIS_HOME / "memory")
        self._global_path = global_agents_path or (JARVIS_HOME / AGENTS_MD_FILENAME)
        self._project_path = project_agents_path or self._find_project_agents_md()

    @staticmethod
    def _find_project_agents_md() -> Optional[Path]:
        """Walk up from cwd looking for .jarvis/AGENTS.md."""
        cwd = Path.cwd()
        for parent in [cwd] + list(cwd.parents):
            candidate = parent / ".jarvis" / AGENTS_MD_FILENAME
            if candidate.exists():
                return candidate
        return None

    # -- generation ---------------------------------------------------------

    def regenerate(self, save: bool = True) -> AgentsMemoryDoc:
        """Rebuild AGENTS.md from all memory/*.md files.

        Reads individual memory files (feedback_*, user_*, project_*, reference_*),
        groups them into sections, merges with any manually written content in the
        existing AGENTS.md, and saves back to disk.
        """
        doc = self._load_existing_global()  # preserve manual edits

        # Clear only the auto-generated sections so manual extras survive
        for title in _SECTION_ORDER:
            doc.sections[title] = AgentsSection(title=title)

        if not self._memory_dir.is_dir():
            log.debug("Memory dir not found, skipping regenerate: %s", self._memory_dir)
            return doc

        for md_file in sorted(self._memory_dir.glob("*.md")):
            if md_file.name in ("MEMORY.md", AGENTS_MD_FILENAME):
                continue
            try:
                self._absorb_memory_file(md_file, doc)
            except Exception as e:
                log.debug("Error absorbing %s: %s", md_file.name, e)

        doc.updated = time.strftime("%Y-%m-%d")
        doc.version += 1

        if save:
            self.save(doc)

        log.info("AGENTS.md regenerated: %d sections, saved=%s", len(doc.sections), save)
        return doc

    def _absorb_memory_file(self, path: Path, doc: AgentsMemoryDoc) -> None:
        """Read one memory/*.md file and add its content to the appropriate section."""
        text = path.read_text(encoding="utf-8")
        meta, body = _split_frontmatter(text)

        mem_type = meta.get("type", "")
        section_title = _SECTION_MAP.get(mem_type)
        if not section_title:
            return  # unknown type — skip

        name = meta.get("name", path.stem)
        description = meta.get("description", "")
        body = body.strip()

        section = doc.get_or_create_section(section_title)

        # Format: "**Name**: Body" — readable, not just raw text
        if body:
            entry = f"**{name}**: {body}" if name else body
        else:
            entry = f"**{name}**: {description}" if description else name
        section.entries.append(entry)

    # -- loading ------------------------------------------------------------

    def load(self) -> AgentsMemoryDoc:
        """Load and merge all AGENTS.md sources (global + project).

        Returns a merged AgentsMemoryDoc ready for system prompt injection.
        """
        doc = self._load_existing_global()

        # Project AGENTS.md overrides / extends global
        if self._project_path and self._project_path.exists():
            try:
                project_text = self._project_path.read_text(encoding="utf-8")
                project_doc = _parse_agents_md(project_text)
                _merge_docs(base=doc, override=project_doc)
                log.debug("Merged project AGENTS.md: %s", self._project_path)
            except Exception as e:
                log.debug("Failed to load project AGENTS.md: %s", e)

        return doc

    def _load_existing_global(self) -> AgentsMemoryDoc:
        if self._global_path.exists():
            try:
                text = self._global_path.read_text(encoding="utf-8")
                return _parse_agents_md(text)
            except Exception as e:
                log.debug("Failed to parse global AGENTS.md: %s", e)
        return AgentsMemoryDoc()

    # -- persistence --------------------------------------------------------

    def save(self, doc: AgentsMemoryDoc) -> None:
        self._global_path.parent.mkdir(parents=True, exist_ok=True)
        self._global_path.write_text(doc.to_markdown(), encoding="utf-8")
        log.debug("Saved AGENTS.md: %s", self._global_path)

    # -- convenience --------------------------------------------------------

    def get_system_prompt_block(self, max_chars: int = MAX_TOTAL_CHARS) -> str:
        """Load and return the system-prompt-ready block. Returns '' if empty."""
        doc = self.load()
        if doc.is_empty():
            return ""
        return doc.to_system_prompt(max_chars=max_chars)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split YAML frontmatter from body. Returns (meta_dict, body_str)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("---", 3)
    if end == -1:
        return {}, text
    meta: dict[str, str] = {}
    for line in text[3:end].strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip().strip('"')
    body = text[end + 3:].strip()
    return meta, body


def _merge_docs(base: AgentsMemoryDoc, override: AgentsMemoryDoc) -> None:
    """Merge override doc into base (in-place). Appends entries from override."""
    for title, sec in override.sections.items():
        base_sec = base.get_or_create_section(title)
        existing = set(base_sec.entries)
        for entry in sec.entries:
            if entry not in existing:
                base_sec.entries.append(entry)
    if override.raw_extra.strip():
        if base.raw_extra.strip():
            base.raw_extra += "\n\n" + override.raw_extra.strip()
        else:
            base.raw_extra = override.raw_extra.strip()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_instance: Optional[AgentsMemory] = None


def get_agents_memory() -> AgentsMemory:
    global _instance
    if _instance is None:
        _instance = AgentsMemory()
    return _instance
