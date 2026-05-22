"""Curator — skill-maintenance engine for JARVIS user skills.

The curator periodically reviews user-authored skills (under the writable
``~/.jarvis/skills/`` root) and maintains the collection:

  - Deterministic lifecycle transitions: active → stale → archived, driven by
    each skill's derived activity timestamp (see ``pipeline.skill_usage``).
  - tar.gz backups of the skills tree before any mutating pass, with rollback.
  - Pinning: pinned skills bypass all auto-transitions.
  - An OPTIONAL, gated aux-LLM consolidation REVIEW that surfaces
    near-duplicate clusters as suggestions (it does not auto-mutate — see
    "LLM consolidation review" below).
  - Per-run JSON + Markdown reports under ``~/.jarvis/logs/curator/``.

Ported from the upstream curator engine, scrubbed and adapted for JARVIS:
  - Paths come from ``tools.runtime`` (state under ``~/.jarvis``).
  - Skill-store ops go through ``pipeline.skill_usage`` (archive = recoverable
    move to ``.archived/``) — never a hard delete.
  - Config comes from ``JARVIS_CURATOR_*`` env vars (JARVIS has no config.yaml
    layer for this), read at call time so operator edits take effect without a
    process restart.

Strict invariants (unchanged from upstream):
  - Only touches user (curatable) skills — never shipped skills.
  - Never auto-deletes — only archives. Archive is recoverable.
  - Pinned skills bypass all auto-transitions.

LLM consolidation review
-------------------------
Upstream drove consolidation by forking a full agent that mutated the skill
library via ``skill_manage`` tool calls. JARVIS's voice agent has no
equivalent forked-agent runtime, so that mutate-via-tool-calls path is
DEFERRED. In its place, ``run_consolidation_review()`` calls the same
small-model aux-LLM that ``pipeline.memory_consolidator`` uses (Groq
llama-3.1-8b-instant via httpx) to cluster near-duplicate skills and returns
those clusters as SUGGESTIONS only — it never archives or rewrites a skill on
its own. It is gated OFF by default (``JARVIS_CURATOR_CONSOLIDATION=1`` to
enable) and degrades to an empty suggestion list when no API key is present
(safe to ship without a key — no network, no regression).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from tools.runtime import get_jarvis_home

from pipeline import skill_usage

logger = logging.getLogger("jarvis.curator")


DEFAULT_INTERVAL_HOURS = 24 * 7  # 7 days
DEFAULT_MIN_IDLE_HOURS = 2
DEFAULT_STALE_AFTER_DAYS = 30
DEFAULT_ARCHIVE_AFTER_DAYS = 90
DEFAULT_BACKUP_KEEP = 5


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _skills_dir() -> Path:
    """The writable user-skills root (shared with skill_usage)."""
    return skill_usage._skills_dir()


def _state_file() -> Path:
    return _skills_dir() / ".curator_state"


def _backups_dir() -> Path:
    return _skills_dir() / ".curator_backups"


def _reports_root() -> Path:
    """Per-run reports live under the JARVIS home logs dir, away from the
    user's authored skill data."""
    root = get_jarvis_home() / "logs" / "curator"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.debug("Curator reports dir create failed: %s", e)
    return root


# ---------------------------------------------------------------------------
# Config — JARVIS_CURATOR_* env vars, read at call time
# ---------------------------------------------------------------------------

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def is_enabled() -> bool:
    """Default ON. ``JARVIS_CURATOR_DISABLED=1`` kills the engine."""
    return os.environ.get("JARVIS_CURATOR_DISABLED", "0") != "1"


def get_interval_hours() -> int:
    return _env_int("JARVIS_CURATOR_INTERVAL_HOURS", DEFAULT_INTERVAL_HOURS)


def get_min_idle_hours() -> float:
    return _env_float("JARVIS_CURATOR_MIN_IDLE_HOURS", DEFAULT_MIN_IDLE_HOURS)


def get_stale_after_days() -> int:
    return _env_int("JARVIS_CURATOR_STALE_AFTER_DAYS", DEFAULT_STALE_AFTER_DAYS)


def get_archive_after_days() -> int:
    return _env_int("JARVIS_CURATOR_ARCHIVE_AFTER_DAYS", DEFAULT_ARCHIVE_AFTER_DAYS)


def get_backup_keep() -> int:
    return max(1, _env_int("JARVIS_CURATOR_BACKUP_KEEP", DEFAULT_BACKUP_KEEP))


