"""File suggestion system for typeahead/autocomplete.

Provides file discovery via git ls-files (fast) or ripgrep (fallback),
with background index building, caching, and fuzzy matching.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


@dataclass
class SuggestionItem:
    id: str
    display_text: str
    description: Optional[str] = None
    metadata: Optional[dict] = None
    color: Optional[str] = None


@dataclass
class FileIndex:
    """Simple file index for fuzzy searching."""

    paths: List[str] = field(default_factory=list)
    _path_set: Set[str] = field(default_factory=set)

    def load_from_file_list(self, file_list: List[str]) -> None:
        self.paths = list(file_list)
        self._path_set = set(file_list)

    def search(self, query: str, max_results: int = 15) -> List[dict]:
        """Simple fuzzy search over indexed paths."""
        if not query:
            return [{"path": p, "score": 0.0} for p in self.paths[:max_results]]

        query_lower = query.lower()
        scored: List[Tuple[str, float]] = []

        for p in self.paths:
            p_lower = p.lower()
            if query_lower in p_lower:
                # Exact substring match - score by position and length ratio
                pos = p_lower.index(query_lower)
                score = pos / max(len(p), 1) + (1 - len(query) / max(len(p), 1))
                scored.append((p, score))
            elif _fuzzy_match(query_lower, p_lower):
                scored.append((p, 0.8))

        scored.sort(key=lambda x: x[1])
        return [{"path": p, "score": s} for p, s in scored[:max_results]]


def _fuzzy_match(query: str, target: str) -> bool:
    """Check if all characters of query appear in order in target."""
    qi = 0
    for char in target:
        if qi < len(query) and char == query[qi]:
            qi += 1
    return qi == len(query)


# Module-level singleton state
_file_index: Optional[FileIndex] = None
_file_list_refresh_task: Optional[asyncio.Task] = None
_cache_generation: int = 0
_cached_tracked_files: List[str] = []
_cached_config_files: List[str] = []
_cached_tracked_dirs: List[str] = []
_last_refresh_ms: float = 0
_last_git_index_mtime: Optional[float] = None
_loaded_tracked_signature: Optional[str] = None
_loaded_merged_signature: Optional[str] = None
_on_index_build_complete_callbacks: List[Callable] = []

REFRESH_THROTTLE_MS = 5000
MAX_SUGGESTIONS = 15


def get_file_index() -> FileIndex:
    global _file_index
    if _file_index is None:
        _file_index = FileIndex()
    return _file_index


def on_index_build_complete(callback: Callable) -> Callable:
    """Subscribe to index build completion. Returns unsubscribe function."""
    _on_index_build_complete_callbacks.append(callback)

    def unsubscribe():
        if callback in _on_index_build_complete_callbacks:
            _on_index_build_complete_callbacks.remove(callback)

    return unsubscribe


def clear_file_suggestion_caches() -> None:
    """Clear all file suggestion caches. Call when resuming a session."""
    global _file_index, _file_list_refresh_task, _cache_generation
    global _cached_tracked_files, _cached_config_files, _cached_tracked_dirs
    global _last_refresh_ms, _last_git_index_mtime
    global _loaded_tracked_signature, _loaded_merged_signature

    _file_index = None
    _file_list_refresh_task = None
    _cache_generation += 1
    _cached_tracked_files = []
    _cached_config_files = []
    _cached_tracked_dirs = []
    _on_index_build_complete_callbacks.clear()
    _last_refresh_ms = 0
    _last_git_index_mtime = None
    _loaded_tracked_signature = None
    _loaded_merged_signature = None


def path_list_signature(paths: List[str]) -> str:
    """Content hash of a path list using FNV-1a sampling.

    Samples every Nth path (plus length). On a 346k-path list this hashes ~700
    paths instead of 14MB.
    """
    n = len(paths)
    stride = max(1, n // 500)
    h = 0x811C9DC5 & 0xFFFFFFFF

    for i in range(0, n, stride):
        p = paths[i]
        for ch in p:
            h = ((h ^ ord(ch)) * 0x01000193) & 0xFFFFFFFF
        h = (h * 0x01000193) & 0xFFFFFFFF

    # Explicitly include last path
    if n > 0:
        last = paths[n - 1]
        for ch in last:
            h = ((h ^ ord(ch)) * 0x01000193) & 0xFFFFFFFF

    return f"{n}:{h:x}"


def get_directory_names(files: List[str]) -> List[str]:
    """Collect all parent directories for each file path.

    Returns unique directory names with a trailing separator.
    E.g., ['src/index.js', 'src/utils/helpers.js'] -> ['src/', 'src/utils/']
    """
    directory_names: Set[str] = set()
    for f in files:
        current_dir = os.path.dirname(f)
        while current_dir and current_dir != ".":
            parent = os.path.dirname(current_dir)
            if parent == current_dir:
                break
            directory_names.add(current_dir)
            current_dir = parent
    return [d + os.sep for d in directory_names]


async def get_directory_names_async(files: List[str]) -> List[str]:
    """Async variant that yields periodically to avoid blocking."""
    directory_names: Set[str] = set()
    chunk_start = time.monotonic()

    for i, f in enumerate(files):
        current_dir = os.path.dirname(f)
        while current_dir and current_dir != ".":
            parent = os.path.dirname(current_dir)
            if parent == current_dir:
                break
            directory_names.add(current_dir)
            current_dir = parent

        # Yield every 256 files if we've been running > 4ms
        if (i & 0xFF) == 0xFF and (time.monotonic() - chunk_start) > 0.004:
            await asyncio.sleep(0)
            chunk_start = time.monotonic()

    return [d + os.sep for d in directory_names]


def find_git_root(cwd: str) -> Optional[str]:
    """Find the root of the git repository containing cwd."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def normalize_git_paths(
    files: List[str], repo_root: str, original_cwd: str
) -> List[str]:
    """Normalize git paths relative to original_cwd."""
    if original_cwd == repo_root:
        return files
    return [
        os.path.relpath(os.path.join(repo_root, f), original_cwd) for f in files
    ]


