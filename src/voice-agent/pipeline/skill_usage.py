"""Skill usage telemetry + lifecycle state for the curator.

Tracks per-skill usage metadata in a sidecar JSON file keyed by skill name.
Counters are bumped by the existing skill tools (skill_view / skill_manage);
the curator reads the derived activity timestamp to decide lifecycle
transitions (active → stale → archived).

Ported from the upstream skill-usage telemetry, adapted for JARVIS:
  - Paths come from ``tools.runtime`` (state lands under ``~/.jarvis``).
  - "Curator-managed" maps to JARVIS's writable user-skills root
    (``~/.jarvis/skills/`` in production, the ``JARVIS_SKILLS_PATHS`` tail in
    tests). Shipped skills under ``src/voice-agent/skills/`` are read-only
    source and are never recorded, archived, or curated — the same boundary
    ``pipeline.skills_authoring`` already enforces.

Design notes:
  - Sidecar JSON, not frontmatter. Keeps operational telemetry out of
    user-authored SKILL.md content.
  - Atomic writes via tempfile + os.replace.
  - All counter bumps are best-effort: failures log at DEBUG and return
    silently. A broken sidecar never breaks the underlying tool call.

Lifecycle states:
    active    -> default
    stale     -> unused > stale_after_days (config)
    archived  -> unused > archive_after_days (config); dir moved to .archived/
    pinned    -> opt-out from auto transitions (boolean flag, orthogonal)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("jarvis.skill_usage")

from pipeline import portable_lock


STATE_ACTIVE = "active"
STATE_STALE = "stale"
STATE_ARCHIVED = "archived"
_VALID_STATES = {STATE_ACTIVE, STATE_STALE, STATE_ARCHIVED}


# ---------------------------------------------------------------------------
# Paths — sidecar lives under the writable user-skills root
# ---------------------------------------------------------------------------

def _skills_dir() -> Path:
    """The writable user-skills root.

    In production this is ``~/.jarvis/skills/``. Under tests / alternate
    profiles it is the LAST entry of ``JARVIS_SKILLS_PATHS`` — the same root
    ``pipeline.skills_authoring`` writes to (user overrides ship). We resolve
    it through the loader so the two modules can never disagree about where
    user skills live.
    """
    from pipeline.skills_loader import _default_roots
    return _default_roots()[-1]


def _usage_file() -> Path:
    return _skills_dir() / ".usage.json"


def _archive_dir() -> Path:
    """Recoverable-archive destination. ``.``-prefixed so the skills loader's
    discovery (which iterates real skill dirs) skips it, matching upstream's
    ``.archive`` semantics."""
    return _skills_dir() / ".archived"


@contextmanager
def _usage_file_lock():
    """Serialize .usage.json read-modify-write cycles across processes."""
    lock_path = _usage_file().with_suffix(".json.lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        yield
        return

    fd = open(lock_path, "a+", encoding="utf-8")
    try:
        portable_lock.lock_exclusive(fd)
        yield
    finally:
        portable_lock.unlock(fd)
        fd.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_timestamp(value: Any) -> Optional[datetime]:
    """Parse an ISO timestamp defensively for activity comparisons."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def latest_activity_at(record: Dict[str, Any]) -> Optional[str]:
    """Return the newest actual activity timestamp for a usage record.

    "Activity" means a skill was used, viewed, or patched. Creation time is
    intentionally excluded so callers can still distinguish never-active
    skills; lifecycle code falls back to ``created_at`` as its own anchor.
    """
    latest_dt: Optional[datetime] = None
    latest_raw: Optional[str] = None
    for key in ("last_used_at", "last_viewed_at", "last_patched_at"):
        raw = record.get(key)
        dt = _parse_iso_timestamp(raw)
        if dt is None:
            continue
        if latest_dt is None or dt > latest_dt:
            latest_dt = dt
            latest_raw = str(raw)
    return latest_raw