def backup_enabled() -> bool:
    """Default ON — the whole point of the backup is safety by default."""
    return os.environ.get("JARVIS_CURATOR_BACKUP_DISABLED", "0") != "1"


def consolidation_enabled() -> bool:
    """LLM consolidation review is OFF by default (gated, suggestion-only)."""
    return os.environ.get("JARVIS_CURATOR_CONSOLIDATION", "0") == "1"


# ---------------------------------------------------------------------------
# .curator_state — persistent scheduler + status
# ---------------------------------------------------------------------------

def _default_state() -> Dict[str, Any]:
    return {
        "last_run_at": None,
        "last_run_duration_seconds": None,
        "last_run_summary": None,
        "last_report_path": None,
        "paused": False,
        "run_count": 0,
    }


def load_state() -> Dict[str, Any]:
    path = _state_file()
    if not path.exists():
        return _default_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            base = _default_state()
            base.update({k: v for k, v in data.items() if k in base})
            return base
    except (OSError, json.JSONDecodeError) as e:
        logger.debug("Failed to read curator state: %s", e)
    return _default_state()


def save_state(data: Dict[str, Any]) -> None:
    path = _state_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".curator_state_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.debug("Failed to save curator state: %s", e, exc_info=True)


def set_paused(paused: bool) -> None:
    state = load_state()
    state["paused"] = bool(paused)
    save_state(state)


def is_paused() -> bool:
    return bool(load_state().get("paused"))


# ---------------------------------------------------------------------------
# Idle / interval check
# ---------------------------------------------------------------------------

def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def should_run_now(now: Optional[datetime] = None) -> bool:
    """Return True if the curator should run automatically.

    Gates: enabled, not paused, and ``last_run_at`` older than the interval.

    First-run behavior: when there is no ``last_run_at`` we DO NOT run
    immediately — we seed ``last_run_at`` to "now" and defer the first real
    pass by one full interval (matches upstream). Manual ``run_curation()``
    bypasses this gate.

    The idle check (min_idle_hours) is applied at the call site that knows
    whether the agent is actively running; here we only enforce static gates.
    """
    if not is_enabled():
        return False
    if is_paused():
        return False

    state = load_state()
    last = _parse_iso(state.get("last_run_at"))
    if last is None:
        if now is None:
            now = datetime.now(timezone.utc)
        try:
            state["last_run_at"] = now.isoformat()
            state["last_run_summary"] = (
                "deferred first run — curator seeded, will run after one interval"
            )
            save_state(state)
        except Exception as e:  # pragma: no cover — best-effort persistence
            logger.debug("Failed to seed curator last_run_at: %s", e)
        return False

    if now is None:
        now = datetime.now(timezone.utc)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    interval = timedelta(hours=get_interval_hours())
    return (now - last) >= interval


# ---------------------------------------------------------------------------
# Automatic state transitions (pure, no LLM)
# ---------------------------------------------------------------------------

def apply_automatic_transitions(now: Optional[datetime] = None) -> Dict[str, int]:
    """Walk every curatable skill and move active/stale/archived based on the
    latest real activity timestamp. Pinned skills are never touched.
    Returns a counter dict describing what changed."""
    if now is None:
        now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=get_stale_after_days())
    archive_cutoff = now - timedelta(days=get_archive_after_days())

    counts = {"marked_stale": 0, "archived": 0, "reactivated": 0, "checked": 0}

    for row in skill_usage.curatable_report():
        counts["checked"] += 1
        name = row["name"]
        if row.get("pinned"):
            continue

        last_activity = _parse_iso(row.get("last_activity_at"))
        # Never-active skills anchor on created_at so a brand-new skill doesn't
        # immediately archive itself.
        anchor = last_activity or _parse_iso(row.get("created_at")) or now
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)

        current = row.get("state", skill_usage.STATE_ACTIVE)

        if anchor <= archive_cutoff and current != skill_usage.STATE_ARCHIVED:
            ok, _msg = skill_usage.archive_skill(name)
            if ok:
                counts["archived"] += 1
        elif anchor <= stale_cutoff and current == skill_usage.STATE_ACTIVE:
            skill_usage.set_state(name, skill_usage.STATE_STALE)
            counts["marked_stale"] += 1
        elif anchor > stale_cutoff and current == skill_usage.STATE_STALE:
            # Used again after being marked stale — reactivate.
            skill_usage.set_state(name, skill_usage.STATE_ACTIVE)
            counts["reactivated"] += 1

    return counts


