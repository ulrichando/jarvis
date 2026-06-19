"""File Tools — read, write, patch, and search files for the JARVIS voice agent.

Registered tool names: ``read_file``, ``write_file``, ``patch``, ``search_files``

Behavioral contracts:
- Device-path blocklist (infinite output or blocking input)
- Binary-extension guard
- Sensitive-system-path write guard
- Read deduplication (skip unchanged files, break re-read loops)
- Per-operation consecutive-call loop detection
- Large-file hint for targeted reads

Implemented directly with Python stdlib — no ShellFileOperations or
upstream-specific environment machinery. JARVIS runs locally; Python file I/O
is the right primitive here.

Faithful behavioral port of the upstream file tools — local stdlib only.
"""
from __future__ import annotations

import difflib
import errno
import json
import logging
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Optional

from .registry import registry, tool_error

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Binary-extension guard — pure string check, no I/O
# ---------------------------------------------------------------------------

_BINARY_EXTENSIONS = frozenset({
    # Images
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff", ".tif",
    # Videos
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".wmv", ".flv", ".m4v", ".mpeg", ".mpg",
    # Audio
    ".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".wma", ".aiff", ".opus",
    # Archives
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar", ".xz", ".z", ".tgz", ".iso",
    # Executables/binaries
    ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a", ".obj", ".lib",
    ".app", ".msi", ".deb", ".rpm",
    # Documents (binary office formats)
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp",
    # Fonts
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    # Bytecode / VM artifacts
    ".pyc", ".pyo", ".class", ".jar", ".war", ".ear", ".node", ".wasm", ".rlib",
    # Database files
    ".sqlite", ".sqlite3", ".db", ".mdb", ".idx",
    # Design / 3D
    ".psd", ".ai", ".eps", ".sketch", ".fig", ".xd", ".blend", ".3ds", ".max",
    # Flash
    ".swf", ".fla",
    # Lock/profiling data
    ".lockb", ".dat", ".data",
})


def _has_binary_extension(path: str) -> bool:
    dot = path.rfind(".")
    if dot == -1:
        return False
    return path[dot:].lower() in _BINARY_EXTENSIONS


# ---------------------------------------------------------------------------
# Device path blocklist — reading these hangs the process
# ---------------------------------------------------------------------------

_BLOCKED_DEVICE_PATHS = frozenset({
    "/dev/zero", "/dev/random", "/dev/urandom", "/dev/full",
    "/dev/stdin", "/dev/tty", "/dev/console",
    "/dev/stdout", "/dev/stderr",
    "/dev/fd/0", "/dev/fd/1", "/dev/fd/2",
})


