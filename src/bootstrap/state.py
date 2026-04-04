"""Global application state management.

Centralized state store for session tracking, cost accounting,
telemetry counters, and configuration flags.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    web_search_requests: int = 0


@dataclass
class AttributedCounter:
    _value: float = 0.0

    def add(self, value: float, additional_attributes: Optional[dict] = None) -> None:
        self._value += value


@dataclass
class SessionCronTask:
    name: str
    schedule: str
    command: str


def _resolve_cwd() -> str:
    try:
        return str(Path.cwd().resolve())
    except Exception:
        return os.getcwd()


@dataclass
class State:
    original_cwd: str = ""
    project_root: str = ""
    total_cost_usd: float = 0.0
    total_api_duration: float = 0.0
    total_api_duration_without_retries: float = 0.0
    total_tool_duration: float = 0.0
    turn_hook_duration_ms: float = 0.0
    turn_tool_duration_ms: float = 0.0
    turn_classifier_duration_ms: float = 0.0
    turn_tool_count: int = 0
    turn_hook_count: int = 0
    turn_classifier_count: int = 0
    start_time: float = 0.0
    last_interaction_time: float = 0.0
    total_lines_added: int = 0
    total_lines_removed: int = 0
    has_unknown_model_cost: bool = False
    cwd: str = ""
    model_usage: dict[str, ModelUsage] = field(default_factory=dict)
    main_loop_model_override: Optional[str] = None
    initial_main_loop_model: Optional[str] = None
    model_strings: Optional[dict] = None
    is_interactive: bool = False
    kairos_active: bool = False
    strict_tool_result_pairing: bool = False
    sdk_agent_progress_summaries_enabled: bool = False
    user_msg_opt_in: bool = False
    client_type: str = "cli"
    session_source: Optional[str] = None
    question_preview_format: Optional[str] = None
    flag_settings_path: Optional[str] = None
    flag_settings_inline: Optional[dict] = None
    allowed_setting_sources: list[str] = field(
        default_factory=lambda: ["userSettings", "projectSettings", "localSettings", "flagSettings", "policySettings"]
    )
    session_ingress_token: Optional[str] = None
    oauth_token_from_fd: Optional[str] = None
    api_key_from_fd: Optional[str] = None
    session_id: str = ""
    parent_session_id: Optional[str] = None
    agent_color_map: dict[str, str] = field(default_factory=dict)
    agent_color_index: int = 0
    last_api_request: Optional[dict] = None
    last_api_request_messages: Optional[list] = None
    last_classifier_requests: Optional[list] = None
    cached_claude_md_content: Optional[str] = None
    in_memory_error_log: list[dict] = field(default_factory=list)
    inline_plugins: list[str] = field(default_factory=list)
    chrome_flag_override: Optional[bool] = None
    use_cowork_plugins: bool = False
    session_bypass_permissions_mode: bool = False
    scheduled_tasks_enabled: bool = False
    session_cron_tasks: list[SessionCronTask] = field(default_factory=list)
    session_created_teams: set[str] = field(default_factory=set)
    session_trust_accepted: bool = False
    session_persistence_disabled: bool = False
    has_exited_plan_mode: bool = False
    needs_plan_mode_exit_attachment: bool = False
    needs_auto_mode_exit_attachment: bool = False
    lsp_recommendation_shown_this_session: bool = False
    init_json_schema: Optional[dict] = None
    registered_hooks: Optional[dict] = None
    plan_slug_cache: dict[str, str] = field(default_factory=dict)
    teleported_session_info: Optional[dict] = None
    invoked_skills: dict[str, dict] = field(default_factory=dict)
    slow_operations: list[dict] = field(default_factory=list)
    sdk_betas: Optional[list[str]] = None
    main_thread_agent_type: Optional[str] = None
    is_remote_mode: bool = False
    direct_connect_server_url: Optional[str] = None
    system_prompt_section_cache: dict[str, Optional[str]] = field(default_factory=dict)
    last_emitted_date: Optional[str] = None
    additional_directories_for_claude_md: list[str] = field(default_factory=list)
    allowed_channels: list[dict] = field(default_factory=list)
    has_dev_channels: bool = False
    session_project_dir: Optional[str] = None
    prompt_cache_1h_allowlist: Optional[list[str]] = None
    prompt_cache_1h_eligible: Optional[bool] = None
    afk_mode_header_latched: Optional[bool] = None
    fast_mode_header_latched: Optional[bool] = None
    cache_editing_header_latched: Optional[bool] = None
    thinking_clear_latched: Optional[bool] = None
    prompt_id: Optional[str] = None
    last_main_request_id: Optional[str] = None
    last_api_completion_timestamp: Optional[float] = None
    pending_post_compaction: bool = False


def _get_initial_state() -> State:
    resolved_cwd = _resolve_cwd()
    return State(
        original_cwd=resolved_cwd,
        project_root=resolved_cwd,
        cwd=resolved_cwd,
        start_time=time.time() * 1000,
        last_interaction_time=time.time() * 1000,
        session_id=str(uuid.uuid4()),
    )


_STATE = _get_initial_state()
_session_switch_callbacks: list[Callable] = []
_interaction_time_dirty = False
_output_tokens_at_turn_start = 0
_current_turn_token_budget: Optional[int] = None
_budget_continuation_count = 0


def get_session_id() -> str:
    return _STATE.session_id


def regenerate_session_id(set_current_as_parent: bool = False) -> str:
    global _STATE
    if set_current_as_parent:
        _STATE.parent_session_id = _STATE.session_id
    _STATE.plan_slug_cache.pop(_STATE.session_id, None)
    _STATE.session_id = str(uuid.uuid4())
    _STATE.session_project_dir = None
    return _STATE.session_id


def get_parent_session_id() -> Optional[str]:
    return _STATE.parent_session_id


def switch_session(session_id: str, project_dir: Optional[str] = None) -> None:
    global _STATE
    _STATE.plan_slug_cache.pop(_STATE.session_id, None)
    _STATE.session_id = session_id
    _STATE.session_project_dir = project_dir
    for cb in _session_switch_callbacks:
        cb(session_id)


def on_session_switch(callback: Callable) -> None:
    _session_switch_callbacks.append(callback)


def get_session_project_dir() -> Optional[str]:
    return _STATE.session_project_dir


def get_original_cwd() -> str:
    return _STATE.original_cwd


def get_project_root() -> str:
    return _STATE.project_root


def set_original_cwd(cwd: str) -> None:
    _STATE.original_cwd = cwd


def set_project_root(cwd: str) -> None:
    _STATE.project_root = cwd


def get_cwd_state() -> str:
    return _STATE.cwd


def set_cwd_state(cwd: str) -> None:
    _STATE.cwd = cwd


def add_to_total_duration_state(duration: float, duration_without_retries: float) -> None:
    _STATE.total_api_duration += duration
    _STATE.total_api_duration_without_retries += duration_without_retries


def add_to_total_cost_state(cost: float, usage: ModelUsage, model: str) -> None:
    _STATE.model_usage[model] = usage
    _STATE.total_cost_usd += cost


def get_total_cost_usd() -> float:
    return _STATE.total_cost_usd


def get_total_api_duration() -> float:
    return _STATE.total_api_duration


def get_total_duration() -> float:
    return time.time() * 1000 - _STATE.start_time


def get_total_tool_duration() -> float:
    return _STATE.total_tool_duration


def add_to_tool_duration(duration: float) -> None:
    _STATE.total_tool_duration += duration
    _STATE.turn_tool_duration_ms += duration
    _STATE.turn_tool_count += 1


def get_turn_hook_duration_ms() -> float:
    return _STATE.turn_hook_duration_ms


def add_to_turn_hook_duration(duration: float) -> None:
    _STATE.turn_hook_duration_ms += duration
    _STATE.turn_hook_count += 1


def reset_turn_hook_duration() -> None:
    _STATE.turn_hook_duration_ms = 0
    _STATE.turn_hook_count = 0


def get_turn_tool_duration_ms() -> float:
    return _STATE.turn_tool_duration_ms


def reset_turn_tool_duration() -> None:
    _STATE.turn_tool_duration_ms = 0
    _STATE.turn_tool_count = 0


def add_to_turn_classifier_duration(duration: float) -> None:
    _STATE.turn_classifier_duration_ms += duration
    _STATE.turn_classifier_count += 1


def reset_turn_classifier_duration() -> None:
    _STATE.turn_classifier_duration_ms = 0
    _STATE.turn_classifier_count = 0


def update_last_interaction_time(immediate: bool = False) -> None:
    global _interaction_time_dirty
    if immediate:
        _STATE.last_interaction_time = time.time() * 1000
        _interaction_time_dirty = False
    else:
        _interaction_time_dirty = True


def flush_interaction_time() -> None:
    global _interaction_time_dirty
    if _interaction_time_dirty:
        _STATE.last_interaction_time = time.time() * 1000
        _interaction_time_dirty = False


def add_to_total_lines_changed(added: int, removed: int) -> None:
    _STATE.total_lines_added += added
    _STATE.total_lines_removed += removed


def get_total_lines_added() -> int:
    return _STATE.total_lines_added


def get_total_lines_removed() -> int:
    return _STATE.total_lines_removed


def get_total_input_tokens() -> int:
    return sum(u.input_tokens for u in _STATE.model_usage.values())


def get_total_output_tokens() -> int:
    return sum(u.output_tokens for u in _STATE.model_usage.values())


def get_model_usage() -> dict[str, ModelUsage]:
    return _STATE.model_usage


def get_main_loop_model_override() -> Optional[str]:
    return _STATE.main_loop_model_override


def set_main_loop_model_override(model: Optional[str]) -> None:
    _STATE.main_loop_model_override = model


def get_last_interaction_time() -> float:
    return _STATE.last_interaction_time


def reset_cost_state() -> None:
    _STATE.total_cost_usd = 0
    _STATE.total_api_duration = 0
    _STATE.total_api_duration_without_retries = 0
    _STATE.total_tool_duration = 0
    _STATE.start_time = time.time() * 1000
    _STATE.total_lines_added = 0
    _STATE.total_lines_removed = 0
    _STATE.has_unknown_model_cost = False
    _STATE.model_usage = {}
    _STATE.prompt_id = None


def mark_post_compaction() -> None:
    _STATE.pending_post_compaction = True


def consume_post_compaction() -> bool:
    was = _STATE.pending_post_compaction
    _STATE.pending_post_compaction = False
    return was


def reset_state_for_tests() -> None:
    global _STATE, _output_tokens_at_turn_start, _current_turn_token_budget, _budget_continuation_count
    _STATE = _get_initial_state()
    _output_tokens_at_turn_start = 0
    _current_turn_token_budget = None
    _budget_continuation_count = 0
    _session_switch_callbacks.clear()
