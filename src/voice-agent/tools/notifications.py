"""Notifications tool — read the user's recent desktop notifications that JARVIS
captured from the system notification bus, instead of screenshotting the screen.

The capture happens out of band in pipeline.notification_listener (a D-Bus
monitor running as a --user service); this tool just reads the store. So
"do I have notifications / did I get a message" is answered without vision.
"""
from __future__ import annotations

import json
import time

from pipeline import notification_store

from .registry import registry


def _age(ts: float) -> str:
    s = max(0, int(time.time() - float(ts or 0)))
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def _handle_notifications(args: dict) -> str:
    limit = args.get("limit", 10)
    try:
        limit = max(1, min(50, int(limit)))
    except (TypeError, ValueError):
        limit = 10

    since_seconds = None
    since_min = args.get("since_minutes")
    if since_min is not None:
        try:
            since_seconds = max(0.0, float(since_min)) * 60.0
        except (TypeError, ValueError):
            since_seconds = None

    recs = notification_store.read(limit=limit, since_seconds=since_seconds)
    items = [
        {
            "app": r.get("app", ""),
            "summary": r.get("summary", ""),
            "body": r.get("body", ""),
            "age": _age(r.get("ts", time.time())),
        }
        for r in recs
    ]
    return json.dumps({"count": len(items), "notifications": items}, ensure_ascii=False)


_SCHEMA = {
    "name": "notifications",
    "description": (
        "Read the user's recent desktop notifications, captured from the system "
        "notification bus — NO screenshot needed. Use this whenever the user asks "
        "'do I have any notifications', 'did I get a message/email', 'what just "
        "popped up', 'any alerts', etc. Returns most-recent-first with app, "
        "summary, body, and age. ALWAYS prefer this over taking a screenshot for "
        "anything notification-related."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max notifications to return (1-50, default 10).",
                "minimum": 1,
                "maximum": 50,
            },
            "since_minutes": {
                "type": "number",
                "description": "Only notifications from the last N minutes (optional).",
            },
        },
        "required": [],
    },
}

registry.register(
    name="notifications",
    schema=_SCHEMA,
    handler=_handle_notifications,
    toolset="notifications",
    check_fn=None,   # always available — reads a local store (empty if nothing captured)
    is_async=False,
    emoji="🔔",
)
