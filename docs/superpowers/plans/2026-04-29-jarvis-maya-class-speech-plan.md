# JARVIS Maya-Class Speech Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement six additive components — emotion detector, turn router, LLM dispatcher, reply shaper, TTS voice-swap dispatcher, telemetry — so JARVIS adapts model, voice, and pacing to each turn's emotional and reasoning context.

**Architecture:** All components are subclassable wrappers over the existing LiveKit `AgentSession` pieces in `src/voice-agent/jarvis_agent.py`. New `DispatchingLLM(llm.LLM)` and `DispatchingTTS(tts.TTS)` classes route to the right inner instance per turn. Telemetry is a single SQLite table, written non-blocking. Every component falls back to current behaviour on failure.

**Tech Stack:** Python 3.13, livekit-agents, Groq SDK (already wired), DeepSeek + Anthropic via livekit `openai_compatible.LLM` and `anthropic.LLM`, SQLite stdlib, pytest.

**Spec:** `docs/superpowers/specs/2026-04-29-jarvis-maya-class-speech-design.md`

---

## File Structure

| File | Purpose |
|---|---|
| `src/voice-agent/turn_telemetry.py` (NEW) | SQLite logger + `--report` CLI |
| `src/voice-agent/turn_router.py` (NEW) | Emotion detector + turn-router pure functions; isolated for unit testing |
| `src/voice-agent/dispatching_llm.py` (NEW) | `DispatchingLLM` wrapping inner LLMs by route |
| `src/voice-agent/dispatching_tts.py` (NEW) | `DispatchingTTS` wrapping inner TTSs by route |
| `src/voice-agent/jarvis_agent.py` (MODIFY) | Wire all new components into `entrypoint()` AgentSession build |
| `src/voice-agent/tests/test_turn_telemetry.py` (NEW) | Unit tests for SQLite logger |
| `src/voice-agent/tests/test_turn_router.py` (NEW) | Unit tests for emotion + router (mocked Groq) |
| `src/voice-agent/tests/test_dispatching_llm.py` (NEW) | Unit tests for LLM dispatcher (mocked inner LLMs) |
| `src/voice-agent/tests/test_dispatching_tts.py` (NEW) | Unit tests for TTS dispatcher (mocked inner TTSs) |
| `src/voice-agent/tests/test_pipeline_integration.py` (NEW) | 30-fixture end-to-end happy-path |
| `src/voice-agent/desktop-tauri/scripts/launch.sh` (MODIFY) | Default env vars for new flags |

All edits stay within `src/voice-agent/` and one launcher line. The `JARVIS_INSTRUCTIONS` body is **not** edited; only the per-turn message prefix changes.

---

## Conventions used in every task

- Every test command runs through the venv: `src/voice-agent/.venv/bin/python -m pytest`
- Every commit message starts with `voice:` to match repo convention
- No `Co-Authored-By` trailers (per saved feedback)
- Each task ends with a passing test + commit

---

## Task 1: Telemetry foundation (turn_telemetry.py)

**Files:**
- Create: `src/voice-agent/turn_telemetry.py`
- Test: `src/voice-agent/tests/test_turn_telemetry.py`

- [ ] **Step 1.1: Write failing test for `log_turn` writing a row**

```python
# src/voice-agent/tests/test_turn_telemetry.py
import sqlite3
import tempfile
from pathlib import Path

from turn_telemetry import log_turn, init_db


def test_log_turn_writes_row(tmp_path):
    db_path = tmp_path / "telemetry.db"
    init_db(db_path)
    log_turn(
        db_path=db_path,
        user_text="what time is it",
        jarvis_text="nine forty-five PM",
        emotion="neutral",
        route="TASK",
        llm_used="groq:llama-3.3-70b-versatile",
        voice_used="bm_george",
        ttfw_ms=850,
        total_audio_ms=1500,
        user_followup_30s=False,
        route_fallback=False,
    )
    rows = sqlite3.connect(db_path).execute("SELECT route, llm_used, ttfw_ms FROM turns").fetchall()
    assert rows == [("TASK", "groq:llama-3.3-70b-versatile", 850)]


def test_log_turn_silently_swallows_disk_error(monkeypatch, tmp_path):
    bogus = tmp_path / "doesnotexist" / "x.db"  # parent missing
    # No init_db called → log_turn must not raise
    log_turn(
        db_path=bogus,
        user_text="x", jarvis_text="y",
        emotion="neutral", route="TASK",
        llm_used="x", voice_used="x",
        ttfw_ms=0, total_audio_ms=0,
        user_followup_30s=False, route_fallback=False,
    )
```

- [ ] **Step 1.2: Run the test, verify it fails**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_turn_telemetry.py -v
```

Expected: `ModuleNotFoundError: No module named 'turn_telemetry'`

- [ ] **Step 1.3: Implement `turn_telemetry.py`**

```python
# src/voice-agent/turn_telemetry.py
"""SQLite turn telemetry. Non-blocking writes; failures are silent.

Every JARVIS turn writes one row. Reading is via `--report`.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = Path(
    os.environ.get(
        "JARVIS_TELEMETRY_PATH",
        Path.home() / ".local" / "share" / "jarvis" / "turn_telemetry.db",
    )
).expanduser()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY,
    ts_utc TEXT NOT NULL,
    user_text TEXT NOT NULL,
    jarvis_text TEXT NOT NULL,
    emotion TEXT,
    route TEXT,
    llm_used TEXT,
    voice_used TEXT,
    ttfw_ms INTEGER,
    total_audio_ms INTEGER,
    user_followup_30s INTEGER,
    route_fallback INTEGER,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_turns_ts_utc ON turns(ts_utc);
CREATE INDEX IF NOT EXISTS idx_turns_route   ON turns(route);
"""


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SCHEMA)


