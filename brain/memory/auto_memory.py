"""Auto Memory -- automatic extraction and persistence of important information
from conversations into structured memory files.

Ported from Claude Code's extractMemories service. Uses heuristic pattern
matching (no LLM dependency) to identify memory-worthy statements in user
messages and saves them as markdown files with YAML frontmatter.

Memory types:
  USER      -- identity, role, background
  FEEDBACK  -- preferences, rules, dos/don'ts
  PROJECT   -- goals, deadlines, what's being built
  REFERENCE -- URLs, external resources, documentation pointers
"""

import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from brain.config import JARVIS_HOME

log = logging.getLogger("jarvis.memory.auto")

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class MemoryType(Enum):
    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


@dataclass
class ExtractedMemory:
    """A single piece of extracted memory ready for persistence."""

    name: str
    description: str
    memory_type: MemoryType
    content: str
    source_message_idx: int = -1


# ---------------------------------------------------------------------------
# Compiled patterns (built once at import time)
# ---------------------------------------------------------------------------

USER_PATTERNS = [
    re.compile(r"I'?m\s+a\s+(.+?)(?:\.|,|$)", re.I),
    re.compile(r"I\s+work\s+(?:as|at|for|in)\s+(.+?)(?:\.|,|$)", re.I),
    re.compile(r"my\s+(?:role|job|title|position)\s+is\s+(.+?)(?:\.|,|$)", re.I),
    re.compile(r"I\s+(?:specialize|focus|work)\s+(?:in|on)\s+(.+?)(?:\.|,|$)", re.I),
]

FEEDBACK_PATTERNS = [
    re.compile(r"(?:don'?t|do\s+not|stop|never|avoid)\s+(.+?)(?:\.|!|$)", re.I),
    re.compile(r"(?:always|prefer|make\s+sure)\s+(.+?)(?:\.|!|$)", re.I),
    re.compile(r"(?:I\s+(?:like|want|need)\s+you\s+to)\s+(.+?)(?:\.|!|$)", re.I),
]

PROJECT_PATTERNS = [
    re.compile(r"(?:we'?re|I'?m)\s+(?:working\s+on|building|implementing)\s+(.+?)(?:\.|,|$)", re.I),
    re.compile(r"(?:the\s+)?deadline\s+is\s+(.+?)(?:\.|,|$)", re.I),
    re.compile(r"(?:the\s+)?goal\s+is\s+(.+?)(?:\.|,|$)", re.I),
]

REFERENCE_PATTERNS = [
    re.compile(
        r"(?:check|see|look\s+at|refer\s+to)\s+(?:the\s+)?(.+?)\s+(?:for|at|in)\s+(.+?)(?:\.|,|$)",
        re.I,
    ),
    re.compile(
        r"(?:bugs|issues|tickets)\s+(?:are\s+)?(?:tracked|logged|filed)\s+(?:in|at|on)\s+(.+?)(?:\.|,|$)",
        re.I,
    ),
    re.compile(r"(https?://\S+)", re.I),
]