def _is_blocked_device(filepath: str) -> bool:
    normalized = os.path.expanduser(filepath)
    if normalized in _BLOCKED_DEVICE_PATHS:
        return True
    if normalized.startswith("/proc/") and normalized.endswith(
        ("/fd/0", "/fd/1", "/fd/2")
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# Sensitive-path write guard — refuse writes to system/credential files
# ---------------------------------------------------------------------------

# Unix system prefixes (lowercase, forward-slash). Matched against the RAW
# path too, so a Unix path emitted on Windows ('/etc/passwd' → C:\etc\passwd
# after resolve()) is still refused.
_SENSITIVE_PATH_PREFIXES = (
    "/etc/", "/boot/", "/usr/lib/systemd/",
    "/private/etc/", "/private/var/",
)
_SENSITIVE_EXACT_PATHS = {"/var/run/docker.sock", "/run/docker.sock"}
# Windows + cross-platform credential/system locations (substring, lowercase,
# forward-slash). Covers System32/SysWOW64 (registry hives, drivers\etc\hosts)
# and per-user secret stores on every OS.
_SENSITIVE_SUBSTRINGS = (
    "/windows/system32/", "/windows/syswow64/",
    "/.ssh/", "/.aws/", "/.gnupg/",
    "/microsoft/credentials/", "/microsoft/crypto/", "/microsoft/vault/",
)


def _check_sensitive_path(filepath: str) -> Optional[str]:
    """Return an error string if the path targets a sensitive system location.

    Cross-platform: Unix prefixes match the raw path (so a Unix-style path is
    refused on Windows where resolve() would rewrite '/etc/passwd' to
    C:\\etc\\passwd), and Windows system/credential locations match the resolved
    path. All comparisons are forward-slashed + lowercased."""
    _err = (
        f"Refusing to write to sensitive system path: {filepath}\n"
        "Use the terminal tool (with elevation) if you genuinely need to modify system files."
    )

    def _norm(s: str) -> str:
        return s.replace("\\", "/").lower()

    raw = _norm(filepath)
    try:
        resolved = _norm(str(Path(filepath).expanduser().resolve()))
    except (OSError, ValueError):
        resolved = raw

    for prefix in _SENSITIVE_PATH_PREFIXES:
        if raw.startswith(prefix) or resolved.startswith(prefix):
            return _err
    for exact in _SENSITIVE_EXACT_PATHS:
        if raw == exact or resolved == exact:
            return _err
    for sub in _SENSITIVE_SUBSTRINGS:
        if sub in raw or sub in resolved:
            return _err
    return None


# ---------------------------------------------------------------------------
# Read-size guard
# ---------------------------------------------------------------------------

_DEFAULT_MAX_READ_CHARS = 100_000
_LARGE_FILE_HINT_BYTES = 512_000  # 512 KB


def _get_max_read_chars() -> int:
    try:
        val = int(os.getenv("JARVIS_FILE_READ_MAX_CHARS", ""))
        if val > 0:
            return val
    except (TypeError, ValueError):
        pass
    return _DEFAULT_MAX_READ_CHARS


# ---------------------------------------------------------------------------
# Internal status text guard — prevent the model from writing dedup stubs
# ---------------------------------------------------------------------------

_READ_DEDUP_STATUS_MESSAGE = (
    "File unchanged since last read. The content from "
    "the earlier read_file result in this conversation is "
    "still current — refer to that instead of re-reading."
)


def _is_internal_file_status_text(content: str) -> bool:
    if not isinstance(content, str):
        return False
    stripped = content.strip()
    if not stripped:
        return False
    if stripped == _READ_DEDUP_STATUS_MESSAGE:
        return True
    if (_READ_DEDUP_STATUS_MESSAGE in stripped
            and len(stripped) <= 2 * len(_READ_DEDUP_STATUS_MESSAGE)):
        return True
    return False


# ---------------------------------------------------------------------------
# Per-session read tracker — dedup + loop detection
# Keyed by a session id string. Voice agent uses "default" for all turns.
# ---------------------------------------------------------------------------

_read_tracker_lock = threading.Lock()
_read_tracker: dict = {}

_READ_HISTORY_CAP = 500
_DEDUP_CAP = 1000
_READ_TIMESTAMPS_CAP = 1000


def _ensure_tracker(session_id: str) -> dict:
    """Return (or create) the tracker dict for this session."""
    td = _read_tracker.get(session_id)
    if td is None:
        _read_tracker[session_id] = td = {
            "last_key": None,
            "consecutive": 0,
            "read_history": [],
            "dedup": {},
            "dedup_hits": {},
            "read_timestamps": {},
        }
    else:
        # Backward-compat: add keys added after session started
        td.setdefault("dedup_hits", {})
        td.setdefault("read_timestamps", {})
    return td


def _cap_tracker(td: dict) -> None:
    """Evict oldest entries once per-task containers exceed their caps."""
    rh = td.get("read_history")
    if rh is not None and len(rh) > _READ_HISTORY_CAP:
        del rh[: len(rh) - _READ_HISTORY_CAP]

    for key in ("dedup", "dedup_hits", "read_timestamps"):
        d = td.get(key)
        if d is not None and len(d) > _DEDUP_CAP:
            excess = len(d) - _DEDUP_CAP
            for _ in range(excess):
                try:
                    d.pop(next(iter(d)))
                except (StopIteration, KeyError):
                    break


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def _resolve_path(filepath: str) -> Path:
    """Resolve filepath; relative paths are anchored at cwd."""
    p = Path(filepath).expanduser()
    if not p.is_absolute():
        base = os.environ.get("TERMINAL_CWD", os.getcwd())
        p = Path(base) / p
    return p.resolve()


def _invalidate_dedup_for_path(filepath: str, session_id: str) -> None:
    """Drop all dedup cache entries for the written path (all offsets/limits)."""
    try:
        resolved = str(_resolve_path(filepath))
    except (OSError, ValueError):
        return
    with _read_tracker_lock:
        td = _read_tracker.get(session_id)
        if td is None:
            return
        dedup = td.get("dedup")
        if not dedup:
            return
        stale = [k for k in dedup if k[0] == resolved]
        for k in stale:
            del dedup[k]


def _update_read_timestamp(filepath: str, session_id: str) -> None:
    """Record current mtime after a successful write to prevent false staleness warnings."""
    _invalidate_dedup_for_path(filepath, session_id)
    try:
        resolved = str(_resolve_path(filepath))
        current_mtime = os.path.getmtime(resolved)
    except (OSError, ValueError):
        return
    with _read_tracker_lock:
        td = _read_tracker.get(session_id)
        if td is not None:
            td.setdefault("read_timestamps", {})[resolved] = current_mtime
            _cap_tracker(td)


def _check_file_staleness(filepath: str, session_id: str) -> Optional[str]:
    """Return a warning string if the file changed since the last read, or None."""
    try:
        resolved = str(_resolve_path(filepath))
    except (OSError, ValueError):
        return None
    with _read_tracker_lock:
        td = _read_tracker.get(session_id)
        if not td:
            return None
        read_mtime = td.get("read_timestamps", {}).get(resolved)
    if read_mtime is None:
        return None
    try:
        current_mtime = os.path.getmtime(resolved)
    except OSError:
        return None
    if current_mtime != read_mtime:
        return (
            f"Warning: {filepath} was modified since you last read it "
            "(external edit or concurrent agent). Consider re-reading before writing."
        )
    return None


# ---------------------------------------------------------------------------
# read_file implementation
# ---------------------------------------------------------------------------

def _read_file_impl(path: str, offset: int = 1, limit: int = 500,
                    session_id: str = "default") -> str:
    """Core read_file logic. Returns a JSON string."""
    # Clamp pagination.
    offset = max(1, int(offset) if offset else 1)
    limit = max(1, min(2000, int(limit) if limit else 500))

    if _is_blocked_device(path):
        return json.dumps({
            "error": (
                f"Cannot read '{path}': this is a device file that would "
                "block or produce infinite output."
            ),
        })

    try:
        resolved = _resolve_path(path)
    except (OSError, ValueError) as exc:
        return tool_error(f"Cannot resolve path '{path}': {exc}")

    if _has_binary_extension(str(resolved)):
        ext = resolved.suffix.lower()
        return json.dumps({
            "error": (
                f"Cannot read binary file '{path}' ({ext}). "
                "Use vision tools for images, or terminal to inspect binary files."
            ),
        })

    # Dedup check: if we already sent this exact content and the file hasn't changed,
    # return a lightweight stub instead of re-sending.
    resolved_str = str(resolved)
    dedup_key = (resolved_str, offset, limit)

    with _read_tracker_lock:
        td = _ensure_tracker(session_id)
        cached_mtime = td["dedup"].get(dedup_key)

    if cached_mtime is not None:
        try:
            current_mtime = os.path.getmtime(resolved_str)
            if current_mtime == cached_mtime:
                with _read_tracker_lock:
                    hits = td["dedup_hits"].get(dedup_key, 0) + 1
                    td["dedup_hits"][dedup_key] = hits
                    _cap_tracker(td)
                if hits >= 2:
                    return json.dumps({
                        "error": (
                            f"BLOCKED: You have called read_file on this "
                            f"exact region {hits + 1} times and the file "
                            "has NOT changed. Proceed with your task using "
                            "the information you already have."
                        ),
                        "path": path,
                        "already_read": hits + 1,
                    }, ensure_ascii=False)
                return json.dumps({
                    "status": "unchanged",
                    "message": _READ_DEDUP_STATUS_MESSAGE,
                    "path": path,
                    "dedup": True,
                    "content_returned": False,
                }, ensure_ascii=False)
        except OSError:
            pass  # stat failed — fall through to full read

    # Perform the read.
    try:
        stat = resolved.stat()
        file_size = stat.st_size
    except OSError:
        file_size = 0

    try:
        with open(resolved, encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except OSError as exc:
        return tool_error(str(exc))

    total_lines = len(all_lines)
    start_idx = offset - 1  # 0-based
    end_idx = start_idx + limit
    selected = all_lines[start_idx:end_idx]
    truncated = end_idx < total_lines

    # Format with line numbers: "N\tCONTENT"
    content = "".join(
        f"{offset + i}\t{line}"
        for i, line in enumerate(selected)
    )

    max_chars = _get_max_read_chars()
    if len(content) > max_chars:
        return json.dumps({
            "error": (
                f"Read produced {len(content):,} characters which exceeds "
                f"the safety limit ({max_chars:,} chars). "
                "Use offset and limit to read a smaller range. "
                f"The file has {total_lines} lines total."
            ),
            "path": path,
            "total_lines": total_lines,
            "file_size": file_size,
        }, ensure_ascii=False)

    # Track for loop detection and dedup.
    read_key = ("read", path, offset, limit)
    with _read_tracker_lock:
        td["dedup_hits"].pop(dedup_key, None)
        td["read_history"].append((path, offset, limit))
        if td["last_key"] == read_key:
            td["consecutive"] += 1
        else:
            td["last_key"] = read_key
            td["consecutive"] = 1
        count = td["consecutive"]

        try:
            _mtime = os.path.getmtime(resolved_str)
            td["dedup"][dedup_key] = _mtime
            td["read_timestamps"][resolved_str] = _mtime
        except OSError:
            pass

        _cap_tracker(td)

    result_dict: dict = {
        "content": content,
        "path": path,
        "total_lines": total_lines,
        "offset": offset,
        "limit": limit,
        "truncated": truncated,
        "file_size": file_size,
    }

    if (file_size and file_size > _LARGE_FILE_HINT_BYTES
            and limit > 200 and truncated):
        result_dict["_hint"] = (
            f"This file is large ({file_size:,} bytes). "
            "Consider reading only the section you need with offset and limit "
            "to keep context usage efficient."
        )

    if count >= 4:
        return json.dumps({
            "error": (
                f"BLOCKED: You have read this exact file region {count} times in a row. "
                "The content has NOT changed. STOP re-reading and proceed with your task."
            ),
            "path": path,
            "already_read": count,
        }, ensure_ascii=False)
    elif count >= 3:
        result_dict["_warning"] = (
            f"You have read this exact file region {count} times consecutively. "
            "Use the information you already have. "
            "If you are stuck in a loop, stop reading and proceed with writing or responding."
        )

    return json.dumps(result_dict, ensure_ascii=False)


# ---------------------------------------------------------------------------
# write_file implementation
# ---------------------------------------------------------------------------

def _write_file_impl(path: str, content: str,
                     session_id: str = "default") -> str:
    """Core write_file logic. Returns a JSON string."""
    sensitive_err = _check_sensitive_path(path)
    if sensitive_err:
        return tool_error(sensitive_err)

    if _is_internal_file_status_text(content):
        return tool_error(
            "Refusing to write internal read_file status text as file content. "
            "Re-read the file or reconstruct the intended file contents before writing."
        )

    stale_warning = _check_file_staleness(path, session_id)

    try:
        resolved = _resolve_path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)
        lines_written = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        result: dict = {
            "success": True,
            "path": str(resolved),
            "bytes_written": len(content.encode("utf-8")),
            "lines_written": lines_written,
        }
        if stale_warning:
            result["_warning"] = stale_warning
        _update_read_timestamp(path, session_id)
        return json.dumps(result, ensure_ascii=False)
    except PermissionError as exc:
        return tool_error(str(exc))
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EPERM, errno.EROFS}:
            return tool_error(str(exc))
        return tool_error(str(exc))
    except Exception as exc:
        logger.error("write_file error: %s: %s", type(exc).__name__, exc, exc_info=True)
        return tool_error(str(exc))


