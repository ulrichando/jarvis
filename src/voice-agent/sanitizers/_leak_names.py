"""Known tool-name leak registry.

When the supervisor LLM (or a small fallback like llama-3.1-8b-instant)
gets confused, it sometimes emits the NAME of a tool as plain content
text instead of via the structured `tool_calls` field. The user hears
JARVIS say "task done" or "ext click" as if it were a sentence.

The pycall sanitizer uses this registry to recognize a leak even when
the tool isn't in the current LLM's live `tool_ctx` — supervisor tools
can leak from a sub-LLM that wouldn't otherwise know them, and the
`ext_*` family is open-ended.

Update `KNOWN_LEAK_NAMES` when adding a new subagent tool that's
likely to leak from a different LLM's content stream.

Hoisted from `sanitizers/pycall.py` 2026-05-10 (Step 7 of the audit).
"""
from __future__ import annotations


__all__ = ["KNOWN_LEAK_NAMES", "is_known_leak"]


# Subagent-internal tools + commonly-leaked names — even though the
# CURRENT LLM may not have them in its tool_ctx, the supervisor or a
# downstream LLM emitting them as plain content is unambiguously a
# leak (the user can't be saying "task_done" as legitimate prose).
KNOWN_LEAK_NAMES: frozenset[str] = frozenset({
    # Subagent task-done sentinel — auto-attached, never in the
    # supervisor LLM's tool_ctx but supervisor-LLM-emitted as text on
    # confused turns.
    "task_done",
    # Transfer/handoff tools — should always go via tool_calls, never
    # as content text.
    "transfer_to_desktop",
    "transfer_to_browser",
    "transfer_to_planner",
    "delegate",
    # Browser ext_* tools — prefixed; bulk-prevented in is_known_leak.
    # Listed here in case the prefix-check misses anything.
    "ext_screenshot",
    "ext_navigate",
    "ext_click",
    "ext_type",
    "ext_new_tab",
    "ext_get_url",
    "ext_back",
    "ext_forward",
    "ext_wait_for_load",
    # Common runtime tools that have leaked in the past.
    "browser_task",
    "browser_task_v2",
    "run_jarvis_cli",
    "bash",
    "media_control",
    "type_in_terminal",
    "launch_app",
    "web_search",
    "read_url",
    "remember_this",
    # Location tools. `get_location` / `set_location` retired
    # 2026-05-17, replaced by saved_address + current_location +
    # set_saved_address. Old names kept in this list so recall-time
    # scrubbing of legacy assistant turns (which may still mention
    # them) catches the leak shape.
    "get_location",
    "set_location",
    "saved_address",
    "current_location",
    "set_saved_address",
})


def is_known_leak(name: str, live_known: frozenset[str] | set[str]) -> bool:
    """True if `name` is plausibly a JARVIS tool whose appearance as
    plain content text is a leak. Combines the live tool_ctx with the
    subagent-internal whitelist + the `ext_*` prefix convention."""
    if name in live_known:
        return True
    if name in KNOWN_LEAK_NAMES:
        return True
    if name.startswith("ext_") and len(name) > 4:
        # Browser-extension tools all share this prefix; the supervisor
        # might emit any of them as text content.
        return True
    if name.startswith("transfer_to_") and len(name) > 12:
        # Future subagents' transfer tools, generated at registry
        # time — defensive cover.
        return True
    return False
