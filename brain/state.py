"""
Centralized session state manager for JARVIS.

Single source of truth for all session-scoped state. Every subsystem
(brain, agent loop, shells, hooks, plugins) reads and writes state
through this module instead of maintaining its own globals.

Ported from Claude Code's bootstrap/state.ts architecture.

Usage:
    from brain.state import get_state_manager, get_state, get_session_id

    sm = get_state_manager()
    sm.set("mode", "agent")
    sm.on("mode_changed", lambda old, new: print(f"{old} -> {new}"))

    state = get_state()
    print(state.session_id, state.total_cost_usd)
"""

from __future__ import annotations

import dataclasses
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SessionState dataclass — all session-scoped state lives here
# ---------------------------------------------------------------------------

@dataclass
class SessionState:
    """Global session state -- single source of truth."""

    # ── Session Identity ──────────────────────────────────────────────────
    session_id: str = ""
    parent_session_id: str = ""
    original_cwd: str = ""          # Never changes after startup
    project_root: str = ""          # Stable for history/skills
    cwd: str = ""                   # Current working directory (mutable)
    session_source: str = ""        # "cli", "web", "desktop", "api"
    client_type: str = "cli"        # "cli", "web", "desktop", "sdk"

    # ── Timing ────────────────────────────────────────────────────────────
    start_time: float = 0.0
    last_interaction_time: float = 0.0
    total_api_duration_ms: float = 0.0
    total_tool_duration_ms: float = 0.0

    # ── Turn-Level Metrics (reset per turn) ───────────────────────────────
    turn_tool_duration_ms: float = 0.0
    turn_tool_count: int = 0
    turn_hook_duration_ms: float = 0.0
    turn_hook_count: int = 0
    turn_output_tokens: int = 0
    turn_token_budget: int = 0

    # ── Cumulative Metrics ────────────────────────────────────────────────
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_write_tokens: int = 0
    total_lines_added: int = 0
    total_lines_removed: int = 0
    model_usage: dict = field(default_factory=dict)  # model -> {input, output, cost}

    # ── Model Configuration ───────────────────────────────────────────────
    initial_model: str = ""
    model_override: str = ""        # From --model flag
    fallback_model: str = ""        # From --fallback-model flag
    effort_level: str = "high"      # low, medium, high, max
    thinking_mode: str = "adaptive" # enabled, adaptive, disabled

    # ── Feature Flags ─────────────────────────────────────────────────────
    is_interactive: bool = True
    is_remote_mode: bool = False
    verbose: bool = False
    bare_mode: bool = False         # Skip hooks, plugins, MCP
    debug_mode: bool = False
    debug_filter: str = ""

    # ── Mode State ────────────────────────────────────────────────────────
    mode: str = "normal"            # normal, agent, plan, berbon, cli
    has_exited_plan_mode: bool = False
    needs_plan_exit_attachment: bool = False

    # ── Permission State ──────────────────────────────────────────────────
    permission_mode: str = "default"    # default, bypass, accept_edits, deny_all, plan
    bypass_permissions: bool = False
    session_trust_accepted: bool = False

    # ── Plugin & Hook State ───────────────────────────────────────────────
    registered_hooks: dict = field(default_factory=dict)    # event -> [hooks]
    inline_plugins: list = field(default_factory=list)
    allowed_channels: list = field(default_factory=list)

    # ── System Prompt State ───────────────────────────────────────────────
    system_prompt_override: str = ""
    system_prompt_append: str = ""
    system_prompt_cache: dict = field(default_factory=dict) # section_name -> content

    # ── API Tracking ─────────────────────────────────────────────────────
    last_api_request: dict | None = None
    last_api_messages: list | None = None
    last_request_id: str = ""
    last_api_timestamp: float = 0.0
    prompt_id: str = ""             # UUID correlating prompt with events

    # ── Error Log (circular buffer) ──────────────────────────────────────
    error_log: list = field(default_factory=list)  # [{error, timestamp}], max 100

    # ── Remote/Online State ──────────────────────────────────────────────
    remote_server_url: str = ""
    session_ingress_token: str = ""
    oauth_token: str = ""
    api_key: str = ""

    # ── Multi-Agent State ────────────────────────────────────────────────
    agent_color_map: dict = field(default_factory=dict)     # agent_id -> color
    agent_color_index: int = 0
    created_teams: set = field(default_factory=set)
    main_agent_type: str = ""
    invoked_skills: dict = field(default_factory=dict)      # key -> InvokedSkillInfo

    # ── Scheduled Tasks ──────────────────────────────────────────────────
    session_cron_tasks: list = field(default_factory=list)

    # ── Settings ─────────────────────────────────────────────────────────
    settings_path: str = ""
    allowed_setting_sources: list = field(default_factory=lambda: ["user", "project"])
    session_persistence_disabled: bool = False

    # ── Cache State ──────────────────────────────────────────────────────
    cached_claudemd_content: str = ""
    additional_dirs: list = field(default_factory=list)     # --add-dir


