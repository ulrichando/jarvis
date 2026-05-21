"""Plan mode — port of claude-code's plan.tsx + EnterPlanModeTool + plans.ts.

Replaces the legacy `transfer_to_planner` subagent (which routed to
deepseek-v4-pro via run_jarvis_cli for multi-step coding work). Plan mode
is a leaner pattern: the supervisor itself enters a "no writes allowed"
phase, explores the codebase with the read/grep/glob tools, drafts a
plan, voices it for approval, then exits plan mode and executes with
bash/edit/write.

Architecture mapping from claude-code → voice JARVIS:

  claude-code                          voice JARVIS
  ─────────────────────────────────────────────────────────────────
  appState.toolPermissionContext.mode  module-level _PLAN_MODE flag
                                       (one voice session = one process)
  ~/.claude/plans/{slug}.md            ~/.jarvis/plans/{slug}.md
  EnterPlanModeTool.tsx                @function_tool enter_plan_mode
  ExitPlanModeTool.tsx                 @function_tool exit_plan_mode
  prepareContextForPlanMode()          assert_not_plan_mode() guard
                                       called by bash/edit/write
  React UI (Box/Text)                  voice TTS — supervisor reads
                                       the plan aloud
  /plan slash command                  not exposed (voice has no slash)
  external editor integration          not exposed (voice has no $EDITOR)

Read-only tools (read, grep, glob, web_fetch, web_search, current_time,
get_location, recall_conversation, etc.) always work. Write tools (bash,
edit, write) refuse with a "you're in plan mode" message until the
supervisor calls exit_plan_mode with a plan that the user has approved.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

from livekit.agents.llm import function_tool

logger = logging.getLogger("jarvis.plan_mode")

# ── Storage layout ──────────────────────────────────────────────────
# Match claude-code's getPlansDirectory() pattern but rooted at
# ~/.jarvis/plans/ (claude-code uses ~/.claude/plans/). One plan file
# per voice session. Slug is "current" for the active session — voice
# doesn't have multi-session UI so the multi-slug mechanism in
# plans.ts isn't needed.
PLANS_DIR = Path.home() / ".jarvis" / "plans"
DEFAULT_SLUG = "current"


def _plans_dir() -> Path:
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    return PLANS_DIR


def get_plan_file_path(slug: str = DEFAULT_SLUG) -> Path:
    """Path to the plan file for a session slug."""
    return _plans_dir() / f"{slug}.md"


def get_plan(slug: str = DEFAULT_SLUG) -> Optional[str]:
    """Read the current plan content. Returns None if no plan exists."""
    p = get_plan_file_path(slug)
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning(f"could not read plan {p}: {e}")
        return None


def write_plan(content: str, slug: str = DEFAULT_SLUG) -> Path:
    """Persist plan content to disk. Returns the file path."""
    p = get_plan_file_path(slug)
    p.write_text(content, encoding="utf-8")
    return p


# ── Mode flag (process-scoped) ──────────────────────────────────────
# Single global because a voice agent has exactly one active session
# per process. claude-code uses session-scoped state because it has
# concurrent IDE windows; voice doesn't.
_PLAN_MODE: bool = False
_PLAN_MODE_ENTERED_AT: float = 0.0


def is_in_plan_mode() -> bool:
    return _PLAN_MODE


def _set_plan_mode(on: bool) -> None:
    global _PLAN_MODE, _PLAN_MODE_ENTERED_AT
    _PLAN_MODE = on
    _PLAN_MODE_ENTERED_AT = time.time() if on else 0.0


def assert_not_plan_mode(tool_name: str) -> Optional[str]:
    """Guard for write tools (bash, edit, write). Returns a refusal
    string if the agent is currently in plan mode, else None.

    Usage in a write tool:
        gate = assert_not_plan_mode("bash")
        if gate:
            return gate
        # ... proceed with the write
    """
    if not _PLAN_MODE:
        return None
    elapsed = time.time() - _PLAN_MODE_ENTERED_AT
    return (
        f"Refused: {tool_name} is a write tool and you are currently in "
        f"plan mode (entered {elapsed:.0f}s ago). Plan mode is read-only "
        f"— use the `read`, `grep`, `glob`, `web_fetch`, and `web_search` "
        f"tools to explore. When the plan is ready, call `exit_plan_mode` "
        f"with the plan content; the user will approve or reject it. "
        f"After approval the mode flips back to default and writes work."
    )


# ── @function_tool entry points ─────────────────────────────────────


@function_tool
async def enter_plan_mode() -> str:
    """Use this tool proactively when you're about to start a non-trivial
    implementation task. Getting user sign-off on your approach before
    writing code prevents wasted effort and ensures alignment.

    This tool transitions you into PLAN MODE where you can explore the
    codebase and design an implementation approach for user approval.
    Read tools (read, grep, glob, web_fetch, web_search) work normally;
    write tools (bash, edit, write) are blocked until you call
    `exit_plan_mode` with a plan and the user approves it.

    ## When to Use This Tool

    Plan mode is valuable when the implementation approach is genuinely
    unclear. Use it when ANY of these apply:

    1. **Significant Architectural Ambiguity**: Multiple reasonable
       approaches exist and the choice matters.
       - "Add caching to the API" — Redis vs in-memory vs file-based
       - "Add real-time updates" — WebSockets vs SSE vs polling

    2. **Unclear Requirements**: You need to explore before you can
       make progress.
       - "Make the app faster" — need to profile bottlenecks first
       - "Refactor this module" — need to understand the target

    3. **High-Impact Restructuring**: The task significantly restructures
       existing code.
       - "Redesign the auth system"
       - "Migrate from one state-management approach to another"

    4. **Multi-File Changes**: The task likely touches more than 2-3
       files.

    ## When NOT to Use This Tool

    Skip plan mode when you can reasonably infer the right approach:
      - Single-line or few-line fixes (typos, obvious bugs, tweaks)
      - Adding a function with clear requirements
      - The user gave specific, detailed instructions
      - The user said "let's do X" — just get started
      - Pure research / read-only exploration (no plan needed)

    ## What Happens in Plan Mode

    In plan mode, you'll:
      1. Thoroughly explore the codebase using `read`, `grep`, `glob`
      2. Understand existing patterns and architecture
      3. Design an implementation approach
      4. Voice the plan to Ulrich for approval
      5. Call `exit_plan_mode(plan=...)` when ready to implement

    Returns: a confirmation message — voice it briefly ("Planning
    mode — exploring first") and start the exploration.
    """
    if _PLAN_MODE:
        return "Already in plan mode. Continue exploring; call exit_plan_mode when the plan is ready."
    _set_plan_mode(True)
    logger.info("entered plan mode")
    return (
        "Plan mode enabled. Read tools work; write tools are blocked. "
        "Explore the codebase, draft a plan, then call "
        "exit_plan_mode(plan=...) for user approval."
    )


@function_tool
async def exit_plan_mode(plan: str) -> str:
    """Exit plan mode and present the implementation plan for approval.

    Call this when you've finished exploring the codebase and have a
    concrete implementation plan to propose. The plan is saved to
    ~/.jarvis/plans/current.md and the supervisor returns to the
    default mode where write tools work.

    The voice supervisor's job after this call is:
      1. Voice a SHORT summary of the plan (2-3 sentences max — full
         text is in the plan file for the user to read separately).
      2. Wait for the user's approval / feedback.
      3. If approved, execute via bash/edit/write.
      4. If rejected, re-enter plan mode and revise.

    Args:
        plan: The full implementation plan as plain text or markdown.
              Include: what files will change, what the change is at
              each, what tests will be added, and any risk callouts.
    """
    if not _PLAN_MODE:
        # Allow exit without a prior enter — useful if the LLM enters,
        # crashes, restarts, and tries to exit. Just record the plan.
        logger.info("exit_plan_mode called outside plan mode — recording plan anyway")

    plan_text = (plan or "").strip()
    if not plan_text:
        return "Error: plan is required. Pass the implementation plan content."

    try:
        path = write_plan(plan_text)
    except OSError as e:
        return f"Error: could not write plan file: {type(e).__name__}: {e}"

    _set_plan_mode(False)
    logger.info(f"exited plan mode; plan saved to {path}")

    # Short summary for the supervisor to voice. The full plan is on
    # disk; voice user can ask "read me the plan" to hear it.
    n_lines = plan_text.count("\n") + 1
    n_chars = len(plan_text)
    return (
        f"Plan recorded ({n_lines} lines, {n_chars} chars) at "
        f"{path}. Voice the gist briefly and wait for approval before "
        f"calling write tools. If the user rejects, re-enter plan mode "
        f"and revise."
    )


@function_tool
async def read_plan() -> str:
    """Read the current implementation plan.

    Use this when the user asks "what's the plan?" / "read me the plan"
    / "what did we decide?". Returns the full plan text or a marker if
    no plan has been written this session.
    """
    content = get_plan()
    if content is None:
        return "No plan written yet for this session."
    return content