# ---------------------------------------------------------------------------
# patch implementation (replace mode + basic patch mode)
# ---------------------------------------------------------------------------

def _patch_impl(
    mode: str = "replace",
    path: Optional[str] = None,
    old_string: Optional[str] = None,
    new_string: Optional[str] = None,
    replace_all: bool = False,
    patch: Optional[str] = None,
    session_id: str = "default",
) -> str:
    """Core patch logic. Returns a JSON string."""
    if mode == "replace":
        if not path:
            return tool_error("path required for mode='replace'")
        if old_string is None or new_string is None:
            return tool_error("old_string and new_string required for mode='replace'")

        sensitive_err = _check_sensitive_path(path)
        if sensitive_err:
            return tool_error(sensitive_err)

        stale_warning = _check_file_staleness(path, session_id)

        try:
            resolved = _resolve_path(path)
            try:
                with open(resolved, encoding="utf-8", errors="replace") as f:
                    original = f.read()
            except OSError as exc:
                return tool_error(str(exc))

            if old_string not in original:
                # Try fuzzy match hints.
                lines_old = old_string.splitlines()
                lines_src = original.splitlines()
                matches = difflib.get_close_matches(
                    old_string, lines_src, n=3, cutoff=0.6
                )
                hint = ""
                if matches:
                    hint = (
                        "\nDid you mean one of these sections?\n"
                        + "\n".join(f"  {m!r}" for m in matches)
                    )
                return json.dumps({
                    "error": f"Could not find old_string in {path}.{hint}",
                    "_hint": (
                        "old_string not found. Use read_file to verify the current "
                        "content, or search_files to locate the text."
                    ),
                }, ensure_ascii=False)

            if replace_all:
                updated = original.replace(old_string, new_string)
                count = original.count(old_string)
            else:
                occurrences = original.count(old_string)
                if occurrences > 1:
                    return json.dumps({
                        "error": (
                            f"old_string appears {occurrences} times in {path}. "
                            "Provide more context to make it unique, or use replace_all=true."
                        ),
                    }, ensure_ascii=False)
                updated = original.replace(old_string, new_string, 1)
                count = 1

            # Generate unified diff for the result.
            diff_lines = list(difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                n=3,
            ))
            diff_str = "".join(diff_lines[:200])  # cap diff size

            with open(resolved, "w", encoding="utf-8") as f:
                f.write(updated)

            result: dict = {
                "success": True,
                "path": str(resolved),
                "replacements": count,
                "diff": diff_str,
            }
            if stale_warning:
                result["_warning"] = stale_warning
            _update_read_timestamp(path, session_id)
            return json.dumps(result, ensure_ascii=False)

        except Exception as exc:
            return tool_error(str(exc))

    elif mode == "patch":
        if not patch:
            return tool_error("patch content required for mode='patch'")
        # V4A patch format: parse and apply each file section.
        return _apply_v4a_patch(patch, session_id)

    else:
        return tool_error(f"Unknown mode: {mode}. Use 'replace' or 'patch'.")