# ===========================================================================
# Backups + rollback (tar.gz)
# ===========================================================================

# Entries under skills/ that must NEVER be rolled into a snapshot.
# .curator_backups is the backup dir itself (recursion bomb).
_EXCLUDE_TOP_LEVEL = {".curator_backups"}

# Snapshot id regex: UTC ISO with colons replaced by dashes (filesystem-safe),
# optional ``-NN`` suffix for two snapshots in the same wall-clock second.
_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z(-\d{2})?$")


def _utc_id(now: Optional[datetime] = None) -> str:
    if now is None:
        now = datetime.now(timezone.utc)
    s = now.replace(microsecond=0).isoformat()
    if s.endswith("+00:00"):
        s = s[:-6]
    return s.replace(":", "-") + "Z"


def _count_skill_files(base: Path) -> int:
    try:
        return sum(1 for _ in base.rglob("SKILL.md"))
    except OSError:
        return 0


def _write_backup_manifest(dest: Path, reason: str, archive_path: Path,
                           skills_counted: int) -> None:
    manifest = {
        "id": dest.name,
        "reason": reason,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "archive": archive_path.name,
        "archive_bytes": archive_path.stat().st_size,
        "skill_files": skills_counted,
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )


def snapshot_skills(reason: str = "manual") -> Optional[Path]:
    """Create a tar.gz snapshot of the skills tree and prune old ones.

    Returns the snapshot directory path, or ``None`` if skipped (backup
    disabled, skills dir missing, or an IO error — logged at debug so the
    curator never aborts a pass because of a backup failure).

    The snapshot includes everything under the skills root EXCEPT
    ``.curator_backups/`` — so ``.archived/``, ``.usage.json``, and
    ``.curator_state`` are all captured and a rollback restores them too.
    """
    if not backup_enabled():
        logger.debug("Curator backup disabled by env; skipping snapshot")
        return None

    skills = _skills_dir()
    if not skills.exists():
        logger.debug("No skills dir — nothing to back up")
        return None

    backups = _backups_dir()
    try:
        backups.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.debug("Failed to create backups dir %s: %s", backups, e)
        return None

    base_id = _utc_id()
    snap_id = base_id
    counter = 1
    while (backups / snap_id).exists():
        snap_id = f"{base_id}-{counter:02d}"
        counter += 1

    dest = backups / snap_id
    try:
        dest.mkdir(parents=True, exist_ok=False)
    except OSError as e:
        logger.debug("Failed to create snapshot dir %s: %s", dest, e)
        return None

    archive = dest / "skills.tar.gz"
    try:
        with tarfile.open(archive, "w:gz", compresslevel=6) as tf:
            for entry in sorted(skills.iterdir()):
                if entry.name in _EXCLUDE_TOP_LEVEL:
                    continue
                tf.add(str(entry), arcname=entry.name, recursive=True)
        _write_backup_manifest(dest, reason, archive, _count_skill_files(skills))
    except (OSError, tarfile.TarError) as e:
        logger.debug("Curator snapshot failed: %s", e, exc_info=True)
        try:
            shutil.rmtree(dest, ignore_errors=True)
        except OSError:
            pass
        return None

    _prune_old_backups(keep=get_backup_keep())
    logger.info("Curator snapshot created: %s (%s)", snap_id, reason)
    return dest


def _prune_old_backups(keep: int) -> List[str]:
    """Delete snapshots beyond the newest *keep*. Returns deleted ids.
    Staging dirs (``.rollback-staging-*``) are pruned independently."""
    backups = _backups_dir()
    if not backups.exists():
        return []
    entries: List[Tuple[str, Path]] = []
    stale_staging: List[Path] = []
    for child in backups.iterdir():
        if not child.is_dir():
            continue
        if child.name.startswith(".rollback-staging-"):
            stale_staging.append(child)
            continue
        if _ID_RE.match(child.name):
            entries.append((child.name, child))
    entries.sort(key=lambda t: t[0], reverse=True)
    deleted: List[str] = []
    for _, path in entries[keep:]:
        try:
            shutil.rmtree(path)
            deleted.append(path.name)
        except OSError as e:
            logger.debug("Failed to prune %s: %s", path, e)
    for path in stale_staging:
        try:
            shutil.rmtree(path)
        except OSError as e:
            logger.debug("Failed to clean stale staging dir %s: %s", path, e)
    return deleted