# Map pattern lists to their memory type and a name/description generator
_PATTERN_GROUPS: list[tuple[list[re.Pattern], MemoryType, str]] = [
    (USER_PATTERNS, MemoryType.USER, "user_profile"),
    (FEEDBACK_PATTERNS, MemoryType.FEEDBACK, "feedback"),
    (PROJECT_PATTERNS, MemoryType.PROJECT, "project"),
    (REFERENCE_PATTERNS, MemoryType.REFERENCE, "reference"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def sanitize_filename(name: str) -> str:
    """Lowercase, replace spaces with underscores, strip non-alphanum chars.

    Truncates to 50 characters.
    """
    name = name.lower().strip()
    name = name.replace(" ", "_")
    name = re.sub(r"[^a-z0-9_\-]", "", name)
    return name[:50]


def _word_set(text: str) -> set[str]:
    """Return the set of lowercase alphanumeric words in *text*."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


# ---------------------------------------------------------------------------
# MemoryExtractor
# ---------------------------------------------------------------------------


class MemoryExtractor:
    """Extracts durable memories from conversation messages and persists them
    as markdown files in the memory directory.

    Usage::

        extractor = MemoryExtractor()
        n_saved = extractor.extract_and_save(messages)
    """

    def __init__(self, memory_dir: str = ""):
        if memory_dir:
            self._memory_dir = Path(memory_dir)
        else:
            self._memory_dir = JARVIS_HOME / "memory"

        self._extracted_message_ids: set[int] = set()
        self._extraction_count: int = 0

    # -- extraction ---------------------------------------------------------

    def extract_from_messages(self, messages: list[dict]) -> list[ExtractedMemory]:
        """Scan user messages for memory-worthy patterns.

        Uses compiled regex patterns to identify statements about identity,
        preferences, project context, and external references.  Skips messages
        already processed (tracked by index).

        Returns a list of :class:`ExtractedMemory` instances.
        """
        memories: list[ExtractedMemory] = []

        for idx, msg in enumerate(messages):
            if idx in self._extracted_message_ids:
                continue

            role = msg.get("role", "")
            content = msg.get("content", "")

            if role != "user" or not isinstance(content, str) or not content.strip():
                continue

            for patterns, mem_type, base_name in _PATTERN_GROUPS:
                for pat in patterns:
                    match = pat.search(content)
                    if match:
                        matched_text = match.group(0).strip()
                        # Use first capture group for the name slug
                        captured = match.group(1).strip() if match.lastindex else matched_text
                        slug = sanitize_filename(captured[:40])
                        name = f"{base_name}_{slug}" if slug else base_name

                        memories.append(
                            ExtractedMemory(
                                name=name,
                                description=matched_text[:120],
                                memory_type=mem_type,
                                content=matched_text,
                                source_message_idx=idx,
                            )
                        )
                        # Only one memory per pattern group per message
                        break

            self._extracted_message_ids.add(idx)

        return memories

    # -- persistence --------------------------------------------------------

    def save_memory(self, memory: ExtractedMemory) -> str:
        """Save a single memory to disk as a markdown file with YAML frontmatter.

        Returns the absolute file path.
        """
        self._memory_dir.mkdir(parents=True, exist_ok=True)

        safe_name = sanitize_filename(memory.name)
        filename = f"{memory.memory_type.value}_{safe_name}.md"
        filepath = self._memory_dir / filename

        frontmatter = (
            f"---\n"
            f"name: {memory.name}\n"
            f"description: {memory.description}\n"
            f"type: {memory.memory_type.value}\n"
            f"---\n"
        )
        text = f"{frontmatter}\n{memory.content}\n"
        filepath.write_text(text, encoding="utf-8")
        log.debug("Saved memory: %s -> %s", memory.name, filepath)
        return str(filepath)

    def update_index(self) -> None:
        """Regenerate the MEMORY.md index from all memory files in the directory."""
        self._memory_dir.mkdir(parents=True, exist_ok=True)

        entries: list[str] = []
        for md_file in sorted(self._memory_dir.glob("*.md")):
            if md_file.name == "MEMORY.md":
                continue
            meta = self._read_frontmatter(md_file)
            if meta:
                title = meta.get("name", md_file.stem)
                desc = meta.get("description", "")
                entries.append(f"- [{title}]({md_file.name}) -- {desc}")

        index_path = self._memory_dir / "MEMORY.md"
        content = "# Auto Memory Index\n\n" + "\n".join(entries) + "\n"
        index_path.write_text(content, encoding="utf-8")
        log.debug("Updated memory index: %s (%d entries)", index_path, len(entries))

    def load_existing_memories(self) -> list[dict]:
        """Read all memory files and return their metadata + content."""
        results: list[dict] = []
        if not self._memory_dir.is_dir():
            return results

        for md_file in sorted(self._memory_dir.glob("*.md")):
            if md_file.name == "MEMORY.md":
                continue
            meta = self._read_frontmatter(md_file)
            if not meta:
                continue
            # Read body (everything after the closing ---)
            raw = md_file.read_text(encoding="utf-8")
            body = self._extract_body(raw)
            results.append(
                {
                    "name": meta.get("name", md_file.stem),
                    "description": meta.get("description", ""),
                    "type": meta.get("type", ""),
                    "content": body,
                    "path": str(md_file),
                }
            )
        return results

    def has_similar_memory(self, content: str, threshold: float = 0.5) -> bool:
        """Check whether a memory with similar content already exists.

        Uses simple word-overlap (Jaccard-like) comparison against all
        existing memory bodies.  Returns True if any existing memory exceeds
        the similarity *threshold*.
        """
        new_words = _word_set(content)
        if not new_words:
            return False

        for existing in self.load_existing_memories():
            existing_words = _word_set(existing.get("content", ""))
            if not existing_words:
                continue
            overlap = len(new_words & existing_words)
            union = len(new_words | existing_words)
            if union > 0 and (overlap / union) >= threshold:
                return True

        return False

    # -- convenience --------------------------------------------------------

    def extract_and_save(self, messages: list[dict]) -> int:
        """Extract memories from messages, deduplicate, save, and update index.

        Returns the number of new memories saved.
        """
        extracted = self.extract_from_messages(messages)
        saved = 0

        for mem in extracted:
            if self.has_similar_memory(mem.content):
                log.debug("Skipping duplicate memory: %s", mem.name)
                continue
            self.save_memory(mem)
            saved += 1

        if saved > 0:
            self._extraction_count += saved
            self.update_index()
            log.info("Auto-memory: saved %d new memories (total extractions: %d)", saved, self._extraction_count)

        return saved

    # -- internal helpers ---------------------------------------------------

    @staticmethod
    def _read_frontmatter(path: Path) -> dict[str, str]:
        """Parse YAML frontmatter (simple key: value) from a markdown file."""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return {}

        if not text.startswith("---"):
            return {}

        end = text.find("---", 3)
        if end == -1:
            return {}

        meta: dict[str, str] = {}
        for line in text[3:end].strip().splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()
        return meta

    @staticmethod
    def _extract_body(raw: str) -> str:
        """Return everything after the YAML frontmatter block."""
        if not raw.startswith("---"):
            return raw
        end = raw.find("---", 3)
        if end == -1:
            return raw
        return raw[end + 3:].strip()


# ---------------------------------------------------------------------------
# Module-level convenience singleton
# ---------------------------------------------------------------------------

_extractor: MemoryExtractor | None = None


def get_memory_extractor() -> MemoryExtractor:
    """Return (and lazily create) the module-level MemoryExtractor singleton."""
    global _extractor
    if _extractor is None:
        _extractor = MemoryExtractor()
    return _extractor
