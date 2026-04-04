"""
Ripgrep wrapper with pagination and output modes.

Provides a standalone, pure-Python
interface to ripgrep (rg) with automatic fallback to Python's re module
when rg is not installed.

Usage:
    from src.agent.ripgrep import search, RipgrepConfig

    result = search(RipgrepConfig(pattern="def main", path="src/", glob="*.py"))
    print(result.output)
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #

@dataclass
class RipgrepConfig:
    """Configuration for a ripgrep search."""
    pattern: str
    path: str = "."
    glob: str = ""                          # file glob filter, e.g. "*.py", "*.{ts,tsx}"
    file_type: str = ""                     # rg --type filter, e.g. "py", "js", "rust"
    output_mode: str = "files_with_matches" # "content", "files_with_matches", "count"
    context_before: int = 0                 # -B lines before match
    context_after: int = 0                  # -A lines after match
    context: int = 0                        # -C lines before and after
    case_insensitive: bool = False
    multiline: bool = False
    head_limit: int = 250                   # max results; 0 = unlimited
    offset: int = 0                         # skip first N results
    show_line_numbers: bool = True


@dataclass
class RipgrepResult:
    """Result of a ripgrep search."""
    output: str                             # formatted output text
    num_matches: int = 0
    num_files: int = 0
    truncated: bool = False
    applied_limit: int | None = None
    applied_offset: int | None = None


# --------------------------------------------------------------------------- #
# Exclusions — directories that are always excluded from searches
# --------------------------------------------------------------------------- #

VCS_DIRS_TO_EXCLUDE = (
    ".git", ".svn", ".hg", ".bzr", ".jj", ".sl",
    "__pycache__", "node_modules", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache",
    "target",   # Rust/Maven
    "dist",     # JS build output
    ".eggs",
    "*.egg-info",
)

# Maximum line length — prevents base64 / minified blobs from flooding output
MAX_COLUMNS = 500

# Subprocess constraints
DEFAULT_TIMEOUT = 20   # seconds
MAX_OUTPUT_BYTES = 10 * 1024 * 1024  # 10 MB

# Default head_limit when unspecified (default 250)
DEFAULT_HEAD_LIMIT = 250


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _apply_pagination(
    items: list[str],
    head_limit: int,
    offset: int,
) -> tuple[list[str], bool, int | None, int | None]:
    """Apply offset + head_limit pagination to a list of lines/entries.

    Returns (paginated_items, truncated, applied_limit_or_None, applied_offset_or_None).
    """
    total = len(items)

    # Offset
    if offset > 0:
        items = items[offset:]

    # Limit — explicit 0 means unlimited
    if head_limit == 0:
        return items, False, None, (offset if offset > 0 else None)

    effective_limit = head_limit if head_limit > 0 else DEFAULT_HEAD_LIMIT
    truncated = len(items) > effective_limit
    items = items[:effective_limit]

    return (
        items,
        truncated,
        effective_limit if truncated else None,
        offset if offset > 0 else None,
    )


def _format_pagination_note(applied_limit: int | None, applied_offset: int | None) -> str:
    parts: list[str] = []
    if applied_limit is not None:
        parts.append(f"limit: {applied_limit}")
    if applied_offset is not None:
        parts.append(f"offset: {applied_offset}")
    if parts:
        return f"\n[Results paginated: {', '.join(parts)}]"
    return ""


# --------------------------------------------------------------------------- #
# find_ripgrep — locate the rg binary
# --------------------------------------------------------------------------- #

_COMMON_RG_LOCATIONS = (
    "/usr/bin/rg",
    "/usr/local/bin/rg",
    "/opt/homebrew/bin/rg",
    os.path.expanduser("~/.cargo/bin/rg"),
    os.path.expanduser("~/.local/bin/rg"),
    "/snap/bin/rg",
)


def find_ripgrep() -> str | None:
    """Find the ripgrep (rg) binary. Returns the path or None."""
    path = shutil.which("rg")
    if path:
        return path
    for loc in _COMMON_RG_LOCATIONS:
        if os.path.isfile(loc) and os.access(loc, os.X_OK):
            return loc
    return None


# --------------------------------------------------------------------------- #
# ripgrep_search — run rg subprocess
# --------------------------------------------------------------------------- #

def _build_rg_args(config: RipgrepConfig) -> list[str]:
    """Build the ripgrep argument list from a RipgrepConfig."""
    args: list[str] = ["--hidden"]

    # Exclude VCS / junk directories
    for d in VCS_DIRS_TO_EXCLUDE:
        args.extend(["--glob", f"!{d}"])

    # Max column width to skip minified / base64 lines
    args.extend(["--max-columns", str(MAX_COLUMNS)])

    # Multiline
    if config.multiline:
        args.extend(["-U", "--multiline-dotall"])

    # Case insensitive
    if config.case_insensitive:
        args.append("-i")

    # Output mode flags
    if config.output_mode == "files_with_matches":
        args.append("-l")
    elif config.output_mode == "count":
        args.append("-c")

    # Line numbers (content mode only)
    if config.show_line_numbers and config.output_mode == "content":
        args.append("-n")

    # Context flags (content mode only)
    if config.output_mode == "content":
        if config.context > 0:
            args.extend(["-C", str(config.context)])
        else:
            if config.context_before > 0:
                args.extend(["-B", str(config.context_before)])
            if config.context_after > 0:
                args.extend(["-A", str(config.context_after)])

    # Pattern — use -e if it starts with a dash
    if config.pattern.startswith("-"):
        args.extend(["-e", config.pattern])
    else:
        args.append(config.pattern)

    # File type filter
    if config.file_type:
        args.extend(["--type", config.file_type])

    # Glob filters (split on whitespace, but preserve brace expansions)
    if config.glob:
        raw_patterns = config.glob.split()
        glob_patterns: list[str] = []
        for raw in raw_patterns:
            if "{" in raw and "}" in raw:
                glob_patterns.append(raw)
            else:
                glob_patterns.extend(p for p in raw.split(",") if p)
        for gp in glob_patterns:
            args.extend(["--glob", gp])

    # Target path
    args.append(os.path.expanduser(config.path))

    return args


def ripgrep_search(config: RipgrepConfig) -> RipgrepResult:
    """Run ripgrep and return structured results.

    Raises RuntimeError if rg is not found.
    """
    rg_path = find_ripgrep()
    if rg_path is None:
        raise RuntimeError("ripgrep (rg) not found")

    cmd = [rg_path] + _build_rg_args(config)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return RipgrepResult(
            output=f"Search timed out after {DEFAULT_TIMEOUT}s. Try a more specific path or pattern.",
            num_matches=0,
            num_files=0,
            truncated=True,
        )

    # Exit codes: 0 = matches, 1 = no matches, 2 = error
    if proc.returncode == 2:
        stderr = proc.stderr.strip()
        return RipgrepResult(output=f"ripgrep error: {stderr}", num_matches=0, num_files=0)

    if proc.returncode == 1 or not proc.stdout.strip():
        return RipgrepResult(output=f"No matches for '{config.pattern}'", num_matches=0, num_files=0)

    # Truncate raw output if it exceeds our buffer
    raw = proc.stdout
    if len(raw.encode("utf-8", errors="replace")) > MAX_OUTPUT_BYTES:
        raw = raw[:MAX_OUTPUT_BYTES]

    lines = [l for l in raw.rstrip("\n").split("\n") if l]

    return _format_results(config, lines)


def _format_results(config: RipgrepConfig, lines: list[str]) -> RipgrepResult:
    """Format raw rg output lines into a RipgrepResult based on output_mode."""

    if config.output_mode == "content":
        paginated, truncated, applied_limit, applied_offset = _apply_pagination(
            lines, config.head_limit, config.offset,
        )
        output = "\n".join(paginated)
        output += _format_pagination_note(applied_limit, applied_offset)
        return RipgrepResult(
            output=output,
            num_matches=len(paginated),
            num_files=0,
            truncated=truncated,
            applied_limit=applied_limit,
            applied_offset=applied_offset,
        )

    if config.output_mode == "count":
        paginated, truncated, applied_limit, applied_offset = _apply_pagination(
            lines, config.head_limit, config.offset,
        )
        total_matches = 0
        file_count = 0
        for line in paginated:
            # Format: filename:count
            idx = line.rfind(":")
            if idx > 0:
                try:
                    total_matches += int(line[idx + 1:])
                    file_count += 1
                except ValueError:
                    pass
        output = "\n".join(paginated)
        summary = (
            f"\n\nFound {total_matches} total "
            f"{'occurrence' if total_matches == 1 else 'occurrences'} "
            f"across {file_count} {'file' if file_count == 1 else 'files'}."
        )
        output += summary
        output += _format_pagination_note(applied_limit, applied_offset)
        return RipgrepResult(
            output=output,
            num_matches=total_matches,
            num_files=file_count,
            truncated=truncated,
            applied_limit=applied_limit,
            applied_offset=applied_offset,
        )

    # files_with_matches (default)
    # Sort by modification time, most recent first
    def _mtime(fp: str) -> float:
        try:
            return os.path.getmtime(fp)
        except OSError:
            return 0.0

    lines.sort(key=_mtime, reverse=True)

    paginated, truncated, applied_limit, applied_offset = _apply_pagination(
        lines, config.head_limit, config.offset,
    )
    num_files = len(paginated)
    if num_files == 0:
        output = "No files found"
    else:
        output = f"Found {num_files} {'file' if num_files == 1 else 'files'}\n"
        output += "\n".join(paginated)
    output += _format_pagination_note(applied_limit, applied_offset)
    return RipgrepResult(
        output=output,
        num_matches=num_files,
        num_files=num_files,
        truncated=truncated,
        applied_limit=applied_limit,
        applied_offset=applied_offset,
    )


# --------------------------------------------------------------------------- #
# grep_fallback — pure-Python fallback when rg is unavailable
# --------------------------------------------------------------------------- #

# Mapping from rg type names to file extensions (common ones)
_TYPE_TO_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "py": (".py", ".pyi", ".pyw"),
    "js": (".js", ".mjs", ".cjs", ".jsx"),
    "ts": (".ts", ".mts", ".cts", ".tsx"),
    "rust": (".rs",),
    "go": (".go",),
    "java": (".java",),
    "c": (".c", ".h"),
    "cpp": (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx", ".h"),
    "rb": (".rb",),
    "sh": (".sh", ".bash", ".zsh"),
    "html": (".html", ".htm"),
    "css": (".css",),
    "json": (".json",),
    "yaml": (".yaml", ".yml"),
    "toml": (".toml",),
    "md": (".md", ".markdown"),
    "xml": (".xml",),
    "sql": (".sql",),
    "lua": (".lua",),
    "php": (".php",),
}


def _should_exclude_dir(dirname: str) -> bool:
    """Check whether a directory name should be excluded."""
    for excl in VCS_DIRS_TO_EXCLUDE:
        if fnmatch.fnmatch(dirname, excl):
            return True
    return False


def _matches_glob(filepath: str, glob_pattern: str) -> bool:
    """Check if a filepath matches a glob pattern."""
    basename = os.path.basename(filepath)
    # Handle brace expansion simply: *.{ts,tsx} -> check each
    if "{" in glob_pattern and "}" in glob_pattern:
        prefix, rest = glob_pattern.split("{", 1)
        options, suffix = rest.split("}", 1)
        return any(
            fnmatch.fnmatch(basename, prefix + opt + suffix)
            for opt in options.split(",")
        )
    return fnmatch.fnmatch(basename, glob_pattern)


def _matches_type(filepath: str, file_type: str) -> bool:
    """Check if a filepath matches a ripgrep --type filter."""
    exts = _TYPE_TO_EXTENSIONS.get(file_type)
    if exts is None:
        return True  # unknown type, don't filter
    return any(filepath.endswith(ext) for ext in exts)


def grep_fallback(config: RipgrepConfig) -> RipgrepResult:
    """Pure-Python fallback search using re + os.walk.

    Slower than ripgrep but always available. Respects the same config
    options: glob, file_type, output_mode, pagination, context, etc.
    """
    root = os.path.expanduser(config.path)
    if not os.path.exists(root):
        return RipgrepResult(output=f"Path does not exist: {root}")

    flags = re.IGNORECASE if config.case_insensitive else 0
    if config.multiline:
        flags |= re.DOTALL | re.MULTILINE
    try:
        regex = re.compile(config.pattern, flags)
    except re.error as e:
        return RipgrepResult(output=f"Invalid regex: {e}")

    # Parse glob patterns
    glob_patterns: list[str] = []
    if config.glob:
        for raw in config.glob.split():
            if "{" in raw and "}" in raw:
                glob_patterns.append(raw)
            else:
                glob_patterns.extend(p for p in raw.split(",") if p)

    # Collect matching files
    matched_files: list[str] = []
    file_match_counts: dict[str, int] = {}
    content_lines: list[str] = []

    # If root is a file, just search it
    if os.path.isfile(root):
        walk_items = [(os.path.dirname(root), [], [os.path.basename(root)])]
    else:
        walk_items = os.walk(root)

    for dirpath, dirnames, filenames in walk_items:
        # Prune excluded directories (in-place to prevent os.walk from descending)
        dirnames[:] = [d for d in dirnames if not _should_exclude_dir(d)]

        for fname in filenames:
            filepath = os.path.join(dirpath, fname)

            # Apply glob filter
            if glob_patterns and not any(_matches_glob(filepath, gp) for gp in glob_patterns):
                continue

            # Apply type filter
            if config.file_type and not _matches_type(filepath, config.file_type):
                continue

            # Try to read the file
            try:
                with open(filepath, "r", errors="replace") as f:
                    file_lines = f.readlines()
            except (OSError, PermissionError):
                continue

            if config.multiline:
                text = "".join(file_lines)
                matches = list(regex.finditer(text))
                if not matches:
                    continue
                match_count = len(matches)
            else:
                match_indices = [
                    i for i, line in enumerate(file_lines)
                    if regex.search(line)
                ]
                if not match_indices:
                    continue
                match_count = len(match_indices)

            matched_files.append(filepath)
            file_match_counts[filepath] = match_count

            # For content mode, build output lines with context
            if config.output_mode == "content" and not config.multiline:
                ctx_before = config.context or config.context_before
                ctx_after = config.context or config.context_after
                shown: set[int] = set()
                for idx in match_indices:
                    start = max(0, idx - ctx_before)
                    end = min(len(file_lines), idx + ctx_after + 1)
                    for i in range(start, end):
                        if i not in shown:
                            shown.add(i)
                            line_text = file_lines[i].rstrip("\n")
                            if len(line_text) > MAX_COLUMNS:
                                line_text = line_text[:MAX_COLUMNS] + " [truncated]"
                            if config.show_line_numbers:
                                content_lines.append(f"{filepath}:{i + 1}:{line_text}")
                            else:
                                content_lines.append(f"{filepath}:{line_text}")
                # Separator between files
                if match_indices:
                    content_lines.append("--")

            # Safety valve — don't scan forever
            if len(matched_files) > 10000:
                break
        if len(matched_files) > 10000:
            break

    # Build result based on output mode
    if config.output_mode == "content":
        # Remove trailing separator
        if content_lines and content_lines[-1] == "--":
            content_lines.pop()
        return _format_results(config, content_lines)

    if config.output_mode == "count":
        count_lines = [f"{fp}:{file_match_counts[fp]}" for fp in matched_files]
        return _format_results(config, count_lines)

    # files_with_matches
    return _format_results(config, matched_files)


# --------------------------------------------------------------------------- #
# search — main entry point (rg with fallback)
# --------------------------------------------------------------------------- #

def search(config: RipgrepConfig) -> RipgrepResult:
    """Search using ripgrep, falling back to pure-Python grep if rg is unavailable.

    This is the main entry point. It tries ripgrep first for speed, and
    falls back to grep_fallback() (re + os.walk) when rg is not installed.
    """
    if find_ripgrep() is not None:
        try:
            return ripgrep_search(config)
        except RuntimeError:
            pass  # rg disappeared between check and exec — fall through
        except Exception:
            pass  # unexpected rg failure — fall through to Python fallback

    return grep_fallback(config)
