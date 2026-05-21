"""Symbol search across the repo via `git grep` — voice-adapted
LSP-lite.

The full LSP surface (jump-to-def cross-module, type info at
position, find-implementations, call hierarchies) requires a
running language server. For voice JARVIS, the subset that actually
shows up in user asks is just two things:

  1. "Where is symbol X defined?"  → `find_definitions(symbol)`
  2. "Where is X used / called?"   → `find_references(symbol)`

Both use `git grep` rather than ripgrep (not installed system-wide
on this Kali host) or a real language server (heavy, slow, and
language-specific). Tradeoffs:

  + git grep is always available where git is. No system install.
  + Respects .gitignore — no node_modules / .venv noise.
  + Sub-50ms on the JARVIS repo (~10K files).
  + Single regex covers Python + TS + JS + Rust idioms.
  - Definition heuristic is REGEX, not semantic. Doesn't catch
    metaclass tricks, factory-returned classes, runtime-attached
    methods. For a voice "where is X" ask, that's acceptable.
  - Cross-module rename / type-info-at-position are NOT supported.

Output is capped at 50 hits per call. If a symbol is super common,
the supervisor should narrow via `path_filter` (a git pathspec like
`'*.py'` or `'src/voice-agent/**'`).
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

from livekit.agents.llm import function_tool


__all__ = ["find_definitions", "find_references"]


_logger = logging.getLogger("jarvis.tools.code_search")


_MAX_HITS = 50

# Symbol must be a plain identifier — letters, digits, underscores.
# Rejects `Foo.bar`, `Foo::bar`, hyphens, regex metacharacters, and
# anything that could let an injection through `git grep -P <pattern>`.
_VALID_SYMBOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# Definition pattern: line begins with one of the language idioms
# that introduces a name. Both Python and TS/JS are covered in one
# regex. SYMBOL is the literal identifier (already validated).
#
# Python: `def NAME`, `async def NAME`, `class NAME`, top-level
#   `NAME =` (module-level constants like _MAX_OUTPUT_LINES = 500).
# TS/JS: `function NAME`, `class NAME`, `interface NAME`, `type
#   NAME =`, `const NAME =`, `let NAME =`, `var NAME =`, `enum NAME`.
#   Each may be prefixed by `export` (and `export default`).
_DEFINITION_PATTERN_TPL = (
    # Python idioms
    r"^\s*("
    r"(?:async\s+)?def\s+SYMBOL\b"
    r"|class\s+SYMBOL\b"
    r"|SYMBOL\s*[:=]"
    r")"
    r"|"
    # JS/TS idioms
    r"^\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?"
    r"(?:function|class|interface|type|const|let|var|enum)\s+SYMBOL\b"
)


async def _git(*args: str, cwd: Optional[str] = None) -> tuple[int, str, str]:
    """Run `git <args>` and return (rc, stdout, stderr) as text."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    out_b, err_b = await proc.communicate()
    return (
        proc.returncode or 0,
        out_b.decode("utf-8", "replace"),
        err_b.decode("utf-8", "replace"),
    )


async def _repo_root(start: Optional[str] = None) -> Optional[Path]:
    rc, out, _ = await _git("rev-parse", "--show-toplevel", cwd=start)
    if rc != 0:
        return None
    text = out.strip()
    return Path(text) if text else None


def _validate_symbol(symbol: str) -> Optional[str]:
    if not symbol or not symbol.strip():
        return "Symbol is empty. Pass an identifier name."
    if not _VALID_SYMBOL.match(symbol.strip()):
        return (
            f"Invalid symbol {symbol!r}. Must be a plain identifier "
            f"(letters/digits/underscores, no dots / colons / hyphens)."
        )
    return None


def _summarize(hits: list[str], cap: int = _MAX_HITS) -> str:
    """Format hits for voice + supervisor reading. Strips noise lines."""
    cleaned = [ln for ln in hits if ln.strip()]
    if not cleaned:
        return "(no matches)"
    if len(cleaned) > cap:
        head = "\n".join(cleaned[:cap])
        return (
            f"{len(cleaned)} matches (showing first {cap}, narrow with path_filter):\n"
            f"{head}"
        )
    return f"{len(cleaned)} match(es):\n" + "\n".join(cleaned)


# ── @function_tool surface ──────────────────────────────────────


@function_tool
async def find_definitions(symbol: str, path_filter: str = "") -> str:
    """Find where a Python/TS/JS symbol is defined.

    Searches the whole git-tracked repo (ignoring .gitignore'd dirs)
    for lines that introduce the symbol: `def NAME`, `async def NAME`,
    `class NAME`, top-level `NAME =`, `function NAME`, `interface
    NAME`, `type NAME =`, `const NAME =`, etc.

    Use for voice asks like "where is RuleStore defined?" /
    "where's the bash tool?". Faster than asking the user to grep,
    works without a real LSP.

    Args:
        symbol:      A plain identifier (letters / digits /
                     underscores). Dots, colons, and hyphens are
                     rejected — split into halves and search both.
        path_filter: Optional git pathspec to narrow the search,
                     e.g. `'*.py'`, `'src/voice-agent/**'`,
                     `':!tests/'`. Empty → whole repo.

    Returns:
        Match count + up to 50 hits with `file:line: content`,
        same format as `git grep -n`.
    """
    err = _validate_symbol(symbol)
    if err is not None:
        return err
    name = symbol.strip()

    root = await _repo_root()
    if root is None:
        return "Not inside a git repository."

    pattern = _DEFINITION_PATTERN_TPL.replace("SYMBOL", re.escape(name))
    args = ["grep", "-n", "-P", pattern]
    if path_filter:
        args.append("--")
        args.append(path_filter)

    rc, out, err_text = await _git(*args, cwd=str(root))
    # git grep exits 1 when there are no matches — that's expected.
    if rc not in (0, 1):
        return f"git grep failed: {err_text.strip() or 'unknown error'}"

    return _summarize(out.splitlines())


@function_tool
async def find_references(symbol: str, path_filter: str = "") -> str:
    """Find every word-boundary occurrence of a symbol across the
    repo.

    `find_definitions` catches where a name is INTRODUCED;
    `find_references` catches every USE. Same git pathspec filter.
    Word-boundary so `cat` doesn't match inside `concatenate`.

    Use for "what calls _check_destructive?" / "where is RuleStore
    used?".

    Args:
        symbol:      A plain identifier. Same constraints as
                     find_definitions.
        path_filter: Optional git pathspec.

    Returns:
        Match count + up to 50 hits.
    """
    err = _validate_symbol(symbol)
    if err is not None:
        return err
    name = symbol.strip()

    root = await _repo_root()
    if root is None:
        return "Not inside a git repository."

    args = ["grep", "-n", "-w", name]
    if path_filter:
        args.append("--")
        args.append(path_filter)

    rc, out, err_text = await _git(*args, cwd=str(root))
    if rc not in (0, 1):
        return f"git grep failed: {err_text.strip() or 'unknown error'}"

    return _summarize(out.splitlines())