# ---------------------------------------------------------------------------
# V4A patch parser (minimal — handles Update File blocks)
# ---------------------------------------------------------------------------

def _apply_v4a_patch(patch_content: str, session_id: str = "default") -> str:
    """Apply a V4A-format multi-file patch. Returns a JSON result string."""
    results = []
    errors = []

    # Split on *** Begin Patch / *** End Patch boundaries
    patch_content = patch_content.strip()
    if patch_content.startswith("*** Begin Patch"):
        patch_content = re.sub(r"^\*\*\* Begin Patch\s*\n?", "", patch_content)
    if patch_content.endswith("*** End Patch"):
        patch_content = re.sub(r"\n?\*\*\* End Patch\s*$", "", patch_content)

    # Split into per-file sections
    file_sections = re.split(r"(?=^\*\*\* (?:Update|Add|Delete) File:)", patch_content, flags=re.MULTILINE)

    for section in file_sections:
        section = section.strip()
        if not section:
            continue

        m_update = re.match(r"^\*\*\* Update File:\s*(.+)$", section, re.MULTILINE)
        m_add = re.match(r"^\*\*\* Add File:\s*(.+)$", section, re.MULTILINE)
        m_delete = re.match(r"^\*\*\* Delete File:\s*(.+)$", section, re.MULTILINE)

        if m_delete:
            filepath = m_delete.group(1).strip()
            sensitive_err = _check_sensitive_path(filepath)
            if sensitive_err:
                errors.append({"file": filepath, "error": sensitive_err})
                continue
            try:
                resolved = _resolve_path(filepath)
                resolved.unlink()
                results.append({"file": filepath, "action": "deleted"})
            except Exception as exc:
                errors.append({"file": filepath, "error": str(exc)})
            continue

        if m_add:
            filepath = m_add.group(1).strip()
            sensitive_err = _check_sensitive_path(filepath)
            if sensitive_err:
                errors.append({"file": filepath, "error": sensitive_err})
                continue
            # Everything after the header line is the new content
            header_end = section.index("\n") + 1 if "\n" in section else len(section)
            new_content = section[header_end:]
            # Strip leading +
            new_content = re.sub(r"^\+", "", new_content, flags=re.MULTILINE)
            try:
                resolved = _resolve_path(filepath)
                resolved.parent.mkdir(parents=True, exist_ok=True)
                with open(resolved, "w", encoding="utf-8") as f:
                    f.write(new_content)
                results.append({"file": filepath, "action": "added"})
                _update_read_timestamp(filepath, session_id)
            except Exception as exc:
                errors.append({"file": filepath, "error": str(exc)})
            continue

        if m_update:
            filepath = m_update.group(1).strip()
            sensitive_err = _check_sensitive_path(filepath)
            if sensitive_err:
                errors.append({"file": filepath, "error": sensitive_err})
                continue
            # Parse hunk sections
            try:
                resolved = _resolve_path(filepath)
                try:
                    with open(resolved, encoding="utf-8", errors="replace") as f:
                        original = f.read()
                except OSError as exc:
                    errors.append({"file": filepath, "error": str(exc)})
                    continue

                updated = _apply_unified_hunks(original, section)
                if updated is None:
                    errors.append({"file": filepath, "error": "Failed to apply patch hunks"})
                    continue

                with open(resolved, "w", encoding="utf-8") as f:
                    f.write(updated)
                results.append({"file": filepath, "action": "updated"})
                _update_read_timestamp(filepath, session_id)
            except Exception as exc:
                errors.append({"file": filepath, "error": str(exc)})
            continue

    if errors and not results:
        return json.dumps({"error": "Patch failed", "errors": errors}, ensure_ascii=False)

    result: dict = {"success": True, "files": results}
    if errors:
        result["errors"] = errors
    return json.dumps(result, ensure_ascii=False)


