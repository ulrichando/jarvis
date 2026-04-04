"""Hooks UI utilities for JARVIS.

Provides constants, formatters, and validators for the hooks system.
Handles HooksConfigMenu, SelectEventMode, SelectHookMode,
ViewHookMode as pure-Python helpers.
"""

from typing import Any


# All hook event types with descriptions, matching brain/hooks.py events.
HOOK_EVENTS: dict[str, dict[str, str]] = {
    "PreToolUse": {
        "summary": "Runs before a tool call is executed",
        "description": (
            "Fires before each tool invocation. Hooks can inspect the tool name "
            "and input, then allow (exit 0), block (exit 2), or warn."
        ),
        "matcher": "tool_name",
    },
    "PostToolUse": {
        "summary": "Runs after a tool call completes",
        "description": (
            "Fires after a tool finishes successfully. Useful for quality checks, "
            "logging, or triggering follow-up actions on tool output."
        ),
        "matcher": "tool_name",
    },
    "PostToolUseFailure": {
        "summary": "Runs when a tool call fails",
        "description": (
            "Fires when a tool execution raises an error. Use for error reporting, "
            "fallback logic, or cleanup."
        ),
        "matcher": "tool_name",
    },
    "PermissionDenied": {
        "summary": "Runs when a tool call is blocked by permissions",
        "description": (
            "Fires when the permission system denies a tool call. Useful for "
            "alerting, audit logging, or offering alternatives."
        ),
        "matcher": "tool_name",
    },
    "Notification": {
        "summary": "Runs on system notifications",
        "description": (
            "Fires when the system emits a notification. Can be used to route "
            "notifications to external services."
        ),
        "matcher": None,
    },
    "Stop": {
        "summary": "Runs before JARVIS considers a task complete",
        "description": (
            "Final verification hook. If exit code is 2, the stop is blocked "
            "and the agent continues working."
        ),
        "matcher": None,
    },
    "SessionStart": {
        "summary": "Runs when a session begins",
        "description": (
            "Fires once at session startup. Useful for environment setup, "
            "loading project context, or starting background services."
        ),
        "matcher": None,
    },
    "SessionEnd": {
        "summary": "Runs when a session ends",
        "description": (
            "Fires at session teardown. Useful for cleanup, saving state, "
            "or sending session summaries."
        ),
        "matcher": None,
    },
    "SubagentStart": {
        "summary": "Runs when a sub-agent is spawned",
        "description": (
            "Fires when a sub-agent (scout, worker, planner) is created. "
            "Blocking hook -- the sub-agent waits until the hook completes."
        ),
        "matcher": None,
    },
    "SubagentStop": {
        "summary": "Runs when a sub-agent finishes",
        "description": (
            "Fires when a sub-agent completes its work. Blocking hook that "
            "runs before results are returned to the parent."
        ),
        "matcher": None,
    },
    "CwdChanged": {
        "summary": "Runs when working directory changes",
        "description": (
            "Fires when the current working directory is changed. "
            "Useful for reloading project-specific settings."
        ),
        "matcher": None,
    },
    "FileChanged": {
        "summary": "Runs when a file is modified",
        "description": (
            "Fires after a file is written, edited, or deleted. "
            "Useful for auto-formatting, linting, or syncing."
        ),
        "matcher": "file_path",
    },
    "ContextCompacted": {
        "summary": "Runs when context window is compacted",
        "description": (
            "Fires after the conversation context is compacted to fit the "
            "context window. Useful for saving summaries or checkpoints."
        ),
        "matcher": None,
    },
}


def format_hook_config(hooks: dict[str, Any]) -> str:
    """Format a hooks configuration dict for CLI display.

    Renders the hooks config as a readable tree showing events, matchers,
    and hook types with their commands.

    Args:
        hooks: The hooks configuration dict, structured as:
            {"PreToolUse": [{"type": "command", "command": "...", "matcher": "..."}]}

    Returns:
        Formatted multiline string.
    """
    if not hooks:
        return "No hooks configured."

    total = sum(
        len(v) if isinstance(v, list) else 1
        for v in hooks.values()
    )
    lines: list[str] = [f"Hooks ({total} configured)", "-" * 40]

    for event, hook_list in sorted(hooks.items()):
        if event not in HOOK_EVENTS:
            continue

        entries = hook_list if isinstance(hook_list, list) else [hook_list]
        lines.append(f"\n  {event} ({len(entries)})")
        meta = HOOK_EVENTS[event]
        lines.append(f"    {meta['summary']}")

        for hook in entries:
            if not isinstance(hook, dict):
                continue
            hook_type = hook.get("type", "unknown")
            display = _get_hook_display_text(hook)
            matcher = hook.get("matcher", "")
            matcher_str = f" [matcher: {matcher}]" if matcher else ""
            lines.append(f"    [{hook_type}] {display}{matcher_str}")

    return "\n".join(lines)


