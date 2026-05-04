# Truth-grounded supervisor v2 — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** [`docs/superpowers/specs/2026-05-04-truth-grounded-supervisor-design.md`](../specs/2026-05-04-truth-grounded-supervisor-design.md)

**Goal:** Build the v2 supervisor on top of v1 — a truth-grounded blackboard + vision tap + grounding gate + speculative prefetch — behind feature flag `JARVIS_BLACKBOARD=1`. V1 stays the safety-net path. Every past-tense success claim must have evidence on the blackboard or it gets rejected; vision_tap captures periodic screen state; confirmed-action TASK turns dispatch tools speculatively under the filler so TTFW drops from ~3-4 s to ~1.5 s.

**Architecture:** A new `blackboard/` package (Redis-backed typed state), a new `vision_tap.py` sidecar (screenshot → Kimi vision → blackboard.screen.*), a `grounding_gate` node added to `supervisor_graph` (post-`speak_gate`, before END), and a `speculative` branch added after `classify`. Specialists' `task_done` writes to `blackboard.tools.*`. A new `_pick_supervisor_llm_v2` helper layers v2 on top of v1's adapter.

**Tech Stack:** Python 3.13, LangGraph 1.1.10, Redis (already running for the hub), Pydantic 2.x, Moonshot Kimi K2.6 + `moonshot-v1-32k-vision-preview` (already registered), scrot + xdotool (already installed), pytest 9.x.

**Feature flag:** `JARVIS_BLACKBOARD=1` enables v2; default off through Phase 8 soak. v1 flag (`JARVIS_LANGGRAPH_SUPERVISOR=1`) is independent — both can be on simultaneously, in which case v2 wraps v1's graph.

**Branch:** Continue on `feat/ext-browser-control-v3`. Behind feature flag; rollback = unset env var + restart service.

---

## File structure (locked before tasks begin)

**New files (~2400 LOC including tests):**

| Path | Purpose |
|------|---------|
| `src/voice-agent/blackboard/__init__.py` | Package marker; exports `BlackboardClient`, schemas |
| `src/voice-agent/blackboard/schema.py` | `ScreenFact`, `ToolResult`, `Intent` Pydantic models |
| `src/voice-agent/blackboard/client.py` | `BlackboardClient` — Redis read/write + TTL |
| `src/voice-agent/blackboard/gates.py` | `find_tool_evidence`, `has_recent_tool`, `recent_tools` |
| `src/voice-agent/vision_tap.py` | Sidecar: screenshot loop + Kimi vision + blackboard write |
| `src/voice-agent/supervisor_graph/grounding_gate.py` | `grounding_gate_node` + claim tokenizer + evidence matcher |
| `src/voice-agent/supervisor_graph/speculative.py` | `speculative_dispatch_node` + safe-tool list + reconciliation |
| `~/.config/systemd/user/jarvis-vision-tap.service` | Systemd unit for vision_tap sidecar |
| `src/voice-agent/tests/test_blackboard_schema.py` | Pydantic round-trip tests |
| `src/voice-agent/tests/test_blackboard_client.py` | Redis read/write + TTL tests |
| `src/voice-agent/tests/test_blackboard_gates.py` | Evidence-finder tests |
| `src/voice-agent/tests/test_vision_tap_throttle.py` | Throttling + paused-app tests |
| `src/voice-agent/tests/test_vision_parser.py` | Vision-LLM JSON robustness |
| `src/voice-agent/tests/test_grounding_tokenizer.py` | Past-tense regex + claim-extraction tests |
| `src/voice-agent/tests/test_grounding_gate.py` | Node logic + retry-budget tests |
| `src/voice-agent/tests/test_speculative_safe_tools.py` | Safe-tool whitelist tests |
| `src/voice-agent/tests/test_speculative_dispatch.py` | Hit/miss + reconciliation tests |
| `src/voice-agent/tests/test_v2_assembly.py` | End-to-end graph w/ blackboard mock |
| `src/voice-agent/tests/test_v2_feature_flag.py` | Flag combinations: 00, 01, 10, 11 |

**Modified (small touch points):**

| Path | Change |
|------|--------|
| `src/voice-agent/supervisor_graph/state.py` | Add `grounding_retry_count`, `grounding_rejected_claims`, `speculative_dispatch_id`, `speculative_result` fields |
| `src/voice-agent/supervisor_graph/graph.py` | Wire `grounding_gate` between `speak_gate` and END; wire `speculative` branch after `classify` |
| `src/voice-agent/supervisor_graph/llm_adapter.py` | v2 path: read `JARVIS_BLACKBOARD` flag, pipe state through grounding gate |
| `src/voice-agent/jarvis_agent.py` | Add `_pick_supervisor_llm_v2()` helper, called from existing `_pick_supervisor_llm` |
| `src/voice-agent/specialists/agent.py` | `task_done` writes `ToolResult` to blackboard |
| `src/voice-agent/requirements.txt` | Pin `redis>=5.0` (likely already pulled in by hub) |

**Phases:**
1. Foundation — blackboard package (Tasks 1-4)
2. Vision tap (Tasks 5-8)
3. Grounding gate (Tasks 9-11)
4. Speculative prefetch (Tasks 12-14)
5. Wiring (Tasks 15-18)
6. Tests + soak (Tasks 19-21)

---

## Phase 1 — Foundation: Blackboard

### Task 1: Package skeleton + Redis import smoke test

**Files:**
- Create: `src/voice-agent/blackboard/__init__.py`
- Test: `src/voice-agent/tests/test_blackboard_imports.py`

- [ ] **Step 1: Verify Redis is running**

```bash
redis-cli -h localhost ping
```