def _apply_unified_hunks(original: str, patch_section: str) -> Optional[str]:
    """Apply V4A-style unified diff hunks to *original*. Returns updated text or None on failure."""
    lines = original.splitlines(keepends=True)
    # Extract hunk blocks (starting with @@)
    hunk_re = re.compile(r"^@@ .* @@.*$", re.MULTILINE)
    hunk_starts = [m.start() for m in hunk_re.finditer(patch_section)]
    if not hunk_starts:
        return None

    result_lines = list(lines)
    offset = 0  # accumulated line offset from prior hunks

    for hi, hstart in enumerate(hunk_starts):
        hend = hunk_starts[hi + 1] if hi + 1 < len(hunk_starts) else len(patch_section)
        hunk_text = patch_section[hstart:hend]
        hunk_lines = hunk_text.splitlines()

        # Parse the @@ header
        header = hunk_lines[0]
        m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", header)
        if not m:
            return None
        src_start = int(m.group(1)) - 1  # 0-based

        # Build old/new from hunk body
        old_chunk: list[str] = []
        new_chunk: list[str] = []
        for line in hunk_lines[1:]:
            if line.startswith("-"):
                old_chunk.append(line[1:])
            elif line.startswith("+"):
                new_chunk.append(line[1:])
            elif line.startswith(" ") or line == "":
                ctx = line[1:] if line.startswith(" ") else "\n"
                old_chunk.append(ctx)
                new_chunk.append(ctx)

        apply_at = src_start + offset
        # Verify context matches
        actual = result_lines[apply_at:apply_at + len(old_chunk)]
        actual_text = [l if l.endswith("\n") else l + "\n" for l in actual]
        old_text = [l if l.endswith("\n") else l + "\n" for l in old_chunk]
        if actual_text != old_text:
            # Fuzzy: just try to proceed anyway with best-effort splice
            pass
        result_lines[apply_at:apply_at + len(old_chunk)] = [
            l if l.endswith("\n") else l + "\n" for l in new_chunk
        ]
        offset += len(new_chunk) - len(old_chunk)

    return "".join(result_lines)


# ---------------------------------------------------------------------------
# search_files implementation (ripgrep-backed with grep fallback)
# ---------------------------------------------------------------------------

