"""Bash — direct subprocess execution as an in-process voice tool.

Replaces the `run_jarvis_cli` subprocess-spawn-claude-code path for atomic
shell commands. Voice supervisor calls bash() directly; result returns in
~50 ms instead of the 5-15 s end-to-end latency of run_jarvis_cli.

Description text + usage rules ported from claude-code's BashTool/prompt.ts
so the model gets the same coaching it would get in claude-code. Voice
nuances:
  - JARVIS runs as `ulrich` with full sudo NOPASSWD (per /etc/sudoers.d/jarvis).
    No sandboxing, no permission prompts — voice has no UI to prompt with.
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
import shlex
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

# Destructive-command patterns. Lifted from claude-code's
# BashTool/destructiveCommandWarning.ts. Voice supervisor uses this list
# in the PUSH BACK rule — match here means we annotate the result so the
# supervisor's chat_ctx carries the warning into its next turn.
_DESTRUCTIVE_PATTERNS = [
    (r"\brm\s+-rf?\s+/", "rm -rf with absolute path"),
    (r"\brm\s+-rf?\s+~", "rm -rf with home expansion"),
    (r"\bdd\s+if=", "dd to disk"),
    (r"\bmkfs\.", "mkfs (formatting filesystem)"),
    (r":\(\)\{\s*:\|", "fork bomb"),
    (r"\bgit\s+push\s+(?:--force|-f)\b", "git force push"),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard"),
    (r"\bgit\s+clean\s+-[fd]+\b", "git clean -fd"),
    (r"\bDROP\s+TABLE\b", "SQL DROP TABLE", re.I),
    (r"\bTRUNCATE\s+TABLE\b", "SQL TRUNCATE TABLE", re.I),
    (r"\bsudo\s+rm\b", "sudo rm"),
    (r">\s*/dev/sd[a-z]", "writing to a raw disk device"),
]

# Banned commands voice supervisor should never invoke directly. claude-code
# soft-redirects these to dedicated tools; voice has dedicated tools for
# read/write/edit too — bash should NOT be how the supervisor reads files.
_BANNED_COMMANDS = {"cat", "head", "tail", "sed", "awk", "less", "more"}
_BANNED_HINT = {
    "cat": "use the read tool",
    "head": "use the read tool",
    "tail": "use the read tool",
    "sed": "use the edit tool",
    "awk": "use the edit tool",
    "less": "use the read tool",
    "more": "use the read tool",
    "echo": "output text directly to the user — don't echo it via bash",
}


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    return (
        f"{head}\n\n"
        f"[output truncated: {len(text):,} chars total, "
        f"showing first {limit//2} + last {limit//2}]\n\n"
        f"{tail}"
    )


def _check_destructive(command: str) -> Optional[str]:
    for entry in _DESTRUCTIVE_PATTERNS:
        if len(entry) == 3:
            pattern, label, flags = entry
            if re.search(pattern, command, flags):
                return label
        else:
            pattern, label = entry
            if re.search(pattern, command):
                return label
    return None


def _check_banned(command: str) -> Optional[str]:
    """Return a hint string if the command starts with a banned utility.
    Best-effort heuristic — not a security control, just a coaching nudge
    so the LLM picks the right tool."""
    try:
        first = shlex.split(command)[0] if command.strip() else ""
    except ValueError:
        return None
    base = os.path.basename(first)
    if base in _BANNED_COMMANDS:
        return _BANNED_HINT.get(base, "use a dedicated tool")
    return None


@function_tool
async def bash(
    command: str,
    description: str = "",
    timeout: int = _DEFAULT_TIMEOUT_S,
    run_in_background: bool = False,
) -> str:
    """Execute a shell command via /bin/bash and return stdout+stderr.

    The working directory persists between commands (the agent process's
    cwd), but shell state does not — every invocation is a fresh shell.
    Environment is initialized from Ulrich's profile.

    AVOID using this tool to run `cat`, `head`, `tail`, `sed`, `awk`, or
    `echo` commands — use the dedicated tools instead (read/edit). Each
    dedicated tool gives a much better experience than bash for the same
    operation.

    Instructions:
      - If the command will create new directories or files, first run
        `ls` to verify the parent directory exists.
      - Always quote file paths that contain spaces.
      - Use absolute paths and avoid `cd` between commands. The cwd is
        the voice-agent's process directory; `cd` only affects the
        single command's subshell.
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

    # Soft coaching: if the LLM reaches for cat/head/tail, redirect.
    banned_hint = _check_banned(cmd)
    if banned_hint:
        first = shlex.split(cmd)[0] if cmd else ""
        return (
            f"Suggestion: instead of `{first}`, "
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

    if run_in_background:
        # Detached subprocess; result not awaited. Voice rarely needs this
        # but it's in the spec for parity with claude-code.
        try:
            await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            return f"Started in background: {description or cmd[:60]}."
        except Exception as e:
            return f"Failed to start background process: {type(e).__name__}: {e}"

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
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