def get_git_index_mtime(cwd: str) -> Optional[float]:
    """Stat .git/index to detect git state changes."""
    repo_root = find_git_root(cwd)
    if not repo_root:
        return None
    try:
        return os.stat(os.path.join(repo_root, ".git", "index")).st_mtime
    except OSError:
        return None


async def get_files_using_git(
    cwd: str, respect_gitignore: bool = True
) -> Optional[List[str]]:
    """Get files using git ls-files (much faster than ripgrep for git repos).

    Returns tracked files immediately, fetches untracked in background.
    """
    repo_root = find_git_root(cwd)
    if not repo_root:
        return None

    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-c",
            "core.quotepath=false",
            "ls-files",
            "--recurse-submodules",
            cwd=repo_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

        if proc.returncode != 0:
            return None

        tracked_files = [
            f for f in stdout.decode().strip().split("\n") if f
        ]
        normalized = normalize_git_paths(tracked_files, repo_root, cwd)
        return normalized

    except (asyncio.TimeoutError, OSError):
        return None


async def get_project_files(
    cwd: str, respect_gitignore: bool = True
) -> List[str]:
    """Get project files using git ls-files (fast) or ripgrep (fallback)."""
    git_files = await get_files_using_git(cwd, respect_gitignore)
    if git_files is not None:
        return git_files

    # Fall back to ripgrep
    try:
        rg_args = [
            "rg",
            "--files",
            "--follow",
            "--hidden",
            "--glob",
            "!.git/",
            "--glob",
            "!.svn/",
            "--glob",
            "!.hg/",
            "--glob",
            "!.bzr/",
        ]
        if not respect_gitignore:
            rg_args.append("--no-ignore-vcs")

        proc = await asyncio.create_subprocess_exec(
            *rg_args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        files = [f for f in stdout.decode().strip().split("\n") if f]
        return [os.path.relpath(f, cwd) for f in files]

    except (asyncio.TimeoutError, FileNotFoundError, OSError):
        return []


def find_common_prefix(a: str, b: str) -> str:
    """Find the common prefix between two strings."""
    min_length = min(len(a), len(b))
    i = 0
    while i < min_length and a[i] == b[i]:
        i += 1
    return a[:i]


def find_longest_common_prefix(suggestions: List[SuggestionItem]) -> str:
    """Find the longest common prefix among suggestion items."""
    if not suggestions:
        return ""

    strings = [item.display_text for item in suggestions]
    prefix = strings[0]
    for s in strings[1:]:
        prefix = find_common_prefix(prefix, s)
        if not prefix:
            return ""
    return prefix


def create_file_suggestion_item(
    file_path: str, score: Optional[float] = None
) -> SuggestionItem:
    """Create a file suggestion item."""
    return SuggestionItem(
        id=f"file-{file_path}",
        display_text=file_path,
        metadata={"score": score} if score is not None else None,
    )


def find_matching_files(
    file_index: FileIndex, partial_path: str
) -> List[SuggestionItem]:
    """Find matching files and folders for a given query."""
    results = file_index.search(partial_path, MAX_SUGGESTIONS)
    return [
        create_file_suggestion_item(r["path"], r["score"]) for r in results
    ]


async def get_paths_for_suggestions(cwd: str) -> FileIndex:
    """Get both files and directory paths for providing path suggestions."""
    global _cached_config_files, _cached_tracked_dirs
    global _loaded_tracked_signature, _loaded_merged_signature

    index = get_file_index()

    try:
        project_files = await get_project_files(cwd)
        all_files = project_files
        directories = await get_directory_names_async(all_files)
        _cached_tracked_dirs = directories
        all_paths = directories + all_files

        sig = path_list_signature(all_paths)
        if sig != _loaded_tracked_signature:
            index.load_from_file_list(all_paths)
            _loaded_tracked_signature = sig
            _loaded_merged_signature = None

    except Exception:
        pass

    return index


def start_background_cache_refresh(cwd: str) -> None:
    """Start a background refresh of the file index cache if not already in progress."""
    global _file_list_refresh_task, _last_refresh_ms, _last_git_index_mtime

    if _file_list_refresh_task is not None:
        return

    index_mtime = get_git_index_mtime(cwd)
    if _file_index is not None:
        git_state_changed = (
            index_mtime is not None and index_mtime != _last_git_index_mtime
        )
        if (
            not git_state_changed
            and (time.time() * 1000 - _last_refresh_ms) < REFRESH_THROTTLE_MS
        ):
            return

    get_file_index()

    async def _refresh():
        global _file_list_refresh_task, _last_refresh_ms, _last_git_index_mtime
        try:
            await get_paths_for_suggestions(cwd)
            for cb in _on_index_build_complete_callbacks:
                cb()
            _last_git_index_mtime = index_mtime
            _last_refresh_ms = time.time() * 1000
        except Exception:
            pass
        finally:
            _file_list_refresh_task = None

    try:
        loop = asyncio.get_running_loop()
        _file_list_refresh_task = loop.create_task(_refresh())
    except RuntimeError:
        pass


async def get_top_level_paths(cwd: str) -> List[str]:
    """Get top-level files and directories in the current working directory."""
    try:
        entries = []
        for entry in os.scandir(cwd):
            rel_path = os.path.relpath(entry.path, cwd)
            if entry.is_dir():
                entries.append(rel_path + os.sep)
            else:
                entries.append(rel_path)
        return entries
    except OSError:
        return []


async def generate_file_suggestions(
    partial_path: str,
    cwd: str,
    show_on_empty: bool = False,
) -> List[SuggestionItem]:
    """Generate file suggestions for the current input.

    Args:
        partial_path: The partial file path to match.
        cwd: Current working directory.
        show_on_empty: Whether to show suggestions even if partial_path is empty.

    Returns:
        List of matching suggestion items.
    """
    if not partial_path and not show_on_empty:
        return []

    # If empty or dot, return current directory suggestions
    if partial_path in ("", ".", "./"):
        top_level = await get_top_level_paths(cwd)
        start_background_cache_refresh(cwd)
        return [create_file_suggestion_item(p) for p in top_level[:MAX_SUGGESTIONS]]

    start_background_cache_refresh(cwd)

    # Handle './' prefix
    normalized_path = partial_path
    current_dir_prefix = "." + os.sep
    if partial_path.startswith(current_dir_prefix):
        normalized_path = partial_path[2:]

    # Handle tilde expansion
    if normalized_path.startswith("~"):
        normalized_path = os.path.expanduser(normalized_path)

    if _file_index:
        matches = find_matching_files(_file_index, normalized_path)
    else:
        matches = []

    return matches


def apply_file_suggestion(
    suggestion: str | SuggestionItem,
    input_text: str,
    partial_path: str,
    start_pos: int,
    on_input_change: Callable[[str], None],
    set_cursor_offset: Callable[[int], None],
) -> None:
    """Apply a file suggestion to the input."""
    suggestion_text = (
        suggestion if isinstance(suggestion, str) else suggestion.display_text
    )
    new_input = (
        input_text[:start_pos]
        + suggestion_text
        + input_text[start_pos + len(partial_path) :]
    )
    on_input_change(new_input)
    new_cursor_pos = start_pos + len(suggestion_text)
    set_cursor_offset(new_cursor_pos)