def _search_files_impl(
    pattern: str,
    target: str = "content",
    path: str = ".",
    file_glob: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    output_mode: str = "content",
    context: int = 0,
    session_id: str = "default",
) -> str:
    """Core search_files logic. Returns a JSON string."""
    offset = max(0, int(offset) if offset else 0)
    limit = max(1, min(500, int(limit) if limit else 50))
    context = max(0, int(context) if context else 0)

    # Track for loop detection
    search_key = ("search", pattern, target, str(path), file_glob or "", limit, offset)
    with _read_tracker_lock:
        td = _ensure_tracker(session_id)
        if td["last_key"] == search_key:
            td["consecutive"] += 1
        else:
            td["last_key"] = search_key
            td["consecutive"] = 1
        count = td["consecutive"]

    if count >= 4:
        return json.dumps({
            "error": (
                f"BLOCKED: You have run this exact search {count} times in a row. "
                "The results have NOT changed. STOP re-searching and proceed with your task."
            ),
            "pattern": pattern,
            "already_searched": count,
        }, ensure_ascii=False)

    try:
        search_root = _resolve_path(path) if path != "." else Path(os.getcwd())
    except Exception:
        search_root = Path(os.getcwd())

    if target in ("files", "find"):
        return _search_by_name(pattern, search_root, limit, offset)
    else:
        return _search_content(
            pattern, search_root, file_glob, limit, offset,
            output_mode, context, count
        )


def _search_by_name(pattern: str, root: Path, limit: int, offset: int) -> str:
    """Find files by glob pattern, sorted by mtime (newest first)."""
    import fnmatch
    try:
        # Use rg --files for speed, fall back to os.walk
        try:
            result = subprocess.run(
                ["rg", "--files", str(root)],
                capture_output=True, text=True, timeout=30
            )
            candidates = result.stdout.splitlines()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            candidates = []
            for dirpath, dirnames, filenames in os.walk(str(root)):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for fname in filenames:
                    candidates.append(os.path.join(dirpath, fname))

        if pattern and pattern not in ("*", "**"):
            candidates = [c for c in candidates if fnmatch.fnmatch(os.path.basename(c), pattern)]

        # Sort by mtime descending.
        try:
            candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        except Exception:
            pass

        total = len(candidates)
        page = candidates[offset:offset + limit]
        truncated = (offset + limit) < total

        result_dict: dict = {
            "files": page,
            "total": total,
            "offset": offset,
            "limit": limit,
            "truncated": truncated,
        }
        out = json.dumps(result_dict, ensure_ascii=False)
        if truncated:
            next_offset = offset + limit
            out += f"\n\n[Hint: Results truncated. Use offset={next_offset} to see more.]"
        return out
    except Exception as exc:
        return tool_error(str(exc))


_SEARCH_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "target", "dist", ".next", ".mypy_cache", ".pytest_cache", "build",
}


def _match_path(line: str) -> str:
    """Extract the file path from an rg/grep 'path:lineno:text' line.
    Handles Windows drive prefixes ('C:\\...') that a naive split(':')[0]
    would truncate to just the drive letter."""
    m = re.match(r"^(.*?):\d+[:\-]", line)
    return m.group(1) if m else line.split(":")[0]


def _python_content_search(
    pattern: str, root: str, file_glob: Optional[str], context: int,
) -> list:
    """Cross-platform pure-Python content search — the fallback used when
    ripgrep isn't installed. Returns rg-style 'path:lineno:text' lines
    (matches + any -C context lines).

    We deliberately do NOT shell out to grep here: MSYS/Git-Bash grep on
    Windows glob-expands the ``--include`` argument before grep sees it, which
    shifts the positional args and silently turns the search pattern into a
    (missing) filename — returning zero matches with no error. grep is also not
    guaranteed present cross-platform. Pure Python always works."""
    import fnmatch
    try:
        rx = re.compile(pattern)
    except re.error:
        rx = re.compile(re.escape(pattern))

    rp = Path(root)
    if rp.is_file():
        files = [rp]
    else:
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SEARCH_SKIP_DIRS]
            for fn in filenames:
                if file_glob and not fnmatch.fnmatch(fn, file_glob):
                    continue
                files.append(Path(dirpath) / fn)

    out: list = []
    for fp in files:
        try:
            data = fp.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:8192]:  # binary file — skip, like rg/grep
            continue
        lines = data.decode("utf-8", errors="replace").splitlines()
        emit: set = set()
        for i, line in enumerate(lines):
            if rx.search(line):
                lo = max(0, i - context)
                hi = min(len(lines), i + context + 1)
                emit.update(range(lo, hi))
        for j in sorted(emit):
            out.append(f"{fp}:{j + 1}:{lines[j]}")
    return out


