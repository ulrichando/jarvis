"""JARVIS Hooks System — deterministic quality gates around tool execution.

Modeled after Claude Code's hooks architecture with full event lifecycle:

Events:
  PreToolUse         — Validate/block/modify tool calls before execution
  PostToolUse        — Run quality checks after tool execution
  PostToolUseFailure — React to tool execution failures
  PermissionDenied   — Respond when a tool call is blocked by permissions
  Notification       — React to system notifications
  Stop               — Final verification before JARVIS considers task complete
  SessionStart       — Run on session startup
  SessionEnd         — Run on session teardown
  SubagentStart      — When a sub-agent is spawned (blocking)
  SubagentStop       — When a sub-agent finishes (blocking)
  CwdChanged         — When working directory changes
  FileChanged        — When a file is modified (post write/edit/delete)
  ContextCompacted   — When context window is compacted

Hooks are defined in:
  ~/.jarvis/hooks.yaml        (user-level)
  .jarvis/hooks.yaml          (project-level)
  .jarvis/settings.json       (project-level, "hooks" key)
  Skill frontmatter           (skill-scoped, temporary)

Hook types:
  command  — Run a shell command (exit 0=allow, 2=block, other=warn)
  http     — POST event data to an HTTP endpoint
  prompt   — Ask the LLM to evaluate (returns ok/not-ok)
  agent    — Run as a sub-agent task (scout/worker/planner)

Hook options:
  async_       — Fire-and-forget, don't block execution
  async_rewake — If async and exit code 2, wake the model
  once         — Only run this hook once, then auto-disable
"""

import os
import re
import json
import logging
import subprocess
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any
from src.config import JARVIS_HOME

log = logging.getLogger(__name__)

# All supported lifecycle events
HOOK_EVENTS = (
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionDenied",
    "Notification",
    "Stop",
    "SessionStart",
    "SessionEnd",
    "SubagentStart",
    "SubagentStop",
    "CwdChanged",
    "FileChanged",
    "ContextCompacted",
)

# Events that can block the action
BLOCKING_EVENTS = {"PreToolUse", "PermissionDenied", "Stop", "SubagentStart", "SubagentStop"}


@dataclass
class Hook:
    """A single hook definition."""
    type: str = "command"          # "command", "http", "prompt", or "agent"
    command: str = ""              # Shell command to run
    url: str = ""                  # HTTP endpoint (type=http)
    prompt: str = ""               # LLM prompt for type=prompt
    timeout: int = 30              # Timeout in seconds
    matcher: str = ""              # Tool name regex (e.g. "Bash", "Edit|Write", "mcp__.*")
    if_filter: str = ""            # Fine-grained filter: "Bash(git *)"
    status_message: str = ""       # Status shown to user while hook runs
    enabled: bool = True           # Toggle without removing
    async_: bool = False           # If True, hook runs in background (fire-and-forget)
    async_rewake: bool = False     # If True AND exit code is 2, wake the model
    once: bool = False             # If True, only run this hook once then disable it


@dataclass
class HookResult:
    """Result of running a hook."""
    allowed: bool = True
    message: str = ""
    modified_args: dict | None = None
    additional_context: str = ""   # Extra context injected into LLM conversation
    decision: str = ""             # "allow", "deny", "ask", "block"


@dataclass
class AggregatedHookResult:
    """Aggregated result from running multiple hooks."""
    results: list[HookResult] = field(default_factory=list)
    final_allowed: bool = True           # Most restrictive wins
    final_message: str = ""              # Combined messages
    any_modified: bool = False           # Whether any hook modified args
    modified_args: dict | None = None    # Last modification wins


@dataclass
class HookConfig:
    """All hooks for every lifecycle event."""
    events: dict[str, list[dict]] = field(default_factory=lambda: {e: [] for e in HOOK_EVENTS})


