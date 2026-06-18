"""File-backed curated memory — durable user-facts that survive chat deletion.

Three stores live under ``get_jarvis_home()/"memories"``:

  - ``MEMORY.md``     — JARVIS's own notes: environment facts, project
    conventions, tool quirks, lessons learned.
  - ``USER.md``       — who Ulrich is: role, preferences, communication
    style, workflow habits, pet peeves.
  - ``PROCEDURES.md`` — named multi-step processes Ulrich asked to save
    (e.g. ``deploy-app``, ``morning-routine``) — invoked by name and
    replayed step-by-step (8000 char cap, larger than MEMORY/USER).

All are injected into the supervisor's system prompt as a FROZEN snapshot
captured once at session start (``snapshot_for_prompt``). Mid-session writes
update the files on disk immediately (durable) but do NOT change the system
prompt — this keeps the prompt prefix stable for the whole session so the
provider-side prefix cache is never invalidated by a memory edit. The
snapshot refreshes on the next session start.

Design (deliberate-writes model — no auto-extraction):
  - The supervisor decides what is worth keeping via the single ``memory``
    tool (``tools/memory.py``): action ∈ add / replace / remove / read.
  - replace/remove identify the target entry by a short unique SUBSTRING
    (``old_text``), not by an ID or the full text.
  - Entry delimiter is ``§`` (section sign); entries can be multiline.
  - Character limits (not tokens) bound each store — char counts are
    model-independent, so the cap stays meaningful across providers.
  - Writes are atomic (temp file + rename) and serialized with an
    exclusive ``.lock`` file so concurrent sessions never corrupt a store.
  - Content is scanned for prompt-injection / exfiltration payloads before
    it is accepted, because every entry lands verbatim in the system prompt.

This module is stdlib-only and import-safe at module scope.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.runtime import get_jarvis_home

from pipeline import portable_lock

logger = logging.getLogger("jarvis.file_memory")

ENTRY_DELIMITER = "\n§\n"

# Char budgets per store. Generous enough for a curated set of durable
# facts, tight enough that the frozen snapshot can't balloon the prompt.
MEMORY_CHAR_LIMIT = 2200
USER_CHAR_LIMIT = 1375
PROCEDURE_CHAR_LIMIT = 8000

# Canonical store targets the tool accepts.
VALID_TARGETS = ("memory", "user", "procedure")


# ---------------------------------------------------------------------------
# Meta-paraphrase reject filter (relocated 2026-05-21 from the retired
# pipeline.memory_extractor when JARVIS swapped to file-backed memory).
#
# Single source of truth for "this string is LLM narration of the
# conversation, not a durable fact". Live captures that motivated it:
#   - "The user inquires about the history of England"
#   - "The user is expressing gratitude for the time spent"
#   - "The conversation has shifted to a casual topic about a bird"
#   - "It seems to be a mixed review of a product or service"
#   - "User appears to be requesting mute"
#
# Used here to block such shapes from entering a store, and re-exported to
# pipeline.skill_review so a proposed skill/memory that drifts into the
# same shape is dropped by the SAME regex.
#
# Anchored at start-of-content; case-insensitive. A hedged-but-real project
# fact like "Coding Kiddos appears to involve teaching" intentionally passes
# (the narration-subject anchors below only fire on "The user/conversation…"
# and "It/This/That seems…" / "User appears…").
# ---------------------------------------------------------------------------
_META_PARAPHRASE_RE = re.compile(
    r"""(?ix)
    (?:
        ^\s*the\s+(?:user|conversation|discussion|topic|exchange)
            \s+(?:is|was|has|appears|seems|seemed|inquires|expresses|expressed|
                 mentions|describes|asks|asked|seeks|sought|wants|wanted)
      | ^\s*(?:it|this|that)\s+(?:seems|appears|looks|sounds)\s+(?:to|like)\b
      | ^\s*user\s+(?:appears|seems|seemed|inquires|inquired|expresses|
               expressed|mentions|mentioned|describes|described|asks|asked|
               seeks|sought|wants|wanted)\b
      | ^\s*the\s+user\s+seeks
    )
    """
)


def is_meta_paraphrase(content: str) -> bool:
    """True for LLM-meta-narration outputs that must not be stored as a
    durable fact. See ``_META_PARAPHRASE_RE``."""
    return bool(_META_PARAPHRASE_RE.search(content or ""))


# ---------------------------------------------------------------------------
# Content scanning — lightweight injection / exfiltration check for content
# that gets injected verbatim into the system prompt.
# ---------------------------------------------------------------------------

_MEMORY_THREAT_PATTERNS = [
    # Prompt injection / role hijack
    (r'ignore\s+(previous|all|above|prior)\s+instructions', "prompt_injection"),
    (r'you\s+are\s+now\s+', "role_hijack"),
    (r'do\s+not\s+tell\s+the\s+user', "deception_hide"),
    (r'system\s+prompt\s+override', "sys_prompt_override"),
    (r'disregard\s+(your|all|any)\s+(instructions|rules|guidelines)', "disregard_rules"),
    (r'act\s+as\s+(if|though)\s+you\s+(have\s+no|don\'t\s+have)\s+(restrictions|limits|rules)', "bypass_restrictions"),
    # Exfiltration via curl/wget carrying a secret-shaped var
    (r'curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_curl"),
    (r'wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_wget"),
    (r'cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)', "read_secrets"),
    # Persistence / key access
    (r'authorized_keys', "ssh_backdoor"),
    (r'\$HOME/\.ssh|\~/\.ssh', "ssh_access"),
    (r'\$HOME/\.jarvis/\.env|\~/\.jarvis/\.env', "jarvis_env"),
]

# Invisible / bidi-control unicode used to smuggle injection payloads past a
# human reviewer skimming the store.
_INVISIBLE_CHARS = {
    '​', '‌', '‍', '⁠', '﻿',
    '‪', '‫', '‬', '‭', '‮',
}


def scan_memory_content(content: str) -> Optional[str]:
    """Scan content for injection / exfil patterns. Return an error string
    if the content must be blocked, else None."""
    for char in _INVISIBLE_CHARS:
        if char in content:
            return (
                f"Blocked: content contains invisible unicode character "
                f"U+{ord(char):04X} (possible injection)."
            )
    for pattern, pid in _MEMORY_THREAT_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return (
                f"Blocked: content matches threat pattern '{pid}'. Memory "
                f"entries are injected into the system prompt and must not "
                f"contain injection or exfiltration payloads."
            )
    return None


# ---------------------------------------------------------------------------
# Atomic file replace (inlined — stdlib-only, no cross-tree dep). Resolves a
# symlink target first so a managed/symlinked store file is updated in place
# rather than detached.
# ---------------------------------------------------------------------------


def _atomic_replace(tmp_path: str, target: str) -> None:
    real_path = os.path.realpath(target) if os.path.islink(target) else target
    os.replace(str(tmp_path), real_path)


def _memory_dir() -> Path:
    """Profile-scoped memories dir, resolved dynamically so a JARVIS_HOME
    override (tests / alternate profiles) is always respected."""
    return get_jarvis_home() / "memories"


class MemoryStore:
    """Bounded curated memory with file persistence.

    Maintains two parallel states:
      - ``_snapshot``: frozen at ``load_from_disk()`` time, used for system
        prompt injection. Never mutated mid-session — keeps the prefix cache
        stable.
      - ``memory_entries`` / ``user_entries``: live state, mutated by tool
        calls and persisted to disk. Tool responses reflect this live state.
    """

    def __init__(
        self,
        memory_char_limit: int = MEMORY_CHAR_LIMIT,
        user_char_limit: int = USER_CHAR_LIMIT,
        procedure_char_limit: int = PROCEDURE_CHAR_LIMIT,
    ):
        self.memory_entries: List[str] = []
        self.user_entries: List[str] = []
        self.procedure_entries: List[str] = []
        self.memory_char_limit = memory_char_limit
        self.user_char_limit = user_char_limit
        self.procedure_char_limit = procedure_char_limit
        # Frozen snapshot for the system prompt — set once at load_from_disk().
        self._snapshot: Dict[str, str] = {"memory": "", "user": "", "procedure": ""}

    # -- Load + snapshot ----------------------------------------------------

    def load_from_disk(self) -> None:
        """Load entries from MEMORY.md + USER.md + PROCEDURES.md and capture
        the frozen system-prompt snapshot. Call once at session start."""
        mem_dir = _memory_dir()
        mem_dir.mkdir(parents=True, exist_ok=True)

        self.memory_entries = self._read_file(mem_dir / "MEMORY.md")
        self.user_entries = self._read_file(mem_dir / "USER.md")
        self.procedure_entries = self._read_file(mem_dir / "PROCEDURES.md")

        # Deduplicate (preserve order, keep first occurrence).
        self.memory_entries = list(dict.fromkeys(self.memory_entries))
        self.user_entries = list(dict.fromkeys(self.user_entries))
        self.procedure_entries = list(dict.fromkeys(self.procedure_entries))

        self._snapshot = {
            "memory": self._render_block("memory", self.memory_entries),
            "user": self._render_block("user", self.user_entries),
            "procedure": self._render_block("procedure", self.procedure_entries),
        }

    def snapshot_for_prompt(self) -> str:
        """Return the FROZEN MEMORY + USER + PROCEDURES blocks for
        system-prompt injection.

        Reflects state captured at ``load_from_disk()`` time, NOT live state —
        mid-session writes don't affect it. Returns "" when all stores were
        empty at load time (keeps the prompt clean for new users)."""
        parts = [
            self._snapshot.get("user", ""),
            self._snapshot.get("memory", ""),
            self._snapshot.get("procedure", ""),
        ]
        body = "\n\n".join(p for p in parts if p)
        return body

    # -- Mutations ----------------------------------------------------------

    def add(self, target: str, content: str) -> Dict[str, Any]:
        """Append a new entry. Error if it would exceed the char limit."""
        content = (content or "").strip()
        if not content:
            return {"success": False, "error": "Content cannot be empty."}

        scan_error = scan_memory_content(content)
        if scan_error:
            return {"success": False, "error": scan_error}
        if is_meta_paraphrase(content):
            return {
                "success": False,
                "error": (
                    "Blocked: that reads as narration of the conversation "
                    "('The user is…' / 'It seems…'), not a durable fact. "
                    "Store a plain assertion instead."
                ),
            }

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)
            entries = self._entries_for(target)
            limit = self._char_limit(target)

            if content in entries:
                return self._success(target, "Entry already exists (no duplicate added).")

            new_total = len(ENTRY_DELIMITER.join(entries + [content]))
            if new_total > limit:
                current = self._char_count(target)
                return {
                    "success": False,
                    "error": (
                        f"Memory at {current:,}/{limit:,} chars. Adding this "
                        f"entry ({len(content)} chars) would exceed the limit. "
                        f"Replace or remove existing entries first."
                    ),
                    "current_entries": entries,
                    "usage": f"{current:,}/{limit:,}",
                }

            entries.append(content)
            self._set_entries(target, entries)
            self._save(target)

        return self._success(target, "Entry added.")

    def replace(self, target: str, old_text: str, new_content: str) -> Dict[str, Any]:
        """Find the entry containing ``old_text`` and replace it wholesale."""
        old_text = (old_text or "").strip()
        new_content = (new_content or "").strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}
        if not new_content:
            return {"success": False, "error": "content cannot be empty. Use 'remove' to delete entries."}

        scan_error = scan_memory_content(new_content)
        if scan_error:
            return {"success": False, "error": scan_error}
        if is_meta_paraphrase(new_content):
            return {
                "success": False,
                "error": (
                    "Blocked: that reads as narration, not a durable fact. "
                    "Store a plain assertion instead."
                ),
            }

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)
            entries = self._entries_for(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return {"success": False, "error": f"No entry matched '{old_text}'."}
            if len(matches) > 1 and len({e for _, e in matches}) > 1:
                previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
                return {
                    "success": False,
                    "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                    "matches": previews,
                }

            idx = matches[0][0]
            limit = self._char_limit(target)
            test = entries.copy()
            test[idx] = new_content
            if len(ENTRY_DELIMITER.join(test)) > limit:
                return {
                    "success": False,
                    "error": (
                        f"Replacement would put memory at "
                        f"{len(ENTRY_DELIMITER.join(test)):,}/{limit:,} chars. "
                        f"Shorten the new content or remove other entries first."
                    ),
                }

            entries[idx] = new_content
            self._set_entries(target, entries)
            self._save(target)

        return self._success(target, "Entry replaced.")

    def remove(self, target: str, old_text: str) -> Dict[str, Any]:
        """Remove the entry containing ``old_text``."""
        old_text = (old_text or "").strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)
            entries = self._entries_for(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return {"success": False, "error": f"No entry matched '{old_text}'."}
            if len(matches) > 1 and len({e for _, e in matches}) > 1:
                previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
                return {
                    "success": False,
                    "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                    "matches": previews,
                }

            entries.pop(matches[0][0])
            self._set_entries(target, entries)
            self._save(target)

        return self._success(target, "Entry removed.")

    def read(self, target: str) -> Dict[str, Any]:
        """Return the LIVE entries for a store (post-mutation truth, not the
        frozen snapshot). Reloads from disk so cross-session writes show."""
        with self._file_lock(self._path_for(target)):
            self._reload_target(target)
        return self._success(target)

    # -- Internal helpers ---------------------------------------------------

    @staticmethod
    @contextmanager
    def _file_lock(path: Path):
        """Exclusive lock for read-modify-write safety. Uses a sidecar
        ``.lock`` file so the store file itself can still be atomically
        replaced via os.replace()."""
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = open(lock_path, "a+", encoding="utf-8")
        try:
            portable_lock.lock_exclusive(fd)
            yield
        finally:
            portable_lock.unlock(fd)
            fd.close()

    @staticmethod
    def _path_for(target: str) -> Path:
        mem_dir = _memory_dir()
        if target == "user":
            return mem_dir / "USER.md"
        if target == "procedure":
            return mem_dir / "PROCEDURES.md"
        return mem_dir / "MEMORY.md"

    def _reload_target(self, target: str) -> None:
        fresh = list(dict.fromkeys(self._read_file(self._path_for(target))))
        self._set_entries(target, fresh)

    def _save(self, target: str) -> None:
        _memory_dir().mkdir(parents=True, exist_ok=True)
        self._write_file(self._path_for(target), self._entries_for(target))

    def _entries_for(self, target: str) -> List[str]:
        if target == "user":
            return self.user_entries
        if target == "procedure":
            return self.procedure_entries
        return self.memory_entries

    def _set_entries(self, target: str, entries: List[str]) -> None:
        if target == "user":
            self.user_entries = entries
        elif target == "procedure":
            self.procedure_entries = entries
        else:
            self.memory_entries = entries

    def _char_count(self, target: str) -> int:
        entries = self._entries_for(target)
        return len(ENTRY_DELIMITER.join(entries)) if entries else 0

    def _char_limit(self, target: str) -> int:
        if target == "user":
            return self.user_char_limit
        if target == "procedure":
            return self.procedure_char_limit
        return self.memory_char_limit

    def _success(self, target: str, message: Optional[str] = None) -> Dict[str, Any]:
        entries = self._entries_for(target)
        current = self._char_count(target)
        limit = self._char_limit(target)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0
        resp: Dict[str, Any] = {
            "success": True,
            "target": target,
            "entries": entries,
            "usage": f"{pct}% — {current:,}/{limit:,} chars",
            "entry_count": len(entries),
        }
        if message:
            resp["message"] = message
        return resp

    def _render_block(self, target: str, entries: List[str]) -> str:
        if not entries:
            return ""
        limit = self._char_limit(target)
        content = ENTRY_DELIMITER.join(entries)
        current = len(content)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0
        if target == "user":
            header = f"USER PROFILE (who Ulrich is) [{pct}% — {current:,}/{limit:,} chars]"
        elif target == "procedure":
            header = f"PROCEDURES (named multi-step processes) [{pct}% — {current:,}/{limit:,} chars]"
        else:
            header = f"MEMORY (your durable notes) [{pct}% — {current:,}/{limit:,} chars]"
        sep = "═" * 46
        return f"{sep}\n{header}\n{sep}\n{content}"

    @staticmethod
    def _read_file(path: Path) -> List[str]:
        """Read a store file and split into entries on ENTRY_DELIMITER.

        No lock needed for reads: writes use atomic rename, so a reader sees
        either the previous complete file or the new complete file."""
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, IOError):
            return []
        if not raw.strip():
            return []
        # Split on the full delimiter (not bare "§") so an entry that happens
        # to contain "§" in its text isn't split mid-entry.
        return [e.strip() for e in raw.split(ENTRY_DELIMITER) if e.strip()]

    @staticmethod
    def _write_file(path: Path, entries: List[str]) -> None:
        """Write entries via atomic temp-file + rename. A truncating "w"
        would create a window where a concurrent reader sees an empty file;
        atomic rename avoids that."""
        content = ENTRY_DELIMITER.join(entries) if entries else ""
        try:
            fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix=".mem_")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                _atomic_replace(tmp_path, str(path))
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except (OSError, IOError) as e:
            raise RuntimeError(f"Failed to write memory file {path}: {e}")


# ---------------------------------------------------------------------------
# Process-wide singleton + module-level convenience surface.
#
# One store per voice-agent process. ``load_from_disk()`` is called once at
# session start (jarvis_agent) to freeze the snapshot; tool calls mutate the
# live state and persist immediately.
# ---------------------------------------------------------------------------

_STORE: Optional[MemoryStore] = None


def get_store() -> MemoryStore:
    """Return the process singleton, loading the frozen snapshot on first use."""
    global _STORE
    if _STORE is None:
        _STORE = MemoryStore()
        _STORE.load_from_disk()
    return _STORE


def reload_store() -> MemoryStore:
    """Force a fresh load from disk and re-freeze the snapshot. Used at
    session start and by tests that want a clean store."""
    global _STORE
    _STORE = MemoryStore()
    _STORE.load_from_disk()
    return _STORE


def snapshot_for_prompt() -> str:
    """Frozen MEMORY + USER text for system-prompt injection. "" when empty."""
    return get_store().snapshot_for_prompt()


def add(target: str, content: str) -> Dict[str, Any]:
    return get_store().add(target, content)


def replace(target: str, old_text: str, new_content: str) -> Dict[str, Any]:
    return get_store().replace(target, old_text, new_content)


def remove(target: str, old_text: str) -> Dict[str, Any]:
    return get_store().remove(target, old_text)


def read(target: str) -> Dict[str, Any]:
    return get_store().read(target)