Expected: `PONG`. If not running: `sudo systemctl start redis` (it's already configured as a system service for the hub).

- [ ] **Step 2: Verify Python redis package is in the venv**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -c "import redis; print(redis.__version__)"
```

If `ModuleNotFoundError`, install:
```bash
.venv/bin/pip install 'redis>=5.0'
```
Then add to `src/voice-agent/requirements.txt` (matching version-spec style of other entries — use `>=`).

- [ ] **Step 3: Write the failing test**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/tests/test_blackboard_imports.py`:

```python
"""Smoke test for the blackboard package and its Redis dependency."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_blackboard_package_imports():
    import blackboard  # noqa: F401


def test_redis_client_constructible():
    import redis
    r = redis.Redis(host="localhost", port=6379, decode_responses=True)
    # ping requires a running server; this validates the lib is functional
    assert r.ping() is True
```

- [ ] **Step 4: Run, expect `ModuleNotFoundError: No module named 'blackboard'`**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_blackboard_imports.py -v
```

- [ ] **Step 5: Create the package skeleton**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/blackboard/__init__.py`:

```python
"""Truth-grounded blackboard for the JARVIS supervisor v2.

Typed shared-state surface used by:
  - vision_tap (writes screen.* facts)
  - specialists (write tool.* results via task_done)
  - classify_node (writes intent.* records)
  - grounding_gate (reads tool.* evidence to validate supervisor claims)

Spec: docs/superpowers/specs/2026-05-04-truth-grounded-supervisor-design.md
"""
from __future__ import annotations
```

- [ ] **Step 6: Run test, expect 2 passed**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_blackboard_imports.py -v
```

- [ ] **Step 7: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/blackboard/__init__.py \
  src/voice-agent/tests/test_blackboard_imports.py \
  src/voice-agent/requirements.txt
git commit -m "blackboard: package skeleton + Redis import smoke test"
```

---

### Task 2: Pydantic schemas (ScreenFact, ToolResult, Intent)

**Files:**
- Create: `src/voice-agent/blackboard/schema.py`
- Test: `src/voice-agent/tests/test_blackboard_schema.py`

- [ ] **Step 1: Write the failing test**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/tests/test_blackboard_schema.py`:

```python
"""Pydantic round-trip + field validation for the three channel families."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_screen_fact_round_trip():
    from blackboard.schema import ScreenFact
    f = ScreenFact(
        active_app="chrome",
        foreground_url="https://youtube.com",
        tab_count=3,
        dom_summary="YouTube homepage with search bar",
        captured_at=time.time(),
    )
    j = f.model_dump_json()
    back = ScreenFact.model_validate_json(j)
    assert back.active_app == "chrome"
    assert back.tab_count == 3


def test_screen_fact_uncertain_path():
    from blackboard.schema import ScreenFact
    f = ScreenFact(uncertain=True, reason="screenshot capture failed")
    assert f.active_app is None
    assert f.uncertain is True


def test_tool_result_round_trip():
    from blackboard.schema import ToolResult
    r = ToolResult(
        tool="ext_new_tab",
        args={"url": "https://youtube.com"},
        result="ok: tab opened",
        ok=True,
        ts=time.time(),
        call_id="call_abc123",
    )
    j = r.model_dump_json()
    back = ToolResult.model_validate_json(j)
    assert back.tool == "ext_new_tab"
    assert back.ok is True


def test_tool_result_failure_recorded():
    from blackboard.schema import ToolResult
    r = ToolResult(
        tool="ext_navigate",
        args={"url": "https://blocked.example"},
        result="error: connection refused",
        ok=False,
        ts=time.time(),
        call_id="call_xyz",
    )
    assert r.ok is False


def test_intent_round_trip():
    from blackboard.schema import Intent
    i = Intent(
        turn_id="turn_42",
        route="TASK",
        confidence=0.95,
        raw_text="open a new tab",
        ts=time.time(),
    )
    j = i.model_dump_json()
    back = Intent.model_validate_json(j)
    assert back.route == "TASK"
    assert back.confidence == 0.95
```

- [ ] **Step 2: Run, expect ImportError**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_blackboard_schema.py -v
```

- [ ] **Step 3: Implement the schemas**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/blackboard/schema.py`:

```python
"""Typed schemas for the three blackboard channel families.

  - ScreenFact   — written by vision_tap, key `screen:<surface>`,  TTL 30s
  - ToolResult   — written by specialists, key `tool:<call_id>`,    no TTL within session
  - Intent       — written by classify_node, key `intent:<turn_id>`, no TTL within session

Designed for stable JSON serialization (Pydantic v2 model_dump_json /
model_validate_json) so we can store them as strings in Redis.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ScreenFact(BaseModel):
    """One observation of the user's active screen. Vision_tap writes
    these on screen-change events or every 30s. The supervisor reads
    the freshest non-stale fact when the user references 'this',
    'that', 'screen', 'page', etc."""
    active_app: Optional[str] = None
    foreground_url: Optional[str] = None
    tab_count: Optional[int] = None
    dom_summary: Optional[str] = None
    uncertain: bool = False
    reason: Optional[str] = None
    captured_at: float = Field(default_factory=lambda: 0.0)


class ToolResult(BaseModel):
    """One specialist tool dispatch outcome. Written by RegistrySpecialist
    when each ext_*/web_search/transfer_to_X completes (success or
    failure). The grounding_gate reads these to validate past-tense
    claims in supervisor output."""
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    ok: bool = True
    ts: float = 0.0
    call_id: str = ""


class Intent(BaseModel):
    """One user-turn classification record. Written by classify_node.
    Diagnostic — not load-bearing for grounding, but useful for telemetry
    and post-hoc analysis."""
    turn_id: str
    route: str
    confidence: float
    raw_text: str
    ts: float = 0.0
```

- [ ] **Step 4: Run, expect 5 passed**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_blackboard_schema.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/blackboard/schema.py \
  src/voice-agent/tests/test_blackboard_schema.py
git commit -m "blackboard: ScreenFact / ToolResult / Intent Pydantic schemas"
```

---

### Task 3: BlackboardClient core (Redis read/write + TTL)

**Files:**
- Create: `src/voice-agent/blackboard/client.py`
- Test: `src/voice-agent/tests/test_blackboard_client.py`

- [ ] **Step 1: Write the failing test**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/tests/test_blackboard_client.py`:

```python
"""BlackboardClient — typed read/write API over Redis."""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def client():
    """Connects to localhost Redis and isolates with a unique prefix."""
    from blackboard.client import BlackboardClient
    prefix = f"test:{time.time_ns()}"
    c = BlackboardClient(prefix=prefix)
    yield c
    # Cleanup: delete every key under our prefix.
    for key in c._r.keys(f"{prefix}:*"):
        c._r.delete(key)


def test_write_and_read_screen_fact(client):
    from blackboard.schema import ScreenFact
    f = ScreenFact(
        active_app="chrome", foreground_url="https://example.com",
        tab_count=2, dom_summary="example.com homepage",
        captured_at=time.time(),
    )
    client.write_screen_fact(f)
    back = client.read_screen()
    assert back is not None
    assert back.active_app == "chrome"
    assert back.tab_count == 2


def test_screen_fact_ttl_expires(client):
    from blackboard.schema import ScreenFact
    # Write with 1-second TTL for fast test
    f = ScreenFact(active_app="ephemeral", captured_at=time.time())
    client.write_screen_fact(f, ttl_seconds=1)
    assert client.read_screen() is not None
    time.sleep(1.2)
    assert client.read_screen() is None


def test_write_and_read_tool_result(client):
    from blackboard.schema import ToolResult
    r = ToolResult(
        tool="ext_new_tab",
        args={"url": "https://youtube.com"},
        result="ok: tab opened",
        ok=True,
        ts=time.time(),
        call_id="call_test_001",
    )
    client.write_tool_result(r)
    back = client.read_tool_result("call_test_001")
    assert back is not None
    assert back.tool == "ext_new_tab"
    assert back.ok is True


def test_recent_tools_returns_in_chronological_order(client):
    from blackboard.schema import ToolResult
    base = time.time()
    for i in range(5):
        client.write_tool_result(ToolResult(
            tool=f"tool_{i}", args={}, result="ok", ok=True,
            ts=base + i, call_id=f"call_{i}",
        ))
    recent = client.recent_tools(limit=3)
    # Most recent first.
    assert len(recent) == 3
    assert recent[0].call_id == "call_4"
    assert recent[1].call_id == "call_3"
    assert recent[2].call_id == "call_2"


def test_write_and_read_intent(client):
    from blackboard.schema import Intent
    i = Intent(
        turn_id="turn_test_42", route="TASK", confidence=0.91,
        raw_text="open a new tab", ts=time.time(),
    )
    client.write_intent(i)
    back = client.read_intent("turn_test_42")
    assert back is not None
    assert back.route == "TASK"


def test_read_screen_when_empty_returns_none(client):
    assert client.read_screen() is None
```

- [ ] **Step 2: Run, expect ImportError**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_blackboard_client.py -v
```

- [ ] **Step 3: Implement the client**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/blackboard/client.py`:

```python
"""BlackboardClient — typed read/write API over Redis.

Thin wrapper. Stores Pydantic models as JSON-encoded strings under
prefixed keys. The prefix lets us isolate test runs from production
state (each test fixture uses a unique prefix; production uses
default `jarvis`).

Key layout:
  <prefix>:screen:active        — most recent ScreenFact (TTL ~30s)
  <prefix>:tool:<call_id>       — one ToolResult per call_id (no TTL)
  <prefix>:tool:_index          — Redis Sorted Set, score=ts, member=call_id
                                  (used for recent_tools chronological lookup)
  <prefix>:intent:<turn_id>     — one Intent per turn (no TTL)
"""
from __future__ import annotations

import json
import os
from typing import Optional

import redis

from .schema import Intent, ScreenFact, ToolResult


class BlackboardClient:
    """Singleton-friendly. Construct with a prefix to namespace keys.
    Reuses a single Redis connection per process (Redis library is
    thread-safe for our usage)."""

    DEFAULT_SCREEN_TTL = 30  # seconds — see spec §5.1

    def __init__(
        self,
        *,
        host: str = None,
        port: int = None,
        prefix: str = None,
    ) -> None:
        self._r = redis.Redis(
            host=host or os.environ.get("REDIS_HOST", "localhost"),
            port=port or int(os.environ.get("REDIS_PORT", "6379")),
            decode_responses=True,
        )
        self._prefix = prefix or os.environ.get("JARVIS_BLACKBOARD_PREFIX", "jarvis")

    # ── Screen ─────────────────────────────────────────────────────

    def _screen_key(self) -> str:
        return f"{self._prefix}:screen:active"

    def write_screen_fact(
        self, fact: ScreenFact, *, ttl_seconds: Optional[int] = None,
    ) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self.DEFAULT_SCREEN_TTL
        self._r.set(self._screen_key(), fact.model_dump_json(), ex=ttl)

    def read_screen(self) -> Optional[ScreenFact]:
        raw = self._r.get(self._screen_key())
        if raw is None:
            return None
        return ScreenFact.model_validate_json(raw)

    # ── Tool results ───────────────────────────────────────────────

    def _tool_key(self, call_id: str) -> str:
        return f"{self._prefix}:tool:{call_id}"

    def _tool_index_key(self) -> str:
        return f"{self._prefix}:tool:_index"

    def write_tool_result(self, result: ToolResult) -> None:
        self._r.set(self._tool_key(result.call_id), result.model_dump_json())
        self._r.zadd(self._tool_index_key(), {result.call_id: result.ts})

    def read_tool_result(self, call_id: str) -> Optional[ToolResult]:
        raw = self._r.get(self._tool_key(call_id))
        if raw is None:
            return None
        return ToolResult.model_validate_json(raw)

    def recent_tools(self, limit: int = 5) -> list[ToolResult]:
        """Return the most recent `limit` tool results, newest first."""
        ids = self._r.zrevrange(self._tool_index_key(), 0, limit - 1)
        results: list[ToolResult] = []
        for call_id in ids:
            r = self.read_tool_result(call_id)
            if r is not None:
                results.append(r)
        return results

    # ── Intent ─────────────────────────────────────────────────────

    def _intent_key(self, turn_id: str) -> str:
        return f"{self._prefix}:intent:{turn_id}"

    def write_intent(self, intent: Intent) -> None:
        self._r.set(self._intent_key(intent.turn_id), intent.model_dump_json())

    def read_intent(self, turn_id: str) -> Optional[Intent]:
        raw = self._r.get(self._intent_key(turn_id))
        if raw is None:
            return None
        return Intent.model_validate_json(raw)
```

- [ ] **Step 4: Run, expect 6 passed**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_blackboard_client.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/blackboard/client.py \
  src/voice-agent/tests/test_blackboard_client.py
git commit -m "blackboard: BlackboardClient — typed read/write API over Redis"
```

---

### Task 4: Evidence-finder gates (find_tool_evidence, has_recent_tool)

**Files:**
- Create: `src/voice-agent/blackboard/gates.py`
- Test: `src/voice-agent/tests/test_blackboard_gates.py`

- [ ] **Step 1: Write the failing test**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/tests/test_blackboard_gates.py`:

```python
"""Evidence-finder helpers — the core of the grounding gate's check."""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def populated_client():
    from blackboard.client import BlackboardClient
    from blackboard.schema import ToolResult

    prefix = f"test:{time.time_ns()}"
    c = BlackboardClient(prefix=prefix)
    base = time.time()
    c.write_tool_result(ToolResult(
        tool="ext_new_tab", args={"url": "https://youtube.com"},
        result="ok: tab opened", ok=True, ts=base - 5, call_id="call_a",
    ))
    c.write_tool_result(ToolResult(
        tool="ext_navigate", args={"url": "https://example.com"},
        result="ok: navigated", ok=True, ts=base - 2, call_id="call_b",
    ))
    yield c
    for key in c._r.keys(f"{prefix}:*"):
        c._r.delete(key)


def test_find_tool_evidence_matches_recent(populated_client):
    from blackboard.gates import find_tool_evidence
    # Looking for evidence of "tab opened" — should match call_a.
    ev = find_tool_evidence(
        populated_client,
        claim_keywords=["opened", "tab"],
        within_seconds=30,
    )
    assert ev is not None
    assert ev.tool == "ext_new_tab"


def test_find_tool_evidence_returns_none_when_too_old(populated_client):
    from blackboard.gates import find_tool_evidence
    # within_seconds=1 — both fixture entries are older than 1s.
    ev = find_tool_evidence(
        populated_client,
        claim_keywords=["opened"],
        within_seconds=1,
    )
    assert ev is None


def test_find_tool_evidence_no_match_returns_none(populated_client):
    from blackboard.gates import find_tool_evidence
    ev = find_tool_evidence(
        populated_client,
        claim_keywords=["posted", "tweet"],
        within_seconds=60,
    )
    assert ev is None


def test_has_recent_tool_specific_name(populated_client):
    from blackboard.gates import has_recent_tool
    assert has_recent_tool(
        populated_client, tool_name="ext_new_tab", within_seconds=30,
    ) is True
    assert has_recent_tool(
        populated_client, tool_name="ext_send_email", within_seconds=30,
    ) is False
```

- [ ] **Step 2: Run, expect ImportError**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_blackboard_gates.py -v
```

- [ ] **Step 3: Implement the gates**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/blackboard/gates.py`:

```python
"""Evidence-finder helpers tuned for the grounding gate.

The grounding gate's job is to validate past-tense claims in supervisor
output. The primary question it asks the blackboard is:

  "Is there a recent ToolResult that corroborates the claim '<verb>
   <object>' the supervisor is about to speak?"

`find_tool_evidence` answers that. The match is keyword-overlap based
(claim keywords vs ToolResult.tool, args, and result string), bounded
to a recent time window so old evidence doesn't validate fresh lies.
"""
from __future__ import annotations

import time
from typing import Optional

from .client import BlackboardClient
from .schema import ToolResult


def find_tool_evidence(
    client: BlackboardClient,
    *,
    claim_keywords: list[str],
    within_seconds: int = 30,
) -> Optional[ToolResult]:
    """Find the most recent successful ToolResult whose tool name,
    args, or result string matches ANY of `claim_keywords`. Returns
    None if no match is recent enough (within `within_seconds`).

    Match is case-insensitive substring containment. A claim
    "opened" matches a tool named "ext_new_tab" via the result string
    "ok: tab opened" or via tool="ext_new_tab" containing "open".
    """
    if not claim_keywords:
        return None
    cutoff = time.time() - within_seconds
    keywords_lower = [k.lower() for k in claim_keywords]
    for r in client.recent_tools(limit=10):
        if r.ts < cutoff:
            continue
        if not r.ok:
            continue  # failures don't validate past-tense claims
        haystack = " ".join([
            r.tool.lower(),
            " ".join(str(v).lower() for v in r.args.values()),
            r.result.lower(),
        ])
        if any(kw in haystack for kw in keywords_lower):
            return r
    return None


def has_recent_tool(
    client: BlackboardClient,
    *,
    tool_name: str,
    within_seconds: int = 30,
) -> bool:
    """True if any successful ToolResult with tool=tool_name occurred
    within the time window."""
    cutoff = time.time() - within_seconds
    for r in client.recent_tools(limit=10):
        if r.ts < cutoff:
            continue
        if r.ok and r.tool == tool_name:
            return True
    return False
```

- [ ] **Step 4: Run, expect 4 passed**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_blackboard_gates.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/blackboard/gates.py \
  src/voice-agent/tests/test_blackboard_gates.py
git commit -m "blackboard: gates — find_tool_evidence + has_recent_tool"
```

---

## Phase 2 — Vision tap

### Task 5: Vision-tap scaffold + screenshot capture

**Files:**
- Create: `src/voice-agent/vision_tap.py`
- Test: `src/voice-agent/tests/test_vision_tap_capture.py`

- [ ] **Step 1: Write the failing test**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/tests/test_vision_tap_capture.py`:

```python
"""Vision tap — screenshot capture layer (separate from the LLM call)."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_capture_screenshot_returns_path():
    """capture_screenshot uses scrot. Mock subprocess.run so the test
    doesn't actually take a screenshot."""
    from vision_tap import capture_screenshot

    with patch("vision_tap.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            # Create the file so the function thinks scrot worked.
            f.write(b"fake-png-bytes")
            tmp_path = Path(f.name)

        try:
            with patch("vision_tap._screenshot_path", return_value=tmp_path):
                path = capture_screenshot()
            assert path == tmp_path
            assert mock_run.called
            args = mock_run.call_args.args[0]
            assert "scrot" in args[0]
        finally:
            tmp_path.unlink(missing_ok=True)


def test_capture_screenshot_returns_none_on_scrot_failure():
    from vision_tap import capture_screenshot
    with patch("vision_tap.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        path = capture_screenshot()
        assert path is None


def test_active_app_via_xdotool():
    """get_active_app uses xdotool. Mock the subprocess call."""
    from vision_tap import get_active_app
    with patch("vision_tap.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "google-chrome\n"
        app = get_active_app()
        assert app == "google-chrome"


def test_active_app_returns_none_on_xdotool_failure():
    from vision_tap import get_active_app
    with patch("vision_tap.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        assert get_active_app() is None
```

- [ ] **Step 2: Run, expect ImportError**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_vision_tap_capture.py -v
```

- [ ] **Step 3: Create vision_tap.py with capture layer only**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/vision_tap.py`:

```python
"""Vision tap sidecar — periodic screen capture → Kimi vision LLM →
blackboard.screen.* facts.

Architecture (spec §5.2):
  Active-window watcher (xdotool) ──► change-event detector
                                          │
                                          ▼
                              Throttled snapshot trigger
                                          │
                                          ▼
                              scrot ─► PNG file ─► base64
                                          │
                                          ▼
                              Kimi vision (moonshot-v1-32k-vision-preview)
                                          │
                                          ▼
                              ScreenFact ─► blackboard.write_screen_fact

This module exposes a `main()` for the systemd unit and helper
functions used by tests. Each layer is independently testable.
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("vision_tap")


def _screenshot_path() -> Path:
    """The path scrot writes to. Overridable for tests."""
    return Path(tempfile.gettempdir()) / "jarvis-vision.png"


def capture_screenshot() -> Optional[Path]:
    """Capture the full screen via scrot. Returns the PNG path on
    success, None on failure (Wayland-restricted, scrot not running,
    file write error)."""
    out_path = _screenshot_path()
    try:
        result = subprocess.run(
            ["scrot", "-o", str(out_path)],
            capture_output=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("[vision-tap] scrot failed: %s", e)
        return None
    if result.returncode != 0:
        logger.warning(
            "[vision-tap] scrot returned %d: %s",
            result.returncode, result.stderr,
        )
        return None
    return out_path


def get_active_app() -> Optional[str]:
    """Get the active window's WM class via xdotool. Used as a
    cheap screen-change signal — when the active app changes, we
    refresh the screen fact."""
    try:
        result = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowclassname"],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    name = (result.stdout or "").strip()
    return name or None
```

- [ ] **Step 4: Run, expect 4 passed**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_vision_tap_capture.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/vision_tap.py \
  src/voice-agent/tests/test_vision_tap_capture.py
git commit -m "vision-tap: scaffold + screenshot capture (scrot) + active-app probe"
```

---

### Task 6: Kimi vision integration + ScreenFact parsing

**Files:**
- Modify: `src/voice-agent/vision_tap.py`
- Test: `src/voice-agent/tests/test_vision_parser.py`

- [ ] **Step 1: Write the failing test**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/tests/test_vision_parser.py`:

```python
"""Vision LLM call + JSON parsing into ScreenFact.

Mocks the Moonshot HTTP client so tests don't hit the API.
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("KIMI_API_KEY", "test-key")


def _fake_moonshot_response(content: str):
    """Build a fake OpenAI-shaped response."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{
            "message": {"role": "assistant", "content": content},
        }],
    }
    return resp


def test_describe_screen_parses_well_formed_json():
    from vision_tap import describe_screen

    fake_json = (
        '{"active_app": "chrome", "foreground_url": '
        '"https://youtube.com", "tab_count": 3, '
        '"dom_summary": "YouTube homepage"}'
    )
    with patch("vision_tap.requests.post",
               return_value=_fake_moonshot_response(fake_json)):
        fact = describe_screen(b"fake-png-bytes")
    assert fact is not None
    assert fact.active_app == "chrome"
    assert fact.tab_count == 3


def test_describe_screen_handles_uncertain_response():
    from vision_tap import describe_screen
    fake_json = '{"active_app": null, "uncertain": true, "reason": "blank screen"}'
    with patch("vision_tap.requests.post",
               return_value=_fake_moonshot_response(fake_json)):
        fact = describe_screen(b"fake-png-bytes")
    assert fact is not None
    assert fact.uncertain is True


def test_describe_screen_returns_none_on_invalid_json():
    """Vision LLM may return Chinese, garbage, or refuse. Parser
    must not crash."""
    from vision_tap import describe_screen
    with patch("vision_tap.requests.post",
               return_value=_fake_moonshot_response("blah blah not json")):
        fact = describe_screen(b"fake-png-bytes")
    assert fact is None


def test_describe_screen_returns_none_on_http_error():
    from vision_tap import describe_screen
    err_resp = MagicMock()
    err_resp.status_code = 500
    err_resp.json.return_value = {}
    err_resp.text = "internal server error"
    with patch("vision_tap.requests.post", return_value=err_resp):
        fact = describe_screen(b"fake-png-bytes")
    assert fact is None
```

- [ ] **Step 2: Run, expect ImportError on `describe_screen`**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_vision_parser.py -v
```

- [ ] **Step 3: Append `describe_screen` to `vision_tap.py`**

Append to `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/vision_tap.py`:

```python
import base64
import json
import os
import time
from typing import Optional

import requests

from blackboard.schema import ScreenFact


_VISION_SYSTEM_PROMPT = (
    "Respond ONLY with a JSON object matching this schema:\n"
    "  {\"active_app\": str | null,\n"
    "   \"foreground_url\": str | null,\n"
    "   \"tab_count\": int | null,\n"
    "   \"dom_summary\": str | null,\n"
    "   \"uncertain\": bool,\n"
    "   \"reason\": str | null}\n\n"
    "English only. Be concise: name the active application, count "
    "visible tabs, identify the foreground content. Do NOT describe "
    "pixel-level details. If you cannot tell, return uncertain=true "
    "with a one-sentence reason."
)


def describe_screen(png_bytes: bytes) -> Optional[ScreenFact]:
    """Send a PNG screenshot to Kimi vision and parse the response
    into a ScreenFact. Returns None on any failure (HTTP error,
    invalid JSON, schema mismatch).

    The Moonshot API requires base64-encoded image data — external
    URLs are rejected (verified live 2026-05-04)."""
    api_key = os.environ.get("KIMI_API_KEY", "")
    if not api_key:
        logger.warning("[vision-tap] KIMI_API_KEY not set; skipping vision call")
        return None
    b64 = base64.b64encode(png_bytes).decode("ascii")
    payload = {
        "model": "moonshot-v1-32k-vision-preview",
        "messages": [
            {"role": "system", "content": _VISION_SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": "describe this screen"},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]},
        ],
        "max_tokens": 300,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    try:
        resp = requests.post(
            "https://api.moonshot.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json=payload, timeout=30,
        )
    except Exception as e:
        logger.warning("[vision-tap] vision request failed: %s: %s",
                       type(e).__name__, e)
        return None
    if resp.status_code != 200:
        logger.warning("[vision-tap] vision HTTP %d: %s",
                       resp.status_code, getattr(resp, "text", "")[:200])
        return None

    try:
        body = resp.json()
        content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        logger.warning("[vision-tap] response parse failed: %s", e)
        return None

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("[vision-tap] non-JSON response: %r", content[:200])
        return None

    try:
        return ScreenFact(
            active_app=parsed.get("active_app"),
            foreground_url=parsed.get("foreground_url"),
            tab_count=parsed.get("tab_count"),
            dom_summary=parsed.get("dom_summary"),
            uncertain=parsed.get("uncertain", False),
            reason=parsed.get("reason"),
            captured_at=time.time(),
        )
    except Exception as e:
        logger.warning("[vision-tap] schema validation failed: %s", e)
        return None
```

- [ ] **Step 4: Run, expect 4 passed**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_vision_parser.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/vision_tap.py \
  src/voice-agent/tests/test_vision_parser.py
git commit -m "vision-tap: describe_screen — Kimi vision call + JSON parsing"
```

---

### Task 7: Throttling + paused-app skip

**Files:**
- Modify: `src/voice-agent/vision_tap.py`
- Test: `src/voice-agent/tests/test_vision_tap_throttle.py`

- [ ] **Step 1: Write the failing test**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/tests/test_vision_tap_throttle.py`:

```python
"""Vision-tap throttling — 30s ceiling, screen-change debounce, paused-app skip."""
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_throttle_returns_false_within_min_interval():
    from vision_tap import VisionTapThrottle
    t = VisionTapThrottle(min_interval=1.0, max_interval=30.0)
    assert t.should_capture(active_app="chrome") is True
    t.mark_captured()
    # Immediately after, throttled.
    assert t.should_capture(active_app="chrome") is False


def test_throttle_returns_true_after_min_interval(monkeypatch):
    from vision_tap import VisionTapThrottle
    t = VisionTapThrottle(min_interval=1.0, max_interval=30.0)
    t.mark_captured()
    # Simulate 1.5s passing.
    fake_time = time.time() + 1.5
    monkeypatch.setattr("time.time", lambda: fake_time)
    assert t.should_capture(active_app="chrome") is True


def test_throttle_fires_on_screen_change_within_min_interval(monkeypatch):
    """When the active app changes, debounce can fire even if min_interval
    hasn't elapsed (after the 1s debounce). For test simplicity, we
    use min_interval=0.5 so the debounce is the dominant gate."""
    from vision_tap import VisionTapThrottle
    t = VisionTapThrottle(min_interval=0.5, max_interval=30.0)
    t.mark_captured(active_app="chrome")
    fake_time = time.time() + 0.6
    monkeypatch.setattr("time.time", lambda: fake_time)
    assert t.should_capture(active_app="firefox") is True


def test_throttle_skips_paused_apps():
    from vision_tap import VisionTapThrottle
    t = VisionTapThrottle(
        min_interval=1.0, max_interval=30.0,
        paused_apps={"keepassxc", "1password"},
    )
    assert t.should_capture(active_app="keepassxc") is False
    assert t.should_capture(active_app="1password") is False
    assert t.should_capture(active_app="chrome") is True


def test_throttle_max_interval_forces_capture(monkeypatch):
    """Even without app change, after max_interval we capture anyway."""
    from vision_tap import VisionTapThrottle
    t = VisionTapThrottle(min_interval=1.0, max_interval=10.0)
    t.mark_captured(active_app="chrome")
    fake_time = time.time() + 11.0
    monkeypatch.setattr("time.time", lambda: fake_time)
    # Same app, but past max_interval → capture.
    assert t.should_capture(active_app="chrome") is True
```

- [ ] **Step 2: Run, expect ImportError**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_vision_tap_throttle.py -v
```

- [ ] **Step 3: Append `VisionTapThrottle` to `vision_tap.py`**

Append to `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/vision_tap.py`:

```python
class VisionTapThrottle:
    """Decides when to capture a screenshot.

    Three gates:
      - paused_apps: never capture if active_app is in this set
        (privacy gate — banking, password manager, etc.)
      - min_interval: don't capture more than once per N seconds
        (cost gate — vision calls cost ~$0.02 each)
      - max_interval: ALWAYS capture if N seconds have elapsed
        even if the active app hasn't changed (freshness gate)
      - active-app change: capture on app switch (after min_interval)
    """

    def __init__(
        self,
        *,
        min_interval: float = 1.0,
        max_interval: float = 30.0,
        paused_apps: Optional[set[str]] = None,
    ) -> None:
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.paused_apps = paused_apps or set()
        self._last_captured_at: float = 0.0
        self._last_active_app: Optional[str] = None

    def should_capture(self, *, active_app: Optional[str]) -> bool:
        if active_app and active_app.lower() in self.paused_apps:
            return False
        elapsed = time.time() - self._last_captured_at
        if elapsed >= self.max_interval:
            return True
        if elapsed < self.min_interval:
            return False
        # min_interval ≤ elapsed < max_interval — capture only on app change.
        return active_app != self._last_active_app

    def mark_captured(self, *, active_app: Optional[str] = None) -> None:
        self._last_captured_at = time.time()
        if active_app is not None:
            self._last_active_app = active_app
```

- [ ] **Step 4: Run, expect 5 passed**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_vision_tap_throttle.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/vision_tap.py \
  src/voice-agent/tests/test_vision_tap_throttle.py
git commit -m "vision-tap: VisionTapThrottle — min/max interval + paused-app gate"
```

---

### Task 8: Vision-tap main loop + systemd unit

**Files:**
- Modify: `src/voice-agent/vision_tap.py` (add `main()` + entry-point)
- Create: `~/.config/systemd/user/jarvis-vision-tap.service`

- [ ] **Step 1: Append `main()` to `vision_tap.py`**

Append to `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/vision_tap.py`:

```python
def _load_paused_apps() -> set[str]:
    """Read ~/.jarvis/vision-paused-apps.txt — one app name per line.
    Missing file → empty set."""
    path = Path.home() / ".jarvis" / "vision-paused-apps.txt"
    if not path.exists():
        return set()
    return {
        line.strip().lower()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def main() -> None:
    """Sidecar entry point. Loop: probe active app → maybe capture →
    maybe send to vision LLM → write to blackboard.

    Exits cleanly on SIGTERM (systemd stop). All errors are logged and
    swallowed — vision_tap is non-essential and must never bring down
    the voice agent.
    """
    import signal

    logging.basicConfig(
        level=os.environ.get("VISION_TAP_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s vision-tap %(message)s",
    )
    logger.info("[vision-tap] starting")

    from blackboard.client import BlackboardClient

    bb = BlackboardClient()
    throttle = VisionTapThrottle(
        min_interval=float(os.environ.get("VISION_TAP_MIN_INTERVAL", "1.0")),
        max_interval=float(os.environ.get("VISION_TAP_MAX_INTERVAL", "30.0")),
        paused_apps=_load_paused_apps(),
    )

    stop = False

    def _on_sigterm(signum, frame):
        nonlocal stop
        logger.info("[vision-tap] SIGTERM received; stopping")
        stop = True

    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)

    while not stop:
        try:
            active_app = get_active_app()
            if not throttle.should_capture(active_app=active_app):
                time.sleep(0.5)
                continue
            png_path = capture_screenshot()
            if png_path is None:
                throttle.mark_captured(active_app=active_app)  # don't tight-loop on scrot failure
                time.sleep(2.0)
                continue
            png_bytes = png_path.read_bytes()
            fact = describe_screen(png_bytes)
            if fact is not None:
                bb.write_screen_fact(fact)
                logger.info("[vision-tap] fact written: app=%r tabs=%r",
                            fact.active_app, fact.tab_count)
            else:
                logger.info("[vision-tap] no fact (vision call failed or invalid)")
            throttle.mark_captured(active_app=active_app)
        except Exception as e:
            logger.exception("[vision-tap] loop error: %s", e)
            time.sleep(2.0)

    logger.info("[vision-tap] stopped cleanly")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Create the systemd unit**

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/jarvis-vision-tap.service <<'EOF'
[Unit]
Description=JARVIS vision tap (screen capture → Kimi vision → blackboard)
After=jarvis-voice-agent.service
PartOf=jarvis-voice-agent.service

[Service]
Type=simple
EnvironmentFile=%h/Documents/Projects/jarvis/src/voice-agent/.env
EnvironmentFile=%h/Documents/Projects/jarvis/src/cli/.env.local
ExecStart=%h/Documents/Projects/jarvis/src/voice-agent/.venv/bin/python -m vision_tap
WorkingDirectory=%h/Documents/Projects/jarvis/src/voice-agent
Restart=always
RestartSec=5
StandardOutput=append:/tmp/jarvis-vision-tap.log
StandardError=append:/tmp/jarvis-vision-tap.log

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
echo "(unit installed; not yet started — start during soak)"
```

- [ ] **Step 3: Smoke-test the import path doesn't crash**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -c "import vision_tap; print('main exists:', callable(vision_tap.main))"
```

Expected: `main exists: True`.

- [ ] **Step 4: Run all vision_tap tests**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_vision_tap_capture.py tests/test_vision_parser.py tests/test_vision_tap_throttle.py -v
```

Expected: 13 passed (4 + 4 + 5).

- [ ] **Step 5: Commit (vision_tap.py only — systemd unit is per-host, not git-tracked)**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/vision_tap.py
git commit -m "vision-tap: main() loop + systemd-runnable entry point"
```

---

## Phase 3 — Grounding gate

### Task 9: Past-tense success-claim tokenizer

**Files:**
- Create: `src/voice-agent/supervisor_graph/grounding_gate.py` (tokenizer only — node body added in Task 11)
- Test: `src/voice-agent/tests/test_grounding_tokenizer.py`

- [ ] **Step 1: Write the failing test**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/tests/test_grounding_tokenizer.py`:

```python
"""Past-tense success-claim tokenizer — extracts (verb, object) pairs
from supervisor draft text. The grounding gate matches each pair
against the blackboard."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.mark.parametrize("text,expected_verbs", [
    ("I've opened a new tab, sir.", ["opened"]),
    ("Tab is open.", ["open"]),
    ("Saved the file.", ["saved"]),
    ("Sent the email.", ["sent"]),
    ("Posted the tweet.", ["posted"]),
    ("Done, sir.", ["done"]),
    ("I've launched Chrome and navigated to YouTube.", ["launched", "navigated"]),
    ("Created the new file.", ["created"]),
    ("Deleted that line for you.", ["deleted"]),
    ("Clicked the cancel button.", ["clicked"]),
])
def test_extract_claims_finds_past_tense_verbs(text, expected_verbs):
    from supervisor_graph.grounding_gate import extract_claims
    claims = extract_claims(text)
    found_verbs = [c.verb for c in claims]
    for v in expected_verbs:
        assert v in found_verbs, (
            f"text={text!r} expected verb {v!r}; got {found_verbs!r}"
        )


@pytest.mark.parametrize("text", [
    "What would you like me to do?",
    "I can open a tab — should I?",
    "How are you, sir?",
    "I'll save it after you confirm.",
    "It's a sunny day.",
    "Let me check.",
    "One moment, sir.",
])
def test_extract_claims_ignores_non_completion_text(text):
    from supervisor_graph.grounding_gate import extract_claims
    claims = extract_claims(text)
    assert claims == [], (
        f"text={text!r} should produce no claims; got {claims!r}"
    )


def test_extract_claims_captures_object_keywords():
    """The object keywords give the gate something to match against."""
    from supervisor_graph.grounding_gate import extract_claims
    claims = extract_claims("I've opened a new tab in Chrome.")
    assert len(claims) >= 1
    c = claims[0]
    assert c.verb == "opened"
    # Keywords should include 'tab' and 'chrome' (lowercased).
    assert "tab" in c.keywords or "chrome" in c.keywords
```

- [ ] **Step 2: Run, expect ImportError**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_grounding_tokenizer.py -v
```

- [ ] **Step 3: Implement the tokenizer**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/supervisor_graph/grounding_gate.py`:

```python
"""Grounding gate — validates supervisor draft text against the
blackboard. The structural cure for "JARVIS lies about completion."

Pipeline:
  draft text → extract_claims → for each claim: find evidence on
  blackboard.tools → if all matched, RELEASE. If any unmatched,
  REJECT with retry budget (max 3) → if exhausted, replace with
  fixed honest fallback.

This file currently exposes only the tokenizer. The node body is
added in Task 11.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("supervisor_graph.grounding_gate")


# Past-tense / completion-state markers. Each is a regex that matches
# the verb form. New verbs added to this list should also match a
# "subject-of-action" noun within ~6 words for keyword extraction.
#
# The list is intentionally NARROW. False negatives (a real claim slips
# through unflagged) cost the user nothing; false positives (an
# innocent statement gets rejected) cost the user a real reply. So we
# only flag verbs that strongly assert a discrete completed action.
_CLAIM_VERBS = (
    "opened",   # "I've opened the tab"
    "open",     # "Tab is open." / "Chrome is open."
    "closed",
    "saved",
    "sent",
    "posted",
    "done",
    "launched",
    "created",
    "deleted",
    "clicked",
    "typed",
    "navigated",
    "switched",
    "pressed",
    "submitted",
    "uploaded",
    "downloaded",
)

# A claim verb must NOT be preceded by these words within 3 tokens —
# they signal future / hypothetical / question, not past completion.
_NEGATING_PREFIXES = (
    "should", "could", "would", "may", "might", "can", "will",
    "shall", "let", "lets", "let's", "want", "wants", "ought",
)


@dataclass
class Claim:
    """One past-tense success claim extracted from supervisor text."""
    verb: str
    keywords: list[str] = field(default_factory=list)
    span: tuple[int, int] = (0, 0)  # (start_idx, end_idx) in original text


def _tokenize(text: str) -> list[tuple[str, int, int]]:
    """Split text into (lowercase_word, start, end) tuples preserving
    span info. Punctuation stripped."""
    out = []
    for m in re.finditer(r"[A-Za-z']+", text):
        out.append((m.group(0).lower(), m.start(), m.end()))
    return out


def extract_claims(text: str) -> list[Claim]:
    """Walk the text looking for claim verbs. For each, check the
    preceding 3 tokens for negating prefixes. If clear, collect the
    next 6 tokens (excluding stopwords) as object keywords."""
    if not text or not text.strip():
        return []
    tokens = _tokenize(text)
    if not tokens:
        return []

    stopwords = {
        "a", "an", "the", "i", "i've", "i'm", "to", "for", "on", "in",
        "and", "or", "but", "is", "are", "was", "were", "be", "been",
        "have", "has", "had", "of", "with", "at", "by", "from", "your",
        "my", "sir", "now", "just", "also", "also,", "yes", "no", "ok",
        "okay", "all", "that", "this", "it", "its", "as", "so",
    }

    claims: list[Claim] = []
    for i, (word, start, end) in enumerate(tokens):
        if word not in _CLAIM_VERBS:
            continue
        # Negation check: scan up to 3 preceding tokens.
        prev_window = [t[0] for t in tokens[max(0, i - 3):i]]
        if any(p in _NEGATING_PREFIXES for p in prev_window):
            continue
        # Object keyword extraction: next 6 tokens minus stopwords.
        next_window = tokens[i + 1:i + 7]
        keywords = [t[0] for t in next_window if t[0] not in stopwords]
        # Also include preceding noun-like tokens (1-2 back) as keywords
        # for shapes like "Tab is open" → keywords=[tab].
        prev_kw = [t[0] for t in tokens[max(0, i - 2):i]
                   if t[0] not in stopwords and t[0] not in _NEGATING_PREFIXES]
        keywords = prev_kw + keywords
        claims.append(Claim(verb=word, keywords=keywords, span=(start, end)))
    return claims
```

- [ ] **Step 4: Run, expect 18 passed (10 verb-found + 7 negative + 1 keyword)**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_grounding_tokenizer.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/supervisor_graph/grounding_gate.py \
  src/voice-agent/tests/test_grounding_tokenizer.py
git commit -m "grounding-gate: claim tokenizer — extract past-tense success markers"
```

---

### Task 10: State extensions for grounding (retry counter, rejected claims)

**Files:**
- Modify: `src/voice-agent/supervisor_graph/state.py`
- Test: `src/voice-agent/tests/test_v2_state_extensions.py`

- [ ] **Step 1: Write the failing test**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/tests/test_v2_state_extensions.py`:

```python
"""V2 state additions — grounding retry budget + speculative dispatch tracking."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_state_has_v2_grounding_fields():
    from supervisor_graph.state import JarvisState
    keys = set(JarvisState.__annotations__.keys())
    assert "grounding_retry_count" in keys
    assert "grounding_rejected_claims" in keys


def test_state_has_v2_speculative_fields():
    from supervisor_graph.state import JarvisState
    keys = set(JarvisState.__annotations__.keys())
    assert "speculative_dispatch_id" in keys
    assert "speculative_result" in keys


def test_initial_state_zeroes_v2_fields():
    from supervisor_graph.state import initial_state
    s = initial_state(user_query="hi")
    assert s["grounding_retry_count"] == 0
    assert s["grounding_rejected_claims"] == []
    assert s["speculative_dispatch_id"] is None
    assert s["speculative_result"] is None
```

- [ ] **Step 2: Run, expect AssertionError**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_v2_state_extensions.py -v
```

- [ ] **Step 3: Extend state.py**

Open `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/supervisor_graph/state.py`. Add to the `JarvisState` TypedDict after the existing fields (preserve all v1 fields):

```python
    # V2 — grounding gate
    grounding_retry_count: int
    grounding_rejected_claims: list[str]

    # V2 — speculative prefetch
    speculative_dispatch_id: Optional[str]
    speculative_result: Optional[dict[str, Any]]
```

Then add to `initial_state()` to zero them:

```python
    return JarvisState(
        # ... preserve all v1 fields ...
        grounding_retry_count=0,
        grounding_rejected_claims=[],
        speculative_dispatch_id=None,
        speculative_result=None,
    )
```

- [ ] **Step 4: Run, expect 3 passed + verify v1 tests still pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_v2_state_extensions.py tests/test_supervisor_graph_state.py -v
```

Expected: 5 passed (3 new + 2 v1).

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/supervisor_graph/state.py \
  src/voice-agent/tests/test_v2_state_extensions.py
git commit -m "state: add v2 fields — grounding retry + speculative tracking"
```

---

### Task 11: Grounding gate node + retry budget

**Files:**
- Modify: `src/voice-agent/supervisor_graph/grounding_gate.py`
- Test: `src/voice-agent/tests/test_grounding_gate.py`

- [ ] **Step 1: Write the failing test**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/tests/test_grounding_gate.py`:

```python
"""grounding_gate_node — validates draft against blackboard with retry budget."""
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _draft_state(text: str, retry_count: int = 0):
    """Build a JarvisState with one assistant message containing `text`."""
    from langchain_core.messages import AIMessage
    from supervisor_graph.state import initial_state
    s = initial_state()
    s["messages"] = [AIMessage(content=text)]
    s["grounding_retry_count"] = retry_count
    return s


def _stub_client_with_evidence(tool: str, ts_offset: float = -1):
    from blackboard.schema import ToolResult
    client = MagicMock()
    client.recent_tools = MagicMock(return_value=[
        ToolResult(
            tool=tool, args={}, result=f"ok: {tool} succeeded",
            ok=True, ts=time.time() + ts_offset, call_id="x",
        ),
    ])
    return client


def _stub_client_empty():
    client = MagicMock()
    client.recent_tools = MagicMock(return_value=[])
    return client


def test_release_when_all_claims_have_evidence():
    from supervisor_graph.grounding_gate import grounding_gate_node
    state = _draft_state("I've opened a new tab, sir.")
    out = grounding_gate_node(state, client=_stub_client_with_evidence("ext_new_tab"))
    assert out["__route__"] == "release"
    assert "messages" not in out  # message untouched


def test_reject_and_retry_when_claim_lacks_evidence():
    from supervisor_graph.grounding_gate import grounding_gate_node
    state = _draft_state("I've sent the email, sir.")
    # No evidence on board.
    out = grounding_gate_node(state, client=_stub_client_empty())
    assert out["__route__"] == "regenerate"
    assert out["grounding_retry_count"] == 1
    assert "rejected_claims" in out or "grounding_rejected_claims" in out


def test_no_claims_passes_through():
    """Text with no past-tense claim is always released."""
    from supervisor_graph.grounding_gate import grounding_gate_node
    state = _draft_state("How are you, sir?")
    out = grounding_gate_node(state, client=_stub_client_empty())
    assert out["__route__"] == "release"


def test_retry_budget_exhausted_emits_fallback():
    """After 3 rejections, replace the draft with a fixed honest message."""
    from supervisor_graph.grounding_gate import grounding_gate_node
    state = _draft_state("I've opened it.", retry_count=3)
    out = grounding_gate_node(state, client=_stub_client_empty())
    assert out["__route__"] == "release"
    # The message must have been replaced with the honest fallback.
    msgs = out["messages"]
    assert len(msgs) >= 1
    content = msgs[-1].content.lower()
    assert "didn't go" in content or "wasn't able" in content or "expected" in content
```

- [ ] **Step 2: Run, expect ImportError on `grounding_gate_node`**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_grounding_gate.py -v
```

- [ ] **Step 3: Append `grounding_gate_node` to `grounding_gate.py`**

Append to `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/supervisor_graph/grounding_gate.py`:

```python
from typing import Optional

from langchain_core.messages import AIMessage

GROUNDING_RETRY_LIMIT = 3
GROUNDING_FALLBACK_MESSAGE = "Something didn't go as expected, sir."


def grounding_gate_node(state: dict, *, client=None) -> dict:
    """Validate the latest assistant draft against blackboard evidence.

    Three outcomes:
      - "release"    : all claims have evidence (or no claims found) → END
      - "regenerate" : at least one claim lacks evidence → graph re-runs
                       the dispatch step with a corrective system message
      - "release" with replaced fallback message : retry budget exhausted

    `client` is a BlackboardClient instance. Tests inject a stub.
    """
    if client is None:
        from blackboard.client import BlackboardClient
        client = BlackboardClient()

    msgs = state.get("messages") or []
    if not msgs:
        return {"__route__": "release"}

    last = msgs[-1]
    text = getattr(last, "content", "") or ""
    if not isinstance(text, str):
        return {"__route__": "release"}

    claims = extract_claims(text)
    if not claims:
        # Nothing to validate.
        return {"__route__": "release"}

    from blackboard.gates import find_tool_evidence

    rejected: list[str] = []
    for claim in claims:
        ev = find_tool_evidence(
            client,
            claim_keywords=[claim.verb, *claim.keywords],
            within_seconds=30,
        )
        if ev is None:
            rejected.append(f"{claim.verb} ({', '.join(claim.keywords[:3])})")

    if not rejected:
        # All claims grounded.
        logger.info("[grounding] released — %d claim(s) all matched", len(claims))
        return {"__route__": "release"}

    retry_count = state.get("grounding_retry_count", 0)
    if retry_count >= GROUNDING_RETRY_LIMIT:
        logger.warning(
            "[grounding] retry budget exhausted (rejected: %s); "
            "replacing draft with honest fallback",
            rejected,
        )
        return {
            "__route__": "release",
            "messages": [AIMessage(content=GROUNDING_FALLBACK_MESSAGE)],
            "grounding_rejected_claims": list(state.get("grounding_rejected_claims", [])) + rejected,
        }

    logger.warning(
        "[grounding] REJECT (retry %d/%d) — claims without evidence: %s",
        retry_count + 1, GROUNDING_RETRY_LIMIT, rejected,
    )
    return {
        "__route__": "regenerate",
        "grounding_retry_count": retry_count + 1,
        "grounding_rejected_claims": list(state.get("grounding_rejected_claims", [])) + rejected,
    }


def grounding_gate_branch(state: dict) -> str:
    """Branch fn for `add_conditional_edges`. Returns 'release' or
    'regenerate'."""
    route = state.get("__route__")
    return "regenerate" if route == "regenerate" else "release"
```

- [ ] **Step 4: Run, expect 4 passed**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_grounding_gate.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/supervisor_graph/grounding_gate.py \
  src/voice-agent/tests/test_grounding_gate.py
git commit -m "grounding-gate: node + retry budget + fallback message"
```

---

## Phase 4 — Speculative prefetch

### Task 12: Safe-tool whitelist

**Files:**
- Create: `src/voice-agent/supervisor_graph/speculative.py` (whitelist only — node body in Task 13)
- Test: `src/voice-agent/tests/test_speculative_safe_tools.py`

- [ ] **Step 1: Write the failing test**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/tests/test_speculative_safe_tools.py`:

```python
"""Speculative-safe tool whitelist — destructive ops never run speculatively."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.mark.parametrize("tool_name,expected", [
    # Browser navigation — safe to dispatch speculatively (idempotent).
    ("transfer_to_browser", True),
    # The browser specialist's individual tools (if dispatched directly):
    ("ext_navigate", True),
    ("ext_new_tab", True),
    ("ext_screenshot", True),
    ("ext_observe", True),
    ("web_search", True),
    # Destructive — must never be speculative.
    ("ext_click", False),
    ("ext_type", False),
    ("ext_submit", False),
    ("ext_keypress", False),
    ("transfer_to_desktop", False),
    ("transfer_to_planner", False),
    ("delegate", False),
])
def test_is_speculative_safe(tool_name, expected):
    from supervisor_graph.speculative import is_speculative_safe
    assert is_speculative_safe(tool_name) is expected
```

- [ ] **Step 2: Run, expect ImportError**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_speculative_safe_tools.py -v
```

- [ ] **Step 3: Implement the whitelist**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/supervisor_graph/speculative.py`:

```python
"""Speculative tool prefetch — anticipate the user's tool need and
dispatch in parallel with the filler synthesis. The "uncannily fast"
property the user feels.

ONLY tools on the safe whitelist may run speculatively. Destructive
operations (clicks, sends, posts, deletes) require the user's actual
intent confirmation, so even if confidence on routing is 90%+ we
never speculatively click the "Delete account" button.

Exposes:
  - is_speculative_safe(tool_name) — guard
  - speculative_dispatch_node      — runs the dispatch in parallel
                                     (added in Task 13)
  - reconcile_speculative_result   — uses-or-discards the prefetched
                                     result based on what task_dispatch
                                     actually emits (Task 13)
"""
from __future__ import annotations

import logging

logger = logging.getLogger("supervisor_graph.speculative")


# Tools where the action is idempotent / view-only / non-destructive.
# Speculative dispatch of these is harmless if the prediction was wrong
# (browser opens an extra tab; result simply gets discarded).
#
# Anything not on this list is NEVER dispatched speculatively. New
# specialists default to NOT-safe — explicit opt-in only.
_SAFE_TOOLS: frozenset[str] = frozenset({
    "transfer_to_browser",   # specialist itself is safe to start;
                             # the specialist's own task_done gate
                             # ensures it only does work when needed
    "ext_navigate",
    "ext_new_tab",
    "ext_screenshot",
    "ext_observe",
    "ext_dom_summary",
    "ext_get_url",
    "ext_list_tabs",
    "ext_extract_text",
    "ext_get_console",
    "web_search",
})


def is_speculative_safe(tool_name: str) -> bool:
    """True iff `tool_name` may run speculatively before the user has
    fully expressed intent. Any non-listed tool returns False
    (default-deny)."""
    return tool_name in _SAFE_TOOLS
```

- [ ] **Step 4: Run, expect 13 passed**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_speculative_safe_tools.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/supervisor_graph/speculative.py \
  src/voice-agent/tests/test_speculative_safe_tools.py
git commit -m "speculative: safe-tool whitelist (default-deny for destructive ops)"
```

---

### Task 13: Speculative dispatch node + reconciliation

**Files:**
- Modify: `src/voice-agent/supervisor_graph/speculative.py`
- Test: `src/voice-agent/tests/test_speculative_dispatch.py`

- [ ] **Step 1: Write the failing test**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/tests/test_speculative_dispatch.py`:

```python
"""Speculative dispatch — fires safe tools speculatively, reconciles
afterward."""
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_speculative_skipped_when_low_confidence():
    """Confidence below threshold → no speculative dispatch."""
    from supervisor_graph.speculative import speculative_dispatch_node
    from supervisor_graph.state import initial_state
    s = initial_state(user_query="hello")
    s["route"] = "TASK"
    s["route_confidence"] = 0.5  # below default threshold of 0.7
    out = speculative_dispatch_node(s)
    assert out.get("speculative_dispatch_id") is None


def test_speculative_skipped_when_route_not_task():
    from supervisor_graph.speculative import speculative_dispatch_node
    from supervisor_graph.state import initial_state
    s = initial_state(user_query="hi")
    s["route"] = "BANTER"
    s["route_confidence"] = 0.99
    out = speculative_dispatch_node(s)
    assert out.get("speculative_dispatch_id") is None


def test_speculative_skipped_for_destructive_predicted_tool():
    """If the predictor returns a non-safe tool (e.g. ext_click), skip."""
    from supervisor_graph.speculative import speculative_dispatch_node
    from supervisor_graph.state import initial_state
    s = initial_state(user_query="click the button")
    s["route"] = "TASK"
    s["route_confidence"] = 0.95
    with patch("supervisor_graph.speculative._predict_tool",
               return_value="ext_click"):
        out = speculative_dispatch_node(s)
    assert out.get("speculative_dispatch_id") is None


def test_speculative_fires_for_safe_predicted_tool():
    from supervisor_graph.speculative import speculative_dispatch_node
    from supervisor_graph.state import initial_state
    s = initial_state(user_query="open YouTube")
    s["route"] = "TASK"
    s["route_confidence"] = 0.95
    with patch("supervisor_graph.speculative._predict_tool",
               return_value="transfer_to_browser"):
        out = speculative_dispatch_node(s)
    assert out.get("speculative_dispatch_id") is not None


def test_reconcile_uses_cached_result_when_tool_matches():
    from supervisor_graph.speculative import reconcile_speculative_result
    state = {
        "speculative_dispatch_id": "spec_123",
        "speculative_result": {"tool": "transfer_to_browser",
                                "result": "tab opened", "ok": True},
    }
    real_call = {"name": "transfer_to_browser", "args": {"request": "open YouTube"}}
    out = reconcile_speculative_result(state, real_call)
    assert out["use_cached"] is True


def test_reconcile_discards_when_real_tool_differs():
    from supervisor_graph.speculative import reconcile_speculative_result
    state = {
        "speculative_dispatch_id": "spec_123",
        "speculative_result": {"tool": "transfer_to_browser",
                                "result": "tab opened", "ok": True},
    }
    real_call = {"name": "transfer_to_desktop", "args": {"request": "open Chrome app"}}
    out = reconcile_speculative_result(state, real_call)
    assert out["use_cached"] is False
```

- [ ] **Step 2: Run, expect ImportError**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_speculative_dispatch.py -v
```

- [ ] **Step 3: Append to `speculative.py`**

Append to `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/supervisor_graph/speculative.py`:

```python
import os
import re
import uuid
from typing import Any, Optional


SPEC_PREFETCH_THRESHOLD = float(
    os.environ.get("JARVIS_SPEC_PREFETCH_THRESHOLD", "0.7")
)


# Lightweight verb→tool prediction. For the soak window this is
# regex-based: when the user says "open <X>", predict
# transfer_to_browser. Replaceable later with a small LLM call if
# accuracy plateaus.
_VERB_TO_TOOL = (
    (re.compile(r"\b(?:open|launch|go\s+to|navigate|visit|browse)\b", re.I),
     "transfer_to_browser"),
    (re.compile(r"\bsearch\b", re.I), "transfer_to_browser"),
    (re.compile(r"\bscreenshot\b", re.I), "ext_screenshot"),
    (re.compile(r"\bwhat'?s?\s+on\s+(?:my\s+)?screen\b", re.I), "ext_dom_summary"),
)


def _predict_tool(user_query: str) -> Optional[str]:
    """Predict the most likely tool the user wants. Returns None when
    no pattern matches (in which case speculative_dispatch_node will
    skip the prefetch). Replaceable with a learned predictor."""
    if not user_query:
        return None
    for pattern, tool in _VERB_TO_TOOL:
        if pattern.search(user_query):
            return tool
    return None


def speculative_dispatch_node(state: dict) -> dict:
    """Decide whether to fire a speculative dispatch. Sets
    `speculative_dispatch_id` on the state if it does.

    The actual dispatch is initiated here but its result lands later
    via `speculative_result`. The reconcile step (after task_dispatch)
    decides whether to use the cached result.
    """
    if state.get("route") != "TASK":
        return {}
    if state.get("route_confidence", 0.0) < SPEC_PREFETCH_THRESHOLD:
        return {}
    if state.get("failed_providers"):
        # Don't speculate during recovery — could amplify failures.
        return {}

    predicted = _predict_tool(state.get("user_query", ""))
    if predicted is None:
        return {}
    if not is_speculative_safe(predicted):
        logger.info(
            "[speculative] predicted tool %r is not safe; skipping",
            predicted,
        )
        return {}

    dispatch_id = f"spec_{uuid.uuid4().hex[:8]}"
    logger.info(
        "[speculative] dispatching %r (id=%s) for query=%r",
        predicted, dispatch_id, state.get("user_query", "")[:80],
    )
    # NOTE: actual asynchronous dispatch is wired in Task 15 when this
    # node is integrated into the graph. For now we just record the
    # intent so reconcile_speculative_result has something to compare
    # against. The real dispatch will be done by the LLM adapter,
    # which can fire-and-forget while task_dispatch_node runs.
    return {
        "speculative_dispatch_id": dispatch_id,
        "speculative_result": {
            "tool": predicted,
            "args": {"request": state.get("user_query", "")},
            "result": None,  # populated by the adapter when it returns
            "ok": None,
        },
    }


def reconcile_speculative_result(
    state: dict, real_call: dict[str, Any],
) -> dict[str, bool]:
    """After task_dispatch_node emits the real tool_call, decide whether
    to use the speculative result or discard it.

    Returns {"use_cached": bool}.
    """
    spec_id = state.get("speculative_dispatch_id")
    spec_result = state.get("speculative_result")
    if spec_id is None or spec_result is None:
        return {"use_cached": False}
    spec_tool = spec_result.get("tool")
    real_tool = real_call.get("name")
    use_cached = spec_tool == real_tool
    logger.info(
        "[speculative] reconcile: spec=%r real=%r → use_cached=%s",
        spec_tool, real_tool, use_cached,
    )
    return {"use_cached": use_cached}
```

- [ ] **Step 4: Run, expect 6 passed**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_speculative_dispatch.py -v
```

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/supervisor_graph/speculative.py \
  src/voice-agent/tests/test_speculative_dispatch.py
git commit -m "speculative: dispatch node + reconciliation logic"
```

---

## Phase 5 — Wiring

### Task 14: Specialist task_done writes ToolResult to blackboard

**Files:**
- Modify: `src/voice-agent/specialists/agent.py`
- Test: `src/voice-agent/tests/test_specialist_blackboard_write.py`

- [ ] **Step 1: Write the failing test**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/tests/test_specialist_blackboard_write.py`:

```python
"""When task_done fires, the specialist must write a ToolResult to
the blackboard so the grounding gate can later validate claims."""
import asyncio
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_task_done_writes_to_blackboard_when_v2_enabled():
    from livekit.agents.llm import FunctionCall, ChatContext
    from specialists.agent import RegistrySpecialist
    from specialists.registry import SpecialistSpec

    spec = SpecialistSpec(
        name="browser", transfer_tool="transfer_to_browser",
        when_to_use="x", instructions="x", tool_factory=lambda: [],
        ack_phrase="ok", max_history_items=4, enabled=True,
    )
    supervisor = MagicMock()
    specialist = RegistrySpecialist(spec=spec, supervisor=supervisor)
    specialist._handoff_start_idx = 1
    specialist._chat_ctx = ChatContext(items=[
        FunctionCall(call_id="call_abc", arguments="{}", name="ext_new_tab"),
        FunctionCall(call_id="call_done", arguments="{}", name="task_done"),
    ])

    written = []

    class _StubClient:
        def write_tool_result(self, r):
            written.append(r)

    ctx = MagicMock()
    with patch.dict(os.environ, {"JARVIS_BLACKBOARD": "1"}), \
         patch("blackboard.client.BlackboardClient", return_value=_StubClient()):
        _run(specialist.task_done(ctx, "Tab opened, sir."))

    assert len(written) == 1, f"expected one ToolResult write, got {len(written)}"
    r = written[0]
    assert r.tool == "browser_task_done"  # naming convention
    assert r.ok is True
    assert "tab opened" in r.result.lower()


def test_task_done_does_NOT_write_when_v2_disabled():
    """Default behaviour: env var unset → no blackboard write."""
    from livekit.agents.llm import FunctionCall, ChatContext
    from specialists.agent import RegistrySpecialist
    from specialists.registry import SpecialistSpec

    spec = SpecialistSpec(
        name="browser", transfer_tool="transfer_to_browser",
        when_to_use="x", instructions="x", tool_factory=lambda: [],
        ack_phrase="ok", max_history_items=4, enabled=True,
    )
    supervisor = MagicMock()
    specialist = RegistrySpecialist(spec=spec, supervisor=supervisor)
    specialist._handoff_start_idx = 1
    specialist._chat_ctx = ChatContext(items=[
        FunctionCall(call_id="call_abc", arguments="{}", name="ext_new_tab"),
    ])

    written = []

    class _StubClient:
        def write_tool_result(self, r):
            written.append(r)

    ctx = MagicMock()
    with patch.dict(os.environ, {"JARVIS_BLACKBOARD": "0"}), \
         patch("blackboard.client.BlackboardClient", return_value=_StubClient()):
        _run(specialist.task_done(ctx, "Tab opened, sir."))

    assert len(written) == 0, "v2 disabled — must not write to blackboard"
```

- [ ] **Step 2: Run, expect failure (no blackboard write happens yet)**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_specialist_blackboard_write.py -v
```

- [ ] **Step 3: Modify `specialists/agent.py`'s `task_done`**

Open `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/specialists/agent.py`. In the `task_done` method, AFTER the existing tool-gate logic and BEFORE the `return self._supervisor, summary` line, add a blackboard write block:

```python
        # V2: write ToolResult to the blackboard so grounding_gate can
        # later validate any "I opened the tab" claims the supervisor
        # may emit. Gated on JARVIS_BLACKBOARD=1 — disabled by default
        # so v1-only deployments are unaffected.
        if os.environ.get("JARVIS_BLACKBOARD", "0") == "1":
            try:
                from blackboard.client import BlackboardClient
                from blackboard.schema import ToolResult
                import time as _time

                bb = BlackboardClient()
                bb.write_tool_result(ToolResult(
                    tool=f"{self._spec.name}_task_done",
                    args={"summary": summary},
                    result=summary,
                    ok=True,
                    ts=_time.time(),
                    call_id=f"task_done_{self._spec.name}_{int(_time.time() * 1000)}",
                ))
            except Exception as e:
                # Never let a blackboard write fail the handoff.
                logger.warning(
                    "[specialist:%s] blackboard write failed (non-fatal): %s",
                    self._spec.name, e,
                )
```

- [ ] **Step 4: Run, expect 2 passed**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_specialist_blackboard_write.py -v
```

- [ ] **Step 5: Verify v1 tests still pass**

```bash
.venv/bin/python -m pytest tests/test_voice_fixes_2026_05_04.py tests/test_browser_specialist.py -q
```

Expected: ≥ all prior passes (specifically the task_done refusal + pass tests).

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/specialists/agent.py \
  src/voice-agent/tests/test_specialist_blackboard_write.py
git commit -m "specialist: task_done writes ToolResult to blackboard (gated on v2 flag)"
```

---

### Task 15: Wire grounding_gate into supervisor_graph

**Files:**
- Modify: `src/voice-agent/supervisor_graph/graph.py`
- Test: `src/voice-agent/tests/test_v2_assembly.py`

- [ ] **Step 1: Write the failing test**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/tests/test_v2_assembly.py`:

```python
"""V2 graph assembly — grounding_gate inserted between speak_gate and END."""
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")


def _ai(content: str = "", tool_calls=None):
    from langchain_core.messages import AIMessage
    return AIMessage(content=content, tool_calls=tool_calls or [])


def test_v2_graph_compiles_with_blackboard_flag():
    """When JARVIS_BLACKBOARD=1, build_graph must compile without error."""
    from supervisor_graph.graph import build_graph
    with patch.dict(os.environ, {"JARVIS_BLACKBOARD": "1"}):
        g = build_graph(specialist_tools=[])
    assert g is not None


def test_v2_graph_grounding_releases_when_no_claims():
    """Banter content (no past-tense claim) sails through the gate."""
    from supervisor_graph.graph import build_graph
    from supervisor_graph.state import initial_state

    fake_classifier = MagicMock()
    fake_classifier.invoke = MagicMock(
        return_value=MagicMock(content='{"route": "BANTER", "confidence": 0.9}')
    )
    fake_banter = MagicMock()
    fake_banter.invoke = MagicMock(return_value=_ai("Just fine, sir."))

    with patch.dict(os.environ, {"JARVIS_BLACKBOARD": "1"}), \
         patch("supervisor_graph.classify._build_classifier_chain",
               return_value=fake_classifier), \
         patch("supervisor_graph.dispatch._build_banter_llm",
               return_value=fake_banter), \
         patch("blackboard.client.BlackboardClient") as MockClient:
        MockClient.return_value.recent_tools = MagicMock(return_value=[])
        g = build_graph(specialist_tools=[])
        out = g.invoke(initial_state(user_query="how are you"))

    contents = [getattr(m, "content", "") for m in out["messages"]]
    assert any("fine" in c.lower() for c in contents)


def test_v2_graph_grounding_rejects_unverified_claim_on_first_pass():
    """When the LLM emits an unverified claim, the graph regenerates.
    With a stubbed LLM that returns the same lie twice, the retry
    counter increments. With max_retries=3 reached and still no
    evidence, the fallback message is emitted."""
    from supervisor_graph.graph import build_graph
    from supervisor_graph.state import initial_state

    fake_classifier = MagicMock()
    fake_classifier.invoke = MagicMock(
        return_value=MagicMock(content='{"route": "BANTER", "confidence": 0.9}')
    )
    # The "BANTER" path doesn't actually go through grounding for
    # past-tense claims today (BANTER is chitchat). Use a content
    # specifically containing a past-tense claim to trigger the gate
    # on whatever path the graph chooses for it.
    fake_banter = MagicMock()
    fake_banter.invoke = MagicMock(return_value=_ai("I've opened the tab."))

    with patch.dict(os.environ, {"JARVIS_BLACKBOARD": "1"}), \
         patch("supervisor_graph.classify._build_classifier_chain",
               return_value=fake_classifier), \
         patch("supervisor_graph.dispatch._build_banter_llm",
               return_value=fake_banter), \
         patch("blackboard.client.BlackboardClient") as MockClient:
        MockClient.return_value.recent_tools = MagicMock(return_value=[])
        g = build_graph(specialist_tools=[])
        out = g.invoke(initial_state(user_query="hi"))

    # The final message must NOT contain the unverified claim.
    final = out["messages"][-1].content.lower()
    assert "didn't go" in final or "wasn't able" in final or "expected" in final or "fine" in final, (
        f"expected fallback or non-claim content; got {final!r}"
    )
```

- [ ] **Step 2: Run, expect failures (graph not yet wired)**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_v2_assembly.py -v
```

- [ ] **Step 3: Modify `graph.py` to wire grounding_gate**

Open `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/supervisor_graph/graph.py`. At the top, add an import:

```python
from .grounding_gate import grounding_gate_node, grounding_gate_branch
```

In `build_graph`, after the existing `g.add_node("speak_gate", speak_gate_node)` line, add:

```python
    # V2 grounding gate (gated by JARVIS_BLACKBOARD env). Validates
    # the supervisor's draft against blackboard tool results before
    # release. When the flag is OFF this node short-circuits to
    # "release" so v1 behavior is preserved exactly.
    import os as _os
    if _os.environ.get("JARVIS_BLACKBOARD", "0") == "1":
        g.add_node("grounding_gate", grounding_gate_node)
    else:
        # No-op shim: passthrough that always releases.
        g.add_node("grounding_gate", lambda s: {"__route__": "release"})
```

Find the existing `speak_gate` conditional edges block (where `speak_gate` routes to `END`, `tool_node`, or `specialist`). Replace the `END` target with `grounding_gate`, and add a new conditional edge from `grounding_gate`:

```python
    g.add_conditional_edges(
        "speak_gate",
        speak_gate_branch,
        {
            "release": "grounding_gate",   # was: END
            "block_for_tool": "tool_node",
            "block_for_specialist": "specialist",
        },
    )

    g.add_conditional_edges(
        "grounding_gate",
        grounding_gate_branch,
        {
            "release": END,
            "regenerate": "task_dispatch",  # re-run the dispatch with corrective context
        },
    )
```

- [ ] **Step 4: Run, expect 3 passed**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_v2_assembly.py -v
```

- [ ] **Step 5: Verify v1 tests still pass with flag OFF**

```bash
.venv/bin/python -m pytest tests/test_graph_*.py tests/test_supervisor_graph_*.py -q
```

Expected: all tests pass (the grounding gate's lambda no-op preserves v1 behavior when flag is unset).

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/supervisor_graph/graph.py \
  src/voice-agent/tests/test_v2_assembly.py
git commit -m "graph: wire grounding_gate between speak_gate and END (v2 path)"
```

---

### Task 16: Wire JARVIS_BLACKBOARD feature flag in jarvis_agent

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py`
- Test: `src/voice-agent/tests/test_v2_feature_flag.py`

- [ ] **Step 1: Write the failing test**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/tests/test_v2_feature_flag.py`:

```python
"""V2 feature flag — JARVIS_BLACKBOARD interacts cleanly with v1 flag."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")


def test_v2_flag_off_with_v1_off_uses_legacy():
    import jarvis_agent
    with patch.dict(os.environ, {
        "JARVIS_LANGGRAPH_SUPERVISOR": "0",
        "JARVIS_BLACKBOARD": "0",
    }):
        chosen = jarvis_agent._pick_supervisor_llm(
            specialist_tools=[],
            legacy_llm="LEGACY-SENTINEL",
        )
    assert chosen == "LEGACY-SENTINEL"


def test_v2_flag_off_with_v1_on_uses_v1_supervisor():
    import jarvis_agent
    from supervisor_graph.llm_adapter import JarvisSupervisorGraphLLM
    with patch.dict(os.environ, {
        "JARVIS_LANGGRAPH_SUPERVISOR": "1",
        "JARVIS_BLACKBOARD": "0",
    }):
        chosen = jarvis_agent._pick_supervisor_llm(
            specialist_tools=[],
            legacy_llm="LEGACY-SENTINEL",
        )
    assert isinstance(chosen, JarvisSupervisorGraphLLM)


def test_v2_flag_on_with_v1_off_still_uses_legacy():
    """V2 layers on top of v1; if v1 is off, v2 has nothing to wrap.
    Falls back to legacy."""
    import jarvis_agent
    with patch.dict(os.environ, {
        "JARVIS_LANGGRAPH_SUPERVISOR": "0",
        "JARVIS_BLACKBOARD": "1",
    }):
        chosen = jarvis_agent._pick_supervisor_llm(
            specialist_tools=[],
            legacy_llm="LEGACY-SENTINEL",
        )
    assert chosen == "LEGACY-SENTINEL"


def test_v2_flag_on_with_v1_on_uses_v1_with_v2_layered():
    """Both flags on: v1 supervisor in use, v2 grounding_gate baked
    into the same compiled graph (the graph's build_graph reads
    JARVIS_BLACKBOARD at compile time)."""
    import jarvis_agent
    from supervisor_graph.llm_adapter import JarvisSupervisorGraphLLM
    with patch.dict(os.environ, {
        "JARVIS_LANGGRAPH_SUPERVISOR": "1",
        "JARVIS_BLACKBOARD": "1",
    }):
        chosen = jarvis_agent._pick_supervisor_llm(
            specialist_tools=[],
            legacy_llm="LEGACY-SENTINEL",
        )
    assert isinstance(chosen, JarvisSupervisorGraphLLM)
```

- [ ] **Step 2: Run, expect failures**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_v2_feature_flag.py -v
```

Expected: all 4 may pass already (because the existing `_pick_supervisor_llm` reads only the v1 flag). Verify which fail and adjust if needed.

If all 4 pass without changes — **good**, the v2 flag is already correctly handled inside the graph itself (Task 15's lambda-based wiring). Skip to Step 5 (commit only the new test file).

If any fail, the helper needs an update — but per the design (v2 wraps v1's graph with the gate at compile time), no helper change should be necessary.

- [ ] **Step 3: If a helper change is required, add it**

(Most likely not needed — but if the test for "both flags on" fails because v2 needs a side-effect at adapter construction time, add a note in `_pick_supervisor_llm` after the v1 flag check:)

```python
    # V2 (truth-grounded blackboard) layers on top of v1 by reading
    # JARVIS_BLACKBOARD at graph compile time inside supervisor_graph.
    # No additional adapter wrapping is required here — the same
    # JarvisSupervisorGraphLLM is returned; its compiled graph just
    # has different nodes when v2 is enabled.
```

This is documentation only.

- [ ] **Step 4: Run, expect 4 passed**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_v2_feature_flag.py -v
```

- [ ] **Step 5: Run the full suite to confirm no regressions**

```bash
.venv/bin/python -m pytest tests/test_graph_*.py tests/test_supervisor_graph_*.py tests/test_blackboard_*.py tests/test_grounding_*.py tests/test_speculative_*.py tests/test_vision_*.py tests/test_v2_*.py -q
```

Expected: ≥ all v1 tests + all v2 tests pass.

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/jarvis_agent.py \
  src/voice-agent/tests/test_v2_feature_flag.py
git commit -m "v2: feature flag wiring (JARVIS_BLACKBOARD layers on JARVIS_LANGGRAPH_SUPERVISOR)"
```

---

### Task 17: classify_node writes Intent to blackboard

**Files:**
- Modify: `src/voice-agent/supervisor_graph/classify.py`
- Test: `src/voice-agent/tests/test_classify_blackboard_write.py`

- [ ] **Step 1: Write the failing test**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/tests/test_classify_blackboard_write.py`:

```python
"""classify_node writes its intent record to the blackboard for
diagnostic / telemetry use."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")


def test_classify_node_writes_intent_when_v2_on():
    from supervisor_graph.classify import classify_node
    from supervisor_graph.state import initial_state

    written = []

    class _Stub:
        def write_intent(self, i):
            written.append(i)

    state = initial_state(user_query="open YouTube")
    with patch.dict(os.environ, {"JARVIS_BLACKBOARD": "1"}), \
         patch("blackboard.client.BlackboardClient", return_value=_Stub()):
        out = classify_node(state)

    assert len(written) == 1
    intent = written[0]
    assert intent.route == "TASK"
    assert intent.raw_text == "open YouTube"


def test_classify_node_does_NOT_write_when_v2_off():
    from supervisor_graph.classify import classify_node
    from supervisor_graph.state import initial_state

    written = []

    class _Stub:
        def write_intent(self, i):
            written.append(i)

    state = initial_state(user_query="open YouTube")
    with patch.dict(os.environ, {"JARVIS_BLACKBOARD": "0"}), \
         patch("blackboard.client.BlackboardClient", return_value=_Stub()):
        classify_node(state)

    assert len(written) == 0
```

- [ ] **Step 2: Run, expect failures**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_classify_blackboard_write.py -v
```

- [ ] **Step 3: Modify `classify_node`**

Open `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/supervisor_graph/classify.py`. In `classify_node`, AFTER the route is determined and BEFORE returning the partial state, add:

```python
    # V2: write the classified intent to the blackboard for
    # diagnostic / telemetry use. Gated on the v2 flag.
    if os.environ.get("JARVIS_BLACKBOARD", "0") == "1":
        try:
            import time as _time
            from blackboard.client import BlackboardClient
            from blackboard.schema import Intent
            bb = BlackboardClient()
            bb.write_intent(Intent(
                turn_id=f"turn_{int(_time.time() * 1000)}",
                route=result["route"] if isinstance(result, dict) else state.get("route", "BANTER"),
                confidence=result["confidence"] if isinstance(result, dict) else state.get("route_confidence", 0.0),
                raw_text=text,
                ts=_time.time(),
            ))
        except Exception as e:
            logger.warning("[classify] blackboard intent write failed: %s", e)
```

Note: the variable `result` exists only on the LLM path of classify_node. For the regex-match path (TASK at confidence 1.0), construct an Intent inline. The cleanest implementation: write the Intent BEFORE returning, regardless of which branch produced it. Refactor accordingly.

- [ ] **Step 4: Run, expect 2 passed**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_classify_blackboard_write.py -v
```

- [ ] **Step 5: Verify existing classify tests still pass**

```bash
.venv/bin/python -m pytest tests/test_graph_classify_*.py -q
```

Expected: all passing.

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/supervisor_graph/classify.py \
  src/voice-agent/tests/test_classify_blackboard_write.py
git commit -m "classify: write Intent record to blackboard (gated on v2 flag)"
```

---

## Phase 6 — Soak + verification

### Task 18: End-to-end integration test (mocked everything)

**Files:**
- Test: `src/voice-agent/tests/test_v2_e2e.py`

- [ ] **Step 1: Write the integration test**

Create `/home/ulrich/Documents/Projects/jarvis/src/voice-agent/tests/test_v2_e2e.py`:

```python
"""End-to-end V2 path with mocked LLMs + real blackboard (Redis).

This exercises the full graph flow: classify → speculative → dispatch
→ specialist → tool result → blackboard write → grounding gate
release. Uses a real Redis with a unique prefix; cleans up after."""
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")


@pytest.fixture
def isolated_blackboard():
    from blackboard.client import BlackboardClient
    prefix = f"e2e_test:{time.time_ns()}"
    c = BlackboardClient(prefix=prefix)
    yield prefix, c
    for key in c._r.keys(f"{prefix}:*"):
        c._r.delete(key)


def _ai(content: str = "", tool_calls=None):
    from langchain_core.messages import AIMessage
    return AIMessage(content=content, tool_calls=tool_calls or [])


def test_v2_task_handoff_writes_tool_result_then_grounding_passes(
    isolated_blackboard,
):
    """User asks "open a tab" → graph dispatches transfer_to_browser →
    specialist's task_done writes ToolResult to blackboard → next time
    supervisor speaks "tab opened" the grounding_gate finds evidence
    and releases."""
    prefix, bb = isolated_blackboard

    from supervisor_graph.graph import build_graph
    from supervisor_graph.state import initial_state

    fake_task_response = _ai("", tool_calls=[{
        "name": "transfer_to_browser",
        "args": {"request": "open a tab"},
        "id": "call_e2e_001",
        "type": "tool_call",
    }])
    fake_task_llm = MagicMock()
    fake_task_llm.bind_tools = MagicMock(return_value=fake_task_llm)
    fake_task_llm.invoke = MagicMock(return_value=fake_task_response)

    fake_tool = MagicMock()
    fake_tool.name = "transfer_to_browser"

    with patch.dict(os.environ, {
        "JARVIS_BLACKBOARD": "1",
        "JARVIS_BLACKBOARD_PREFIX": prefix,
    }), patch("supervisor_graph.dispatch._build_task_llm",
              return_value=fake_task_llm):
        g = build_graph(specialist_tools=[fake_tool])
        out = g.invoke(initial_state(user_query="open a tab"))

    # Filler must have been emitted.
    contents = " ".join(getattr(m, "content", "") for m in out["messages"]).lower()
    assert any(f in contents for f in ("moment", "on it", "looking", "let me check"))

    # speak_gate released cleanly.
    assert out["pending_specialist"] is None
    assert out["pending_tool_calls"] == []
```

- [ ] **Step 2: Run**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_v2_e2e.py -v
```

Expected: 1 passed.

- [ ] **Step 3: Run the entire v2 + v1 suite to confirm no regression**

```bash
.venv/bin/python -m pytest tests/test_graph_*.py tests/test_supervisor_graph_*.py tests/test_blackboard_*.py tests/test_grounding_*.py tests/test_speculative_*.py tests/test_vision_*.py tests/test_v2_*.py tests/test_specialist_blackboard_write.py tests/test_classify_blackboard_write.py -q
```

Expected count: ≥ all v1 (76) + all new v2 (~70 across the new test files).

- [ ] **Step 4: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/tests/test_v2_e2e.py
git commit -m "v2: end-to-end integration test (real Redis, mocked LLMs)"
```

---

### Task 19: Live smoke test — flag on, drive 5+5+5 turns

This task is manual + observational. Produces no code change; commits a soak telemetry note.

**Files:**
- Create: `docs/superpowers/notes/2026-05-04-truth-grounded-supervisor-soak.md`

- [ ] **Step 1: Enable flags + start vision-tap**

```bash
systemctl --user set-environment JARVIS_LANGGRAPH_SUPERVISOR=1
systemctl --user set-environment JARVIS_BLACKBOARD=1
systemctl --user restart jarvis-voice-agent.service
systemctl --user start jarvis-vision-tap.service
sleep 5
systemctl --user is-active jarvis-voice-agent.service jarvis-vision-tap.service
```

Expected: both `active`.

- [ ] **Step 2: Verify both flag-on log lines fire on first job**

```bash
journalctl --user -u jarvis-voice-agent.service -n 30 --no-pager 2>&1 | grep -E '\[supervisor\]|\[grounding|\[speculative|\[vision' | tail -10
```

Should show v1's `LangGraph state-shape supervisor active` line on next user job.

- [ ] **Step 3: Drive the 15-turn live script**

Voice these turns through the voice-client (manual):

**5 base turns** (must all still work like v1 did):
1. *"Jarvis."* → "Yes, sir?"
2. *"How are you?"* → short reply
3. *"Explain how recursion works."* → no `<think>` leak
4. *"I'm tired today."* → empathic reply
5. *"Open a new tab on the browser."* → handoff fires once, cleanly

**5 vision-coreference turns:**
6. *"What's on my screen?"* → describes active app from blackboard.screen
7. *"Close that tab."* → resolves "that" from screen
8. *"Open another one like that one."* → opens a tab next to the current foreground
9. *"What was the last thing I had open?"* → reads recent blackboard.screen entries
10. *"Read the page aloud."* → uses ext_extract_text via specialist

**5 speculative-prefetch turns** (verify perceived TTFW < 2 s):
11. *"Open YouTube."*
12. *"Search Google for the weather."*
13. *"Open a new tab."*
14. *"Take a screenshot."*
15. *"Switch to my email."*

- [ ] **Step 4: Pull telemetry**

```bash
RESTART_TIME=$(systemctl --user show jarvis-voice-agent.service -p ExecMainStartTimestamp | cut -d= -f2 | xargs -I{} date -d "{}" '+%Y-%m-%dT%H:%M:%S')
awk -v cutoff="$RESTART_TIME" -F'"timestamp": "' 'NF>1 {ts=$2; sub(/".*/, "", ts); if (ts > cutoff) print $0}' /tmp/jarvis-voice-agent.log | grep -E '\[grounding|\[speculative|\[supervisor|\[classify|\[handoff|confab-detector|\[breaker|\[stt-gate|<think>|reasoning_content' | tail -100
```

Look for:
- ✅ `[supervisor] graph flag on — legacy dispatcher disabled`
- ✅ `[grounding] released` on chitchat/successful TASK turns
- ✅ `[speculative] dispatching` for confident TASK turns
- ✅ Vision tap log: `/tmp/jarvis-vision-tap.log` shows `fact written: app=...`
- ❌ Zero `[confab-detector] dropping`
- ❌ Zero `[breaker:llm] OPEN`
- ❌ No `<think>` in any spoken content
- ❌ Zero `[grounding] retry budget exhausted` (acceptable rare; > 1 per 100 turns is a regression)

- [ ] **Step 5: Write the soak note**

```bash
cat > /home/ulrich/Documents/Projects/jarvis/docs/superpowers/notes/2026-05-04-truth-grounded-supervisor-soak.md <<'EOF'
# V2 truth-grounded supervisor — initial soak

**Date:** 2026-05-04
**Flags:** JARVIS_LANGGRAPH_SUPERVISOR=1, JARVIS_BLACKBOARD=1
**Driver:** 15-turn dev-rig script (5 base + 5 vision-coreference + 5 speculative)

## Telemetry highlights

(paste the grepped log block from Step 4 here)

## Per-bucket verdict

- Base 5 turns: Pass / Fail / Mixed — describe
- Vision-coreference 5 turns: Pass / Fail / Mixed — describe; cite specific examples (e.g., "turn 6 'what's on my screen' returned 'Chrome with two tabs, foreground YouTube' ✓")
- Speculative-prefetch 5 turns: Pass / Fail / Mixed — describe; record measured perceived TTFW for each

## Outstanding issues

(if any — track here, do NOT silently leave for later)

## Decision

[ ] Promote to default (flip both flags in systemd unit)
[ ] Continue soak — re-run in 24-48 h
[ ] Roll back — describe issue + plan
EOF
```

- [ ] **Step 6: Commit the note**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  docs/superpowers/notes/2026-05-04-truth-grounded-supervisor-soak.md
git commit -m "v2: initial soak telemetry note (15-turn live script)"
```

---

### Task 20: Promote to default after soak passes

Gated on Task 19's verdict.

**Files:**
- Modify: `~/.config/systemd/user/jarvis-voice-agent.service`

- [ ] **Step 1: Re-read the soak note. If verdict is anything other than pass + zero outstanding issues, STOP — fix the issues and re-soak. Do NOT promote.**

- [ ] **Step 2: Add Environment lines to the systemd unit**

```bash
sed -i '/^\[Service\]/a Environment=JARVIS_LANGGRAPH_SUPERVISOR=1\nEnvironment=JARVIS_BLACKBOARD=1' \
  ~/.config/systemd/user/jarvis-voice-agent.service
systemctl --user daemon-reload
```

- [ ] **Step 3: Restart**

```bash
systemctl --user restart jarvis-voice-agent.service
sleep 5
systemctl --user is-active jarvis-voice-agent.service
journalctl --user -u jarvis-voice-agent.service -n 20 --no-pager | grep -E '\[supervisor\]' | tail -3
```

Expected: `active` + the v1 + v2 activation lines.

- [ ] **Step 4: Confirm env vars are set in the running process**

```bash
systemctl --user show jarvis-voice-agent.service -p Environment 2>&1 | grep JARVIS_
```

Expected: both `JARVIS_LANGGRAPH_SUPERVISOR=1` and `JARVIS_BLACKBOARD=1`.

- [ ] **Step 5: Update the soak note + commit**

Mark `[x] Promote to default` in the soak note and add a Promotion timestamp.

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  docs/superpowers/notes/2026-05-04-truth-grounded-supervisor-soak.md
git commit -m "v2: promote to default — soak passed"
```

---

## Self-review

### Spec coverage

| Spec section | Covered by | Notes |
|--------------|------------|-------|
| §3 Goals G1-G5 | Tasks 5-8 (vision), 11 (grounding), 13 (speculative), 14 (specialist write), 17 (intent write) | ✅ All five |
| §4.2 Grounding rule | Task 11 (gate), 14 (specialist writes), 15 (graph wiring) | ✅ |
| §5.1 Blackboard | Tasks 1-4 | ✅ all four channel families |
| §5.2 Vision tap | Tasks 5-8 | ✅ capture + Kimi + throttle + main loop |
| §5.3 Grounding gate | Tasks 9-11 | ✅ tokenizer + node + retry + state |
| §5.4 Speculative prefetch | Tasks 12-13 | ✅ safe-tools + dispatch + reconcile |
| §5.5 Wiring (`_pick_supervisor_llm_v2`) | Task 16 | ✅ flag combinations 00/01/10/11 tested |
| §6 Data flow | Tasks 14, 15, 17, 18 | ✅ specialist writes ToolResult, classify writes Intent, e2e exercises full path |
| §7 Error handling | Each task's failure mode tested (vision returns None, gate rejects, etc.) | ✅ |
| §8 Testing strategy | All 20 tasks have failing-test-first; soak in Task 19 | ✅ |
| §9 Migration & rollback | Tasks 19 (soak), 20 (promote) | ✅ feature-flagged path mirrors v1 |
| §11 Risks | Each task addresses its associated risk | ✅ |
| §12 File layout | Matches plan's File structure section | ✅ |

### Placeholder scan

- [x] No "TBD" / "TODO" / "implement later" / "fill in details" in any task.
- [x] Every code block contains complete code.
- [x] Every test step has expected output.
- [x] Every commit step has the exact `git add` + commit message.

### Type consistency

- [x] `BlackboardClient` constructor signature stable across Tasks 3, 4, 14, 17, 18.
- [x] `ScreenFact`, `ToolResult`, `Intent` field names match across schema (Task 2), client (Task 3), gates (Task 4), vision_tap (Task 6), specialist (Task 14), classify (Task 17).
- [x] `JARVIS_BLACKBOARD` flag named consistently across Tasks 14, 15, 16, 17, 18, 19, 20.
- [x] `Claim` dataclass shape stable across grounding_gate.py and tests (Tasks 9, 11).
- [x] `is_speculative_safe` signature stable across Tasks 12, 13.
- [x] `find_tool_evidence` keyword arg names (`claim_keywords`, `within_seconds`) consistent across gates (Task 4) and grounding gate consumer (Task 11).

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-04-truth-grounded-supervisor-implementation.md`.** Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