def _read_backup_manifest(snap_dir: Path) -> Dict[str, Any]:
    mf = snap_dir / "manifest.json"
    if not mf.exists():
        return {}
    try:
        return json.loads(mf.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def list_backups() -> List[Dict[str, Any]]:
    """Return all restorable snapshots, newest first. Only entries with a real
    ``skills.tar.gz`` are listed; transient staging dirs are excluded."""
    backups = _backups_dir()
    if not backups.exists():
        return []
    out: List[Dict[str, Any]] = []
    for child in sorted(backups.iterdir(), reverse=True):
        if not child.is_dir() or not _ID_RE.match(child.name):
            continue
        if not (child / "skills.tar.gz").exists():
            continue
        mf = _read_backup_manifest(child)
        mf.setdefault("id", child.name)
        mf.setdefault("path", str(child))
        if "archive_bytes" not in mf:
            try:
                mf["archive_bytes"] = (child / "skills.tar.gz").stat().st_size
            except OSError:
                mf["archive_bytes"] = 0
        out.append(mf)
    return out


def _resolve_backup(backup_id: Optional[str]) -> Optional[Path]:
    """Return the path of the requested backup, or the newest one if
    *backup_id* is None. None if no match."""
    backups = _backups_dir()
    if not backups.exists():
        return None
    if backup_id:
        target = backups / backup_id
        if (
            target.is_dir()
            and _ID_RE.match(backup_id)
            and (target / "skills.tar.gz").exists()
        ):
            return target
        return None
    candidates = [
        c for c in sorted(backups.iterdir(), reverse=True)
        if c.is_dir() and _ID_RE.match(c.name) and (c / "skills.tar.gz").exists()
    ]
    return candidates[0] if candidates else None


def rollback(backup_id: Optional[str] = None) -> Tuple[bool, str, Optional[Path]]:
    """Restore the skills tree from a snapshot.

    Strategy:
      1. Resolve the target snapshot (explicit id or newest).
      2. Take a safety snapshot of the CURRENT tree so the rollback is undoable.
      3. Move current top-level entries (except ``.curator_backups``) into a
         staging dir so the extract lands in an empty tree.
      4. Extract the chosen snapshot into the skills root.
      5. On extract failure, move staged contents back (best-effort).

    Returns ``(ok, message, snapshot_path)``.
    """
    target = _resolve_backup(backup_id)
    if target is None:
        return (
            False,
            "no matching backup found"
            + (f" for id '{backup_id}'" if backup_id else ""),
            None,
        )
    archive = target / "skills.tar.gz"
    if not archive.exists():
        return (False, f"snapshot {target.name} has no skills.tar.gz — corrupted?", None)

    skills = _skills_dir()
    skills.mkdir(parents=True, exist_ok=True)
    backups = _backups_dir()
    backups.mkdir(parents=True, exist_ok=True)

    # Safety snapshot FIRST. If it fails, bail before touching anything.
    try:
        snapshot_skills(reason=f"pre-rollback to {target.name}")
    except Exception as e:
        return (False, f"pre-rollback safety snapshot failed: {e}", None)

    staged = backups / f".rollback-staging-{_utc_id()}"
    try:
        staged.mkdir(parents=True, exist_ok=False)
    except OSError as e:
        return (False, f"failed to create staging dir: {e}", None)

    moved: List[Tuple[Path, Path]] = []
    try:
        for entry in list(skills.iterdir()):
            if entry.name in _EXCLUDE_TOP_LEVEL:
                continue
            dest = staged / entry.name
            shutil.move(str(entry), str(dest))
            moved.append((entry, dest))
    except OSError as e:
        for orig, dest in moved:
            try:
                shutil.move(str(dest), str(orig))
            except OSError:
                pass
        try:
            shutil.rmtree(staged, ignore_errors=True)
        except OSError:
            pass
        return (False, f"failed to stage current skills: {e}", None)

    try:
        with tarfile.open(archive, "r:gz") as tf:
            for member in tf.getmembers():
                name = member.name
                if name.startswith("/") or ".." in Path(name).parts:
                    raise tarfile.TarError(f"refusing to extract unsafe path: {name!r}")
            try:
                tf.extractall(str(skills), filter="data")  # type: ignore[call-arg]
            except TypeError:
                # Python < 3.12 — no filter kwarg
                tf.extractall(str(skills))
    except (OSError, tarfile.TarError) as e:
        for orig, dest in moved:
            try:
                shutil.move(str(dest), str(orig))
            except OSError:
                pass
        try:
            shutil.rmtree(staged, ignore_errors=True)
        except OSError:
            pass
        return (False, f"snapshot extract failed (state restored): {e}", None)

    try:
        shutil.rmtree(staged, ignore_errors=True)
    except OSError:
        pass

    # Pick up the restored skill files in the in-process registry.
    try:
        from pipeline.skills_loader import reload_skills
        reload_skills()
    except Exception as e:
        logger.debug("Curator rollback skills reload failed: %s", e)

    logger.info("Curator rollback: restored from %s", target.name)
    return (True, f"restored from snapshot {target.name}", target)


def format_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} GB"


