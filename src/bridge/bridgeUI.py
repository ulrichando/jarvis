"""Bridge CLI UI -- status display and logging for Remote Control."""

from __future__ import annotations

import sys
import time
import logging
from typing import Any, Callable, Optional

from .bridgeStatusUtil import (
    TOOL_DISPLAY_EXPIRY_MS,
    build_active_footer_text,
    build_bridge_connect_url,
    build_bridge_session_url,
    build_idle_footer_text,
    format_duration,
    timestamp,
    truncate_prompt,
    wrap_with_osc8_link,
    FAILED_FOOTER_TEXT,
    StatusState,
)
from .types import BridgeConfig, SessionActivity, SpawnMode

logger = logging.getLogger(__name__)


def create_bridge_logger(verbose: bool = False, write: Optional[Callable[[str], None]] = None):
    """Create a bridge logger for CLI display."""
    _write = write or (lambda s: sys.stdout.write(s))
    status_line_count = 0
    current_state: StatusState = "idle"
    current_state_text = "Ready"
    repo_name = ""
    branch = ""
    connect_url = ""
    cached_ingress_url = ""
    cached_environment_id = ""
    active_session_url: Optional[str] = None
    session_active_count = 0
    session_max = 1
    spawn_mode: SpawnMode = "single-session"
    last_tool_summary: Optional[str] = None
    last_tool_time = 0.0
    session_display_info: dict[str, dict] = {}

    def clear_status_lines():
        nonlocal status_line_count
        if status_line_count <= 0:
            return
        _write(f"\x1b[{status_line_count}A")
        _write("\x1b[J")
        status_line_count = 0

    def write_status(text: str):
        nonlocal status_line_count
        _write(text)
        status_line_count += text.count("\n")

    def print_log(line: str):
        clear_status_lines()
        _write(line)

    class BridgeLoggerImpl:
        def print_banner(self, config: BridgeConfig, environment_id: str) -> None:
            nonlocal connect_url, cached_ingress_url, cached_environment_id
            cached_ingress_url = config.session_ingress_url
            cached_environment_id = environment_id
            connect_url = build_bridge_connect_url(environment_id, cached_ingress_url)
            if verbose:
                _write(f"Remote Control\n")
                _write(f"Environment ID: {environment_id}\n")
            _write("\n")

        def log_session_start(self, session_id: str, prompt: str) -> None:
            if verbose:
                short = truncate_prompt(prompt, 80)
                print_log(f"[{timestamp()}] Session started: \"{short}\" ({session_id})\n")

        def log_session_complete(self, session_id: str, duration_ms: int) -> None:
            print_log(f"[{timestamp()}] Session completed ({format_duration(duration_ms)}) {session_id}\n")

        def log_session_failed(self, session_id: str, error: str) -> None:
            print_log(f"[{timestamp()}] Session failed: {error} {session_id}\n")

        def log_status(self, message: str) -> None:
            print_log(f"[{timestamp()}] {message}\n")

        def log_verbose(self, message: str) -> None:
            if verbose:
                print_log(f"[{timestamp()}] {message}\n")

        def log_error(self, message: str) -> None:
            print_log(f"[{timestamp()}] Error: {message}\n")

        def log_reconnected(self, disconnected_ms: int) -> None:
            print_log(f"[{timestamp()}] Reconnected after {format_duration(disconnected_ms)}\n")

        def set_repo_info(self, repo: str, branch_name: str) -> None:
            nonlocal repo_name, branch
            repo_name = repo
            branch = branch_name

        def set_debug_log_path(self, path: str) -> None:
            pass

        def update_idle_status(self) -> None:
            nonlocal current_state, current_state_text, active_session_url
            current_state = "idle"
            current_state_text = "Ready"
            active_session_url = None
            clear_status_lines()
            write_status(f"* {current_state_text}\n")

        def set_attached(self, session_id: str) -> None:
            nonlocal current_state, current_state_text, active_session_url
            current_state = "attached"
            current_state_text = "Connected"
            if session_max <= 1:
                active_session_url = build_bridge_session_url(
                    session_id, cached_environment_id, cached_ingress_url
                )
            clear_status_lines()
            write_status(f"* {current_state_text}\n")

        def update_reconnecting_status(self, delay_str: str, elapsed_str: str) -> None:
            nonlocal current_state
            current_state = "reconnecting"
            clear_status_lines()
            write_status(f"* Reconnecting - retrying in {delay_str} - disconnected {elapsed_str}\n")

        def update_failed_status(self, error: str) -> None:
            nonlocal current_state
            current_state = "failed"
            clear_status_lines()
            write_status(f"X Remote Control Failed\n")
            if error:
                write_status(f"{error}\n")

        def update_session_status(self, session_id: str, elapsed: str, activity: SessionActivity, trail: list[str]) -> None:
            nonlocal last_tool_summary, last_tool_time
            if activity.type == "tool_start":
                last_tool_summary = activity.summary
                last_tool_time = time.time() * 1000

        def clear_status(self) -> None:
            clear_status_lines()

        def toggle_qr(self) -> None:
            pass

        def update_session_count(self, active: int, max_count: int, mode: SpawnMode) -> None:
            nonlocal session_active_count, session_max, spawn_mode
            session_active_count = active
            session_max = max_count
            spawn_mode = mode

        def set_spawn_mode_display(self, mode: Optional[str]) -> None:
            pass

        def add_session(self, session_id: str, url: str) -> None:
            session_display_info[session_id] = {"url": url}

        def update_session_activity(self, session_id: str, activity: SessionActivity) -> None:
            info = session_display_info.get(session_id)
            if info:
                info["activity"] = activity

        def set_session_title(self, session_id: str, title: str) -> None:
            info = session_display_info.get(session_id)
            if info:
                info["title"] = title

        def remove_session(self, session_id: str) -> None:
            session_display_info.pop(session_id, None)

        def refresh_display(self) -> None:
            pass

    return BridgeLoggerImpl()
