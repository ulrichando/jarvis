"""JARVIS Hooks System — deterministic quality gates around tool execution.

Inspired by Claude Code's hooks architecture:
- PreToolUse: Validate/block/modify tool calls before execution
- PostToolUse: Run quality checks after tool execution (lint, format, test)
- Stop: Final verification before JARVIS considers task complete

Hooks are defined in:
- ~/.jarvis/hooks.yaml (user-level)
- .jarvis/hooks.yaml (project-level)
- Skill frontmatter (skill-scoped)

Hook types:
- command: Run a shell command (exit 0=allow, exit 2=block)
- prompt: Ask JARVIS to evaluate (returns yes/no)
"""

import os
import json
import subprocess
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from brain.config import JARVIS_HOME


@dataclass
class Hook:
    """A single hook definition."""
    type: str = "command"       # "command" or "prompt"
    command: str = ""           # Shell command to run
    prompt: str = ""            # LLM prompt for type=prompt
    timeout: int = 30           # Timeout in seconds
    matcher: str = ""           # Tool name pattern to match (e.g. "Bash", "Edit|Write")


@dataclass
class HookResult:
    """Result of running a hook."""
    allowed: bool = True
    message: str = ""
    modified_args: dict | None = None  # If hook wants to modify tool args


@dataclass
class HookConfig:
    """All hooks for a lifecycle event."""
    pre_tool_use: list[dict] = field(default_factory=list)
    post_tool_use: list[dict] = field(default_factory=list)
    stop: list[dict] = field(default_factory=list)


class HooksManager:
    """Manages and executes hooks around tool calls."""

    def __init__(self):
        self._config = HookConfig()
        self._active_skill_hooks: dict = {}  # Temporary skill-scoped hooks

    def load(self):
        """Load hooks from user and project config files."""
        # User-level hooks
        user_hooks = JARVIS_HOME / "hooks.yaml"
        if user_hooks.exists():
            self._merge_config(user_hooks)

        # Project-level hooks
        project_hooks = Path.cwd() / ".jarvis" / "hooks.yaml"
        if project_hooks.exists():
            self._merge_config(project_hooks)

    def _merge_config(self, path: Path):
        """Load and merge hooks from a YAML file."""
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except Exception:
            return

        hooks = data.get("hooks", data)  # Support both {hooks: {...}} and flat format
        if "PreToolUse" in hooks:
            self._config.pre_tool_use.extend(hooks["PreToolUse"])
        if "PostToolUse" in hooks:
            self._config.post_tool_use.extend(hooks["PostToolUse"])
        if "Stop" in hooks:
            self._config.stop.extend(hooks["Stop"])

    def set_skill_hooks(self, hooks: dict):
        """Temporarily activate skill-scoped hooks."""
        self._active_skill_hooks = hooks

    def clear_skill_hooks(self):
        """Deactivate skill-scoped hooks."""
        self._active_skill_hooks = {}

    # ── Hook Execution ──────────────────────────────────────────────

    def run_pre_tool_use(self, tool_name: str, tool_args: dict) -> HookResult:
        """Run PreToolUse hooks before a tool executes.

        Returns:
            HookResult with allowed=False to block, or modified_args to change input.
        """
        all_hooks = self._config.pre_tool_use + self._active_skill_hooks.get("PreToolUse", [])
        return self._run_hooks(all_hooks, tool_name, tool_args, event="PreToolUse")

    def run_post_tool_use(self, tool_name: str, tool_args: dict, result: str) -> HookResult:
        """Run PostToolUse hooks after a tool executes."""
        all_hooks = self._config.post_tool_use + self._active_skill_hooks.get("PostToolUse", [])
        return self._run_hooks(all_hooks, tool_name, tool_args, event="PostToolUse", result=result)

    def run_stop(self) -> HookResult:
        """Run Stop hooks before JARVIS finishes a task."""
        all_hooks = self._config.stop + self._active_skill_hooks.get("Stop", [])
        if not all_hooks:
            return HookResult(allowed=True)
        return self._run_hooks(all_hooks, "", {}, event="Stop")

    def _run_hooks(
        self, hooks: list[dict], tool_name: str,
        tool_args: dict, event: str, result: str = "",
    ) -> HookResult:
        """Execute a list of hook definitions."""
        for hook_def in hooks:
            # Check matcher
            matcher = hook_def.get("matcher", "")
            if matcher and not self._matches(matcher, tool_name):
                continue

            # Get the actual hook entries
            hook_entries = hook_def.get("hooks", [hook_def])
            if not isinstance(hook_entries, list):
                hook_entries = [hook_entries]

            for entry in hook_entries:
                hook_type = entry.get("type", "command")

                if hook_type == "command":
                    hr = self._run_command_hook(entry, tool_name, tool_args, event, result)
                    if not hr.allowed:
                        return hr
                    if hr.modified_args is not None:
                        tool_args = hr.modified_args

        return HookResult(allowed=True)

    def _matches(self, matcher: str, tool_name: str) -> bool:
        """Check if a matcher pattern matches the tool name."""
        # Support "Edit|Write" style patterns
        patterns = [p.strip() for p in matcher.split("|")]
        return any(
            tool_name == p or tool_name.startswith(p.rstrip("*"))
            for p in patterns
        )

    def _run_command_hook(
        self, entry: dict, tool_name: str,
        tool_args: dict, event: str, result: str,
    ) -> HookResult:
        """Run a command-type hook."""
        command = entry.get("command", "")
        timeout = entry.get("timeout", 30)

        if not command:
            return HookResult(allowed=True)

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
                # Success — check if hook returned modified args
                if proc.stdout.strip():
                    try:
                        data = json.loads(proc.stdout)
                        if "tool_input" in data:
                            return HookResult(allowed=True, modified_args=data["tool_input"])
                    except json.JSONDecodeError:
                        pass
                return HookResult(allowed=True, message=proc.stdout.strip())

            elif proc.returncode == 2:
                # Blocked
                msg = proc.stderr.strip() or f"Hook blocked {tool_name}"
                return HookResult(allowed=False, message=msg)

            else:
                # Non-blocking error — log but allow
                return HookResult(allowed=True, message=f"Hook warning: {proc.stderr.strip()}")

        except subprocess.TimeoutExpired:
            return HookResult(allowed=True, message=f"Hook timed out after {timeout}s")
        except Exception as e:
            return HookResult(allowed=True, message=f"Hook error: {e}")

    # ── Status ──────────────────────────────────────────────────────

    @property
    def has_hooks(self) -> bool:
        return bool(
            self._config.pre_tool_use
            or self._config.post_tool_use
            or self._config.stop
            or self._active_skill_hooks
        )

    def summary(self) -> dict:
        return {
            "pre_tool_use": len(self._config.pre_tool_use),
            "post_tool_use": len(self._config.post_tool_use),
            "stop": len(self._config.stop),
            "skill_hooks_active": bool(self._active_skill_hooks),
        }