def summarize_backups() -> str:
    rows = list_backups()
    if not rows:
        return "No curator snapshots yet."
    header = f"{'id':<24}  {'reason':<40}  {'skills':>6}  {'size':>8}"
    lines = [header, "-" * len(header)]
    for r in rows:
        lines.append(
            f"{r.get('id', '?'):<24}  "
            f"{(r.get('reason', '?') or '?')[:40]:<40}  "
            f"{r.get('skill_files', 0):>6}  "
            f"{format_size(int(r.get('archive_bytes', 0))):>8}"
        )
    return "\n".join(lines)


# ===========================================================================
# LLM consolidation review (gated, suggestion-only)
# ===========================================================================

_CONSOLIDATION_PROMPT = """You are running an UMBRELLA-BUILDING consolidation pass over a library of
agent skills. This is NOT a passive audit or a pairwise duplicate-finder.

GOAL — A library of many narrow, one-session skills is a FAILURE of the
library, not a feature. The right target shape is CLASS-LEVEL skills: one
broad skill with labeled subsections beats five narrow siblings for
discoverability. Group prefix and topic clusters into class-level umbrella
skills.

HARD RULES — do not violate:
1. DO NOT touch pinned skills. Skip them entirely.
2. DO NOT use usage counters (use_count, use=0) as a reason to skip
   consolidation. Counters are often zero simply because a skill is new or
   rarely triggered — that is absence of evidence, not evidence of low value.
   Judge overlap on CONTENT, not on use_count.
3. DO NOT reject consolidation because "each skill has a distinct trigger".
   Pairwise distinctness is the wrong bar. The right bar is: "would a human
   maintainer write these as one skill with N labeled subsections, or as N
   separate skills?" When the answer is the former, merge.
4. Output JSON ONLY (no prose outside the JSON).

HOW TO IDENTIFY CLUSTERS:
- Scan for PREFIX CLUSTERS (skills sharing a first word or domain keyword,
  e.g. git-clone + git-push → git cluster).
- Scan for TOPIC CLUSTERS (skills addressing the same domain regardless of
  name prefix, e.g. "summarize text" + "condense article" → summarization).

THREE CONSOLIDATION MODES — use the right one per cluster:
  a. MERGE INTO EXISTING UMBRELLA — one existing skill is already broad
     enough to serve as the umbrella. Nominate it as the umbrella name and
     absorb the narrower siblings into it.
  b. CREATE NEW UMBRELLA — no existing member is broad enough. Propose a
     new short hyphenated class-level name that covers the shared workflow.
  c. DEMOTE TO REFERENCE — a sibling has narrow-but-valuable session-specific
     content better kept as a subsection or support reference inside the
     umbrella rather than as a top-level skill.

CLUSTERING RULES:
- Cluster on what the skill DOES (its description), not on exact name matches.
- Reuse an existing member's name as the umbrella when one is already broad
  enough; else propose a new short hyphenated name.
- A cluster needs 2+ members to be included in the output.

OUTPUT FORMAT — JSON ONLY:
{{"clusters": [{{"members": [...skill names...], "umbrella": "<name>",
  "reason": "<one short sentence — what class these serve>"}}]}}

Every member name MUST be one of the candidate names below. If nothing
overlaps, output {{"clusters": []}}.

NOTE: This output is SUGGESTION-ONLY. No skill is mutated, archived, or
renamed by this review. The operator reviews and decides which suggestions
to apply.

CANDIDATES ({n} skills):
{candidates_block}

OUTPUT:"""