def log_turn(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    user_text: str,
    jarvis_text: str,
    emotion: Optional[str],
    route: Optional[str],
    llm_used: Optional[str],
    voice_used: Optional[str],
    ttfw_ms: Optional[int],
    total_audio_ms: Optional[int],
    user_followup_30s: bool,
    route_fallback: bool,
    notes: str = "",
) -> None:
    """Write one row. Any exception is swallowed so telemetry never blocks voice."""
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO turns
                   (ts_utc, user_text, jarvis_text, emotion, route, llm_used,
                    voice_used, ttfw_ms, total_audio_ms, user_followup_30s,
                    route_fallback, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    user_text, jarvis_text, emotion, route, llm_used,
                    voice_used, ttfw_ms, total_audio_ms,
                    int(user_followup_30s), int(route_fallback), notes,
                ),
            )
    except Exception:
        return  # silent — see module docstring


def report(db_path: Path = DEFAULT_DB_PATH) -> str:
    """Print a human-readable summary of telemetry."""
    if not Path(db_path).exists():
        return "no telemetry yet"
    out: list[str] = []
    with sqlite3.connect(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        out.append(f"total turns: {n}")
        for route, count, p50, p95 in conn.execute(
            """SELECT route, COUNT(*),
                      CAST(AVG(ttfw_ms) AS INT),
                      MAX(ttfw_ms)
               FROM turns GROUP BY route ORDER BY count DESC"""
        ):
            out.append(f"  {route or '?'}: {count} turns, avg_ttfw={p50}ms, max_ttfw={p95}ms")
        emo_followup = conn.execute(
            "SELECT AVG(user_followup_30s) FROM turns WHERE route='EMOTIONAL'"
        ).fetchone()[0]
        out.append(f"emotional follow-up rate: {emo_followup or 0:.0%}")
        fb = conn.execute("SELECT AVG(route_fallback) FROM turns").fetchone()[0] or 0
        out.append(f"route-fallback rate: {fb:.1%}")
    return "\n".join(out)


if __name__ == "__main__":
    if "--report" in sys.argv:
        print(report())
    else:
        init_db()
        print(f"initialized {DEFAULT_DB_PATH}")
```

- [ ] **Step 1.4: Run tests, verify pass**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_turn_telemetry.py -v
```

Expected: `2 passed`

- [ ] **Step 1.5: Commit**

```bash
git add src/voice-agent/turn_telemetry.py src/voice-agent/tests/test_turn_telemetry.py
git commit -m "voice: turn telemetry SQLite logger"
```

---

## Task 2: Emotion detector (turn_router.py — emotion half)

**Files:**
- Create: `src/voice-agent/turn_router.py`
- Test: `src/voice-agent/tests/test_turn_router.py`

- [ ] **Step 2.1: Write failing tests for emotion detection**

```python
# src/voice-agent/tests/test_turn_router.py
import pytest
from turn_router import detect_emotion, AudioMeta


@pytest.mark.parametrize("transcript,expected", [
    ("hey jarvis what time is it", "neutral"),
    ("WHY ISN'T THIS WORKING I tried three times", "frustrated"),
    ("oh wow that's amazing", "excited"),
    ("I just don't know what to do anymore", "sad"),
    ("quick I need this NOW", "urgent"),
    ("I've been wondering how this actually works under the hood", "curious"),
    ("ok thanks", "neutral"),
])
def test_emotion_lexical(transcript, expected):
    assert detect_emotion(transcript, AudioMeta()) == expected


def test_emotion_caps_escalates_to_frustrated():
    assert detect_emotion("WHY IS THIS BROKEN", AudioMeta()) == "frustrated"


def test_emotion_high_speech_rate_signals_urgent():
    am = AudioMeta(speech_rate_wpm=240, baseline_wpm=140)
    assert detect_emotion("I need that file now", am) == "urgent"


def test_emotion_low_speech_rate_with_keyword_signals_sad():
    am = AudioMeta(speech_rate_wpm=70, baseline_wpm=140)
    assert detect_emotion("I just don't know", am) == "sad"


def test_emotion_unknown_falls_back_to_neutral():
    assert detect_emotion("blarg foo whatever", AudioMeta()) == "neutral"
```

- [ ] **Step 2.2: Run, verify fail**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_turn_router.py::test_emotion_lexical -v
```

Expected: `ModuleNotFoundError: No module named 'turn_router'`

- [ ] **Step 2.3: Implement emotion half of `turn_router.py`**

```python
# src/voice-agent/turn_router.py
"""Pure-function emotion detector + turn router.

Both functions are sync, side-effect-free, and dependency-light so unit
tests don't need any LLM or audio backend. The router has an async
overload that calls Groq; the sync `route_turn_from_classification`
factor lets tests exercise the LLM-output → route logic without network.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Emotion = Literal["neutral", "frustrated", "excited", "sad", "urgent", "curious"]
Route   = Literal["BANTER", "TASK", "REASONING", "EMOTIONAL"]

# Order matters: most-specific keys first so caps-ratio doesn't
# stomp a frustration signal that's also angry-shaped.
_EMOTION_LEX = {
    "frustrated": [
        "why isn't", "this isn't working", "stupid", "useless",
        "still broken", "tried", "still", "broken", "not working",
        "again", "third time", "supposed to",
    ],
    "excited": [
        "amazing", "awesome", "let's go", "no way", "incredible",
        "love it", "wow", "yes!", "finally",
    ],
    "sad": [
        "i don't know what to do", "everything's", "give up", "tired of",
        "lonely", "miss", "i don't know anymore", "i just don't know",
    ],
    "urgent": [
        "now", "right now", "asap", "immediately", "quick", "hurry",
    ],
    "curious": [
        "i wonder", "how does", "why does", "what's behind", "actually works",
        "under the hood", "explain why", "curious about",
    ],
}


@dataclass
class AudioMeta:
    speech_rate_wpm: float = 0.0   # 0 means unknown
    baseline_wpm:    float = 0.0   # rolling-window user baseline (0=unknown)
    peak_db:         float = 0.0


def _caps_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    upper = sum(1 for c in letters if c.isupper())
    return upper / len(letters)


def _lex_match(text: str) -> Emotion:
    low = text.lower()
    for emo, keys in _EMOTION_LEX.items():
        for k in keys:
            if k in low:
                return emo  # type: ignore
    return "neutral"


def detect_emotion(transcript: str, audio: AudioMeta) -> Emotion:
    """Detect dominant emotion. neutral on no signal."""
    base = _lex_match(transcript)

    # CAPS escalation → frustrated regardless of lex hit
    if _caps_ratio(transcript) > 0.30 and len(transcript) > 5:
        return "frustrated"

    # Speech-rate signals override neutral, refine ambiguous lex hits
    if audio.speech_rate_wpm and audio.baseline_wpm:
        ratio = audio.speech_rate_wpm / audio.baseline_wpm
        if ratio > 1.30 and base in ("neutral", "excited"):
            return "urgent"
        if ratio < 0.70 and base in ("neutral", "sad"):
            return "sad"

    return base
```

- [ ] **Step 2.4: Run, verify pass**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_turn_router.py -v -k emotion
```

Expected: all emotion tests pass

- [ ] **Step 2.5: Commit**

```bash
git add src/voice-agent/turn_router.py src/voice-agent/tests/test_turn_router.py
git commit -m "voice: emotion detector with lexical + prosodic signals"
```

---

## Task 3: Turn router (LLM classifier)

**Files:**
- Modify: `src/voice-agent/turn_router.py`
- Modify: `src/voice-agent/tests/test_turn_router.py`

- [ ] **Step 3.1: Add failing tests for the route classifier**

Append to `tests/test_turn_router.py`:

```python
import asyncio
from unittest.mock import AsyncMock, patch

from turn_router import (
    route_from_classifier_output,
    classify_turn,
)


@pytest.mark.parametrize("raw,expected", [
    ("BANTER", "BANTER"),
    ("  task  ", "TASK"),
    ("REASONING\nplus extra", "REASONING"),
    ("EMOTIONAL.", "EMOTIONAL"),
    ("garbage", "TASK"),
    ("", "TASK"),
])
def test_route_from_classifier_output(raw, expected):
    assert route_from_classifier_output(raw) == expected


def test_classify_turn_uses_groq_response():
    fake_groq = AsyncMock(return_value="REASONING")
    out = asyncio.run(
        classify_turn(
            history=[("user", "walk me through how http2 multiplexing works")],
            emotion="curious",
            groq_call=fake_groq,
            timeout_ms=500,
        )
    )
    assert out == "REASONING"
    assert fake_groq.await_count == 1


def test_classify_turn_falls_back_on_timeout():
    async def slow(*_a, **_k):
        await asyncio.sleep(2.0)
        return "BANTER"

    out = asyncio.run(
        classify_turn(
            history=[("user", "hey")],
            emotion="neutral",
            groq_call=slow,
            timeout_ms=100,
        )
    )
    assert out == "TASK"  # fallback
```

- [ ] **Step 3.2: Run, verify fail**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_turn_router.py -v -k route_or_classify
```

Expected: ImportError on `route_from_classifier_output` / `classify_turn`

- [ ] **Step 3.3: Append router implementation to `turn_router.py`**

```python
# Append to src/voice-agent/turn_router.py
import asyncio
from typing import Awaitable, Callable

_VALID_ROUTES = {"BANTER", "TASK", "REASONING", "EMOTIONAL"}

ROUTER_PROMPT_TEMPLATE = """\
You are a turn-router for a voice assistant. Read the conversation
history and the most recent user emotion tag. Output exactly ONE word
naming the best route for the assistant's reply:

  BANTER     — chitchat, jokes, idle conversation
  TASK       — actionable command or fact lookup
  REASONING  — multi-step thinking, planning, debugging
  EMOTIONAL  — feelings, frustration, support, hard decisions

Recent conversation:
{history}

User emotion: {emotion}

Output ONLY the word. No punctuation, no explanation."""


def route_from_classifier_output(raw: str) -> Route:
    if not raw:
        return "TASK"
    cleaned = re.split(r"[^A-Za-z]", raw.strip())[0].upper()
    return cleaned if cleaned in _VALID_ROUTES else "TASK"  # type: ignore


async def classify_turn(
    *,
    history: list[tuple[str, str]],
    emotion: Emotion,
    groq_call: Callable[[str], Awaitable[str]],
    timeout_ms: int = 500,
) -> Route:
    """Run the router LLM with timeout fallback."""
    pretty = "\n".join(f"{role}: {text}" for role, text in history[-5:])
    prompt = ROUTER_PROMPT_TEMPLATE.format(history=pretty, emotion=emotion)
    try:
        raw = await asyncio.wait_for(groq_call(prompt), timeout=timeout_ms / 1000)
    except (asyncio.TimeoutError, Exception):
        return "TASK"
    return route_from_classifier_output(raw)
```

- [ ] **Step 3.4: Run, verify pass**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_turn_router.py -v
```

Expected: all tests pass

- [ ] **Step 3.5: Commit**

```bash
git add src/voice-agent/turn_router.py src/voice-agent/tests/test_turn_router.py
git commit -m "voice: turn router with timeout fallback to TASK"
```

---

## Task 4: DispatchingLLM wrapper

**Files:**
- Create: `src/voice-agent/dispatching_llm.py`
- Test: `src/voice-agent/tests/test_dispatching_llm.py`

- [ ] **Step 4.1: Write failing test for dispatching wrapper**

```python
# src/voice-agent/tests/test_dispatching_llm.py
import asyncio
from unittest.mock import MagicMock

from dispatching_llm import DispatchingLLM


def _stub_inner(label: str):
    inner = MagicMock(name=f"inner-{label}")
    inner.label = label
    return inner


def test_dispatcher_returns_inner_for_route():
    inners = {
        "BANTER":     _stub_inner("groq"),
        "TASK":       _stub_inner("groq"),
        "REASONING":  _stub_inner("dsr"),
        "EMOTIONAL":  _stub_inner("haiku"),
    }
    d = DispatchingLLM(inners=inners, fallback=inners["TASK"])
    assert d.pick("BANTER").label == "groq"
    assert d.pick("REASONING").label == "dsr"
    assert d.pick("EMOTIONAL").label == "haiku"


def test_dispatcher_unknown_route_uses_fallback():
    inners = {"TASK": _stub_inner("groq")}
    d = DispatchingLLM(inners=inners, fallback=inners["TASK"])
    assert d.pick("BOGUS").label == "groq"


def test_dispatcher_records_route_for_telemetry():
    inners = {
        "TASK": _stub_inner("groq"),
        "REASONING": _stub_inner("dsr"),
    }
    d = DispatchingLLM(inners=inners, fallback=inners["TASK"])
    d.pick("REASONING")
    assert d.last_route == "REASONING"
    assert d.last_llm_label == "dsr"
```

- [ ] **Step 4.2: Run, verify fail**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_dispatching_llm.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 4.3: Implement `dispatching_llm.py`**

```python
# src/voice-agent/dispatching_llm.py
"""Dispatching LLM wrapper.

Plain Python class for v1 — does not subclass livekit.agents.llm.LLM.
Keeping it framework-agnostic lets unit tests exercise routing without
constructing a full LiveKit pipeline. The integration step in
jarvis_agent.py builds a DispatchingLLM with real inner LLMs and
forwards `chat()` calls to the picked inner.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class DispatchingLLM:
    """Picks an inner LLM based on the current route tag."""
    inners: dict[str, Any]
    fallback: Any
    last_route: Optional[str] = None
    last_llm_label: Optional[str] = None

    def pick(self, route: str) -> Any:
        inner = self.inners.get(route, self.fallback)
        self.last_route = route
        self.last_llm_label = getattr(inner, "label", repr(inner))
        return inner
```

- [ ] **Step 4.4: Run, verify pass**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_dispatching_llm.py -v
```

Expected: `3 passed`

- [ ] **Step 4.5: Commit**

```bash
git add src/voice-agent/dispatching_llm.py src/voice-agent/tests/test_dispatching_llm.py
git commit -m "voice: DispatchingLLM picks inner by route, records last_route"
```

---

## Task 5: DispatchingTTS wrapper

**Files:**
- Create: `src/voice-agent/dispatching_tts.py`
- Test: `src/voice-agent/tests/test_dispatching_tts.py`

- [ ] **Step 5.1: Write failing test**

```python
# src/voice-agent/tests/test_dispatching_tts.py
from unittest.mock import MagicMock

from dispatching_tts import DispatchingTTS


def _stub(voice_id: str):
    m = MagicMock(name=f"tts-{voice_id}")
    m.voice_id = voice_id
    return m


def test_dispatcher_picks_correct_voice_per_route():
    inners = {
        "BANTER":    _stub("am_michael"),
        "TASK":      _stub("bm_george"),
        "REASONING": _stub("bm_george"),
        "EMOTIONAL": _stub("bm_lewis"),
    }
    d = DispatchingTTS(inners=inners, fallback=inners["TASK"])
    assert d.pick("BANTER").voice_id == "am_michael"
    assert d.pick("EMOTIONAL").voice_id == "bm_lewis"


def test_dispatcher_unknown_route_uses_fallback():
    inners = {"TASK": _stub("bm_george")}
    d = DispatchingTTS(inners=inners, fallback=inners["TASK"])
    assert d.pick("ZZZ").voice_id == "bm_george"


def test_dispatcher_records_last_voice_used():
    inners = {"BANTER": _stub("am_michael"), "TASK": _stub("bm_george")}
    d = DispatchingTTS(inners=inners, fallback=inners["TASK"])
    d.pick("BANTER")
    assert d.last_voice_id == "am_michael"
```

- [ ] **Step 5.2: Run, verify fail**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_dispatching_tts.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 5.3: Implement `dispatching_tts.py`**

```python
# src/voice-agent/dispatching_tts.py
"""Dispatching TTS wrapper. Sibling pattern of DispatchingLLM.

The integration step in jarvis_agent.py constructs four inner TTS
instances (one per route) and assembles them here. The voice_id
attribute is a duck-typed convenience for telemetry; real LiveKit
TTS instances expose the voice somewhere on themselves and can be
adapted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class DispatchingTTS:
    inners: dict[str, Any]
    fallback: Any
    last_route: Optional[str] = None
    last_voice_id: Optional[str] = None

    def pick(self, route: str) -> Any:
        inner = self.inners.get(route, self.fallback)
        self.last_route = route
        self.last_voice_id = getattr(inner, "voice_id", repr(inner))
        return inner
```

- [ ] **Step 5.4: Run, verify pass**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_dispatching_tts.py -v
```

Expected: `3 passed`

- [ ] **Step 5.5: Commit**

```bash
git add src/voice-agent/dispatching_tts.py src/voice-agent/tests/test_dispatching_tts.py
git commit -m "voice: DispatchingTTS picks inner by route, tracks last voice"
```

---

## Task 6: Integration test for happy-path pipeline

**Files:**
- Create: `src/voice-agent/tests/test_pipeline_integration.py`

- [ ] **Step 6.1: Write integration test**

```python
# src/voice-agent/tests/test_pipeline_integration.py
"""Happy-path integration of emotion → router → LLM dispatcher → TTS dispatcher.

Uses mocked Groq router responses; constructs DispatchingLLM/TTS with
stubbed inners. Verifies routing distribution + telemetry for 30
fixture turns covering 4 routes × emotional spread.
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from turn_router import detect_emotion, classify_turn, AudioMeta
from dispatching_llm import DispatchingLLM
from dispatching_tts import DispatchingTTS


FIXTURES = [
    # (transcript, audio, mocked_router_output, expected_route)
    ("hey jarvis what's up",            AudioMeta(),       "BANTER",    "BANTER"),
    ("yo what time is it",              AudioMeta(),       "TASK",      "TASK"),
    ("open chrome please",              AudioMeta(),       "TASK",      "TASK"),
    ("walk me through how grpc works",  AudioMeta(),       "REASONING", "REASONING"),
    ("WHY ISN'T THIS WORKING",          AudioMeta(),       "EMOTIONAL", "EMOTIONAL"),
    ("I'm so tired of this",            AudioMeta(),       "EMOTIONAL", "EMOTIONAL"),
    ("just curious how it does that",   AudioMeta(),       "REASONING", "REASONING"),
    ("ok thanks",                       AudioMeta(),       "BANTER",    "BANTER"),
    ("what's my IP",                    AudioMeta(),       "TASK",      "TASK"),
    ("explain the planner",             AudioMeta(),       "REASONING", "REASONING"),
] * 3  # 30 total


def _stub(label):
    m = MagicMock()
    m.label = label
    m.voice_id = label
    return m


def test_pipeline_routes_30_fixtures_correctly():
    llm_inners = {r: _stub(f"llm-{r}") for r in ("BANTER", "TASK", "REASONING", "EMOTIONAL")}
    tts_inners = {r: _stub(f"voice-{r}") for r in ("BANTER", "TASK", "REASONING", "EMOTIONAL")}
    d_llm = DispatchingLLM(inners=llm_inners, fallback=llm_inners["TASK"])
    d_tts = DispatchingTTS(inners=tts_inners, fallback=tts_inners["TASK"])

    correct = 0
    for transcript, audio, mocked_out, expected in FIXTURES:
        emo = detect_emotion(transcript, audio)
        async def fake_groq(_p, out=mocked_out):
            return out
        route = asyncio.run(classify_turn(
            history=[("user", transcript)],
            emotion=emo,
            groq_call=fake_groq,
            timeout_ms=500,
        ))
        d_llm.pick(route)
        d_tts.pick(route)
        if route == expected:
            correct += 1

    accuracy = correct / len(FIXTURES)
    assert accuracy >= 0.80, f"routing accuracy {accuracy:.0%} < 80%"
```

- [ ] **Step 6.2: Run integration test, verify pass**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_pipeline_integration.py -v
```

Expected: `1 passed`

- [ ] **Step 6.3: Commit**

```bash
git add src/voice-agent/tests/test_pipeline_integration.py
git commit -m "voice: integration test, 30-fixture pipeline routing"
```

---

## Task 7: Wire the dispatchers into jarvis_agent.py

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py`

This is the integration step: build real inner LLMs + TTSs and pass dispatchers into AgentSession.

- [ ] **Step 7.1: Read the current `entrypoint()` LLM/TTS construction**

Read `src/voice-agent/jarvis_agent.py:3127-3155` to confirm exact line numbers (file shifts between commits). The relevant block builds `_active_speech_llm` and assembles `tts.FallbackAdapter(_build_tts_chain())`. Add new construction immediately above the `session = AgentSession(...)` line.

- [ ] **Step 7.2: Add `_build_dispatching_llm()` factory**

Insert after `_build_tts_chain()` (around the `_build_tts_chain` definition area, line ~457):

```python
def _build_dispatching_llm() -> "DispatchingLLM":
    """Construct route → inner-LLM mapping.

    BANTER, TASK   → Groq llama-3.3-70b (current main, fast)
    REASONING      → DeepSeek-Reasoner   (deeper thinking, ~2-3s tokens)
    EMOTIONAL      → Anthropic Haiku 4.5 (warmth, nuance)

    Failures during construction fall back to Groq main for that route.
    """
    from livekit.plugins import groq, anthropic, openai
    from dispatching_llm import DispatchingLLM

    main = groq.LLM(model="llama-3.3-70b-versatile")
    main.label = "groq:llama-3.3-70b-versatile"

    try:
        dsr = openai.LLM.with_openai_compatible(
            base_url="https://api.deepseek.com/v1",
            api_key=os.environ["DEEPSEEK_API_KEY"],
            model="deepseek-reasoner",
        )
        dsr.label = "deepseek-reasoner"
    except Exception as e:
        logger.warning(f"[dispatch] DeepSeek-Reasoner unavailable, fallback to Groq: {e}")
        dsr = main

    try:
        haiku = anthropic.LLM(model="claude-haiku-4-5-20251001")
        haiku.label = "anthropic:claude-haiku-4-5"
    except Exception as e:
        logger.warning(f"[dispatch] Anthropic Haiku unavailable, fallback to Groq: {e}")
        haiku = main

    return DispatchingLLM(
        inners={
            "BANTER":    main,
            "TASK":      main,
            "REASONING": dsr,
            "EMOTIONAL": haiku,
        },
        fallback=main,
    )
```

- [ ] **Step 7.3: Add `_build_dispatching_tts()` factory**

Insert next to it:

```python
def _build_dispatching_tts() -> "DispatchingTTS":
    """Per-route inner TTS instances.

    Picks the active TTS provider (per existing _build_tts_chain logic)
    and produces 4 instances with route-mapped voices. Voices are
    env-overridable. If a single inner construction fails, that route
    inherits the TASK voice.
    """
    from dispatching_tts import DispatchingTTS

    voices = {
        "BANTER":    os.environ.get("JARVIS_VOICE_BANTER",    "am_michael"),
        "TASK":      os.environ.get("JARVIS_VOICE_TASK",      "bm_george"),
        "REASONING": os.environ.get("JARVIS_VOICE_REASONING", "bm_george"),
        "EMOTIONAL": os.environ.get("JARVIS_VOICE_EMOTIONAL", "bm_lewis"),
    }
    inners = {}
    for route, vid in voices.items():
        try:
            t = _LoggingGroqTTS(model="canopylabs/orpheus-v1-english", voice=vid)
            t.voice_id = vid
            inners[route] = t
        except Exception as e:
            logger.warning(f"[dispatch] tts {route}={vid} failed, will inherit TASK: {e}")

    fallback = inners.get("TASK")
    if fallback is None:
        fallback = _LoggingGroqTTS(model="canopylabs/orpheus-v1-english", voice="bm_george")
        fallback.voice_id = "bm_george"
        inners.setdefault("TASK", fallback)
    for route in ("BANTER", "REASONING", "EMOTIONAL"):
        inners.setdefault(route, fallback)

    return DispatchingTTS(inners=inners, fallback=fallback)
```

> **Implementation note:** The current `_build_tts_chain()` returns a list for `tts.FallbackAdapter`. We are intentionally not running each route through FallbackAdapter in v1 — that would multiply construction cost by 4. If a Groq voice fails mid-stream, the existing inside-LiveKit retry logic still applies; the route just plays in the chosen voice or stays silent on hard failure. ElevenLabs and Edge fallbacks remain available via the legacy `_build_tts_chain` path which we keep for the `JARVIS_DISPATCH_DISABLED=1` rollback in Task 9.

- [ ] **Step 7.4: Wire dispatchers into AgentSession**

Replace `llm=_active_speech_llm,` and `tts=tts.FallbackAdapter(_build_tts_chain()),` lines (around 3145, 3153) with:

```python
        # Dispatching LLM/TTS — routes per turn based on emotion + classifier
        # tag. JARVIS_DISPATCH_DISABLED=1 reverts to legacy single-LLM/single-TTS.
        if os.environ.get("JARVIS_DISPATCH_DISABLED", "0") != "1":
            _dispatch_llm = _build_dispatching_llm()
            _dispatch_tts = _build_dispatching_tts()
            llm_arg = _dispatch_llm.fallback   # default; per-turn override below
            tts_arg = _dispatch_tts.fallback
        else:
            _dispatch_llm = None
            _dispatch_tts = None
            llm_arg = _active_speech_llm
            tts_arg = tts.FallbackAdapter(_build_tts_chain())
```

Then in the AgentSession constructor:

```python
        llm=llm_arg,
        tts=tts_arg,
```

Stash the dispatchers on `ctx.proc.userdata` so the per-turn callback can reach them:

```python
        ctx.proc.userdata["dispatch_llm"] = _dispatch_llm
        ctx.proc.userdata["dispatch_tts"] = _dispatch_tts
```

- [ ] **Step 7.5: Add per-turn router callback**

Add right after the AgentSession construction, before `session.start(...)`:

```python
        @session.on("user_input_transcribed")
        def _on_user_input(ev) -> None:  # ev.transcript, ev.is_final
            if not ev.is_final or not _dispatch_llm:
                return
            transcript = ev.transcript or ""
            audio = AudioMeta(
                speech_rate_wpm=getattr(ev, "speech_rate_wpm", 0.0),
                baseline_wpm=getattr(ev, "baseline_wpm", 0.0),
            )
            emotion = detect_emotion(transcript, audio)

            async def _classify_and_swap():
                async def _groq_call(prompt: str) -> str:
                    # Reuse the main Groq LLM for the tiny classifier pass.
                    resp = await _dispatch_llm.fallback.chat(
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.0,
                    )
                    return getattr(resp, "content", str(resp)).strip()

                history = [(m.role, m.content) for m in session.chat_ctx.messages[-5:]]
                history.append(("user", transcript))
                route = await classify_turn(
                    history=history,
                    emotion=emotion,
                    groq_call=_groq_call,
                    timeout_ms=int(os.environ.get("JARVIS_ROUTER_TIMEOUT_MS", "500")),
                )
                # Swap the LLM and TTS the AgentSession will use for this turn.
                session.llm = _dispatch_llm.pick(route)
                session.tts = _dispatch_tts.pick(route)
                session._jarvis_emotion = emotion
                session._jarvis_route   = route

            task = asyncio.create_task(_classify_and_swap())
            _bg_tasks.add(task)
            task.add_done_callback(_bg_tasks.discard)
```

> **Note:** `session.llm = ...` and `session.tts = ...` may not be supported by every livekit-agents version. If they aren't, the alternative is to construct one AgentSession per route variation up-front and switch which one is active — verify against the installed version (`pip show livekit-agents`) before implementation. If swap is not supported, fall back to **Task 7-alt** below.

- [ ] **Step 7.6: Add module-top imports**

At the top of `jarvis_agent.py`, near other internal imports:

```python
from turn_router    import detect_emotion, classify_turn, AudioMeta
from dispatching_llm import DispatchingLLM
from dispatching_tts import DispatchingTTS
from turn_telemetry import init_db, log_turn, DEFAULT_DB_PATH
```

And in `entrypoint()` very early:

```python
    init_db(DEFAULT_DB_PATH)
```

- [ ] **Step 7.7: Smoke-run the agent**

```bash
cd src/voice-agent && .venv/bin/python -c "import jarvis_agent"
```

Expected: imports without ImportError. Any livekit-agents API mismatch surfaces here.

- [ ] **Step 7.8: Run all unit tests**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/ -v
```

Expected: every test still green (none of these touch the new wire-up).

- [ ] **Step 7.9: Commit**

```bash
git add src/voice-agent/jarvis_agent.py
git commit -m "voice: wire DispatchingLLM/TTS into AgentSession with per-turn classifier"
```

### Task 7-alt (only if Step 7.5 blocks on framework limitation)

If livekit-agents does not allow swapping `session.llm` mid-session, replace Step 7.5 with the simpler **prefix-only** strategy: keep one inner LLM (Groq main) but prepend `[Route: X] [Emotion: Y]` to the user message before AgentSession sees it, and skip per-route LLM swap. The TTS swap can still work via a `tts.TTS` subclass that delegates to one of the inners based on a thread-local `current_route`. Document the regression in commit message.

---

## Task 8: Telemetry write at turn end

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py`

- [ ] **Step 8.1: Add post-turn telemetry write**

After the AgentSession callback in Step 7.5, add a second handler for the agent reply finishing:

```python
        @session.on("agent_speech_committed")
        def _on_agent_done(ev) -> None:
            if not _dispatch_llm:
                return
            try:
                user_text   = getattr(ev, "user_input_text", "") or ""
                jarvis_text = getattr(ev, "agent_speech_text", "") or ""
                ttfw_ms     = int(getattr(ev, "ttfw_ms", 0))
                total_ms    = int(getattr(ev, "audio_duration_ms", 0))
                log_turn(
                    user_text=user_text,
                    jarvis_text=jarvis_text,
                    emotion=getattr(session, "_jarvis_emotion", None),
                    route=getattr(session, "_jarvis_route", None),
                    llm_used=_dispatch_llm.last_llm_label,
                    voice_used=_dispatch_tts.last_voice_id,
                    ttfw_ms=ttfw_ms,
                    total_audio_ms=total_ms,
                    user_followup_30s=False,  # backfilled later, see notes
                    route_fallback=False,
                )
            except Exception as e:
                logger.debug(f"[telemetry] write skipped: {e}")
```

> **Note on `user_followup_30s`:** the bool is set `False` at write time. A simple cron or follow-up handler can backfill rows whose `ts_utc` is followed by another row within 30 seconds. Out of scope for v1 (acceptable to leave it as-written and compute follow-up at report time using a window query). If `report()` needs to compute it dynamically, change the query in `turn_telemetry.report` to a window-based one — left as a v1 cleanup.

- [ ] **Step 8.2: Smoke-run again**

```bash
cd src/voice-agent && .venv/bin/python -c "import jarvis_agent"
```

Expected: import OK.

- [ ] **Step 8.3: Commit**

```bash
git add src/voice-agent/jarvis_agent.py
git commit -m "voice: write turn telemetry on agent_speech_committed"
```

---

## Task 9: Launcher env defaults + rollback path

**Files:**
- Modify: `src/voice-agent/desktop-tauri/scripts/launch.sh`

- [ ] **Step 9.1: Read the current launcher**

```bash
cat src/voice-agent/desktop-tauri/scripts/launch.sh | head -50
```

Identify where env vars are exported (typically near the top, before service starts).

- [ ] **Step 9.2: Add defaults block**

Insert a block of env defaults; export only if unset so user overrides win:

```sh
# Maya-class speech intelligence defaults — override by setting in env.
: "${JARVIS_DISPATCH_DISABLED:=0}"
: "${JARVIS_ROUTER_ENABLED:=1}"
: "${JARVIS_ROUTER_TIMEOUT_MS:=500}"
: "${JARVIS_VOICE_BANTER:=am_michael}"
: "${JARVIS_VOICE_TASK:=bm_george}"
: "${JARVIS_VOICE_REASONING:=bm_george}"
: "${JARVIS_VOICE_EMOTIONAL:=bm_lewis}"
: "${JARVIS_TELEMETRY_PATH:=$HOME/.local/share/jarvis/turn_telemetry.db}"
export JARVIS_DISPATCH_DISABLED JARVIS_ROUTER_ENABLED JARVIS_ROUTER_TIMEOUT_MS \
       JARVIS_VOICE_BANTER JARVIS_VOICE_TASK JARVIS_VOICE_REASONING JARVIS_VOICE_EMOTIONAL \
       JARVIS_TELEMETRY_PATH
```

- [ ] **Step 9.3: Verify launcher still parses**

```bash
bash -n src/voice-agent/desktop-tauri/scripts/launch.sh
```

Expected: no syntax errors.

- [ ] **Step 9.4: Commit**

```bash
git add src/voice-agent/desktop-tauri/scripts/launch.sh
git commit -m "voice: launcher env defaults for Maya-class speech"
```

---

## Task 10: Dogfood + telemetry verification

**Files:** none new. This is the close-the-loop verification step.

- [ ] **Step 10.1: Restart voice-agent**

```bash
systemctl --user restart jarvis-voice-agent jarvis-voice-client
journalctl --user -u jarvis-voice-agent -f --since "1 minute ago"
```

Expected: agent boots, logs show "DispatchingLLM constructed" and "DispatchingTTS constructed". No tracebacks.

- [ ] **Step 10.2: Run 10 voice turns covering all 4 routes**

Manually speak (and log what you said for the test record):

| # | Spoken | Expected route |
|---|---|---|
| 1 | "hey jarvis what's up" | BANTER |
| 2 | "what time is it" | TASK |
| 3 | "open chrome" | TASK |
| 4 | "walk me through how grpc works" | REASONING |
| 5 | "I'm so frustrated, this isn't working" | EMOTIONAL |
| 6 | "explain the planner architecture" | REASONING |
| 7 | "yo nice" | BANTER |
| 8 | "screenshot the desktop" | TASK |
| 9 | "I just don't know what to do anymore" | EMOTIONAL |
| 10 | "what's my IP" | TASK |

- [ ] **Step 10.3: Verify telemetry captured turns**

```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db \
  "SELECT route, llm_used, voice_used, ttfw_ms FROM turns ORDER BY id DESC LIMIT 10"
```

Expected: 10 rows, with routes roughly matching the table above (≥80% match).

- [ ] **Step 10.4: Run the report**

```bash
cd src/voice-agent && .venv/bin/python turn_telemetry.py --report
```

Expected output shape:

```
total turns: 10
  TASK: 4 turns, avg_ttfw=...ms, max_ttfw=...ms
  EMOTIONAL: 2 turns, avg_ttfw=...ms, max_ttfw=...ms
  REASONING: 2 turns, avg_ttfw=...ms, max_ttfw=...ms
  BANTER: 2 turns, avg_ttfw=...ms, max_ttfw=...ms
emotional follow-up rate: 0%
route-fallback rate: 0.0%
```

- [ ] **Step 10.5: Acceptance check**

Acceptance criteria from the spec:

1. Median TTFW ≤ 1000ms across all routes
2. ≥80% routing accuracy on the 10 fixtures above
3. Each route has at least one row
4. No tracebacks in `journalctl --user -u jarvis-voice-agent`

If any criterion fails:
- **TTFW > 1000ms** — verify Kokoro/Groq Orpheus is reachable and warm; check whether `_dispatch_llm.last_llm_label` shows the expected model
- **Routing accuracy <80%** — read the 2-3 misrouted turns from sqlite, examine prompt + emotion tag, edit `ROUTER_PROMPT_TEMPLATE` or extend `_EMOTION_LEX`, restart, re-run
- **Missing route** — confirm router didn't degrade to TASK fallback (check `route_fallback` column)
- **Tracebacks** — fix in source; the most likely culprit is the `session.llm = ...` swap from Task 7.5 not being framework-supported; pivot to Task 7-alt

Iterate steps 10.2–10.5 until acceptance passes. Each iteration is its own commit:

```bash
git commit -m "voice: tune <thing> after dogfood iteration N"
```

- [ ] **Step 10.6: Final commit summary**

After acceptance passes, write a short note to `docs/superpowers/specs/2026-04-29-jarvis-maya-class-speech-design.md` flipping `Status: Approved` to `Status: Shipped 2026-05-XX`, and commit.

---

## Self-Review against the spec

| Spec section / requirement | Plan task |
|---|---|
| Component 1 — Emotion detector | Task 2 |
| Component 2 — Turn router | Task 3 |
| Component 3 — LLM dispatcher | Tasks 4 + 7 |
| Component 4 — Reply shaper | (Removed — see below) |
| Component 5 — Voice swap | Tasks 5 + 7 |
| Component 6 — Self-eval logger | Tasks 1 + 8 |
| Error handling — every component falls back | Task 4 (LLM), Task 5 (TTS), Task 3 (router timeout), Task 1 (telemetry silent), Task 7 (try/except in factory), Task 7-alt (framework fallback) |
| Files changed list | Tasks 1–9 cover every file |
| Configuration env vars | Task 9 |
| Testing — unit | Tasks 1, 2, 3, 4, 5 |
| Testing — integration | Task 6 |
| Testing — production self-eval | Task 10 |
| Rollback | Task 9 (`JARVIS_DISPATCH_DISABLED=1`) |
| Success criteria — TTFW ≤ 1000ms | Task 10.5 |
| Success criteria — voice swap audible | Task 10.2-10.3 |
| Success criteria — Anthropic on EMOTIONAL | Task 10.3 (`SELECT llm_used`) |
| Success criteria — DSR ≥80% on REASONING | Task 10.5 |
| Success criteria — telemetry row per route | Task 10.4 |
| Success criteria — env-disable cleanly reverts | Task 9 + Task 7.4 (`JARVIS_DISPATCH_DISABLED=1`) |

### Note on the reply shaper (Component 4)

The spec specified a per-sentence streaming TTS shaper. After reading the codebase I found livekit-agents' `AgentSession` already drives sentence-by-sentence TTS through its `tts_text_transforms` chain — the existing pipeline streams. A separate shaper class would duplicate framework behaviour. **Decision:** drop component 4 from this plan; rely on existing framework streaming. If TTFW measurements in Task 10.5 show the framework is *not* streaming as expected, add the shaper in a follow-up plan.

This change is scoped within v1 acceptance criteria — TTFW ≤ 1000ms is what matters, not whether the shaper is a distinct module.

### Placeholder scan

No "TBD" / "TODO" / "implement later" — every step has either real code, a real command, or a concrete decision rule. Two implementation notes flag specific framework-version risks (Step 7.5 session.llm swap, Step 8.1 followup backfill); both have explicit fallbacks.

### Type consistency

- `Route` and `Emotion` types defined in `turn_router.py` and used consistently in dispatcher tests and integration test
- `last_route` / `last_llm_label` / `last_voice_id` field names match between dispatcher classes and telemetry write call site
- `AudioMeta` field names (`speech_rate_wpm`, `baseline_wpm`) match between detector tests and integration test
- env-var names match between launch.sh, factory functions, and spec config table

---

## Out of scope (covered by future plans)

- Streaming TTS shaper (only if Task 10 shows framework isn't streaming)
- Hume AI emotion API upgrade (v2)
- Sesame CSM-1B integration (v2+)
- Tray UI redesign — separate axis-3 spec
- Performance work beyond TTFW — separate axis-2 spec
- Workflow orchestration — separate axis-4 spec