def _search_content(
    pattern: str,
    root: Path,
    file_glob: Optional[str],
    limit: int,
    offset: int,
    output_mode: str,
    context: int,
    loop_count: int,
) -> str:
    """Search file contents using ripgrep (pure-Python fallback when absent)."""
    try:
        cmd = ["rg", "--line-number", "--no-heading"]
        if context > 0:
            cmd += ["-C", str(context)]
        if file_glob:
            cmd += ["--glob", file_glob]
        # (rg skips binary file contents by default — no flag needed.)
        cmd += [pattern, str(root)]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, errors="replace")
            # rg exit codes: 0 = matches, 1 = no matches, ≥2 = error
            # (bad flag / pattern / IO). An error leaves stdout empty —
            # take the grep fallback rather than reporting zero matches.
            if proc.returncode >= 2:
                raise FileNotFoundError(proc.stderr.strip()[:200] or "rg failed")
            raw_lines = proc.stdout.splitlines()
        except FileNotFoundError:
            # ripgrep not installed → pure-Python fallback (NOT grep — MSYS grep
            # on Windows glob-expands --include and returns zero matches; grep
            # also isn't guaranteed cross-platform).
            try:
                raw_lines = _python_content_search(pattern, str(root), file_glob, context)
            except Exception as exc2:
                return tool_error(f"search_files: ripgrep unavailable and Python fallback failed: {exc2}")
        except subprocess.TimeoutExpired:
            return tool_error("search_files: search timed out after 30 seconds")

        total = len(raw_lines)
        page_lines = raw_lines[offset:offset + limit]
        truncated = (offset + limit) < total

        if output_mode == "files_only":
            # Extract unique file paths.
            seen: set = set()
            files: list = []
            for line in page_lines:
                fp = _match_path(line)
                if fp not in seen:
                    seen.add(fp)
                    files.append(fp)
            result_dict: dict = {
                "files": files,
                "total_matches": total,
                "offset": offset,
                "limit": limit,
                "truncated": truncated,
            }
        elif output_mode == "count":
            # Count matches per file.
            counts: dict = {}
            for line in raw_lines:
                fp = _match_path(line)
                counts[fp] = counts.get(fp, 0) + 1
            result_dict = {
                "counts": counts,
                "total_matches": total,
                "offset": offset,
                "limit": limit,
            }
        else:  # content (default)
            result_dict = {
                "matches": page_lines,
                "total_matches": total,
                "offset": offset,
                "limit": limit,
                "truncated": truncated,
            }

        if loop_count >= 3:
            result_dict["_warning"] = (
                f"You have run this exact search {loop_count} times consecutively. "
                "Use the information you already have."
            )

        out = json.dumps(result_dict, ensure_ascii=False)
        if truncated:
            next_offset = offset + limit
            out += f"\n\n[Hint: Results truncated. Use offset={next_offset} to see more, or narrow with a more specific pattern or file_glob.]"
        return out

    except Exception as exc:
        return tool_error(str(exc))


# ---------------------------------------------------------------------------
# Notify consecutive tracker on non-read/search tool calls
# ---------------------------------------------------------------------------

def notify_other_tool_call(session_id: str = "default") -> None:
    """Reset the consecutive read/search counter for a session.

    Call whenever a tool OTHER than read_file / search_files is executed.
    Ensures we only warn on *truly consecutive* repeated reads.
    """
    with _read_tracker_lock:
        td = _read_tracker.get(session_id)
        if td:
            td["last_key"] = None
            td["consecutive"] = 0
            if "dedup_hits" in td:
                td["dedup_hits"].clear()


# ---------------------------------------------------------------------------
# Schemas + handlers + registration
# ---------------------------------------------------------------------------

READ_FILE_SCHEMA = {
    "name": "read_file",
    "description": (
        "Read a text file with line numbers and pagination. "
        "Use this instead of cat/head/tail in terminal. "
        "Output format: 'LINE_NUM\\tCONTENT'. "
        "Use offset and limit for large files. "
        "Reads exceeding ~100K characters are rejected; use offset and limit to read specific sections. "
        "NOTE: Cannot read images or binary files — use vision tools for images. "
        "DO NOT summarize or describe a file's contents before calling this tool — "
        "claiming to know what's in a file without reading it is confab."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to read (absolute, relative, or ~/path)",
            },
            "offset": {
                "type": "integer",
                "description": "Line number to start reading from (1-indexed, default: 1)",
                "default": 1,
                "minimum": 1,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to read (default: 500, max: 2000)",
                "default": 500,
                "maximum": 2000,
            },
        },
        "required": ["path"],
    },
}

WRITE_FILE_SCHEMA = {
    "name": "write_file",
    "description": (
        "Write content to a file, completely replacing existing content. "
        "Use this instead of echo/cat heredoc in terminal. "
        "Creates parent directories automatically. "
        "OVERWRITES the entire file — use 'patch' for targeted edits. "
        "DO NOT reply 'Saved', 'Created', 'Written' UNLESS this tool has "
        "actually been called and returned success in the same turn."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to write (created if absent, overwritten if present)",
            },
            "content": {
                "type": "string",
                "description": "Complete content to write to the file",
            },
        },
        "required": ["path", "content"],
    },
}