def parse_consolidation_output(
    raw: Optional[str],
    valid_names: Set[str],
) -> List[Dict[str, Any]]:
    """Parse the LLM's JSON output into validated suggestion clusters.

    Pure function. Returns [] for any input that fails validation. Each
    returned cluster: ``{"members": [...], "umbrella": str, "reason": str}``.
    Every member must be a real candidate name; clusters need 2+ members.
    """
    if not raw or not isinstance(raw, str):
        return []
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    clusters_raw = obj.get("clusters") if isinstance(obj, dict) else None
    if not isinstance(clusters_raw, list):
        return []

    out: List[Dict[str, Any]] = []
    for c in clusters_raw:
        if not isinstance(c, dict):
            continue
        members = c.get("members")
        if not isinstance(members, list) or len(members) < 2:
            continue
        if not all(isinstance(m, str) for m in members):
            continue
        if any(m not in valid_names for m in members):
            continue
        umbrella = c.get("umbrella")
        umbrella = umbrella.strip() if isinstance(umbrella, str) else ""
        reason = c.get("reason")
        reason = reason.strip() if isinstance(reason, str) else ""
        out.append({
            "members": list(members),
            "umbrella": umbrella or members[0],
            "reason": reason,
        })
    return out


def _call_consolidation_llm(candidates: List[Dict[str, str]]) -> str:
    """Call Groq llama-3.1-8b-instant with the consolidation-review prompt.

    Mirrors ``pipeline.memory_consolidator._call_consolidator_llm`` exactly so
    failure modes (missing key, timeout, non-2xx) are identical. Returns a JSON
    string; on any failure returns ``{"clusters": []}``.
    """
    import httpx

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.debug("[curator] GROQ_API_KEY missing — skipping consolidation LLM")
        return '{"clusters": []}'

    candidates_block = "\n".join(
        f"- {c['name']}: {(c.get('description') or '').replace(chr(10), ' ')[:200]}"
        for c in candidates
    )
    prompt = _CONSOLIDATION_PROMPT.format(
        n=len(candidates),
        candidates_block=candidates_block,
    )

    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1200,
                    "temperature": 0.0,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning("[curator] consolidation LLM call failed: %s: %s", type(e).__name__, e)
        return '{"clusters": []}'


def run_consolidation_review(
    llm_fn: Optional[Callable[[List[Dict[str, str]]], str]] = None,
) -> List[Dict[str, Any]]:
    """Suggestion-only consolidation review over curatable skills.

    Returns a list of cluster suggestions (never mutates the library). Gated:
    returns [] unless ``JARVIS_CURATOR_CONSOLIDATION=1``. ``llm_fn`` is a seam
    for tests; production uses ``_call_consolidation_llm``.
    """
    if not consolidation_enabled():
        return []

    from pipeline.skills_loader import SKILLS

    rows = skill_usage.curatable_report()
    candidates: List[Dict[str, str]] = []
    valid_names: Set[str] = set()
    for r in rows:
        name = r.get("name")
        if not name:
            continue
        sk = SKILLS.get(name)
        desc = (sk.description if sk is not None else "") or ""
        candidates.append({"name": name, "description": desc})
        valid_names.add(name)

    if len(candidates) < 2:
        return []

    fn = llm_fn or _call_consolidation_llm
    try:
        raw = fn(candidates)
    except Exception as e:
        logger.warning("[curator] consolidation review LLM error: %s: %s", type(e).__name__, e)
        return []

    return parse_consolidation_output(raw, valid_names)


# ===========================================================================
# Per-run reports
# ===========================================================================

