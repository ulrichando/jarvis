"""Code search tool — find symbol definitions and references in source code.

Provides two registered tools:

  ``find_definitions`` — locate where a function, class, or variable is
      defined in the codebase.  Uses ripgrep (rg) with language-aware
      patterns; falls back to grep when rg is absent.

  ``code_search`` — general-purpose pattern search over source files with
      context lines.  Equivalent to ``search_files`` but scoped to code
      files by default and optimised for symbol / API discovery.

Both tools are gated behind a check_fn that returns False when neither
``rg`` nor ``grep`` is available (practically always True on Linux).

No LSP, no index, no network.  Subprocess-only; results are capped so the
voice supervisor's context window stays manageable.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .registry import registry, tool_error

logger = logging.getLogger(__name__)

# Hard cap on returned lines to keep voice responses compact.
_MAX_RESULT_LINES = 60
_MAX_RESULT_CHARS = 8_000
_DEFAULT_CONTEXT = 2   # lines of context around each match

# Source-file glob used by code_search when no file_glob is given.
_CODE_GLOB = "*.{py,js,ts,jsx,tsx,rs,go,java,c,cpp,h,hpp,sh,rb,lua}"

# Patterns for common definition forms, keyed by language/paradigm.
# These are used when rg supports --type (which it always does).
_DEF_PATTERNS: dict[str, str] = {
    "python": r"^(def |class |async def )",
    "js":     r"^(function |class |const |let |var |export )",
    "rust":   r"^(fn |pub fn |pub struct |struct |impl |trait )",
    "go":     r"^(func |type )",
    "java":   r"^(public |private |protected |class |interface |enum )",
}


def _rg_available() -> bool:
    return shutil.which("rg") is not None


def _grep_available() -> bool:
    return shutil.which("grep") is not None


def _check_code_search() -> bool:
    """Tool is available when rg or grep can be found."""
    return _rg_available() or _grep_available()


def _cap_output(text: str) -> str:
    """Trim output to _MAX_RESULT_CHARS, adding a hint if truncated."""
    if len(text) <= _MAX_RESULT_CHARS:
        return text
    return text[:_MAX_RESULT_CHARS] + "\n… [output truncated — narrow the search pattern or path]"


def _run_rg(
    pattern: str,
    path: str,
    *,
    context: int = 0,
    file_glob: Optional[str] = None,
    fixed_strings: bool = False,
    max_count: int = _MAX_RESULT_LINES,
    extra_args: list[str] | None = None,
) -> tuple[str, int]:
    """Run ripgrep and return (stdout, returncode).  Never raises."""
    cmd = ["rg", "--no-heading", "--line-number", "--color=never"]
    if fixed_strings:
        cmd.append("--fixed-strings")
    if context > 0:
        cmd += ["-C", str(context)]
    if file_glob:
        cmd += ["--glob", file_glob]
    cmd += ["--max-count", str(max_count)]
    if extra_args:
        cmd.extend(extra_args)
    cmd += [pattern, path]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            cwd=None,
        )
        return result.stdout, result.returncode
    except subprocess.TimeoutExpired:
        return "[rg timed out after 15s — narrow the path or pattern]", 1
    except FileNotFoundError:
        return "", 127


def _run_grep(
    pattern: str,
    path: str,
    *,
    context: int = 0,
    file_glob: Optional[str] = None,
    fixed_strings: bool = False,
    max_count: int = _MAX_RESULT_LINES,
    recursive: bool = True,
) -> tuple[str, int]:
    """Fallback grep when rg is not available."""
    cmd = ["grep", "--line-number", "--color=never"]
    if recursive:
        cmd.append("-r")
    if fixed_strings:
        cmd.append("-F")
    else:
        cmd.append("-E")
    if context > 0:
        cmd += ["-C", str(context)]
    cmd += ["-m", str(max_count)]
    cmd += [pattern, path]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.stdout, result.returncode
    except subprocess.TimeoutExpired:
        return "[grep timed out after 15s — narrow the path or pattern]", 1
    except FileNotFoundError:
        return "", 127


def _search(
    pattern: str,
    path: str,
    context: int,
    file_glob: Optional[str],
    fixed_strings: bool,
    max_lines: int,
) -> str:
    """Run rg (or grep fallback) and return raw text output."""
    if _rg_available():
        out, rc = _run_rg(
            pattern, path,
            context=context,
            file_glob=file_glob,
            fixed_strings=fixed_strings,
            max_count=max_lines,
        )
    else:
        out, rc = _run_grep(
            pattern, path,
            context=context,
            file_glob=file_glob,
            fixed_strings=fixed_strings,
            max_count=max_lines,
        )
    # rc 0 = matches found, 1 = no matches, 2+ = error
    if rc == 127:
        return "[neither rg nor grep is available]"
    if rc >= 2:
        return f"[search error (exit {rc})]"
    return out


# ---------------------------------------------------------------------------
# find_definitions handler
# ---------------------------------------------------------------------------

# Language-aware definition pattern built by prepending the symbol name.
# Matches: `def symbol(`, `class Symbol:`, `const symbol =`, `fn symbol(`, etc.
_DEF_PATTERN_TEMPLATE = (
    r"(\bdef {sym}\b|\bclass {sym}\b|\basync def {sym}\b"
    r"|\bfn {sym}\b|\bfunc {sym}\b"
    r"|\bconst {sym}\b|\blet {sym}\b|\bvar {sym}\b"
    r"|\binterface {sym}\b|\btrait {sym}\b|\bstruct {sym}\b"
    r"|\btype {sym}\b|\benum {sym}\b"
    r"|\bpub fn {sym}\b|\bpub struct {sym}\b|\bpub trait {sym}\b)"
)


def _handle_find_definitions(args: dict) -> str:
    symbol: str = (args.get("symbol") or "").strip()
    if not symbol:
        return tool_error("symbol is required")

    path: str = (args.get("path") or ".").strip() or "."
    file_glob: Optional[str] = (args.get("file_glob") or "").strip() or None
    context: int = max(0, min(int(args.get("context", _DEFAULT_CONTEXT) or _DEFAULT_CONTEXT), 10))

    # Escape the symbol for use in the regex.
    escaped = re.escape(symbol)
    pattern = _DEF_PATTERN_TEMPLATE.format(sym=escaped)

    raw = _search(pattern, path, context, file_glob, fixed_strings=False, max_lines=_MAX_RESULT_LINES)
    raw = _cap_output(raw)

    if not raw.strip():
        return json.dumps({
            "success": True,
            "symbol": symbol,
            "path": path,
            "matches": [],
            "hint": (
                f"No definition found for {symbol!r} in {path!r}. "
                "Try a broader path or check the symbol name."
            ),
        }, ensure_ascii=False)

    lines = raw.strip().splitlines()
    return json.dumps({
        "success": True,
        "symbol": symbol,
        "path": path,
        "match_count": len([l for l in lines if l and not l.startswith("--")]),
        "output": raw.strip(),
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# code_search handler
# ---------------------------------------------------------------------------

def _handle_code_search(args: dict) -> str:
    pattern: str = (args.get("pattern") or "").strip()
    if not pattern:
        return tool_error("pattern is required")

    path: str = (args.get("path") or ".").strip() or "."
    file_glob: Optional[str] = (args.get("file_glob") or "").strip() or None
    context: int = max(0, min(int(args.get("context", _DEFAULT_CONTEXT) or _DEFAULT_CONTEXT), 10))
    fixed: bool = bool(args.get("fixed_strings", False))

    # Default glob: code files only, when no explicit glob given.
    if not file_glob:
        file_glob = _CODE_GLOB

    raw = _search(pattern, path, context, file_glob, fixed_strings=fixed, max_lines=_MAX_RESULT_LINES)
    raw = _cap_output(raw)

    if not raw.strip():
        return json.dumps({
            "success": True,
            "pattern": pattern,
            "path": path,
            "matches": [],
            "hint": (
                f"No matches for {pattern!r} in code files under {path!r}. "
                "Try a different pattern or set file_glob to broaden the search."
            ),
        }, ensure_ascii=False)

    lines = [l for l in raw.strip().splitlines() if l and not l.startswith("--")]
    return json.dumps({
        "success": True,
        "pattern": pattern,
        "path": path,
        "file_glob": file_glob,
        "match_count": len(lines),
        "output": raw.strip(),
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Schemas + registration
# ---------------------------------------------------------------------------

_FIND_DEFINITIONS_SCHEMA = {
    "name": "find_definitions",
    "description": (
        "Find where a function, class, variable, or type is DEFINED in the codebase. "
        "Uses ripgrep with a language-aware pattern covering Python def/class, "
        "JS const/let/function, Rust fn/struct/trait, Go func/type, etc.\n\n"
        "Examples:\n"
        "  find_definitions(symbol=\"recall_conversation\")  — find the Python function\n"
        "  find_definitions(symbol=\"JarvisAgent\", path=\"src/voice-agent\")  — scoped to a dir\n"
        "  find_definitions(symbol=\"AudioProcessor\", file_glob=\"*.ts\")  — TS only\n\n"
        "Returns matching lines with context. For broad symbol search, use code_search instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Name of the symbol to find the definition of (exact name, case-sensitive).",
            },
            "path": {
                "type": "string",
                "description": "Directory or file to search in (default: current working directory).",
                "default": ".",
            },
            "file_glob": {
                "type": "string",
                "description": "Optional glob to restrict search (e.g. '*.py', '*.ts'). Default: all code files.",
            },
            "context": {
                "type": "integer",
                "description": "Lines of context to include around each match (default 2, max 10).",
                "default": _DEFAULT_CONTEXT,
            },
        },
        "required": ["symbol"],
    },
}

_CODE_SEARCH_SCHEMA = {
    "name": "code_search",
    "description": (
        "Search source code files for a pattern (regex or literal string). "
        "Defaults to code file types; use file_glob to restrict or broaden.\n\n"
        "Examples:\n"
        "  code_search(pattern=\"import asyncio\")  — find imports\n"
        "  code_search(pattern=\"JARVIS_HOME\", path=\"src/voice-agent\")  — env var usage\n"
        "  code_search(pattern=\"def.*session\", file_glob=\"*.py\")  — regex\n"
        "  code_search(pattern=\"TODO\", fixed_strings=true)  — literal string\n\n"
        "For finding where a symbol is DEFINED, prefer find_definitions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Search pattern (regex by default, or literal string with fixed_strings=true).",
            },
            "path": {
                "type": "string",
                "description": "Directory or file to search in (default: current working directory).",
                "default": ".",
            },
            "file_glob": {
                "type": "string",
                "description": (
                    "Glob to filter files (e.g. '*.py', '*.ts'). "
                    f"Defaults to code extensions: {_CODE_GLOB}"
                ),
            },
            "context": {
                "type": "integer",
                "description": "Lines of context around each match (default 2, max 10).",
                "default": _DEFAULT_CONTEXT,
            },
            "fixed_strings": {
                "type": "boolean",
                "description": "Treat pattern as a literal string instead of regex (default false).",
                "default": False,
            },
        },
        "required": ["pattern"],
    },
}

registry.register(
    name="find_definitions",
    schema=_FIND_DEFINITIONS_SCHEMA,
    handler=_handle_find_definitions,
    toolset="code_search",
    check_fn=_check_code_search,
    is_async=False,
    emoji="🔎",
)

registry.register(
    name="code_search",
    schema=_CODE_SEARCH_SCHEMA,
    handler=_handle_code_search,
    toolset="code_search",
    check_fn=_check_code_search,
    is_async=False,
    emoji="🔎",
)