PATCH_SCHEMA = {
    "name": "patch",
    "description": (
        "Targeted find-and-replace edits in files. Use this instead of sed/awk in terminal. "
        "Returns a unified diff.\n\n"
        "REPLACE MODE (mode='replace', default): find a unique string and replace it. "
        "REQUIRED PARAMETERS: mode, path, old_string, new_string.\n"
        "PATCH MODE (mode='patch'): apply V4A multi-file patches for bulk changes. "
        "REQUIRED PARAMETERS: mode, patch.\n\n"
        "DO NOT reply 'Edited', 'Fixed', 'Updated' UNLESS this tool has actually "
        "been called this turn and returned a diff. Tool first, words after."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["replace", "patch"],
                "description": "Edit mode. 'replace' (default): requires path + old_string + new_string. 'patch': requires patch content only.",
                "default": "replace",
            },
            "path": {
                "type": "string",
                "description": "REQUIRED when mode='replace'. File path to edit.",
            },
            "old_string": {
                "type": "string",
                "description": "REQUIRED when mode='replace'. Exact text to find and replace. Must be unique in the file unless replace_all=true.",
            },
            "new_string": {
                "type": "string",
                "description": "REQUIRED when mode='replace'. Replacement text. Pass empty string '' to delete the matched text.",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences instead of requiring a unique match (default: false)",
                "default": False,
            },
            "patch": {
                "type": "string",
                "description": (
                    "REQUIRED when mode='patch'. V4A format patch content. Format:\n"
                    "*** Begin Patch\n*** Update File: path/to/file\n"
                    "@@ context hint @@\n context line\n-removed line\n+added line\n"
                    "*** End Patch"
                ),
            },
        },
        "required": ["mode"],
    },
}

SEARCH_FILES_SCHEMA = {
    "name": "search_files",
    "description": (
        "Search file contents or find files by name. "
        "Use this instead of grep/rg/find/ls in terminal. Ripgrep-backed.\n\n"
        "Content search (target='content'): regex search inside files. "
        "Output modes: full matches with line numbers, file paths only, or match counts.\n\n"
        "File search (target='files'): find files by glob pattern (e.g., '*.py', '*config*'). "
        "Results sorted by modification time (newest first)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern for content search, or glob pattern (e.g., '*.py') for file search",
            },
            "target": {
                "type": "string",
                "enum": ["content", "files"],
                "description": "'content' searches inside file contents, 'files' searches for files by name",
                "default": "content",
            },
            "path": {
                "type": "string",
                "description": "Directory or file to search in (default: current working directory)",
                "default": ".",
            },
            "file_glob": {
                "type": "string",
                "description": "Filter files by pattern in content search mode (e.g., '*.py' to only search Python files)",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return (default: 50)",
                "default": 50,
            },
            "offset": {
                "type": "integer",
                "description": "Skip first N results for pagination (default: 0)",
                "default": 0,
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_only", "count"],
                "description": "Output format for content search: 'content' shows matching lines, 'files_only' lists file paths, 'count' shows match counts per file",
                "default": "content",
            },
            "context": {
                "type": "integer",
                "description": "Number of context lines before and after each match (content search only)",
                "default": 0,
            },
        },
        "required": ["pattern"],
    },
}


def _handle_read_file(args: dict, **kw) -> str:
    return _read_file_impl(
        path=args.get("path", ""),
        offset=args.get("offset", 1),
        limit=args.get("limit", 500),
        session_id=kw.get("session_id") or "default",
    )


def _handle_write_file(args: dict, **kw) -> str:
    if not args.get("path") or not isinstance(args.get("path"), str):
        return tool_error(
            "write_file: missing required field 'path'. Re-emit the tool call with "
            "both 'path' and 'content' set."
        )
    if "content" not in args:
        return tool_error(
            "write_file: missing required field 'content'. Re-emit the tool call with "
            "the full content payload."
        )
    if not isinstance(args["content"], str):
        return tool_error(
            f"write_file: 'content' must be a string, got {type(args['content']).__name__}."
        )
    return _write_file_impl(
        path=args["path"],
        content=args["content"],
        session_id=kw.get("session_id") or "default",
    )


def _handle_patch(args: dict, **kw) -> str:
    return _patch_impl(
        mode=args.get("mode", "replace"),
        path=args.get("path"),
        old_string=args.get("old_string"),
        new_string=args.get("new_string"),
        replace_all=args.get("replace_all", False),
        patch=args.get("patch"),
        session_id=kw.get("session_id") or "default",
    )


def _handle_search_files(args: dict, **kw) -> str:
    target_map = {"grep": "content", "find": "files"}
    raw_target = args.get("target", "content")
    target = target_map.get(raw_target, raw_target)
    return _search_files_impl(
        pattern=args.get("pattern", ""),
        target=target,
        path=args.get("path", "."),
        file_glob=args.get("file_glob"),
        limit=args.get("limit", 50),
        offset=args.get("offset", 0),
        output_mode=args.get("output_mode", "content"),
        context=args.get("context", 0),
        session_id=kw.get("session_id") or "default",
    )


registry.register(
    name="read_file",
    schema=READ_FILE_SCHEMA,
    handler=_handle_read_file,
    toolset="file",
    is_async=False,
    emoji="📖",
    max_result_size_chars=100_000,
)

registry.register(
    name="write_file",
    schema=WRITE_FILE_SCHEMA,
    handler=_handle_write_file,
    toolset="file",
    is_async=False,
    emoji="✍️",
    max_result_size_chars=100_000,
)

registry.register(
    name="patch",
    schema=PATCH_SCHEMA,
    handler=_handle_patch,
    toolset="file",
    is_async=False,
    emoji="🔧",
    max_result_size_chars=100_000,
)

registry.register(
    name="search_files",
    schema=SEARCH_FILES_SCHEMA,
    handler=_handle_search_files,
    toolset="file",
    is_async=False,
    emoji="🔎",
    max_result_size_chars=100_000,
)