# ---------------------------------------------------------------------------
# StateManager — wraps SessionState with getters, setters, and signals
# ---------------------------------------------------------------------------

class StateManager:
    """Thread-safe(ish) state manager with change-notification signals.

    All mutations should go through ``set()`` or the dedicated helper
    methods so that listeners are notified on change.
    """

    AGENT_COLORS = ["blue", "green", "yellow", "magenta", "cyan", "red", "white"]

    def __init__(self) -> None:
        self._state = SessionState()
        self._listeners: dict[str, list[Callable]] = {}
        self._initialize()

    # ── Bootstrap ─────────────────────────────────────────────────────────

    def _initialize(self) -> None:
        """Set initial state from environment."""
        cwd = os.path.realpath(os.getcwd())
        self._state.session_id = str(uuid.uuid4())
        self._state.original_cwd = cwd
        self._state.project_root = cwd
        self._state.cwd = cwd
        self._state.start_time = time.time()
        self._state.last_interaction_time = time.time()

    # ── Core Access ───────────────────────────────────────────────────────

    @property
    def state(self) -> SessionState:
        return self._state

    def get(self, field_name: str, default: Any = None) -> Any:
        """Read a state field by name."""
        return getattr(self._state, field_name, default)

    def set(self, field_name: str, value: Any) -> None:
        """Write a state field and emit ``<field>_changed`` if it changed."""
        old = getattr(self._state, field_name, None)
        setattr(self._state, field_name, value)
        if old != value:
            self._emit(f"{field_name}_changed", old, value)

    # ── Signals ───────────────────────────────────────────────────────────

    def on(self, event: str, callback: Callable) -> None:
        """Subscribe *callback* to *event*."""
        self._listeners.setdefault(event, []).append(callback)

    def off(self, event: str, callback: Callable) -> None:
        """Unsubscribe *callback* from *event*."""
        if event in self._listeners:
            self._listeners[event] = [
                c for c in self._listeners[event] if c != callback
            ]

    def _emit(self, event: str, *args: Any) -> None:
        for cb in self._listeners.get(event, []):
            try:
                cb(*args)
            except Exception:
                log.debug("Listener for %s raised an exception", event, exc_info=True)

    # ── Session Identity ──────────────────────────────────────────────────

    def regenerate_session_id(self) -> str:
        """Create a brand-new session id and notify listeners."""
        self._state.session_id = str(uuid.uuid4())
        self._emit("session_changed", self._state.session_id)
        return self._state.session_id

    def switch_session(self, session_id: str) -> None:
        """Switch to an existing session (e.g. resume from persistence)."""
        old = self._state.session_id
        self._state.session_id = session_id
        self._emit("session_switched", old, session_id)

    # ── Timing ────────────────────────────────────────────────────────────

    def update_interaction_time(self) -> None:
        self._state.last_interaction_time = time.time()

    def get_total_duration(self) -> float:
        """Wall-clock seconds since session start."""
        return time.time() - self._state.start_time

    # ── Cost & Token Tracking ─────────────────────────────────────────────

    def add_cost(
        self,
        cost: float,
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read: int = 0,
        cache_write: int = 0,
    ) -> None:
        self._state.total_cost_usd += cost
        self._state.total_input_tokens += input_tokens
        self._state.total_output_tokens += output_tokens
        self._state.total_cache_read_tokens += cache_read
        self._state.total_cache_write_tokens += cache_write
        if model:
            if model not in self._state.model_usage:
                self._state.model_usage[model] = {
                    "input": 0,
                    "output": 0,
                    "cost": 0.0,
                }
            self._state.model_usage[model]["input"] += input_tokens
            self._state.model_usage[model]["output"] += output_tokens
            self._state.model_usage[model]["cost"] += cost

    def add_lines_changed(self, added: int = 0, removed: int = 0) -> None:
        self._state.total_lines_added += added
        self._state.total_lines_removed += removed

    # ── Turn-Level ────────────────────────────────────────────────────────

    def reset_turn_metrics(self) -> None:
        """Zero out per-turn counters at the start of each user turn."""
        self._state.turn_tool_duration_ms = 0.0
        self._state.turn_tool_count = 0
        self._state.turn_hook_duration_ms = 0.0
        self._state.turn_hook_count = 0

    def add_tool_duration(self, duration_ms: float) -> None:
        self._state.turn_tool_duration_ms += duration_ms
        self._state.turn_tool_count += 1
        self._state.total_tool_duration_ms += duration_ms

    def add_hook_duration(self, duration_ms: float) -> None:
        self._state.turn_hook_duration_ms += duration_ms
        self._state.turn_hook_count += 1

    def snapshot_turn_tokens(self, budget: int = 0) -> None:
        """Reset turn-level token tracking (called before LLM request)."""
        self._state.turn_output_tokens = 0
        self._state.turn_token_budget = budget

    # ── Mode Transitions ──────────────────────────────────────────────────

    def set_mode(self, new_mode: str) -> None:
        old_mode = self._state.mode
        if old_mode == new_mode:
            return
        # Plan mode exit tracking
        if old_mode == "plan" and new_mode != "plan":
            self._state.has_exited_plan_mode = True
            self._state.needs_plan_exit_attachment = True
        elif new_mode == "plan":
            self._state.needs_plan_exit_attachment = False
        self._state.mode = new_mode
        self._emit("mode_changed", old_mode, new_mode)

    # ── Error Log (circular buffer, max 100) ──────────────────────────────

    def log_error(self, error: str) -> None:
        self._state.error_log.append({
            "error": error,
            "timestamp": datetime.now().isoformat(),
        })
        if len(self._state.error_log) > 100:
            self._state.error_log.pop(0)

    # ── Hook Registration (merge, don't overwrite) ────────────────────────

    def register_hooks(self, hooks: dict) -> None:
        for event, matchers in hooks.items():
            if event not in self._state.registered_hooks:
                self._state.registered_hooks[event] = []
            self._state.registered_hooks[event].extend(matchers)

    def clear_hooks(self) -> None:
        self._state.registered_hooks.clear()

    # ── Multi-Agent ───────────────────────────────────────────────────────

    def assign_agent_color(self, agent_id: str) -> str:
        if agent_id in self._state.agent_color_map:
            return self._state.agent_color_map[agent_id]
        color = self.AGENT_COLORS[
            self._state.agent_color_index % len(self.AGENT_COLORS)
        ]
        self._state.agent_color_map[agent_id] = color
        self._state.agent_color_index += 1
        return color

    # ── Skill Tracking ────────────────────────────────────────────────────

    def add_invoked_skill(
        self,
        skill_name: str,
        skill_path: str = "",
        content: str = "",
        agent_id: str = "",
    ) -> None:
        key = f"{agent_id}:{skill_name}" if agent_id else skill_name
        self._state.invoked_skills[key] = {
            "skill_name": skill_name,
            "skill_path": skill_path,
            "content": content,
            "invoked_at": time.time(),
            "agent_id": agent_id,
        }

    def get_skills_for_agent(self, agent_id: str = "") -> list[dict]:
        prefix = f"{agent_id}:" if agent_id else ""
        return [
            v
            for k, v in self._state.invoked_skills.items()
            if k.startswith(prefix)
        ]

    # ── Cron Tasks ────────────────────────────────────────────────────────

    def add_cron_task(self, task: dict) -> None:
        self._state.session_cron_tasks.append(task)

    def remove_cron_tasks(self, ids: set[str]) -> int:
        """Remove tasks by id set. Returns count of tasks removed."""
        before = len(self._state.session_cron_tasks)
        self._state.session_cron_tasks = [
            t for t in self._state.session_cron_tasks if t.get("id") not in ids
        ]
        return before - len(self._state.session_cron_tasks)

    # ── Cost Restoration ──────────────────────────────────────────────────

    def restore_cost_state(
        self,
        total_cost: float = 0,
        model_usage: dict | None = None,
        total_input: int = 0,
        total_output: int = 0,
        last_duration: float = 0,
    ) -> None:
        """Restore cost/token state from a persisted session."""
        self._state.total_cost_usd = total_cost
        self._state.total_input_tokens = total_input
        self._state.total_output_tokens = total_output
        if model_usage:
            self._state.model_usage = model_usage
        if last_duration > 0:
            self._state.start_time = time.time() - last_duration

    def reset_cost_state(self) -> None:
        """Zero out all cost/token accumulators (e.g. ``/reset``)."""
        self._state.total_cost_usd = 0.0
        self._state.total_input_tokens = 0
        self._state.total_output_tokens = 0
        self._state.total_cache_read_tokens = 0
        self._state.total_cache_write_tokens = 0
        self._state.total_lines_added = 0
        self._state.total_lines_removed = 0
        self._state.model_usage.clear()
        self._state.start_time = time.time()

    # ── Summary ───────────────────────────────────────────────────────────

    def get_summary(self) -> dict:
        """Return a snapshot dict suitable for /status or API responses."""
        s = self._state
        return {
            "session_id": s.session_id,
            "duration": time.time() - s.start_time,
            "mode": s.mode,
            "model": s.model_override or s.initial_model,
            "cost_usd": s.total_cost_usd,
            "input_tokens": s.total_input_tokens,
            "output_tokens": s.total_output_tokens,
            "lines_added": s.total_lines_added,
            "lines_removed": s.total_lines_removed,
            "tool_calls": s.turn_tool_count,
            "errors": len(s.error_log),
            "is_remote": s.is_remote_mode,
            "client_type": s.client_type,
        }

    # ── Serialization ─────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize state for persistence or API transport."""
        d = dataclasses.asdict(self._state)
        # Convert set to list for JSON compatibility
        d["created_teams"] = list(d.get("created_teams", []))
        return d

    def from_dict(self, data: dict) -> None:
        """Restore state from a previously serialized dict."""
        for k, v in data.items():
            if hasattr(self._state, k):
                if k == "created_teams":
                    v = set(v) if isinstance(v, list) else v
                setattr(self._state, k, v)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_state_manager: StateManager | None = None


def get_state_manager() -> StateManager:
    """Return (and lazily create) the global StateManager singleton."""
    global _state_manager
    if _state_manager is None:
        _state_manager = StateManager()
    return _state_manager


# Convenience shortcuts
def get_state() -> SessionState:
    """Shortcut to the current SessionState dataclass."""
    return get_state_manager().state


def get_session_id() -> str:
    """Shortcut to the current session id."""
    return get_state_manager().state.session_id