def _write_run_report(
    *,
    started_at: datetime,
    elapsed_seconds: float,
    auto_counts: Dict[str, int],
    before_report: List[Dict[str, Any]],
    before_names: Set[str],
    after_report: List[Dict[str, Any]],
    suggestions: List[Dict[str, Any]],
    backup_id: Optional[str],
) -> Optional[Path]:
    """Write run.json + REPORT.md under logs/curator/{YYYYMMDD-HHMMSS}/.
    Returns the report directory path, or None if it couldn't be written."""
    root = _reports_root()
    stamp = started_at.strftime("%Y%m%d-%H%M%S")
    run_dir = root / stamp
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = root / f"{stamp}-{suffix}"
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except Exception as e:
        logger.debug("Curator run dir create failed: %s", e)
        return None

    after_by_name = {r.get("name"): r for r in after_report if isinstance(r, dict)}
    after_names = set(after_by_name.keys())
    removed = sorted(before_names - after_names)
    added = sorted(after_names - before_names)
    before_by_name = {r.get("name"): r for r in before_report if isinstance(r, dict)}

    transitions: List[Dict[str, str]] = []
    for name in sorted(after_names & before_names):
        s_before = (before_by_name.get(name) or {}).get("state")
        s_after = (after_by_name.get(name) or {}).get("state")
        if s_before and s_after and s_before != s_after:
            transitions.append({"name": name, "from": s_before, "to": s_after})

    payload = {
        "started_at": started_at.isoformat(),
        "duration_seconds": round(elapsed_seconds, 2),
        "backup_id": backup_id,
        "auto_transitions": auto_counts,
        "counts": {
            "before": len(before_names),
            "after": len(after_names),
            "delta": len(after_names) - len(before_names),
            "archived_this_run": len(removed),
            "added_this_run": len(added),
            "state_transitions": len(transitions),
            "consolidation_suggestions": len(suggestions),
        },
        "archived": removed,
        "added": added,
        "state_transitions": transitions,
        "consolidation_suggestions": suggestions,
    }

    try:
        (run_dir / "run.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        logger.debug("Curator run.json write failed: %s", e)

    try:
        (run_dir / "REPORT.md").write_text(_render_report_markdown(payload), encoding="utf-8")
    except Exception as e:
        logger.debug("Curator REPORT.md write failed: %s", e)

    return run_dir


def _render_report_markdown(p: Dict[str, Any]) -> str:
    lines: List[str] = []
    started = p.get("started_at", "")
    duration = p.get("duration_seconds", 0) or 0
    mins, secs = divmod(int(duration), 60)
    dur_label = f"{mins}m {secs}s" if mins else f"{secs}s"
    counts = p.get("counts") or {}

    lines.append(f"# Curator run — {started}\n")
    lines.append(
        f"Duration: {dur_label}  ·  User skills: "
        f"{counts.get('before', 0)} -> {counts.get('after', 0)} "
        f"({counts.get('delta', 0):+d})\n"
    )
    if p.get("backup_id"):
        lines.append(f"Pre-run snapshot: `{p['backup_id']}`\n")

    auto = p.get("auto_transitions") or {}
    lines.append("## Auto-transitions (pure, no LLM)\n")
    lines.append(f"- checked: {auto.get('checked', 0)}")
    lines.append(f"- marked stale: {auto.get('marked_stale', 0)}")
    lines.append(f"- archived (time-based staleness): {auto.get('archived', 0)}")
    lines.append(f"- reactivated: {auto.get('reactivated', 0)}")
    lines.append("")

    archived = p.get("archived") or []
    if archived:
        lines.append(f"### Archived this run ({len(archived)})\n")
        lines.append(
            "_Directories moved to `<skills>/.archived/` — recoverable via "
            "`curator.restore_skill(<name>)` or a full snapshot rollback._\n"
        )
        for n in archived[:50]:
            lines.append(f"- `{n}`")
        lines.append("")

    trans = p.get("state_transitions") or []
    if trans:
        lines.append(f"### State transitions ({len(trans)})\n")
        for t in trans:
            lines.append(f"- `{t.get('name')}`: {t.get('from')} -> {t.get('to')}")
        lines.append("")

    suggestions = p.get("consolidation_suggestions") or []
    if suggestions:
        lines.append(f"### Consolidation suggestions ({len(suggestions)})\n")
        lines.append(
            "_Suggestion-only — the curator did NOT mutate these skills. "
            "Review and merge manually via `skill_manage`._\n"
        )
        for s in suggestions[:50]:
            members = ", ".join(s.get("members") or [])
            umbrella = s.get("umbrella", "?")
            reason = (s.get("reason") or "").strip()
            line = f"- [{members}] -> `{umbrella}`"
            if reason:
                line += f" — {reason}"
            lines.append(line)
        lines.append("")

    lines.append("## Recovery\n")
    lines.append("- Restore an archived skill: `curator.restore_skill(<name>)`")
    lines.append("- Roll back the whole skills tree: `curator.rollback()`")
    lines.append("- All archives + snapshots live under the skills root and are recoverable.")
    lines.append("")
    return "\n".join(lines)


# ===========================================================================
# Orchestrator — manual entry point
# ===========================================================================

def run_curation(
    *,
    dry_run: bool = False,
    on_summary: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Execute a single curation pass synchronously.

    Steps:
      1. Snapshot the skills tree (best-effort; skipped on dry-run).
      2. Apply deterministic stale/archive transitions (skipped on dry-run).
      3. Run the gated, suggestion-only consolidation review (if enabled).
      4. Write a per-run report and update ``.curator_state``.

    ``dry_run=True`` makes the pass report-only: no snapshot, no transitions,
    no state-counter bump — but the report is still written and the
    consolidation review (read-only) still runs, so callers can preview what a
    live pass would surface.

    Returns a summary dict (never raises out of the deterministic core).
    """
    start = datetime.now(timezone.utc)

    try:
        before_report = skill_usage.curatable_report()
    except Exception:
        before_report = []
    before_names = {r.get("name") for r in before_report if isinstance(r, dict)}

    backup_id: Optional[str] = None
    if dry_run:
        counts = {
            "checked": len(before_report),
            "marked_stale": 0,
            "archived": 0,
            "reactivated": 0,
        }
    else:
        try:
            snap = snapshot_skills(reason="pre-curation-run")
            if snap is not None:
                backup_id = snap.name
                if on_summary:
                    try:
                        on_summary(f"curator: snapshot created ({snap.name})")
                    except Exception:
                        pass
        except Exception as e:
            logger.debug("Curator pre-run snapshot failed: %s", e, exc_info=True)
        counts = apply_automatic_transitions(now=start)

    auto_parts = []
    if counts.get("marked_stale"):
        auto_parts.append(f"{counts['marked_stale']} marked stale")
    if counts.get("archived"):
        auto_parts.append(f"{counts['archived']} archived")
    if counts.get("reactivated"):
        auto_parts.append(f"{counts['reactivated']} reactivated")
    auto_summary = ", ".join(auto_parts) if auto_parts else "no changes"

    # Suggestion-only consolidation review.
    suggestions: List[Dict[str, Any]] = []
    try:
        suggestions = run_consolidation_review()
    except Exception as e:
        logger.debug("Curator consolidation review failed: %s", e, exc_info=True)

    try:
        after_report = skill_usage.curatable_report()
    except Exception:
        after_report = []

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()

    report_path: Optional[Path] = None
    try:
        report_path = _write_run_report(
            started_at=start,
            elapsed_seconds=elapsed,
            auto_counts=counts,
            before_report=before_report,
            before_names=before_names,
            after_report=after_report,
            suggestions=suggestions,
            backup_id=backup_id,
        )
    except Exception as e:
        logger.debug("Curator report write failed: %s", e, exc_info=True)

    prefix = "dry-run: " if dry_run else ""
    summary = f"{prefix}{auto_summary}"
    if suggestions:
        summary += f"; {len(suggestions)} consolidation suggestion(s)"

    state = load_state()
    if not dry_run:
        state["last_run_at"] = start.isoformat()
        state["run_count"] = int(state.get("run_count", 0)) + 1
    state["last_run_duration_seconds"] = elapsed
    state["last_run_summary"] = summary
    if report_path is not None:
        state["last_report_path"] = str(report_path)
    save_state(state)

    if on_summary:
        try:
            on_summary(f"curator: {summary}")
        except Exception:
            pass

    return {
        "started_at": start.isoformat(),
        "dry_run": dry_run,
        "auto_transitions": counts,
        "consolidation_suggestions": suggestions,
        "backup_id": backup_id,
        "report_path": str(report_path) if report_path else None,
        "summary": summary,
    }


def maybe_run_curation(
    *,
    idle_for_seconds: Optional[float] = None,
    on_summary: Optional[Callable[[str], None]] = None,
) -> Optional[Dict[str, Any]]:
    """Best-effort: run a curation pass if all gates pass. Returns the result
    dict if a pass ran, else None. Never raises.

    NOTE: the auto-trigger loop that calls this from the live agent (idle
    detection on the turn boundary) is a LATER step and is intentionally NOT
    wired into the turn loop yet. This is the engine seam it will call.
    """
    try:
        if not should_run_now():
            return None
        if idle_for_seconds is not None:
            min_idle_s = get_min_idle_hours() * 3600.0
            if idle_for_seconds < min_idle_s:
                return None
        return run_curation(on_summary=on_summary)
    except Exception as e:
        logger.debug("maybe_run_curation failed: %s", e, exc_info=True)
        return None
