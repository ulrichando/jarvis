"""JARVIS Codebase Indexer — core builder.

Two-tier design:
  Tier 1: Always-fresh directory tree via os.walk + pathspec gitignore.
           Run every session start, ~50ms, zero staleness risk.
  Tier 2: Per-file symbol cache with (mtime, size) invalidation.
           Accumulated passively; symbols extracted via regex (no LLM).
           Stored at .jarvis/file-summaries.json.

Session-start cleanup: orphaned entries (deleted/renamed files) are purged
automatically by comparing cache keys against the walked file set.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import logging

log = logging.getLogger("jarvis.indexer")

# ── Constants ─────────────────────────────────────────────────────────

SUMMARIES_FILE = "file-summaries.json"
INDEX_VERSION = 2

# Always skip these regardless of .gitignore
ALWAYS_IGNORE: set[str] = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".env", "dist", "build", ".next", ".nuxt", "target", ".tox",
    "htmlcov", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".coverage", "*.egg-info", ".eggs", "site-packages",
    "static-react",  # built frontend assets
}

MAX_FILES = 3000
MAX_DEPTH = 6
MAX_SYMBOLS_PER_FILE = 8

# Language detection by extension
LANG_MAP: dict[str, str] = {
    ".py": "Python", ".pyw": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".d.ts": "TypeScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java", ".kt": "Kotlin",
    ".c": "C", ".h": "C",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
    ".md": "Markdown", ".mdx": "Markdown",
    ".json": "JSON", ".jsonc": "JSON",
    ".yaml": "YAML", ".yml": "YAML",
    ".toml": "TOML",
    ".html": "HTML", ".htm": "HTML",
    ".css": "CSS", ".scss": "CSS", ".sass": "CSS", ".less": "CSS",
    ".sql": "SQL",
    ".lua": "Lua",
    ".r": "R", ".R": "R",
}

# Top-level symbol extraction patterns per language (group 1 = symbol name)
SYMBOL_PATTERNS: dict[str, list[str]] = {
    "Python": [
        r"^class\s+(\w+)",
        r"^async def\s+(\w+)",
        r"^def\s+(\w+)",
    ],
    "JavaScript": [
        r"^export\s+(?:default\s+)?(?:async\s+)?(?:function\s*\*?\s*|class\s+)(\w+)",
        r"^export\s+(?:const|let|var)\s+(\w+)",
        r"^(?:async\s+)?function\s+(\w+)",
        r"^class\s+(\w+)",
    ],
    "TypeScript": [
        r"^export\s+(?:default\s+)?(?:async\s+)?(?:function\s*\*?\s*|class\s+|interface\s+|type\s+|enum\s+)(\w+)",
        r"^export\s+(?:const|let|var)\s+(\w+)",
        r"^(?:async\s+)?function\s+(\w+)",
        r"^class\s+(\w+)",
        r"^interface\s+(\w+)",
        r"^type\s+(\w+)\s*=",
    ],
    "Rust": [
        r"^pub\s+(?:async\s+)?fn\s+(\w+)",
        r"^pub\s+struct\s+(\w+)",
        r"^pub\s+enum\s+(\w+)",
        r"^pub\s+trait\s+(\w+)",
        r"^(?:async\s+)?fn\s+(\w+)",
    ],
    "Go": [
        r"^func\s+(\w+)",
        r"^func\s+\(\w+\s+\*?\w+\)\s+(\w+)",  # method
        r"^type\s+(\w+)\s+(?:struct|interface)",
    ],
    "Java": [
        r"^(?:public|private|protected)?\s*(?:static\s+)?(?:class|interface|enum)\s+(\w+)",
        r"^(?:public|private|protected)\s+(?:static\s+)?\w+\s+(\w+)\s*\(",
    ],
    "Kotlin": [
        r"^(?:fun|class|object|interface)\s+(\w+)",
    ],
}


# ── Gitignore loading ─────────────────────────────────────────────────

def _load_ignore_spec(root: Path):
    """Load gitignore patterns using pathspec.GitIgnoreSpec.

    Handles nested .gitignore files by walking up to root.
    Falls back to None if pathspec is unavailable.
    """
    try:
        import pathspec  # noqa: PLC0415
    except ImportError:
        return None

    patterns: list[str] = []

    # Collect .gitignore files: root + up to 3 sub-levels
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        if depth > 3:
            dirnames.clear()
            continue
        if ".gitignore" in filenames:
            try:
                text = (Path(dirpath) / ".gitignore").read_text(errors="replace")
                # Make patterns relative to root for pathspec
                rel_dir = Path(dirpath).relative_to(root)
                for line in text.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        if rel_dir != Path("."):
                            patterns.append(str(rel_dir / line))
                        else:
                            patterns.append(line)
            except Exception:
                pass

    if not patterns:
        return None

    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def _is_ignored(rel_path: str, spec) -> bool:
    """Check if a relative path matches any ignore pattern."""
    if spec is None:
        return False
    return spec.match_file(rel_path)


def _basename_ignored(name: str) -> bool:
    """Check if a file/dir name is in the always-ignore set."""
    if name in ALWAYS_IGNORE:
        return True
    # Pattern matching for globs like *.egg-info
    for pattern in ALWAYS_IGNORE:
        if "*" in pattern:
            import fnmatch
            if fnmatch.fnmatch(name, pattern):
                return True
    return False


# ── Symbol extraction ─────────────────────────────────────────────────

def extract_symbols(filepath: Path, lang: str) -> list[str]:
    """Extract top-level symbol names from a file using regex.

    Returns up to MAX_SYMBOLS_PER_FILE names, deduped.
    """
    patterns = SYMBOL_PATTERNS.get(lang, [])
    if not patterns:
        return []

    symbols: list[str] = []
    seen: set[str] = set()

    try:
        # Read first 8KB only — top-level symbols are at the top of files
        content = filepath.read_bytes()[:8192].decode(errors="replace")
    except Exception:
        return []

    for line in content.splitlines():
        for pattern in patterns:
            m = re.match(pattern, line)
            if m:
                name = m.group(1)
                if name not in seen and not name.startswith("_"):
                    symbols.append(name)
                    seen.add(name)
                if len(symbols) >= MAX_SYMBOLS_PER_FILE:
                    return symbols

    return symbols


# ── Tree walker ───────────────────────────────────────────────────────

def walk_tree(root: Path) -> tuple[list[dict], int]:
    """Walk the project tree, respecting gitignore and always-ignore rules.

    Returns (entries, total_found) where:
      entries     — list of dicts {rel_path, abs_path, size_bytes, lang},
                    capped at MAX_FILES, sorted by size descending
      total_found — true count of all matching files (may exceed MAX_FILES)
                    used to warn the user when the cap was hit
    """
    spec = _load_ignore_spec(root)
    entries: list[dict] = []
    total_found = 0
    cap_hit = False

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dir_rel = str(Path(dirpath).relative_to(root))

        # Depth check
        depth = len(Path(dirpath).relative_to(root).parts)
        if depth >= MAX_DEPTH:
            dirnames.clear()
            continue

        # Prune ignored directories in-place (stops os.walk from descending)
        dirnames[:] = [
            d for d in dirnames
            if not _basename_ignored(d)
            and not _is_ignored(
                str(Path(dir_rel) / d) if dir_rel != "." else d,
                spec
            )
        ]
        dirnames.sort()  # deterministic order

        for fname in filenames:
            if _basename_ignored(fname):
                continue

            rel_path = str(Path(dir_rel) / fname) if dir_rel != "." else fname
            if _is_ignored(rel_path, spec):
                continue

            abs_path = Path(dirpath) / fname
            try:
                st = abs_path.stat()
                size = st.st_size
            except Exception:
                continue

            # Skip very large files (binaries, compiled assets)
            if size > 2 * 1024 * 1024:  # 2MB
                continue

            total_found += 1

            if not cap_hit:
                suffix = abs_path.suffix.lower()
                lang = LANG_MAP.get(abs_path.suffix) or LANG_MAP.get(suffix, "")
                entries.append({
                    "rel_path": rel_path,
                    "abs_path": str(abs_path),
                    "size_bytes": size,
                    "lang": lang,
                })
                if len(entries) >= MAX_FILES:
                    cap_hit = True
                    # Keep walking to get accurate total_found count

    return sorted(entries, key=lambda e: e["size_bytes"], reverse=True), total_found


# ── Summary cache ─────────────────────────────────────────────────────

def _summaries_path(root: Path) -> Path:
    return root / ".jarvis" / SUMMARIES_FILE


def load_summaries(root: Path) -> dict:
    """Load the file summary cache from .jarvis/file-summaries.json.

    Returns dict: rel_path → {mtime, size_bytes, symbols, summary}
    Returns {} if file doesn't exist or is corrupt.
    """
    path = _summaries_path(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        # Support versioned format
        if isinstance(data, dict) and "entries" in data:
            return data["entries"]
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_summaries(root: Path, cache: dict) -> None:
    """Write the summary cache to .jarvis/file-summaries.json."""
    jarvis_dir = root / ".jarvis"
    if not jarvis_dir.exists():
        return  # only save if .jarvis/ exists (project initialized)
    path = _summaries_path(root)
    try:
        payload = {
            "version": INDEX_VERSION,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "entries": cache,
        }
        path.write_text(json.dumps(payload, indent=2))
    except Exception as e:
        log.warning("Failed to save summaries: %s", e)


def get_cached_entry(cache: dict, rel_path: str, abs_path: str) -> Optional[dict]:
    """Return cached entry if mtime+size match current file stat.

    Returns None if not cached or stale (content changed).
    The (mtime, size) tuple catches virtually all real-world modifications
    on Linux/ext4 without requiring a full content hash.
    """
    entry = cache.get(rel_path)
    if not entry:
        return None
    try:
        st = Path(abs_path).stat()
        if (
            abs(st.st_mtime - entry.get("mtime", 0)) < 0.001
            and st.st_size == entry.get("size_bytes", -1)
        ):
            return entry
    except Exception:
        pass
    return None


def purge_orphans(cache: dict, valid_rel_paths: set[str]) -> int:
    """Remove cache entries for files that no longer exist.

    This handles file deletion and renames, preventing JARVIS from
    referencing paths that don't exist.

    Returns number of entries removed.
    """
    dead = [k for k in cache if k not in valid_rel_paths]
    for k in dead:
        del cache[k]
    return len(dead)


# ── Context renderer ──────────────────────────────────────────────────

def _fmt_size(size_bytes: int) -> str:
    """Format file size compactly: 1.2KB, 34KB, 1.2MB."""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        kb = size_bytes / 1024
        return f"{kb:.0f}KB" if kb >= 10 else f"{kb:.1f}KB"
    else:
        mb = size_bytes / (1024 * 1024)
        return f"{mb:.1f}MB"


def _detect_languages(entries: list[dict]) -> list[str]:
    """Return sorted list of languages present in the file set."""
    langs: dict[str, int] = {}
    for e in entries:
        lang = e.get("lang", "")
        if lang and lang not in ("Markdown", "JSON", "YAML", "TOML"):
            langs[lang] = langs.get(lang, 0) + 1
    return sorted(langs, key=lambda l: -langs[l])


def render_context(
    root: Path,
    entries: list[dict],
    cache: dict,
    max_chars: int = 10000,
) -> tuple[str, bool]:
    """Render tree + symbols into a compact markdown block.

    Format:
        ## Project Index [N files · Lang1, Lang2 · YYYY-MM-DD]
        src/brain.py (68KB) Brain, think, think_stream
        src/agent/loop.py (47KB) agent_loop, dispatch_subagent
        src/commands/handlers/ (18 files)
        ...

    Files are sorted by size desc. Directories with many small files
    are collapsed into "dir/ (N files)" lines.

    Returns (context_str, char_truncated) where char_truncated=True means
    the output was cut short by max_chars before all entries were rendered.
    """
    if not entries:
        return ""

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    langs = _detect_languages(entries)
    lang_str = ", ".join(langs[:4]) if langs else "unknown"
    header = f"## Project Index [{len(entries)} files · {lang_str} · {today}]\n"

    lines: list[str] = []
    chars_used = len(header)

    # Group files by top-level directory for collapsed display
    # Files with symbols are always shown individually (they're useful)
    # Files without symbols in large dirs get collapsed

    dir_counts: dict[str, int] = {}
    for e in entries:
        parts = Path(e["rel_path"]).parts
        top_dir = parts[0] if len(parts) > 1 else ""
        if top_dir:
            dir_counts[top_dir] = dir_counts.get(top_dir, 0) + 1

    shown_dirs: set[str] = set()
    collapsed_dirs: dict[str, list[dict]] = {}
    char_truncated = False

    for entry in entries:
        rel = entry["rel_path"]
        abs_p = entry["abs_path"]
        size = entry["size_bytes"]
        lang = entry.get("lang", "")

        # Get symbols: from cache if valid, else extract now
        cached = get_cached_entry(cache, rel, abs_p)
        if cached:
            symbols = cached.get("symbols", [])
            summary = cached.get("summary", "")
        else:
            symbols = extract_symbols(Path(abs_p), lang) if lang else []
            summary = ""
            # Update cache entry with fresh data
            if lang:
                try:
                    st = Path(abs_p).stat()
                    cache[rel] = {
                        "mtime": st.st_mtime,
                        "size_bytes": st.st_size,
                        "symbols": symbols,
                        "summary": summary,
                        "lang": lang,
                    }
                except Exception:
                    pass

        has_info = bool(symbols or summary)
        parts = Path(rel).parts
        top_dir = parts[0] if len(parts) > 1 else ""

        # Decide: show individually or collapse into dir group
        # Show individually if: has symbols, OR is a root-level file, OR dir has few files
        dir_file_count = dir_counts.get(top_dir, 0)
        show_individually = has_info or not top_dir or dir_file_count <= 3

        if not show_individually:
            # Collapse into directory group
            collapsed_dirs.setdefault(top_dir, []).append(entry)
            continue

        # Format the line
        symbols_str = ", ".join(symbols[:6]) if symbols else ""
        summary_part = summary if summary and not symbols_str else ""
        info = symbols_str or summary_part
        size_str = _fmt_size(size)

        if info:
            line = f"{rel} ({size_str}) {info}\n"
        else:
            line = f"{rel} ({size_str})\n"

        if chars_used + len(line) > max_chars:
            char_truncated = True
            break
        lines.append(line)
        chars_used += len(line)

        if top_dir:
            shown_dirs.add(top_dir)

    # Add collapsed directory lines for dirs not individually shown
    for top_dir, dir_entries in sorted(collapsed_dirs.items()):
        if top_dir in shown_dirs:
            continue  # Already showed some files from this dir individually
        n = len(dir_entries)
        # Collect unique sub-dirs for a hint
        sub_dirs = sorted({
            Path(e["rel_path"]).parts[1]
            for e in dir_entries
            if len(Path(e["rel_path"]).parts) > 2
        })[:4]
        hint = ", ".join(sub_dirs) if sub_dirs else ""
        line = f"{top_dir}/ ({n} files){' · ' + hint if hint else ''}\n"
        if chars_used + len(line) > max_chars:
            char_truncated = True
            break
        lines.append(line)
        chars_used += len(line)

    if not lines:
        return "", False

    return header + "".join(lines), char_truncated


# ── Public API ────────────────────────────────────────────────────────

def get_context(root: Path = None, max_chars: int = 10000) -> str:
    """Main entry point: build and return the codebase context string.

    Called by PromptBuilder at session start.
    - Always-fresh tree (Tier 1): walks filesystem every call
    - Cached symbols (Tier 2): loaded from .jarvis/file-summaries.json,
      validated by (mtime, size), orphans purged

    Only returns content if .jarvis/ exists (project must be initialized).
    Returns "" if .jarvis/ doesn't exist or project is too small (< 5 files).
    """
    if root is None:
        root = Path.cwd()

    jarvis_dir = root / ".jarvis"
    if not jarvis_dir.exists():
        return ""

    try:
        # Walk the tree (always fresh) — returns (entries, total_found)
        entries, total_found = walk_tree(root)
        if len(entries) < 5:
            return ""  # Not worth injecting for tiny projects

        # Load summary cache
        cache = load_summaries(root)

        # Purge orphaned entries (deleted/renamed files)
        valid_paths = {e["rel_path"] for e in entries}
        purge_orphans(cache, valid_paths)

        # Render context — returns (context_str, char_truncated)
        context_str, char_truncated = render_context(root, entries, cache, max_chars=max_chars)

        # Append cap warning if any limit was hit
        file_cap_hit = total_found > MAX_FILES
        if file_cap_hit or char_truncated:
            shown = len(entries)
            warn_parts = []
            if file_cap_hit:
                warn_parts.append(f"{shown}/{total_found} files (file cap: {MAX_FILES})")
            if char_truncated:
                warn_parts.append(f"token budget reached ({max_chars//4:,} tokens)")
            warning = (
                f"\n⚠ Index truncated: {' · '.join(warn_parts)}. "
                f"Run `/index rebuild` or increase the limit in src/indexer/builder.py "
                f"(MAX_FILES) or src/prompt_builder.py (max_chars) to include more."
            )
            context_str += warning

        # Save updated cache (with fresh symbols + purged orphans)
        if context_str:
            save_summaries(root, cache)

        return context_str

    except Exception as e:
        log.debug("Codebase index failed (non-fatal): %s", e)
        return ""


def build_index(root: Path = None, force: bool = False) -> dict:
    """Pre-build the full symbol index for all files.

    Called by /index build command. Extracts symbols for every file
    in the tree and writes to .jarvis/file-summaries.json.

    Returns stats dict: {files_scanned, symbols_found, cache_size_bytes, duration_ms}
    """
    import time
    if root is None:
        root = Path.cwd()

    jarvis_dir = root / ".jarvis"
    if not jarvis_dir.exists():
        return {"error": ".jarvis/ not found. Run /init first."}

    t0 = time.monotonic()
    entries, total_found = walk_tree(root)

    # Load existing cache (to avoid re-extracting unchanged files)
    cache = load_summaries(root) if not force else {}

    # Purge orphans
    valid_paths = {e["rel_path"] for e in entries}
    purge_orphans(cache, valid_paths)

    symbols_found = 0
    files_updated = 0

    for entry in entries:
        rel = entry["rel_path"]
        abs_p = entry["abs_path"]
        lang = entry.get("lang", "")

        if not lang:
            continue

        # Check if cache entry is still valid
        cached = get_cached_entry(cache, rel, abs_p)
        if cached and not force:
            symbols_found += len(cached.get("symbols", []))
            continue

        # Extract symbols
        symbols = extract_symbols(Path(abs_p), lang)
        symbols_found += len(symbols)

        try:
            st = Path(abs_p).stat()
            cache[rel] = {
                "mtime": st.st_mtime,
                "size_bytes": st.st_size,
                "symbols": symbols,
                "summary": "",
                "lang": lang,
            }
            files_updated += 1
        except Exception:
            pass

    save_summaries(root, cache)

    duration_ms = int((time.monotonic() - t0) * 1000)
    summaries_path = _summaries_path(root)
    cache_bytes = summaries_path.stat().st_size if summaries_path.exists() else 0

    return {
        "files_scanned": len(entries),
        "total_found": total_found,
        "file_cap_hit": total_found > MAX_FILES,
        "files_updated": files_updated,
        "symbols_found": symbols_found,
        "cache_size_bytes": cache_bytes,
        "duration_ms": duration_ms,
    }


def get_status(root: Path = None) -> dict:
    """Return status info about the current index.

    Used by /index status command.
    """
    if root is None:
        root = Path.cwd()

    jarvis_dir = root / ".jarvis"
    summaries_path = _summaries_path(root)

    status = {
        "initialized": jarvis_dir.exists(),
        "index_exists": summaries_path.exists(),
        "entries": 0,
        "files_with_symbols": 0,
        "cache_size_bytes": 0,
        "updated_at": None,
        "estimated_tokens": 0,
    }

    if not summaries_path.exists():
        return status

    try:
        data = json.loads(summaries_path.read_text())
        entries = data.get("entries", data) if isinstance(data, dict) else {}
        status["entries"] = len(entries)
        status["files_with_symbols"] = sum(
            1 for v in entries.values() if v.get("symbols")
        )
        status["cache_size_bytes"] = summaries_path.stat().st_size
        status["updated_at"] = data.get("updated_at") if isinstance(data, dict) else None

        # Estimate tokens: roughly 1 token per 4 chars of rendered context
        sample_context = get_context(root, max_chars=50000)
        status["estimated_tokens"] = len(sample_context) // 4
    except Exception:
        pass

    return status
