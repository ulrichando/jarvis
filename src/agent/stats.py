"""JARVIS Stats — usage statistics and activity tracking."""

import os
import json
import time
import logging
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, date
from src.config import JARVIS_HOME

log = logging.getLogger(__name__)

STATS_FILE = JARVIS_HOME / "stats.json"


@dataclass
class DailyStats:
    """Statistics for a single day."""
    date: str  # YYYY-MM-DD
    sessions: int = 0
    messages: int = 0
    tool_calls: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    model_usage: dict = field(default_factory=dict)  # model -> {input, output}
    files_modified: int = 0
    commands_run: int = 0
    errors: int = 0


@dataclass
class SessionStats:
    """Statistics for a single session."""
    session_id: str
    start_time: float
    end_time: float = 0.0
    messages: int = 0
    tool_calls: int = 0
    tokens_total: int = 0
    duration_seconds: float = 0.0


class StatsTracker:
    """Track and aggregate usage statistics."""

    def __init__(self):
        self._daily: dict[str, DailyStats] = {}
        self._sessions: list[SessionStats] = []
        self._current_session: SessionStats | None = None
        self._peak_hour_counts: dict[int, int] = {}  # hour -> message count
        self._load()

    def start_session(self, session_id: str = ""):
        """Start tracking a new session."""
        self._current_session = SessionStats(
            session_id=session_id or str(time.time()),
            start_time=time.time(),
        )
        today = self._today()
        if today not in self._daily:
            self._daily[today] = DailyStats(date=today)
        self._daily[today].sessions += 1

    def record_message(self, role: str = "user"):
        """Record a message."""
        today = self._today()
        if today not in self._daily:
            self._daily[today] = DailyStats(date=today)
        self._daily[today].messages += 1
        if self._current_session:
            self._current_session.messages += 1
        # Track peak hours
        hour = datetime.now().hour
        self._peak_hour_counts[hour] = self._peak_hour_counts.get(hour, 0) + 1

    def record_tool_call(self, tool_name: str = ""):
        """Record a tool call."""
        today = self._today()
        if today not in self._daily:
            self._daily[today] = DailyStats(date=today)
        self._daily[today].tool_calls += 1
        if self._current_session:
            self._current_session.tool_calls += 1

    def record_tokens(self, model: str, input_tokens: int = 0, output_tokens: int = 0):
        """Record token usage."""
        today = self._today()
        if today not in self._daily:
            self._daily[today] = DailyStats(date=today)
        self._daily[today].tokens_input += input_tokens
        self._daily[today].tokens_output += output_tokens
        if model not in self._daily[today].model_usage:
            self._daily[today].model_usage[model] = {"input": 0, "output": 0}
        self._daily[today].model_usage[model]["input"] += input_tokens
        self._daily[today].model_usage[model]["output"] += output_tokens
        if self._current_session:
            self._current_session.tokens_total += input_tokens + output_tokens

    def record_error(self):
        """Record an error."""
        today = self._today()
        if today not in self._daily:
            self._daily[today] = DailyStats(date=today)
        self._daily[today].errors += 1

    def record_file_modified(self):
        """Record a file modification."""
        today = self._today()
        if today not in self._daily:
            self._daily[today] = DailyStats(date=today)
        self._daily[today].files_modified += 1

    def end_session(self):
        """End the current session."""
        if self._current_session:
            self._current_session.end_time = time.time()
            self._current_session.duration_seconds = (
                self._current_session.end_time - self._current_session.start_time
            )
            self._sessions.append(self._current_session)
            # Keep last 100 sessions
            if len(self._sessions) > 100:
                self._sessions = self._sessions[-100:]
            self._current_session = None
        self._save()

    def get_streak(self) -> dict:
        """Calculate current and longest streak of consecutive active days."""
        if not self._daily:
            return {"current": 0, "longest": 0}

        dates = sorted(self._daily.keys())
        current_streak = 1
        longest_streak = 1
        streak = 1

        for i in range(1, len(dates)):
            prev = date.fromisoformat(dates[i-1])
            curr = date.fromisoformat(dates[i])
            if (curr - prev).days == 1:
                streak += 1
                longest_streak = max(longest_streak, streak)
            else:
                streak = 1

        # Check if current streak includes today
        today = self._today()
        if dates and dates[-1] == today:
            current_streak = streak
        else:
            current_streak = 0

        return {"current": current_streak, "longest": longest_streak}

    def get_peak_hour(self) -> int:
        """Get the hour of day with most messages."""
        if not self._peak_hour_counts:
            return 12
        return max(self._peak_hour_counts, key=self._peak_hour_counts.get)

    def get_today_stats(self) -> DailyStats:
        """Get stats for today."""
        today = self._today()
        return self._daily.get(today, DailyStats(date=today))

    def get_total_stats(self) -> dict:
        """Get aggregate stats across all days."""
        total_messages = sum(d.messages for d in self._daily.values())
        total_tokens = sum(d.tokens_input + d.tokens_output for d in self._daily.values())
        total_tools = sum(d.tool_calls for d in self._daily.values())
        total_sessions = sum(d.sessions for d in self._daily.values())
        total_errors = sum(d.errors for d in self._daily.values())
        days_active = len(self._daily)
        streak = self.get_streak()

        return {
            "days_active": days_active,
            "total_sessions": total_sessions,
            "total_messages": total_messages,
            "total_tokens": total_tokens,
            "total_tool_calls": total_tools,
            "total_errors": total_errors,
            "streak_current": streak["current"],
            "streak_longest": streak["longest"],
            "peak_hour": self.get_peak_hour(),
            "avg_messages_per_day": total_messages / max(1, days_active),
            "avg_tokens_per_day": total_tokens / max(1, days_active),
        }

    def get_summary(self) -> str:
        """Human-readable stats summary."""
        stats = self.get_total_stats()
        today = self.get_today_stats()
        lines = [
            f"Today: {today.messages} messages, {today.tool_calls} tools, "
            f"{today.tokens_input + today.tokens_output:,} tokens",
            f"Total: {stats['days_active']} days active, {stats['total_sessions']} sessions, "
            f"{stats['total_messages']:,} messages",
            f"Streak: {stats['streak_current']} days (longest: {stats['streak_longest']})",
            f"Peak hour: {stats['peak_hour']}:00",
        ]
        return "\n".join(lines)

    def _today(self) -> str:
        return date.today().isoformat()

    def _save(self):
        """Save stats to disk."""
        try:
            data = {
                "daily": {k: vars(v) for k, v in self._daily.items()},
                "peak_hours": self._peak_hour_counts,
                "sessions": [vars(s) for s in self._sessions[-20:]],
            }
            STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATS_FILE.write_text(json.dumps(data, indent=2))
        except Exception as e:
            log.warning("Failed to save stats: %s", e)

    def _load(self):
        """Load stats from disk."""
        if not STATS_FILE.exists():
            return
        try:
            data = json.loads(STATS_FILE.read_text())
            for k, v in data.get("daily", {}).items():
                self._daily[k] = DailyStats(**v)
            self._peak_hour_counts = {int(k): v for k, v in data.get("peak_hours", {}).items()}
            for s in data.get("sessions", []):
                self._sessions.append(SessionStats(**s))
        except Exception as e:
            log.warning("Failed to load stats: %s", e)


# Module singleton
_tracker: StatsTracker | None = None

def get_stats_tracker() -> StatsTracker:
    global _tracker
    if _tracker is None:
        _tracker = StatsTracker()
    return _tracker
