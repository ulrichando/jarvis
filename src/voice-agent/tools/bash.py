"""Bash — direct subprocess execution as an in-process voice tool.

Replaces the `run_jarvis_cli` subprocess-spawn-claude-code path for atomic
shell commands. Voice supervisor calls bash() directly; result returns in
~50 ms instead of the 5-15 s end-to-end latency of run_jarvis_cli.

Description text + usage rules ported from claude-code's BashTool/prompt.ts
so the model gets the same coaching it would get in claude-code. Voice
nuances:
  - JARVIS runs as `ulrich`. **As of 2026-05-16, there is NO sudoers
    NOPASSWD entry** (earlier docs claimed otherwise; live `sudo -n` test
    confirmed it requires a password). Commands that need sudo fail fast
    with "a password is required" — this is a feature, not a bug. No
    sandboxing of $HOME-scoped operations; voice has no UI to prompt
    with, so destructive commands are detected via _DESTRUCTIVE_PATTERNS
    and surfaced as a warning in the result text (not blocked).
  - Output is truncated for voice TTS sanity (30 KB hard cap).
  - Destructive-command detection routes the warning into the supervisor's
    chat_ctx instead of throwing — the supervisor prompt's PUSH BACK
    section handles voicing the concern.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Optional

from livekit.agents.llm import function_tool

from tools.plan_mode import assert_not_plan_mode

logger = logging.getLogger("jarvis.bash")

# claude-code defaults: 2 min default, 10 min max. Voice doesn't ask the
# user to wait minutes — but `npm test` / `cargo build` legitimately take
# longer than 30 s, so default is 2 min. Long-running work should use
# run_in_background once we add that.
_DEFAULT_TIMEOUT_S = 120
_MAX_TIMEOUT_S = 600

# Hard cap on returned text. claude-code uses 30 000 chars; we match.
_MAX_OUTPUT_CHARS = 30_000

# Working-directory persistence across bash() calls. Mirrors claude-
# code's BashTool behavior: `cd /some/path` in one call persists for
# the next. Env vars and aliases STILL don't persist (each invocation
# is a fresh /bin/bash -c subshell) — only cwd. Added 2026-05-12.
#
# Initialised lazily on first use to the voice-agent process cwd.
# Tests can reset via `reset_cwd_for_test()`. Disable persistence by
# setting `JARVIS_BASH_PERSIST_CWD=0` (matches the upstream env var
# `CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR=1` to disable in claude-code,
# inverted polarity — JARVIS defaults to ENABLED, opt-out via =0).
_BASH_CWD: Optional[str] = None

# Unique sentinel printed by the wrapper after the user's command so
# we can capture the new cwd from stdout. 16 hex chars of randomness
# baked into the literal so a command can't accidentally produce it
# (the user would have to know this literal to forge it, and even
# then, only the LAST occurrence on a line by itself is parsed).
_CWD_SENTINEL = "__JARVIS_BASH_CWD_E2F1B0D49C7A6F38__"

# Destructive-command patterns. Lifted from claude-code's
# BashTool/destructiveCommandWarning.ts. Voice supervisor uses this
# list in the PUSH BACK rule — match here means we annotate the
# result so the supervisor's chat_ctx carries the warning into its
# next turn.
#
# Each entry is (compiled_regex, label). Pre-compiling keeps the
# fast-path branch-free and lets each pattern carry its own flags
# without the 2-tuple-vs-3-tuple shape mess the old code had.
_DESTRUCTIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brm\s+-rf?\s+/"),                       "rm -rf with absolute path"),
    (re.compile(r"\brm\s+-rf?\s+~"),                       "rm -rf with home expansion"),
    (re.compile(r"\bdd\s+if="),                            "dd to disk"),
    (re.compile(r"\bmkfs\."),                              "mkfs (formatting filesystem)"),
    (re.compile(r":\(\)\{\s*:\|"),                         "fork bomb"),
    (re.compile(r"\bgit\s+push\s+(?:--force|-f)\b"),       "git force push"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"),              "git reset --hard"),
    (re.compile(r"\bgit\s+clean\s+-[fd]+\b"),              "git clean -fd"),
    (re.compile(r"\bDROP\s+TABLE\b", re.I),                "SQL DROP TABLE"),
    (re.compile(r"\bTRUNCATE\s+TABLE\b", re.I),            "SQL TRUNCATE TABLE"),
    (re.compile(r"\bsudo\s+rm\b"),                         "sudo rm"),
    (re.compile(r">\s*/dev/sd[a-z]"),                      "writing to a raw disk device"),
]

# Banned commands the supervisor should NEVER invoke through bash —
# they have dedicated in-process tools (read/edit) which are faster
# and cleaner. Soft redirect, not a security gate. Match is hit when
# the banned name appears at a CLAUSE START (start of the command,
# or right after `;` / `&&` / `||` / `|` / `(` / backtick) — so
# `grep cat /etc/passwd` is NOT flagged (cat is an argument), but
# `ls; cat foo` IS (cat is a new command).
_BANNED_COMMANDS = {"cat", "head", "tail", "sed", "awk", "less", "more"}
_BANNED_HINT = {
    "cat":  "use the read tool",
    "head": "use the read tool",
    "tail": "use the read tool",
    "less": "use the read tool",
    "more": "use the read tool",
    "sed":  "use the edit tool",
    "awk":  "use the edit tool",
}
# Matches identifiers (command names) at the start of the command or
# immediately after a shell clause boundary. Supports:
#   start-of-string, `;`, `|`, `||`, `(`, `&&`, backtick, redirect-
#   subshell `$(`.
# Backslash-escaped names (`\cat foo`) are NOT matched — explicit
# user escape hatch.
_CLAUSE_RE = re.compile(r"(?:^|[;|(`]|&&|\|\||\$\()\s*([A-Za-z_][\w-]*)")

# Snap window for newline-aligned truncation. The cut moves up to
# this many characters in either direction to land on a `\n` boundary
# so the supervisor doesn't see a partial line that looks like noise.
_TRUNCATE_SNAP_WINDOW = 200


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    """Return `text` if under `limit`, else a head+tail elision that
    snaps each cut point to the nearest newline within a small window
    so the truncated boundary lands cleanly on a line break."""
    if len(text) <= limit:
        return text

    head_target = limit // 2
    tail_target = limit - head_target

    # Snap head end: prefer the LAST newline at or before
    # head_target (so the elision happens AFTER a complete line),
    # widening up to SNAP_WINDOW chars forward if no newline appears
    # before the target.
    head_search_lo = max(0, head_target - _TRUNCATE_SNAP_WINDOW)
    head_search_hi = min(len(text), head_target + _TRUNCATE_SNAP_WINDOW)
    head_nl = text.rfind("\n", head_search_lo, head_target + 1)
    if head_nl < 0:
        head_nl = text.find("\n", head_target, head_search_hi)
    head_end = head_nl if head_nl >= 0 else head_target

    # Snap tail start: prefer the FIRST newline at or after
    # (len-tail_target), so the elision RESUMES at a complete line.
    tail_start_target = len(text) - tail_target
    tail_search_lo = max(0, tail_start_target - _TRUNCATE_SNAP_WINDOW)
    tail_search_hi = min(len(text), tail_start_target + _TRUNCATE_SNAP_WINDOW)
    tail_nl = text.find("\n", tail_start_target, tail_search_hi)
    if tail_nl < 0:
        tail_nl = text.rfind("\n", tail_search_lo, tail_start_target)
    tail_start = (tail_nl + 1) if tail_nl >= 0 else tail_start_target

    # Defensive: if snapping pulled head_end past tail_start (small
    # input + large snap window), bail back to the unsnapped midpoint.
    if head_end >= tail_start:
        head_end = head_target
        tail_start = tail_start_target

    head = text[:head_end].rstrip()
    tail = text[tail_start:].lstrip()
    omitted = len(text) - len(head) - len(tail)
    return (
        f"{head}\n\n"
        f"[output truncated: {len(text):,} chars total, "
        f"~{omitted:,} chars omitted from the middle]\n\n"
        f"{tail}"
    )


def _check_destructive(command: str) -> Optional[str]:
    for pattern, label in _DESTRUCTIVE_PATTERNS:
        if pattern.search(command):
            return label
    return None


def _get_bash_cwd() -> str:
    """Resolve the current persistent bash cwd (initialised lazily)."""
    global _BASH_CWD
    if _BASH_CWD is None:
        _BASH_CWD = os.getcwd()
    return _BASH_CWD


def _set_bash_cwd(path: str) -> None:
    """Update the persistent cwd. No validation here — the caller
    has already extracted it from a successful pwd output."""
    global _BASH_CWD
    _BASH_CWD = path


def reset_cwd_for_test() -> None:
    """Clear the persistent cwd. Test-only — production never calls this."""
    global _BASH_CWD
    _BASH_CWD = None


def _wrap_for_cwd_capture(cmd: str) -> str:
    """Return a wrapped command that prints the sentinel + final pwd
    on stdout so the bash() caller can update the persistent cwd.

    Preserves the user command's exit status: `$?` is captured into
    `__jrc` immediately after the user command, then restored via
    `exit $__jrc` at the end.

    NEWLINES, not semicolons, separate the wrapper stanzas from
    `cmd`. A trailing `#` comment inside `cmd` would consume `;`-
    chained wrapper stanzas; a newline terminates the comment and
    starts a fresh statement. Same reason no brace group: `{ cmd; }`
    breaks when cmd has a trailing comment.
    """
    return (
        f"{cmd}\n"
        f"__jrc=$?\n"
        f"printf '\\n%s%s\\n' '{_CWD_SENTINEL}' \"$(pwd)\"\n"
        f"exit $__jrc"
    )


def _extract_new_cwd(stdout_text: str) -> tuple[str, Optional[str]]:
    """Find the trailing sentinel-prefixed cwd line; return
    (stripped_stdout, new_cwd_or_None). If the sentinel isn't there
    (e.g. command crashed before the wrapper's printf), return the
    text unchanged and None.
    """
    idx = stdout_text.rfind(_CWD_SENTINEL)
    if idx < 0:
        return stdout_text, None
    # Path is the rest of the line after the sentinel.
    end = stdout_text.find("\n", idx)
    line_tail = stdout_text[idx + len(_CWD_SENTINEL):end if end >= 0 else None]
    new_cwd = line_tail.strip() or None
    # Strip the sentinel-line (and the preceding newline if present)
    # from the visible output so the supervisor doesn't see it.
    sentinel_line_start = stdout_text.rfind("\n", 0, idx)
    if sentinel_line_start < 0:
        sentinel_line_start = 0
    stripped = stdout_text[:sentinel_line_start]
    return stripped.rstrip("\n"), new_cwd


def _check_banned(command: str) -> Optional[tuple[str, str]]:
    """Return `(banned_name, hint)` if any clause in `command` starts
    with a banned utility, else `None`.

    Scans every command position — start of string OR right after a
    shell clause boundary (`;`, `&&`, `||`, `|`, `(`, `` ` ``, `$(`).
    `grep cat /etc/passwd` is NOT flagged (cat is mid-token, an
    argument). `ls; cat foo` IS flagged on the cat.

    Best-effort coaching, not a security control. The LLM can still
    invoke a banned command via `\\cat foo`, here-docs, or pipes
    constructed across multiple bash() calls.
    """
    if not command.strip():
        return None
    for match in _CLAUSE_RE.finditer(command):
        base = os.path.basename(match.group(1))
        if base in _BANNED_COMMANDS:
            return base, _BANNED_HINT.get(base, "use a dedicated tool")
    return None


@function_tool
async def bash(
    command: str,
    description: str = "",
    timeout: int = _DEFAULT_TIMEOUT_S,
    run_in_background: bool = False,
) -> str:
    """Execute a shell command via /bin/bash and return stdout+stderr.

    Each call is a FRESH /bin/bash subshell — exported variables and
    aliases do NOT carry over. The WORKING DIRECTORY does persist,
    though: a `cd /some/path` in one call survives to the next bash()
    call within this voice-agent process. Mirrors claude-code's
    Bash tool semantics; the starting cwd is the voice-agent's
    process directory (`src/voice-agent/`).

    Set `JARVIS_BASH_PERSIST_CWD=0` to disable cwd persistence and
    return to fresh-cwd-every-call.

    For multi-step work where env vars or aliases DO need to share
    a shell, chain in one call:

        bash("cd /tmp && export FOO=1 && ./run.sh")

    AVOID using this tool to run `cat`, `head`, `tail`, `sed`, `awk`,
    `less`, or `more` — they have dedicated in-process tools
    (read/edit) which are faster and cleaner. The banned-command
    check is a soft coaching redirect, not a security gate.

    Instructions:
      - If the command will create new directories or files, first run
        `ls` to verify the parent directory exists.
      - Always quote file paths that contain spaces.
      - Prefer absolute paths over chained `cd` for clarity.
      - You may specify an optional `timeout` in seconds (max 600).
        Default 120 s.
      - For long-running commands you don't need the result of right now,
        pass `run_in_background=True` and you'll be notified when done.
        DO NOT also use `&` in the command — the runner handles that.

    For git commands:
      - Prefer creating a new commit over amending.
      - Before destructive operations (git reset --hard, git push --force,
        git checkout --), consider safer alternatives.
      - Never skip hooks (--no-verify) unless explicitly requested.
      - Never force-push to main/master without user confirmation.

    Args:
        command:           The shell command to execute.
        description:       Short (5-10 word) description of what the
                           command does. Helps the user follow along.
        timeout:           Seconds before the command is killed (max 600).
        run_in_background: True to run async; False (default) waits for
                           completion.
    """
    cmd = (command or "").strip()
    if not cmd:
        return "Error: empty command. Pass a non-empty bash command."

    # Plan-mode gate. Bash can have side effects (mkdir, git commit,
    # apt install) so it counts as a write tool. Read-only bash patterns
    # (ls, cat ${file}, ps, df) would be safe but we don't try to parse
    # — the LLM should use the dedicated read/grep/glob tools in plan
    # mode, which is what plan-mode is designed to encourage.
    gate = assert_not_plan_mode("bash")
    if gate:
        return gate

    # Soft coaching: if the LLM reaches for cat/head/tail anywhere
    # in the command (start, or after a `;`/`&&`/`|`/`(`/etc.),
    # redirect to the dedicated tool. The escape hatch is to prepend
    # `\` to the command name.
    banned = _check_banned(cmd)
    if banned is not None:
        banned_name, banned_hint = banned
        return (
            f"Suggestion: instead of `{banned_name}`, "
            f"{banned_hint}. (If you really meant bash, prepend `\\` to "
            f"the command name, but the dedicated tool is faster.)"
        )

    # Destructive-command annotation. We DO run the command — voice has
    # full root and the user is the admin — but we tag the result so the
    # supervisor's chat_ctx carries the warning. The supervisor prompt's
    # PUSH BACK section will voice the concern.
    destructive = _check_destructive(cmd)

    # Clamp timeout.
    try:
        t = max(1, min(int(timeout or _DEFAULT_TIMEOUT_S), _MAX_TIMEOUT_S))
    except Exception:
        t = _DEFAULT_TIMEOUT_S

    logger.info(f"bash → {description or cmd[:60]}{' [destructive]' if destructive else ''}")

    persist_cwd = os.environ.get("JARVIS_BASH_PERSIST_CWD", "1") == "1"
    bash_cwd = _get_bash_cwd() if persist_cwd else None

    if run_in_background:
        # Detached subprocess; result not awaited. Voice rarely needs this
        # but it's in the spec for parity with claude-code. Background
        # commands DON'T update the persistent cwd — they're independent
        # of the foreground conversation flow.
        try:
            await asyncio.create_subprocess_exec(
                "/bin/bash", "-c", cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
                cwd=bash_cwd,
            )
            return f"Started in background: {description or cmd[:60]}."
        except Exception as e:
            return f"Failed to start background process: {type(e).__name__}: {e}"

    try:
        # create_subprocess_shell uses /bin/sh (dash on Kali), which
        # silently mishandles bashisms — `[[ ]]`, brace expansion,
        # arrays, `((...))`, `$'...'`, etc. The docstring promises bash
        # and claude-code's coaching assumes bash, so we exec
        # /bin/bash -c <cmd> directly. 2026-05-12 fix.
        #
        # Cwd persistence (2026-05-12): start in the per-session cached
        # cwd and use a wrapped command that prints the new cwd via
        # the unique sentinel so we can update the cache after the
        # call. If persistence is disabled (env=0) the wrap is skipped.
        exec_cmd = _wrap_for_cwd_capture(cmd) if persist_cwd else cmd
        proc = await asyncio.create_subprocess_exec(
            "/bin/bash", "-c", exec_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=bash_cwd,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=t
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            return f"Command timed out after {t} s. Consider running in the background or breaking it up."
    except Exception as e:
        return f"Failed to launch shell: {type(e).__name__}: {e}"

    out = stdout_b.decode("utf-8", errors="replace")
    err = stderr_b.decode("utf-8", errors="replace")
    rc = proc.returncode

    # Cwd persistence: pull the wrapper's pwd marker out of stdout
    # AND update the persistent cache. Only fires when persist_cwd
    # is on AND the wrapper actually completed (no timeout/kill).
    if persist_cwd:
        out, new_cwd = _extract_new_cwd(out)
        if new_cwd:
            _set_bash_cwd(new_cwd)

    parts = []
    if destructive:
        parts.append(f"[note: destructive command detected — {destructive}]")
    if out:
        parts.append(out.rstrip())
    if err:
        parts.append(f"[stderr]\n{err.rstrip()}")
    parts.append(f"[exit {rc}]")
    combined = "\n".join(parts)

    return _truncate(combined)
