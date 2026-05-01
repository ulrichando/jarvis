#!/usr/bin/env python3
"""One-time backfill — classify NULL-route turns using the same
regex/lex logic the live Phase 10.4 path uses, so historical rows
stop showing as `?: N turns (X%)` in the report's 24h window.

Idempotent: only updates rows where route IS NULL and route IS still
NULL on read. Re-running this is a no-op once the rows have been
classified.

Approach mirrors the live `_on_user_input_for_dispatch` deterministic
default-route logic (BANTER fast-path → REASONING fast-path →
frustrated|sad → EMOTIONAL → TASK).
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

# Add voice-agent module path so we can reuse the live regex / detect_emotion.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src" / "voice-agent"))

from turn_router import detect_emotion, AudioMeta  # noqa: E402

# These regex constants are imported lazily because jarvis_agent.py runs
# install() side effects on import — we don't want them firing in a
# read-only backfill script.
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location(
    "_jagent_for_backfill",
    str(ROOT / "src" / "voice-agent" / "jarvis_agent.py"),
)
# The module's import-time install() calls require livekit + DEEPSEEK_API_KEY
# in env. For a read-only backfill we instead inline-copy the two regexes:
import re

# Mirror jarvis_agent's BANTER fast-path. Short conversational openers.
_BANTER_FAST_PATH_RE = re.compile(
    r"^\s*(hey jarvis|hi jarvis|hello jarvis|yo jarvis|"
    r"how are you|how's it going|how you doing|how have you been|"
    r"what's up|how was your|good (morning|afternoon|evening|night)|"
    r"thanks|thank you|thank's|cheers|appreciate it|"
    r"you're welcome|no worries|np|"
    r"never mind|nevermind|forget it|"
    r"goodbye|bye|see ya|see you|talk to you later|night|good night)\b",
    re.IGNORECASE,
)
_REASONING_FAST_PATH_RE = re.compile(
    r"^\s*("
    r"why does\b|why is\b|why are\b|why would\b|why should\b|why can't\b|"
    r"why might\b|why do\b|"
    r"how does\b|how do\b|"
    r"explain\s+\w|walk me through\s+\w|tell me how\b|can you explain\b|"
    r"design\s+\w|debug\s+\w|trace\s+\w|architect\s+\w|"
    r"compare\b|what'?s the difference\b|"
    r"step[ -]?by[ -]?step\b|"
    r"step\s+\w"
    r")",
    re.IGNORECASE,
)


def classify(user_text: str, jarvis_text: str) -> tuple[str, str]:
    """Return (route, emotion) for a historical row."""
    text = user_text or ""
    word_count = len(text.split())
    emotion = detect_emotion(text, AudioMeta())
    if word_count <= 6 and _BANTER_FAST_PATH_RE.match(text):
        route = "BANTER"
    elif _REASONING_FAST_PATH_RE.match(text):
        route = "REASONING"
    elif emotion in ("frustrated", "sad"):
        route = "EMOTIONAL"
    else:
        route = "TASK"
    return route, emotion


def main() -> None:
    db = Path(
        os.environ.get(
            "JARVIS_TELEMETRY_PATH",
            Path.home() / ".local" / "share" / "jarvis" / "turn_telemetry.db",
        )
    ).expanduser()
    if not db.exists():
        print(f"no db at {db}", file=sys.stderr)
        sys.exit(1)

    with sqlite3.connect(db) as conn:
        rows = list(conn.execute(
            "SELECT id, user_text, jarvis_text FROM turns WHERE route IS NULL"
        ))
        if not rows:
            print("no NULL-route rows to backfill")
            return
        print(f"found {len(rows)} NULL-route rows; classifying…")
        for rid, user_text, jarvis_text in rows:
            route, emotion = classify(user_text or "", jarvis_text or "")
            conn.execute(
                "UPDATE turns SET route=?, emotion=COALESCE(emotion,?) "
                "WHERE id=? AND route IS NULL",
                (route, emotion, rid),
            )
            print(f"  id={rid}: {route} (emotion={emotion}) — "
                  f"user={user_text[:40]!r}")
        conn.commit()
    print("done")


if __name__ == "__main__":
    main()