class HooksManager:
    """Manages and executes hooks around tool calls and lifecycle events."""

    def __init__(self):
        self._config = HookConfig()
        self._active_skill_hooks: dict = {}
        self._runtime_hooks: list[dict] = []  # Added via /hook add

    def load(self):
        """Load hooks from user config, project config, and settings.json."""
        # User-level hooks.yaml
        user_hooks = JARVIS_HOME / "hooks.yaml"
        if user_hooks.exists():
            self._merge_yaml(user_hooks)

        # Project-level hooks.yaml
        project_hooks = Path.cwd() / ".jarvis" / "hooks.yaml"
        if project_hooks.exists():
            self._merge_yaml(project_hooks)

        # Project-level settings.json (hooks key)
        settings = Path.cwd() / ".jarvis" / "settings.json"
        if settings.exists():
            self._merge_settings_json(settings)

    def _merge_yaml(self, path: Path):
        """Load and merge hooks from a YAML file."""
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except Exception as e:
            log.warning("Failed to load hooks from %s: %s", path, e)
            return

        hooks = data.get("hooks", data)  # Support both {hooks: {...}} and flat
        self._merge_events(hooks, source=str(path))

    def _merge_settings_json(self, path: Path):
        """Load hooks from a settings.json file."""
        try:
            data = json.loads(path.read_text())
            hooks = data.get("hooks", {})
            if hooks:
                self._merge_events(hooks, source=str(path))
        except Exception as e:
            log.warning("Failed to load hooks from %s: %s", path, e)

    def _merge_events(self, hooks: dict, source: str = ""):
        """Merge hook definitions into the config."""
        for event in HOOK_EVENTS:
            if event in hooks:
                entries = hooks[event]
                if isinstance(entries, list):
                    for entry in entries:
                        entry.setdefault("_source", source)
                    self._config.events[event].extend(entries)

    # ── Skill-scoped hooks ──────────────────────────────────────────

    def set_skill_hooks(self, hooks: dict):
        """Temporarily activate skill-scoped hooks."""
        self._active_skill_hooks = hooks

    def clear_skill_hooks(self):
        """Deactivate skill-scoped hooks."""
        self._active_skill_hooks = {}

    # ── Runtime hooks (via /hook add) ───────────────────────────────

    def add_hook(self, event: str, command: str, matcher: str = "",
                 hook_type: str = "command", timeout: int = 30) -> bool:
        """Add a runtime hook. Returns True on success."""
        # Normalize event name
        event = self._normalize_event(event)
        if event not in HOOK_EVENTS:
            return False

        entry = {
            "type": hook_type,
            "command": command,
            "matcher": matcher,
            "timeout": timeout,
            "_source": "runtime",
        }
        self._config.events[event].append(entry)
        self._runtime_hooks.append({"event": event, **entry})
        return True

    def remove_hook(self, event: str, command: str = "") -> bool:
        """Remove hook(s) for an event. If command given, remove only that one."""
        event = self._normalize_event(event)
        if event not in HOOK_EVENTS:
            return False

        before = len(self._config.events[event])
        if command:
            self._config.events[event] = [
                h for h in self._config.events[event]
                if h.get("command", "") != command
            ]
            self._runtime_hooks = [
                h for h in self._runtime_hooks
                if not (h["event"] == event and h.get("command", "") == command)
            ]
        else:
            self._config.events[event] = []
            self._runtime_hooks = [h for h in self._runtime_hooks if h["event"] != event]

        return len(self._config.events[event]) < before

    def _normalize_event(self, event: str) -> str:
        """Normalize event name: pre_tool_use -> PreToolUse, etc."""
        # Already correct
        if event in HOOK_EVENTS:
            return event
        # Snake_case conversion
        mapping = {
            "pre_tool_use": "PreToolUse",
            "pre_command": "PreToolUse",
            "post_tool_use": "PostToolUse",
            "post_command": "PostToolUse",
            "post_tool_use_failure": "PostToolUseFailure",
            "on_error": "PostToolUseFailure",
            "permission_denied": "PermissionDenied",
            "notification": "Notification",
            "stop": "Stop",
            "on_startup": "SessionStart",
            "session_start": "SessionStart",
            "on_shutdown": "SessionEnd",
            "session_end": "SessionEnd",
            "subagent_start": "SubagentStart",
            "subagent_stop": "SubagentStop",
            "cwd_changed": "CwdChanged",
            "file_changed": "FileChanged",
            "context_compacted": "ContextCompacted",
        }
        return mapping.get(event.lower(), event)

    # ── Hook Execution ──────────────────────────────────────────────

    def run_pre_tool_use(self, tool_name: str, tool_args: dict) -> HookResult:
        """Run PreToolUse hooks. Can block or modify tool args."""
        all_hooks = self._get_hooks_for("PreToolUse")
        return self._run_hooks(all_hooks, tool_name, tool_args, event="PreToolUse")

    def run_post_tool_use(self, tool_name: str, tool_args: dict, result: str) -> HookResult:
        """Run PostToolUse hooks after successful execution."""
        all_hooks = self._get_hooks_for("PostToolUse")
        return self._run_hooks(all_hooks, tool_name, tool_args, event="PostToolUse", result=result)

    def run_post_tool_use_failure(self, tool_name: str, tool_args: dict, error: str) -> HookResult:
        """Run PostToolUseFailure hooks after tool errors."""
        all_hooks = self._get_hooks_for("PostToolUseFailure")
        return self._run_hooks(all_hooks, tool_name, tool_args, event="PostToolUseFailure", result=error)

    def run_permission_denied(self, tool_name: str, tool_args: dict, reason: str) -> HookResult:
        """Run PermissionDenied hooks when a tool is blocked by permissions."""
        all_hooks = self._get_hooks_for("PermissionDenied")
        return self._run_hooks(all_hooks, tool_name, tool_args, event="PermissionDenied", result=reason)

    def run_notification(self, message: str, notification_type: str = "") -> HookResult:
        """Run Notification hooks."""
        all_hooks = self._get_hooks_for("Notification")
        return self._run_hooks(
            all_hooks, notification_type, {},
            event="Notification", result=message,
        )

    def run_stop(self) -> HookResult:
        """Run Stop hooks before JARVIS finishes a task."""
        all_hooks = self._get_hooks_for("Stop")
        if not all_hooks:
            return HookResult(allowed=True)
        return self._run_hooks(all_hooks, "", {}, event="Stop")

    def run_session_start(self) -> HookResult:
        """Run SessionStart hooks."""
        all_hooks = self._get_hooks_for("SessionStart")
        if not all_hooks:
            return HookResult(allowed=True)
        return self._run_hooks(all_hooks, "", {}, event="SessionStart")

    def run_session_end(self) -> HookResult:
        """Run SessionEnd hooks."""
        all_hooks = self._get_hooks_for("SessionEnd")
        if not all_hooks:
            return HookResult(allowed=True)
        return self._run_hooks(all_hooks, "", {}, event="SessionEnd")

    def _get_hooks_for(self, event: str) -> list[dict]:
        """Get all hooks for an event (config + skill-scoped)."""
        return self._config.events.get(event, []) + self._active_skill_hooks.get(event, [])

    def _run_hooks(
        self, hooks: list[dict], tool_name: str,
        tool_args: dict, event: str, result: str = "",
    ) -> HookResult:
        """Execute a list of hook definitions. Most restrictive result wins."""
        combined = HookResult(allowed=True)

        for hook_def in hooks:
            # Skip disabled hooks
            if not hook_def.get("enabled", True):
                continue

            # Skip once-hooks that already ran
            if hook_def.get("once", False) and hook_def.get("_ran_once", False):
                continue

            # Check matcher (regex-based)
            matcher = hook_def.get("matcher", "")
            if matcher and not self._matches(matcher, tool_name):
                continue

            # Check if-filter for fine-grained matching
            if_filter = hook_def.get("if", hook_def.get("if_filter", ""))
            if if_filter and not self._matches_if(if_filter, tool_name, tool_args):
                continue

            # Get the actual hook entries (support nested "hooks" key like Claude Code)
            hook_entries = hook_def.get("hooks", [hook_def])
            if not isinstance(hook_entries, list):
                hook_entries = [hook_entries]

            for entry in hook_entries:
                if not entry.get("enabled", True):
                    continue

                # Skip once-entries that already ran
                if entry.get("once", False) and entry.get("_ran_once", False):
                    continue

                # Handle async hooks: log and skip (caller wires actual async execution)
                if entry.get("async", entry.get("async_", False)):
                    log.info(
                        "Async hook queued for %s (type=%s, rewake=%s): %s",
                        event, entry.get("type", "command"),
                        entry.get("async_rewake", False),
                        entry.get("command", entry.get("task", entry.get("url", ""))),
                    )
                    # Mark once-hooks as ran even for async
                    if entry.get("once", False):
                        entry["_ran_once"] = True
                    continue

                hook_type = entry.get("type", "command")
                hr = HookResult(allowed=True)

                if hook_type == "command":
                    hr = self._run_command_hook(entry, tool_name, tool_args, event, result)
                elif hook_type == "http":
                    hr = self._run_http_hook(entry, tool_name, tool_args, event, result)
                elif hook_type == "agent":
                    hr = self._run_agent_hook(entry, tool_name, tool_args, event, result)
                # prompt type requires brain reference — handled externally

                # Mark once-hooks as ran
                if entry.get("once", False):
                    entry["_ran_once"] = True
                if hook_def.get("once", False):
                    hook_def["_ran_once"] = True

                # Most restrictive wins
                if not hr.allowed and event in BLOCKING_EVENTS:
                    return hr
                if hr.modified_args is not None:
                    tool_args = hr.modified_args
                    combined.modified_args = hr.modified_args
                if hr.message:
                    combined.message = (combined.message + "\n" + hr.message).strip()
                if hr.additional_context:
                    combined.additional_context = hr.additional_context
                if hr.decision:
                    combined.decision = hr.decision

        return combined

    def _run_agent_hook(
        self, entry: dict, tool_name: str,
        tool_args: dict, event: str, result: str,
    ) -> HookResult:
        """Run an agent-type hook (sub-agent task).

        Returns a placeholder result. The brain can wire up actual agent
        execution by replacing or extending this method.
        """
        agent_type = entry.get("agent_type", "scout")
        task_template = entry.get("task", "")

        # Variable substitution
        task = task_template.replace("$TOOL_NAME", tool_name or "")
        task = task.replace("$EVENT", event)
        task = task.replace("$RESULT", result[:2000] if result else "")

        log.info("Agent hook (%s): %s", agent_type, task)
        return HookResult(
            allowed=True,
            message=f"Agent hook: {task}",
            additional_context=f"[agent-hook:{agent_type}] {task}",
        )

    # ── Aggregated Hook Execution ──────────────────────────────────

    def run_hooks_aggregated(
        self, event: str, tool_name: str = "",
        tool_args: dict | None = None, result: str = "",
    ) -> AggregatedHookResult:
        """Run all hooks for an event and return aggregated results."""
        if tool_args is None:
            tool_args = {}

        all_hooks = self._get_hooks_for(event)
        agg = AggregatedHookResult()

        for hook_def in all_hooks:
            if not hook_def.get("enabled", True):
                continue
            if hook_def.get("once", False) and hook_def.get("_ran_once", False):
                continue

            matcher = hook_def.get("matcher", "")
            if matcher and not self._matches(matcher, tool_name):
                continue

            if_filter = hook_def.get("if", hook_def.get("if_filter", ""))
            if if_filter and not self._matches_if(if_filter, tool_name, tool_args):
                continue

            # Run this single hook definition through _run_hooks
            hr = self._run_hooks([hook_def], tool_name, tool_args, event, result)
            agg.results.append(hr)

            if not hr.allowed:
                agg.final_allowed = False
            if hr.message:
                agg.final_message = (agg.final_message + "\n" + hr.message).strip()
            if hr.modified_args is not None:
                agg.any_modified = True
                agg.modified_args = hr.modified_args
                tool_args = hr.modified_args  # Chain modifications

        return agg

    # ── Convenience Runners for New Events ─────────────────────────

    def run_subagent_start(self, agent_type: str, task: str) -> HookResult:
        """Run SubagentStart hooks when a sub-agent is spawned."""
        all_hooks = self._get_hooks_for("SubagentStart")
        if not all_hooks:
            return HookResult(allowed=True)
        return self._run_hooks(
            all_hooks, agent_type,
            {"agent_type": agent_type, "task": task},
            event="SubagentStart", result=task,
        )

    def run_subagent_stop(self, agent_type: str, task: str, result: str) -> HookResult:
        """Run SubagentStop hooks when a sub-agent finishes."""
        all_hooks = self._get_hooks_for("SubagentStop")
        if not all_hooks:
            return HookResult(allowed=True)
        return self._run_hooks(
            all_hooks, agent_type,
            {"agent_type": agent_type, "task": task},
            event="SubagentStop", result=result,
        )

    def run_cwd_changed(self, old_cwd: str, new_cwd: str) -> HookResult:
        """Run CwdChanged hooks when working directory changes."""
        all_hooks = self._get_hooks_for("CwdChanged")
        if not all_hooks:
            return HookResult(allowed=True)
        return self._run_hooks(
            all_hooks, "cwd",
            {"old_cwd": old_cwd, "new_cwd": new_cwd},
            event="CwdChanged", result=new_cwd,
        )

    def run_file_changed(self, path: str, change_type: str) -> HookResult:
        """Run FileChanged hooks when a file is modified.

        Args:
            path: File path that changed.
            change_type: One of "write", "edit", "delete".
        """
        all_hooks = self._get_hooks_for("FileChanged")
        if not all_hooks:
            return HookResult(allowed=True)
        return self._run_hooks(
            all_hooks, change_type,
            {"path": path, "change_type": change_type},
            event="FileChanged", result=path,
        )

    def run_context_compacted(self, before_tokens: int, after_tokens: int) -> HookResult:
        """Run ContextCompacted hooks when context window is compacted."""
        all_hooks = self._get_hooks_for("ContextCompacted")
        if not all_hooks:
            return HookResult(allowed=True)
        return self._run_hooks(
            all_hooks, "context",
            {"before_tokens": before_tokens, "after_tokens": after_tokens},
            event="ContextCompacted",
            result=f"{before_tokens} -> {after_tokens}",
        )

    def _matches(self, matcher: str, tool_name: str) -> bool:
        """Check if a matcher pattern matches the tool name (regex-based)."""
        if not tool_name:
            return True  # Empty tool_name matches all (for Stop, Session events)
        try:
            return bool(re.fullmatch(matcher, tool_name))
        except re.error:
            # Fallback to simple pipe-separated or wildcard matching
            patterns = [p.strip() for p in matcher.split("|")]
            return any(
                tool_name == p or
                (p.endswith("*") and tool_name.startswith(p[:-1]))
                for p in patterns
            )

    def _matches_if(self, if_filter: str, tool_name: str, tool_args: dict) -> bool:
        """Check fine-grained if-filter like 'Bash(git *)' or 'Edit(*.py)'."""
        m = re.match(r"(\w+)\((.+)\)", if_filter)
        if not m:
            return True

        filter_tool, pattern = m.group(1), m.group(2)
        if filter_tool != tool_name:
            return False

        # Match pattern against the primary argument
        primary = ""
        if tool_name == "bash":
            primary = tool_args.get("command", "")
        elif tool_name in ("edit_file", "write_file", "read_file"):
            primary = tool_args.get("path", tool_args.get("file_path", ""))
        elif tool_name == "search_files":
            primary = tool_args.get("pattern", "")
        else:
            # Try common arg names
            primary = tool_args.get("command", tool_args.get("path", ""))

        # Convert glob pattern to regex
        regex = pattern.replace(".", r"\.").replace("*", ".*")
        try:
            return bool(re.search(regex, primary))
        except re.error:
            return pattern in primary

    def _run_command_hook(
        self, entry: dict, tool_name: str,
        tool_args: dict, event: str, result: str,
    ) -> HookResult:
        """Run a command-type hook."""
        command = entry.get("command", "")
        timeout = entry.get("timeout", 30)

        if not command:
            return HookResult(allowed=True)

        # Expand environment variables
        command = os.path.expandvars(command)

        # Build stdin payload (JSON)
        payload = json.dumps({
            "hook_event_name": event,
            "tool_name": tool_name,
            "tool_input": tool_args,
            "tool_result": result[:2000] if result else "",
            "cwd": os.getcwd(),
        })

        try:
            proc = subprocess.run(
                command, shell=True,
                input=payload, capture_output=True, text=True,
                timeout=timeout, cwd=os.getcwd(),
            )

            if proc.returncode == 0:
                return self._parse_hook_output(proc.stdout.strip(), event)

            elif proc.returncode == 2:
                msg = proc.stderr.strip() or f"Hook blocked {tool_name}"
                return HookResult(allowed=False, message=msg, decision="deny")

            else:
                # Non-blocking error
                return HookResult(allowed=True, message=f"Hook warning: {proc.stderr.strip()}")

        except subprocess.TimeoutExpired:
            return HookResult(allowed=True, message=f"Hook timed out after {timeout}s")
        except Exception as e:
            return HookResult(allowed=True, message=f"Hook error: {e}")

    def _run_http_hook(
        self, entry: dict, tool_name: str,
        tool_args: dict, event: str, result: str,
    ) -> HookResult:
        """Run an HTTP hook by POSTing event data to the endpoint."""
        url = entry.get("url", "")
        timeout = entry.get("timeout", 30)
        headers = entry.get("headers", {})

        if not url:
            return HookResult(allowed=True)

        # Expand env vars in URL and headers
        url = os.path.expandvars(url)
        headers = {k: os.path.expandvars(v) for k, v in headers.items()}
        headers.setdefault("Content-Type", "application/json")

        payload = {
            "hook_event_name": event,
            "tool_name": tool_name,
            "tool_input": tool_args,
            "tool_result": result[:2000] if result else "",
            "cwd": os.getcwd(),
        }

        try:
            import urllib.request
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers=headers, method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode()
                if resp.status < 300:
                    return self._parse_hook_output(body.strip(), event)
                else:
                    return HookResult(allowed=True, message=f"HTTP hook returned {resp.status}")
        except Exception as e:
            return HookResult(allowed=True, message=f"HTTP hook error: {e}")

    def _parse_hook_output(self, output: str, event: str) -> HookResult:
        """Parse structured JSON output from a hook (command or http)."""
        if not output:
            return HookResult(allowed=True)

        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return HookResult(allowed=True, message=output)

        hr = HookResult(allowed=True)

        # Legacy: direct tool_input modification
        if "tool_input" in data:
            hr.modified_args = data["tool_input"]

        # additionalContext injection
        if "additionalContext" in data:
            hr.additional_context = data["additionalContext"]

        # systemMessage becomes the hook message
        if "systemMessage" in data:
            hr.message = data["systemMessage"]

        # Claude Code style hookSpecificOutput
        specific = data.get("hookSpecificOutput", {})
        if specific:
            decision = specific.get("permissionDecision", specific.get("decision", ""))
            hr.decision = decision

            if decision == "deny" or decision == "block":
                reason = specific.get("permissionDecisionReason", specific.get("reason", "Blocked by hook"))
                hr.allowed = False
                hr.message = reason

            if "updatedInput" in specific:
                hr.modified_args = specific["updatedInput"]

        # continue: false stops everything
        if data.get("continue") is False:
            hr.allowed = False
            hr.message = data.get("stopReason", "Hook requested stop")

        return hr

    # ── Listing & Status ────────────────────────────────────────────

    def list_hooks(self) -> list[dict]:
        """List all configured hooks with event, command, matcher, status."""
        hooks = []
        for event in HOOK_EVENTS:
            for h in self._config.events.get(event, []):
                entries = h.get("hooks", [h])
                if not isinstance(entries, list):
                    entries = [entries]
                for entry in entries:
                    hooks.append({
                        "event": event,
                        "type": entry.get("type", "command"),
                        "command": entry.get("command", entry.get("url", "")),
                        "matcher": h.get("matcher", entry.get("matcher", "")),
                        "if": h.get("if", entry.get("if_filter", "")),
                        "timeout": entry.get("timeout", 30),
                        "enabled": entry.get("enabled", True),
                        "source": h.get("_source", "config"),
                        "status_message": entry.get("statusMessage", entry.get("status_message", "")),
                    })
        # Include skill hooks
        for event, entries in self._active_skill_hooks.items():
            for entry in entries:
                hooks.append({
                    "event": event,
                    "type": entry.get("type", "command"),
                    "command": entry.get("command", ""),
                    "matcher": entry.get("matcher", ""),
                    "enabled": True,
                    "source": "skill",
                })
        return hooks

    @property
    def has_hooks(self) -> bool:
        return any(self._config.events.get(e) for e in HOOK_EVENTS) or bool(self._active_skill_hooks)

    def summary(self) -> dict:
        """Summary of hook counts per event."""
        result = {e: len(self._config.events.get(e, [])) for e in HOOK_EVENTS}
        result["skill_hooks_active"] = bool(self._active_skill_hooks)
        result["total"] = sum(v for k, v in result.items() if k != "skill_hooks_active")
        return result

    # ── Persistence ─────────────────────────────────────────────────

    def save_to_yaml(self, path: Path | None = None):
        """Save current hooks config to a YAML file."""
        if path is None:
            path = Path.cwd() / ".jarvis" / "hooks.yaml"

        data = {}
        for event in HOOK_EVENTS:
            entries = self._config.events.get(event, [])
            if entries:
                # Strip internal metadata
                clean = []
                for h in entries:
                    entry = {k: v for k, v in h.items() if not k.startswith("_")}
                    clean.append(entry)
                data[event] = clean

        if data:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml.dump({"hooks": data}, default_flow_style=False, sort_keys=False))
            log.info("Hooks saved to %s", path)
