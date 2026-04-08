"""HooksManager — lifecycle hooks for tool execution, sessions, and events."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("jarvis.hooks")

HOOK_EVENTS = (
    # ── Tool lifecycle ────────────────────────────────────────────────
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionDenied",
    # ── LLM lifecycle ─────────────────────────────────────────────────
    "BeforeModelResolve",   # before LLM provider is selected
    "BeforePromptBuild",    # before system prompt is assembled
    "LLMInput",             # messages about to be sent to the LLM (can modify)
    "LLMOutput",            # raw LLM response received (can modify)
    # ── Agent loop lifecycle ──────────────────────────────────────────
    "AgentStart",           # agent loop begins (first iteration)
    "AgentEnd",             # agent loop ends (no more tool_calls)
    # ── Context compaction ────────────────────────────────────────────
    "CompactionStart",      # context compaction is about to happen
    "CompactionEnd",        # compaction complete (before/after token counts)
    "ContextCompacted",     # alias kept for back-compat
    # ── Memory ───────────────────────────────────────────────────────
    "MemoryRead",           # memory is about to be retrieved
    "MemoryWrite",          # memory is about to be stored
    # ── Plugin / skill lifecycle ─────────────────────────────────────
    "PluginLoad",           # a plugin file has been loaded
    "PluginUnload",         # a plugin is being unloaded
    "SkillInvoke",          # a skill is about to execute
    "SkillComplete",        # a skill has finished executing
    # ── Session / notification ────────────────────────────────────────
    "Notification", "Stop", "SessionStart", "SessionEnd",
    # ── Sub-agent ─────────────────────────────────────────────────────
    "SubagentStart", "SubagentStop",
    # ── Environment ──────────────────────────────────────────────────
    "CwdChanged", "FileChanged",
)

BLOCKING_EVENTS = {
    "PreToolUse", "PermissionDenied", "Stop",
    "SubagentStart", "SubagentStop",
    "LLMInput",         # hooks can veto/rewrite the prompt before it is sent
    "AgentStart",       # hooks can abort the agent loop before it begins
    "SkillInvoke",      # hooks can block a skill from running
    "MemoryWrite",      # hooks can block a memory write
}

_EVENT_ALIASES = {
    # Tool lifecycle
    "pre_tool_use": "PreToolUse", "pre_command": "PreToolUse",
    "post_tool_use": "PostToolUse", "post_command": "PostToolUse",
    "on_error": "PostToolUseFailure", "post_tool_use_failure": "PostToolUseFailure",
    "permission_denied": "PermissionDenied",
    # LLM lifecycle
    "before_model_resolve": "BeforeModelResolve",
    "before_prompt_build": "BeforePromptBuild",
    "llm_input": "LLMInput", "llm_request": "LLMInput",
    "llm_output": "LLMOutput", "llm_response": "LLMOutput",
    # Agent loop
    "agent_start": "AgentStart", "agent_begin": "AgentStart",
    "agent_end": "AgentEnd", "agent_done": "AgentEnd",
    # Compaction
    "compaction_start": "CompactionStart",
    "compaction_end": "CompactionEnd",
    "context_compacted": "ContextCompacted",
    # Memory
    "memory_read": "MemoryRead",
    "memory_write": "MemoryWrite", "memory_store": "MemoryWrite",
    # Plugin / skill
    "plugin_load": "PluginLoad",
    "plugin_unload": "PluginUnload",
    "skill_invoke": "SkillInvoke", "skill_start": "SkillInvoke",
    "skill_complete": "SkillComplete", "skill_end": "SkillComplete",
    # Session
    "notification": "Notification",
    "stop": "Stop",
    "session_start": "SessionStart", "on_startup": "SessionStart",
    "session_end": "SessionEnd", "on_shutdown": "SessionEnd",
    # Sub-agent
    "subagent_start": "SubagentStart", "subagent_stop": "SubagentStop",
    # Environment
    "cwd_changed": "CwdChanged", "file_changed": "FileChanged",
}


@dataclass
class HookResult:
    allowed: bool = True
    message: str = ""
    modified_args: Optional[dict] = None
    additional_context: str = ""
    decision: str = ""


@dataclass
class HookConfig:
    events: dict[str, list[dict]] = field(
        default_factory=lambda: {e: [] for e in HOOK_EVENTS}
    )


class HooksManager:

    def __init__(self):
        self._config = HookConfig()
        self._active_skill_hooks: dict[str, list[dict]] = {}
        self._runtime_hooks: list[dict] = []
        self.rules: list = []

    def load(self) -> None:
        for path in [Path.home() / ".jarvis" / "hooks.yaml", Path(".jarvis") / "hooks.yaml"]:
            if path.exists():
                self._load_yaml(path)
        settings = Path(".jarvis") / "settings.json"
        if settings.exists():
            try:
                data = json.loads(settings.read_text())
                for event in HOOK_EVENTS:
                    entries = data.get("hooks", {}).get(event, [])
                    if isinstance(entries, list):
                        self._config.events[event].extend(entries)
            except Exception:
                pass

    def _load_yaml(self, path: Path) -> None:
        try:
            import yaml
            data = yaml.safe_load(path.read_text()) or {}
            hooks_data = data.get("hooks", data) if isinstance(data, dict) else {}
            if not isinstance(hooks_data, dict):
                return
            for event in HOOK_EVENTS:
                entries = hooks_data.get(event, [])
                if isinstance(entries, list):
                    self._config.events[event].extend(entries)
        except Exception as e:
            log.warning("Failed to load hooks from %s: %s", path, e)

    def set_skill_hooks(self, hooks: dict) -> None:
        self._active_skill_hooks = hooks or {}

    def clear_skill_hooks(self) -> None:
        self._active_skill_hooks = {}

    def _normalize_event(self, event: str) -> str:
        if event in HOOK_EVENTS:
            return event
        return _EVENT_ALIASES.get(event.lower().replace("-", "_"), event)

    def add_hook(self, event: str, command: str, matcher: str = "",
                 hook_type: str = "command", timeout: int = 30) -> bool:
        event = self._normalize_event(event)
        if event not in HOOK_EVENTS:
            return False
        entry = {"type": hook_type, "command": command if isinstance(command, str) else str(command),
                 "matcher": matcher, "timeout": timeout, "_source": "runtime"}
        self._config.events[event].append(entry)
        self._runtime_hooks.append({"event": event, **entry})
        return True

    def remove_hook(self, event: str, command: str = "") -> bool:
        event = self._normalize_event(event)
        if event not in HOOK_EVENTS:
            return False
        hooks = self._config.events.get(event, [])
        if not hooks:
            return False
        if not command:
            self._config.events[event] = []
            return True
        before = len(hooks)
        self._config.events[event] = [h for h in hooks if h.get("command") != command]
        return len(self._config.events[event]) < before

    def _get_hooks(self, event: str) -> list[dict]:
        raw = list(self._config.events.get(event, []))
        raw.extend(self._active_skill_hooks.get(event, []))
        # Expand nested hooks format: {matcher: "...", hooks: [{...}, {...}]}
        hooks = []
        for entry in raw:
            if "hooks" in entry and isinstance(entry["hooks"], list):
                matcher = entry.get("matcher", "")
                for sub in entry["hooks"]:
                    expanded = {**sub, "matcher": sub.get("matcher", matcher)}
                    hooks.append(expanded)
            else:
                hooks.append(entry)
        return [h for h in hooks if h.get("enabled", True)]

    def _matches(self, matcher: str, tool_name: str) -> bool:
        if not matcher or not tool_name:
            return True
        try:
            return bool(re.search(matcher, tool_name))
        except re.error:
            return matcher in tool_name

    def _matches_if(self, if_filter: str, tool_name: str, tool_args: dict) -> bool:
        """Fine-grained if-filter: 'bash(git *)' matches bash with git commands."""
        if not if_filter:
            return True
        m = re.match(r'^(\w+)\((.+)\)$', if_filter)
        if not m:
            return self._matches(if_filter, tool_name)
        filter_tool, pattern = m.group(1), m.group(2)
        if filter_tool != tool_name:
            return False
        # Match pattern against common arg fields
        for key in ("command", "path", "file_path", "query", "task", "text"):
            val = tool_args.get(key, "")
            if val:
                glob_re = pattern.replace("*", ".*")
                try:
                    if re.search(glob_re, str(val)):
                        return True
                except re.error:
                    if pattern in str(val):
                        return True
        return False

    def _run_command_hook(self, entry: dict, tool_name: str, tool_args: dict,
                          event: str, result: str) -> HookResult:
        command = entry.get("command", "")
        if not command:
            return HookResult()
        payload = json.dumps({"hook_event_name": event, "tool_name": tool_name,
                              "tool_input": tool_args, "tool_result": result, "cwd": os.getcwd()})
        try:
            # Use shlex.split to avoid shell=True injection risks.
            # Falls back to shell=True only if the command contains shell
            # operators (pipes, redirects) that require a shell.
            use_shell = any(c in command for c in ('|', '>', '<', '&&', '||', ';', '`', '$'))
            cmd_arg = command if use_shell else shlex.split(command)
            proc = subprocess.run(cmd_arg, shell=use_shell, input=payload,
                                  capture_output=True, text=True, timeout=entry.get("timeout", 30))
            if proc.returncode == 0:
                return self._parse_output(proc.stdout, event)
            elif proc.returncode == 2:
                return HookResult(allowed=False, message=proc.stdout.strip() or "Blocked by hook", decision="block")
            return HookResult()
        except subprocess.TimeoutExpired:
            log.warning("Hook timed out: %s", command)
            return HookResult(message=f"Hook timed out: {command}")
        except Exception as e:
            log.warning("Hook error: %s", e)
            return HookResult()

    def _parse_output(self, output: str, event: str) -> HookResult:
        if not output.strip():
            return HookResult()
        try:
            data = json.loads(output)
            r = HookResult()
            if "tool_input" in data:
                r.modified_args = data["tool_input"]
            if "additionalContext" in data:
                r.additional_context = data["additionalContext"]
            if "systemMessage" in data:
                r.message = data["systemMessage"]
            if data.get("continue") is False:
                r.allowed = False
                r.decision = "block"
            hso = data.get("hookSpecificOutput", {})
            if hso.get("permissionDecision") == "deny":
                r.allowed = False
                r.decision = "deny"
                reason = hso.get("permissionDecisionReason", "")
                if reason:
                    r.message = reason
            if "updatedInput" in hso:
                r.modified_args = hso["updatedInput"]
            return r
        except json.JSONDecodeError:
            return HookResult(message=output.strip())

    def _run_event(self, event: str, tool_name: str = "",
                   tool_args: dict | None = None, result: str = "") -> HookResult:
        hooks = self._get_hooks(event)
        if not hooks:
            return HookResult()
        args = tool_args or {}
        combined = HookResult()
        for entry in hooks:
            if not self._matches(entry.get("matcher", ""), tool_name):
                continue
            if_filter = entry.get("if", "")
            if if_filter and not self._matches_if(if_filter, tool_name, args):
                continue
            hook_type = entry.get("type", "command")
            if hook_type == "command":
                hr = self._run_command_hook(entry, tool_name, args, event, result)
            else:
                continue
            if not hr.allowed:
                return hr
            if hr.modified_args:
                args = hr.modified_args
                combined.modified_args = args
            if hr.message:
                combined.message = hr.message
            if hr.additional_context:
                combined.additional_context = hr.additional_context
            if entry.get("once"):
                entry["enabled"] = False
        return combined

    # ── Public event runners ──

    def run_pre_tool_use(self, tool_name: str, tool_args: dict) -> HookResult:
        return self._run_event("PreToolUse", tool_name, tool_args)

    def run_post_tool_use(self, tool_name: str, tool_args: dict, result: str) -> HookResult:
        return self._run_event("PostToolUse", tool_name, tool_args, result)

    def run_post_tool_use_failure(self, tool_name: str, tool_args: dict, error: str) -> HookResult:
        return self._run_event("PostToolUseFailure", tool_name, tool_args, error)

    def run_permission_denied(self, tool_name: str, tool_args: dict, reason: str) -> HookResult:
        return self._run_event("PermissionDenied", tool_name, tool_args, reason)

    def run_notification(self, message: str, notification_type: str = "") -> HookResult:
        return self._run_event("Notification", notification_type, {"message": message})

    def run_stop(self) -> HookResult:
        return self._run_event("Stop")

    def run_session_start(self) -> HookResult:
        return self._run_event("SessionStart")

    def run_session_end(self) -> HookResult:
        return self._run_event("SessionEnd")

    def run_subagent_start(self, agent_type: str, task: str) -> HookResult:
        return self._run_event("SubagentStart", agent_type, {"task": task})

    def run_subagent_stop(self, agent_type: str, task: str, result: str) -> HookResult:
        return self._run_event("SubagentStop", agent_type, {"task": task}, result)

    def run_cwd_changed(self, old_cwd: str, new_cwd: str) -> HookResult:
        return self._run_event("CwdChanged", "", {"old_cwd": old_cwd, "new_cwd": new_cwd})

    def run_file_changed(self, path: str, change_type: str) -> HookResult:
        return self._run_event("FileChanged", change_type, {"path": path})

    def run_context_compacted(self, before_tokens: int, after_tokens: int) -> HookResult:
        return self._run_event("ContextCompacted", "", {"before_tokens": before_tokens, "after_tokens": after_tokens})

    def run_before_model_resolve(self, provider_hints: dict) -> HookResult:
        return self._run_event("BeforeModelResolve", "", provider_hints)

    def run_before_prompt_build(self, context: dict) -> HookResult:
        return self._run_event("BeforePromptBuild", "", context)

    def run_llm_input(self, messages: list, model: str = "") -> HookResult:
        return self._run_event("LLMInput", model, {"messages": messages})

    def run_llm_output(self, response: str, model: str = "") -> HookResult:
        return self._run_event("LLMOutput", model, {"response": response})

    def run_agent_start(self, task: str = "") -> HookResult:
        return self._run_event("AgentStart", "", {"task": task})

    def run_agent_end(self, task: str = "", result: str = "") -> HookResult:
        return self._run_event("AgentEnd", "", {"task": task}, result)

    def run_compaction_start(self, before_tokens: int) -> HookResult:
        return self._run_event("CompactionStart", "", {"before_tokens": before_tokens})

    def run_compaction_end(self, before_tokens: int, after_tokens: int) -> HookResult:
        return self._run_event("CompactionEnd", "", {"before_tokens": before_tokens, "after_tokens": after_tokens})

    def run_memory_read(self, query: str, session_id: str = "") -> HookResult:
        return self._run_event("MemoryRead", "", {"query": query, "session_id": session_id})

    def run_memory_write(self, content: str, metadata: dict | None = None) -> HookResult:
        return self._run_event("MemoryWrite", "", {"content": content, "metadata": metadata or {}})

    def run_plugin_load(self, plugin_id: str, path: str = "") -> HookResult:
        return self._run_event("PluginLoad", plugin_id, {"path": path})

    def run_plugin_unload(self, plugin_id: str) -> HookResult:
        return self._run_event("PluginUnload", plugin_id, {})

    def run_skill_invoke(self, skill_name: str, args: str = "") -> HookResult:
        return self._run_event("SkillInvoke", skill_name, {"args": args})

    def run_skill_complete(self, skill_name: str, result: str = "") -> HookResult:
        return self._run_event("SkillComplete", skill_name, {}, result)

    async def run_pre_hooks(self, tool_name: str, tool_input: dict) -> dict:
        hr = await asyncio.to_thread(self.run_pre_tool_use, tool_name, tool_input)
        return hr.modified_args if hr.modified_args else tool_input

    async def run_post_hooks(self, tool_name: str, result: str) -> str:
        hr = await asyncio.to_thread(self.run_post_tool_use, tool_name, {}, result)
        # Return hook-modified result if the hook produced one, else original
        return hr.modified_args.get("result", result) if hr.modified_args else result

    # ── Introspection ──

    def list_hooks(self) -> list[dict]:
        result = []
        for event in HOOK_EVENTS:
            for entry in self._config.events.get(event, []):
                result.append({"event": event, "type": entry.get("type", "command"),
                               "command": entry.get("command", ""), "matcher": entry.get("matcher", ""),
                               "timeout": entry.get("timeout", 30), "enabled": entry.get("enabled", True),
                               "source": entry.get("_source", "config")})
        return result

    @property
    def has_hooks(self) -> bool:
        return any(self._config.events.get(e) for e in HOOK_EVENTS) or bool(self._active_skill_hooks)

    def summary(self) -> dict:
        r = {e: len(self._config.events.get(e, [])) for e in HOOK_EVENTS}
        r["total"] = sum(r.values())
        r["skill_hooks_active"] = bool(self._active_skill_hooks)
        return r

    def save_to_yaml(self, path: Path | None = None) -> None:
        if path is None:
            path = Path(".jarvis") / "hooks.yaml"
        try:
            import yaml
            data: dict[str, Any] = {}
            for event in HOOK_EVENTS:
                cleaned = [{k: v for k, v in h.items() if not k.startswith("_")}
                           for h in self._config.events.get(event, [])]
                if cleaned:
                    data[event] = cleaned
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml.dump({"hooks": data}, default_flow_style=False))
        except Exception as e:
            log.error("Failed to save hooks: %s", e)