def _get_hook_display_text(hook: dict[str, Any]) -> str:
    """Return a short display string for a hook config entry."""
    hook_type = hook.get("type", "unknown")
    if hook_type == "command":
        return hook.get("command", "(no command)")
    elif hook_type == "http":
        return hook.get("url", "(no url)")
    elif hook_type == "prompt":
        prompt = hook.get("prompt", "(no prompt)")
        return prompt[:60] + "..." if len(prompt) > 60 else prompt
    elif hook_type == "agent":
        return hook.get("task", "(no task)")
    return f"({hook_type})"


def format_hook_result(event: str, hook_type: str, result: dict[str, Any]) -> str:
    """Format a hook execution result for display.

    Args:
        event: The hook event name (e.g. "PreToolUse").
        hook_type: The hook type (e.g. "command", "prompt").
        result: Dict with execution result, typically containing:
            - exit_code (int): Process exit code for command hooks.
            - output (str): Captured stdout/stderr.
            - decision (str): "allow" | "block" | "warn" for PreToolUse.
            - error (str): Error message if execution failed.

    Returns:
        Formatted single-line or multiline string.
    """
    exit_code = result.get("exit_code")
    decision = result.get("decision", "")
    output = result.get("output", "")
    error = result.get("error", "")

    # Decision indicator
    if decision == "allow":
        indicator = "\033[32m[ALLOW]\033[0m"
    elif decision == "block":
        indicator = "\033[31m[BLOCK]\033[0m"
    elif decision == "warn":
        indicator = "\033[33m[WARN]\033[0m"
    elif error:
        indicator = "\033[31m[ERROR]\033[0m"
    else:
        indicator = "[OK]"

    parts = [f"{indicator} {event} ({hook_type})"]

    if exit_code is not None and hook_type == "command":
        parts.append(f"exit code {exit_code}")

    if error:
        parts.append(f"Error: {error}")
    elif output:
        # Show first line of output
        first_line = output.strip().split("\n")[0]
        if first_line:
            parts.append(first_line)

    return " | ".join(parts)


def validate_hook(
    event: str,
    hook_type: str,
    config: dict[str, Any],
) -> dict[str, list[str]]:
    """Validate a hook configuration entry.

    Checks that the event is known, the hook type is supported, and
    required fields are present.

    Args:
        event: Hook event name.
        hook_type: Hook type ("command", "prompt", "http", "agent").
        config: The hook configuration dict.

    Returns:
        Dict with "errors" (blocking) and "warnings" (non-blocking) lists.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Validate event
    if event not in HOOK_EVENTS:
        errors.append(f"Unknown hook event: {event}")

    # Validate type
    valid_types = {"command", "prompt", "http", "agent"}
    if hook_type not in valid_types:
        errors.append(
            f"Unknown hook type: {hook_type}. "
            f"Must be one of: {', '.join(sorted(valid_types))}"
        )

    # Type-specific validation
    if hook_type == "command":
        if not config.get("command"):
            errors.append("Command hooks require a 'command' field.")
    elif hook_type == "http":
        if not config.get("url"):
            errors.append("HTTP hooks require a 'url' field.")
    elif hook_type == "prompt":
        if not config.get("prompt"):
            errors.append("Prompt hooks require a 'prompt' field.")
    elif hook_type == "agent":
        if not config.get("task"):
            errors.append("Agent hooks require a 'task' field.")

    # Validate matcher usage
    matcher = config.get("matcher")
    if matcher and event in HOOK_EVENTS:
        event_meta = HOOK_EVENTS[event]
        if event_meta.get("matcher") is None:
            warnings.append(
                f"Event '{event}' does not support matchers. "
                f"The matcher '{matcher}' will be ignored."
            )

    # Warn on risky patterns
    if hook_type == "command":
        cmd = config.get("command", "")
        if "rm -rf" in cmd:
            warnings.append("Command contains 'rm -rf' -- ensure this is intentional.")
        if "sudo" in cmd:
            warnings.append("Command uses 'sudo' -- this may require interactive auth.")

    return {"errors": errors, "warnings": warnings}


def build_hook_entry(
    event: str,
    hook_type: str,
    command: str = "",
    matcher: str = "",
    **extra: Any,
) -> dict[str, Any]:
    """Build a hook configuration dict suitable for hooks.yaml.

    Args:
        event: The hook event (e.g. "PreToolUse").
        hook_type: The hook type ("command", "prompt", "http", "agent").
        command: Shell command (for command hooks) or prompt text (for prompt hooks).
        matcher: Optional tool/file matcher pattern.
        **extra: Additional fields (url, task, async_, once, etc.).

    Returns:
        A dict ready to be inserted into hooks config.
    """
    entry: dict[str, Any] = {"type": hook_type}

    if hook_type == "command":
        entry["command"] = command
    elif hook_type == "prompt":
        entry["prompt"] = command
    elif hook_type == "http":
        entry["url"] = extra.pop("url", command)
    elif hook_type == "agent":
        entry["task"] = extra.pop("task", command)

    if matcher:
        entry["matcher"] = matcher

    # Pass through optional fields
    for key in ("async_", "async_rewake", "once", "timeout"):
        if key in extra:
            # Convert async_ to async for YAML compat
            yaml_key = "async" if key == "async_" else key
            entry[yaml_key] = extra[key]

    return entry