def activity_count(record: Dict[str, Any]) -> int:
    """Return the total observed activity count across use/view/patch events."""
    total = 0
    for key in ("use_count", "view_count", "patch_count"):
        try:
            total += int(record.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return total


# ---------------------------------------------------------------------------
# Provenance — which skills are curator-managed (user skills, eligible)
# ---------------------------------------------------------------------------

def _read_skill_name(skill_md: Path, fallback: str) -> str:
    """Parse the ``name:`` field from a SKILL.md YAML frontmatter."""
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return fallback
    in_frontmatter = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped == "---":
            if in_frontmatter:
                break
            in_frontmatter = True
            continue
        if in_frontmatter and stripped.startswith("name:"):
            value = stripped.split(":", 1)[1].strip().strip("\"'")
            if value:
                return value
    return fallback


def is_curatable(skill_name: str) -> bool:
    """Whether *skill_name* resolves to a user skill under the writable root.

    The curator operates exclusively on user skills. Shipped skills (under
    ``src/voice-agent/skills/``) are read-only source and are never archived
    or curated, mirroring ``skills_authoring._resolve_user_skill``.
    """
    if not skill_name:
        return False
    return _find_skill_dir(skill_name) is not None


def list_curatable_skill_names() -> List[str]:
    """Enumerate user skills (under the writable root) eligible for curation.

    Skips anything under a ``.``-prefixed dir (``.archived/``,
    ``.curator_backups/``) so archived skills aren't re-enumerated as live.
    """
    base = _skills_dir()
    if not base.exists():
        return []
    names: List[str] = []
    for skill_md in base.rglob("SKILL.md"):
        try:
            rel = skill_md.relative_to(base)
        except ValueError:
            continue
        parts = rel.parts
        if parts and parts[0].startswith("."):
            continue
        name = _read_skill_name(skill_md, fallback=skill_md.parent.name)
        names.append(name)
    return sorted(set(names))


def list_archived_skill_names() -> List[str]:
    """Enumerate skills in ``<skills>/.archived/``. Layout is flat
    (``.archived/<skill>/``) so the directory name is the skill name."""
    archive_root = _archive_dir()
    if not archive_root.exists():
        return []
    return sorted({p.name for p in archive_root.iterdir() if p.is_dir()})


def _find_skill_dir(skill_name: str) -> Optional[Path]:
    """Locate the directory for a *live* user skill by its frontmatter
    ``name:`` field. Skips ``.``-prefixed dirs (archive/backups). Returns
    None when the skill isn't found under the writable root."""
    base = _skills_dir()
    if not base.exists():
        return None
    for skill_md in base.rglob("SKILL.md"):
        try:
            rel = skill_md.relative_to(base)
        except ValueError:
            continue
        if rel.parts and rel.parts[0].startswith("."):
            continue
        if _read_skill_name(skill_md, fallback=skill_md.parent.name) == skill_name:
            return skill_md.parent
    return None


# ---------------------------------------------------------------------------
# Sidecar I/O
# ---------------------------------------------------------------------------

def _empty_record() -> Dict[str, Any]:
    return {
        "use_count": 0,
        "view_count": 0,
        "last_used_at": None,
        "last_viewed_at": None,
        "patch_count": 0,
        "last_patched_at": None,
        "created_at": _now_iso(),
        "state": STATE_ACTIVE,
        "pinned": False,
        "archived_at": None,
    }


def load_usage() -> Dict[str, Dict[str, Any]]:
    """Read the entire .usage.json map. Returns empty dict on missing/corrupt."""
    path = _usage_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("Failed to read %s: %s", path, e)
        return {}
    if not isinstance(data, dict):
        return {}
    clean: Dict[str, Dict[str, Any]] = {}
    for k, v in data.items():
        if isinstance(v, dict):
            clean[str(k)] = v
    return clean


def save_usage(data: Dict[str, Dict[str, Any]]) -> None:
    """Write the usage map atomically. Best-effort — errors logged, not raised."""
    path = _usage_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=".usage_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.debug("Failed to write %s: %s", path, e, exc_info=True)


def get_record(skill_name: str) -> Dict[str, Any]:
    """Return the record for *skill_name*, creating a fresh one if missing.
    Missing keys are backfilled so callers can always index fields."""
    data = load_usage()
    rec = data.get(skill_name)
    if not isinstance(rec, dict):
        return _empty_record()
    base = _empty_record()
    for k, v in base.items():
        rec.setdefault(k, v)
    return rec


def _mutate(skill_name: str, mutator) -> None:
    """Load, apply *mutator(record)* in place, save. Best-effort.

    Only user skills (under the writable root) are recorded. Shipped skills
    are never tracked.
    """
    if not skill_name:
        return
    try:
        if not is_curatable(skill_name):
            return
        with _usage_file_lock():
            data = load_usage()
            rec = data.get(skill_name)
            if not isinstance(rec, dict):
                rec = _empty_record()
            mutator(rec)
            data[skill_name] = rec
            save_usage(data)
    except Exception as e:
        logger.debug("skill_usage._mutate(%s) failed: %s", skill_name, e, exc_info=True)


# ---------------------------------------------------------------------------
# Public counter-bump helpers
# ---------------------------------------------------------------------------

def record_use(skill_name: str) -> None:
    """Bump use_count and last_used_at. Called when a skill is actively used
    (e.g. loaded into the prompt path / referenced from an assistant turn)."""
    def _apply(rec: Dict[str, Any]) -> None:
        rec["use_count"] = int(rec.get("use_count") or 0) + 1
        rec["last_used_at"] = _now_iso()
        # Using a stale skill reactivates it immediately — don't wait for the
        # next curator tick to flip the state back.
        if rec.get("state") == STATE_STALE:
            rec["state"] = STATE_ACTIVE
    _mutate(skill_name, _apply)


def bump_view(skill_name: str) -> None:
    """Bump view_count and last_viewed_at. Called from skill_view()."""
    def _apply(rec: Dict[str, Any]) -> None:
        rec["view_count"] = int(rec.get("view_count") or 0) + 1
        rec["last_viewed_at"] = _now_iso()
    _mutate(skill_name, _apply)


def bump_patch(skill_name: str) -> None:
    """Bump patch_count and last_patched_at. Called from skill_manage (patch/edit)."""
    def _apply(rec: Dict[str, Any]) -> None:
        rec["patch_count"] = int(rec.get("patch_count") or 0) + 1
        rec["last_patched_at"] = _now_iso()
    _mutate(skill_name, _apply)


def set_state(skill_name: str, state: str) -> None:
    """Set lifecycle state. No-op if *state* is invalid."""
    if state not in _VALID_STATES:
        logger.debug("set_state: invalid state %r for %s", state, skill_name)
        return

    def _apply(rec: Dict[str, Any]) -> None:
        rec["state"] = state
        if state == STATE_ARCHIVED:
            rec["archived_at"] = _now_iso()
        elif state == STATE_ACTIVE:
            rec["archived_at"] = None
    _mutate(skill_name, _apply)


def set_pinned(skill_name: str, pinned: bool) -> None:
    """Pin / unpin a skill. Pinned skills are never auto-archived."""
    def _apply(rec: Dict[str, Any]) -> None:
        rec["pinned"] = bool(pinned)
    _mutate(skill_name, _apply)


def pin(skill_name: str) -> None:
    set_pinned(skill_name, True)


def unpin(skill_name: str) -> None:
    set_pinned(skill_name, False)


def is_pinned(skill_name: str) -> bool:
    return bool(get_record(skill_name).get("pinned"))


def forget(skill_name: str) -> None:
    """Drop a skill's usage entry entirely. Called when the skill is deleted."""
    if not skill_name:
        return
    try:
        with _usage_file_lock():
            data = load_usage()
            if skill_name in data:
                del data[skill_name]
                save_usage(data)
    except Exception as e:
        logger.debug("skill_usage.forget(%s) failed: %s", skill_name, e, exc_info=True)


# ---------------------------------------------------------------------------
# Archive / restore — recoverable-move pattern (never hard-delete)
# ---------------------------------------------------------------------------

def archive_skill(skill_name: str) -> Tuple[bool, str]:
    """Move a user skill directory to ``<skills>/.archived/``.

    Returns (ok, message). Never archives shipped skills — callers are
    responsible for checking provenance, but we double-check here as a safety
    net. The sidecar state is flipped to ``archived`` on success.
    """
    skill_dir = _find_skill_dir(skill_name)
    if skill_dir is None:
        return False, f"skill '{skill_name}' not found (or not a user skill)"

    archive_root = _archive_dir()
    try:
        archive_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"failed to create archive dir: {e}"

    # Flip the sidecar state to archived BEFORE moving the dir. Once the dir
    # lives under .archived/, `_find_skill_dir` (and thus the `is_curatable`
    # guard in `_mutate`) no longer sees it, so a post-move `set_state` would
    # silently no-op. Set it while the skill is still a live user skill.
    set_state(skill_name, STATE_ARCHIVED)

    # Flatten any nesting into a single ".archived/<skill>/" so restores are
    # simple. On collision, append a UTC timestamp.
    dest = archive_root / skill_dir.name
    if dest.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        dest = archive_root / f"{skill_dir.name}-{stamp}"

    try:
        skill_dir.rename(dest)
    except OSError:
        # Cross-device — fall back to shutil.move.
        try:
            shutil.move(str(skill_dir), str(dest))
        except Exception as e2:
            # Roll the state back so we don't leave a phantom-archived record
            # for a skill that's still live on disk.
            set_state(skill_name, STATE_ACTIVE)
            return False, f"failed to archive: {e2}"

    return True, f"archived to {dest}"


def restore_skill(skill_name: str) -> Tuple[bool, str]:
    """Move an archived skill back to the writable skills root.

    Restores to the flat top-level layout. Refuses to clobber a live skill of
    the same name.
    """
    archive_root = _archive_dir()
    if not archive_root.exists():
        return False, "no archive directory"

    # Exact match first, then timestamped-dupe prefix match. Recursive walk
    # handles any nested archive layout left by older paths.
    candidates = [
        p for p in archive_root.rglob("*")
        if p.is_dir() and p.name == skill_name
    ]
    if not candidates:
        candidates = sorted(
            [p for p in archive_root.rglob("*")
             if p.is_dir() and p.name.startswith(f"{skill_name}-")],
            reverse=True,
        )
    if not candidates:
        return False, f"skill '{skill_name}' not found in archive"

    src = candidates[0]
    dest = _skills_dir() / skill_name
    if dest.exists():
        return False, f"destination already exists: {dest}"

    try:
        src.rename(dest)
    except OSError:
        try:
            shutil.move(str(src), str(dest))
        except Exception as e:
            return False, f"failed to restore: {e}"

    set_state(skill_name, STATE_ACTIVE)
    return True, f"restored to {dest}"


# ---------------------------------------------------------------------------
# Reporting — for the curator engine / CLI
# ---------------------------------------------------------------------------

def curatable_report() -> List[Dict[str, Any]]:
    """Return a list of ``{name, state, pinned, last_activity_at, ...}``
    records for every user (curatable) skill. Missing usage records are
    backfilled with defaults so callers can always index fields."""
    data = load_usage()
    rows: List[Dict[str, Any]] = []
    for name in list_curatable_skill_names():
        rec = data.get(name)
        if not isinstance(rec, dict):
            rec = _empty_record()
        base = _empty_record()
        for k, v in base.items():
            rec.setdefault(k, v)
        row = {"name": name, **rec}
        row["last_activity_at"] = latest_activity_at(row)
        row["activity_count"] = activity_count(row)
        rows.append(row)
    return rows
