# JARVIS supervisor — LangGraph state-shape rebuild — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** [`docs/superpowers/specs/2026-05-04-supervisor-langgraph-design.md`](../specs/2026-05-04-supervisor-langgraph-design.md)

**Goal:** Replace the JARVIS supervisor's free-form "LLM-decides-everything" path with a LangGraph state machine that structurally cannot lie about completion (state-shape gating), routes deterministically (verb-initial regex + strict-JSON classifier), forces tool calls when required, and recovers from provider failures without confabulating success.

**Architecture:** A compiled LangGraph `StateGraph` is wrapped behind the LiveKit `LLM` interface. The existing `AgentSession`, audio path, specialists, hub, bridge, browser extension, and CLI proxy stay unchanged. The graph has 6 nodes (classify → dispatch → tool_node → reflect → speak_gate → END plus a specialist sub-graph) and a `JarvisState` with two load-bearing channels (`pending_tool_calls`, `pending_specialist`) that the terminal `speak_gate` node refuses to cross while non-empty.

**Tech Stack:** Python 3.13, LangGraph 1.1.10, langchain-core 1.3.2, langchain-groq 1.1.2, LiveKit Agents 1.5.6, Groq (llama-3.3-70b-versatile, llama-3-groq-8b-tool-use, qwen3-32b), DeepSeek (chat), pytest 9.x.

**Feature flag:** `JARVIS_LANGGRAPH_SUPERVISOR=1` enables the new path; default off through Phase 5; flipped on in Phase 6 after soak.

**Branch / worktree:** Work directly on `feat/ext-browser-control-v3`. Behind feature flag; rollback = unset env var + restart service.

---

## Phase 1 — Foundation (state, tests directory)

### Task 1: Create the `supervisor_graph` package skeleton + verify imports

**Files:**
- Create: `src/voice-agent/supervisor_graph/__init__.py`
- Test: `src/voice-agent/tests/test_supervisor_graph_imports.py`

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_supervisor_graph_imports.py
"""Verify the supervisor_graph package and its required dependencies
import cleanly. If LangGraph or langchain-core moves a symbol, this
test surfaces it before any other test runs."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_supervisor_graph_package_imports():
    import supervisor_graph  # noqa: F401


def test_required_langgraph_symbols_available():
    from langgraph.graph import END, START, StateGraph
    from langgraph.prebuilt import ToolNode
    from langgraph.checkpoint.sqlite import SqliteSaver
    assert START is not None
    assert END is not None
    assert StateGraph is not None
    assert ToolNode is not None
    assert SqliteSaver is not None


def test_required_langchain_symbols_available():
    from langchain_core.messages import (
        AIMessage, HumanMessage, ToolMessage, SystemMessage,
    )
    assert all(c is not None for c in (
        AIMessage, HumanMessage, ToolMessage, SystemMessage,
    ))
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_supervisor_graph_imports.py -v
```

Expected: `ModuleNotFoundError: No module named 'supervisor_graph'`

- [ ] **Step 3: Create the package skeleton**

```python
# src/voice-agent/supervisor_graph/__init__.py
"""LangGraph state-shape supervisor for the JARVIS voice agent.

Replaces the free-form supervisor LLM with a compiled state machine
whose terminal `speak_gate` node refuses to fire while any tool call
is unresolved. Spec: docs/superpowers/specs/2026-05-04-supervisor-
langgraph-design.md.

Public surface stays minimal — `build_graph()` returns a compiled
graph; `JarvisSupervisorGraphLLM` adapts it behind the LiveKit
`livekit.agents.llm.LLM` interface.
"""
from __future__ import annotations
```

- [ ] **Step 4: Verify SqliteSaver is available (it's in the optional `langgraph-checkpoint-sqlite` extra)**

```bash
cd src/voice-agent && .venv/bin/python -c "from langgraph.checkpoint.sqlite import SqliteSaver; print('ok')"
```

If it fails with `ModuleNotFoundError`, install:
```bash
cd src/voice-agent && .venv/bin/pip install langgraph-checkpoint-sqlite
```

Then re-run the verification. Expected: `ok`.

- [ ] **Step 5: Run the test suite to verify it passes**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_supervisor_graph_imports.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/supervisor_graph/__init__.py \
  src/voice-agent/tests/test_supervisor_graph_imports.py
git commit -m "supervisor-graph: package skeleton + dep import smoke test"
```

---

### Task 2: Define `JarvisState` TypedDict

**Files:**
- Create: `src/voice-agent/supervisor_graph/state.py`
- Test: `src/voice-agent/tests/test_supervisor_graph_state.py`

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_supervisor_graph_state.py
"""JarvisState is the contract every node reads from and writes to.
Pin its shape so a refactor can't silently drop a channel."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_state_required_channels_present():
    from supervisor_graph.state import JarvisState
    # TypedDict introspection — annotations dict carries the channels.
    keys = set(JarvisState.__annotations__.keys())
    required = {
        # Conversation
        "messages", "user_query", "audio_meta",
        # Routing
        "route", "route_confidence",
        # State-shape gate (load-bearing)
        "pending_tool_calls", "pending_specialist",
        "last_tool_result", "handoff_filler_voiced",
        # Recovery
        "failed_providers", "retry_attempt",
    }
    missing = required - keys
    assert not missing, f"JarvisState missing channels: {missing}"


def test_initial_state_factory():
    from supervisor_graph.state import JarvisState, initial_state
    s = initial_state(user_query="hello")
    assert s["user_query"] == "hello"
    assert s["pending_tool_calls"] == []
    assert s["pending_specialist"] is None
    assert s["handoff_filler_voiced"] is False
    assert s["retry_attempt"] == 0
    assert s["failed_providers"] == []
    assert s["messages"] == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_supervisor_graph_state.py -v
```

Expected: `ModuleNotFoundError: No module named 'supervisor_graph.state'`

- [ ] **Step 3: Create state.py**

```python
# src/voice-agent/supervisor_graph/state.py
"""JarvisState — the TypedDict every node in the supervisor graph
reads from and writes to.

The two load-bearing channels are `pending_tool_calls` and
`pending_specialist`. The terminal `speak_gate` node refuses to fire
while either is non-empty; that is the structural cure for the
"supervisor lies about completion" failure mode.

Channel design notes:
  - `messages` uses LangGraph's `add_messages` reducer so concurrent
    nodes can append cleanly. Standard LangGraph pattern.
  - `pending_tool_calls` is a list of tool_call_ids (strings). When a
    tool_call is emitted by a dispatch node, its id appears here.
    When a matching ToolMessage arrives, the id is removed.
  - `pending_specialist` is the name of an in-flight specialist
    handoff (e.g. "browser"). Set when transfer_to_X fires; cleared
    when the specialist's task_done returns.
  - `handoff_filler_voiced` is a single-shot flag. The graph emits a
    non-committal filler ("One moment, sir.") exactly once per handoff
    so the user hears a voice while the specialist works — but never
    a completion claim.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


Route = Literal["BANTER", "TASK", "REASONING", "EMOTIONAL", "WAITING"]


class JarvisState(TypedDict):
    # Conversation channels
    messages: Annotated[list[BaseMessage], add_messages]
    user_query: str
    audio_meta: dict[str, Any]

    # Routing
    route: Route
    route_confidence: float

    # State-shape gate (the structural cure)
    pending_tool_calls: list[str]
    pending_specialist: Optional[str]
    last_tool_result: Optional[str]
    handoff_filler_voiced: bool

    # Recovery
    failed_providers: list[str]
    retry_attempt: int


def initial_state(user_query: str = "", audio_meta: Optional[dict] = None) -> JarvisState:
    """Construct a clean state for a new turn. The graph compile path
    expects every key present (TypedDict gates aren't enforced at
    runtime, but our nodes assume them)."""
    return JarvisState(
        messages=[],
        user_query=user_query,
        audio_meta=audio_meta or {},
        route="BANTER",
        route_confidence=0.0,
        pending_tool_calls=[],
        pending_specialist=None,
        last_tool_result=None,
        handoff_filler_voiced=False,
        failed_providers=[],
        retry_attempt=0,
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_supervisor_graph_state.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/supervisor_graph/state.py \
  src/voice-agent/tests/test_supervisor_graph_state.py
git commit -m "supervisor-graph: JarvisState TypedDict + initial_state factory"
```

---

## Phase 2 — Routing (verb-initial + strict-JSON LLM)

### Task 3: Verb-initial regex pre-classifier

**Files:**
- Create: `src/voice-agent/supervisor_graph/classify.py`
- Test: `src/voice-agent/tests/test_graph_classify_regex.py`

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_graph_classify_regex.py
"""Verb-initial regex must catch ~80% of TASK utterances at zero
latency. Source for the verb list: production traffic 2026-04 to
2026-05 — every observed user TASK started with one of these."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.mark.parametrize("utterance", [
    "open a new tab on the current browser",
    "Open Chrome.",
    "open YouTube",
    "play the news",
    "find me an iPhone on Amazon",
    "launch a terminal",
    "close that tab",
    "switch to firefox",
    "search google for the weather",
    "navigate to github.com",
    "read the page",
    "send a message",
    "create a new file",
    "delete that line",
    "post on twitter",
    "buy an iphone 15",
    # Imperative without explicit verb-leading punct
    "Open the docs, please",
    # Short imperatives still match
    "Open YouTube",
])
def test_verb_initial_regex_classifies_task(utterance):
    from supervisor_graph.classify import is_verb_initial_task
    assert is_verb_initial_task(utterance), (
        f"expected TASK match for {utterance!r}"
    )


@pytest.mark.parametrize("utterance", [
    "how are you",
    "what's up",
    "hey jarvis",
    "good morning",
    "thanks",
    "can you tell me a joke",
    "what time is it",   # question, not a command
    "do you remember last night",
    "I think we should refactor",
    "actually never mind",
])
def test_verb_initial_regex_rejects_non_task(utterance):
    from supervisor_graph.classify import is_verb_initial_task
    assert not is_verb_initial_task(utterance), (
        f"unexpected TASK match for {utterance!r}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_classify_regex.py -v
```

Expected: `ModuleNotFoundError: No module named 'supervisor_graph.classify'`

- [ ] **Step 3: Create classify.py with the regex**

```python
# src/voice-agent/supervisor_graph/classify.py
"""Routing classifier for the JARVIS supervisor graph.

Two layers:
  1. `is_verb_initial_task(text)` — pure regex. Catches the imperative
     command shape that voice TASK utterances overwhelmingly take
     ("open a tab", "play the news", "search YouTube"). Zero LLM cost.
  2. `classify_with_llm(text, history)` — strict-JSON Groq classifier
     for everything the regex doesn't catch. Returns
     {route, confidence}.

The two layers compose in the `classify_node` (added in Task 4) which
runs the regex first, falls back to the LLM only when the regex
doesn't match.
"""
from __future__ import annotations

import re

# Imperatives observed in production voice traffic. Verb-initial
# (allowing leading whitespace + optional vocative "jarvis,").
# Capturing the verb at word boundary so "opens" and "opening" don't
# accidentally match (those are statements, not commands).
_VERB_LIST = (
    r"open|launch|close|switch|toggle|start|stop|run|execute|"
    r"play|pause|resume|skip|next|previous|"
    r"search|find|look\s+up|google|youtube|"
    r"navigate|go(?:\s+to)?|visit|"
    r"read|show|tell|"
    r"send|email|message|post|tweet|"
    r"create|make|new|"
    r"delete|remove|clear|"
    r"buy|order|book|"
    r"type|click|press|scroll|"
    r"copy|paste|save|"
    r"call"
)

# Allow optional preamble: leading whitespace, optional vocative
# ("Jarvis,"), optional politeness ("please").
_VERB_INITIAL_RE = re.compile(
    rf"^\s*"
    rf"(?:(?:hey|yo|ok(?:ay)?|please)\s+)*"
    rf"(?:jarvis[\s,]+)?"
    rf"(?:can\s+you\s+|could\s+you\s+|would\s+you\s+|please\s+)*"
    rf"(?:{_VERB_LIST})\b",
    re.IGNORECASE,
)


def is_verb_initial_task(text: str) -> bool:
    """True if `text` matches the verb-initial imperative pattern.
    Returns False on empty or whitespace-only input."""
    if not text or not text.strip():
        return False
    return bool(_VERB_INITIAL_RE.match(text))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_classify_regex.py -v
```

Expected: 28 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/supervisor_graph/classify.py \
  src/voice-agent/tests/test_graph_classify_regex.py
git commit -m "supervisor-graph: verb-initial regex pre-classifier (TASK shortcut)"
```

---

### Task 4: LLM-backed classifier with strict JSON schema

**Files:**
- Modify: `src/voice-agent/supervisor_graph/classify.py`
- Test: `src/voice-agent/tests/test_graph_classify_llm.py`

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_graph_classify_llm.py
"""LLM classifier produces a strict JSON {route, confidence} object
for utterances the regex doesn't catch. Mock the LLM so the test
doesn't hit Groq."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")


def test_classify_with_llm_extracts_route_and_confidence():
    from supervisor_graph.classify import classify_with_llm

    # Mock the inner Groq client to return a strict-JSON content.
    fake_response = MagicMock()
    fake_response.content = '{"route": "REASONING", "confidence": 0.92}'

    fake_chain = MagicMock()
    fake_chain.invoke = MagicMock(return_value=fake_response)

    with patch(
        "supervisor_graph.classify._build_classifier_chain",
        return_value=fake_chain,
    ):
        result = classify_with_llm("explain how recursion works", history=[])

    assert result == {"route": "REASONING", "confidence": 0.92}


def test_classify_with_llm_falls_back_to_banter_on_parse_error():
    from supervisor_graph.classify import classify_with_llm

    fake_response = MagicMock()
    fake_response.content = "garbage, not json"

    fake_chain = MagicMock()
    fake_chain.invoke = MagicMock(return_value=fake_response)

    with patch(
        "supervisor_graph.classify._build_classifier_chain",
        return_value=fake_chain,
    ):
        result = classify_with_llm("hello?", history=[])

    # Conservative default: BANTER with low confidence.
    assert result["route"] == "BANTER"
    assert result["confidence"] <= 0.3
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_classify_llm.py -v
```

Expected: `ImportError: cannot import name 'classify_with_llm'`

- [ ] **Step 3: Extend classify.py with the LLM classifier**

Append to `src/voice-agent/supervisor_graph/classify.py`:

```python
import json
import logging
import os
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq

logger = logging.getLogger("supervisor_graph.classify")

# 4-route enum the dispatch graph branches on.
_ROUTE_NAMES = ["BANTER", "TASK", "REASONING", "EMOTIONAL"]


_CLASSIFIER_SYSTEM_PROMPT = """You are a routing classifier for a voice assistant.

Read the user's utterance and the last few conversation turns. Output ONLY
a JSON object with two fields:
  - "route": one of {routes}
  - "confidence": a number between 0.0 and 1.0

Route definitions:
  - BANTER: chitchat, greetings, casual replies, acknowledgments
  - TASK: any imperative request to take an action (open, find, run, etc.)
  - REASONING: explain, analyze, plan, calculate, compare
  - EMOTIONAL: the user expresses a feeling and seeks support

If unsure, prefer BANTER with confidence 0.3 — the regex pre-classifier
already caught most TASK cases by the time you see this. Output ONLY
the JSON object, nothing else.""".replace("{routes}", str(_ROUTE_NAMES))


def _build_classifier_chain():
    """Return a LangChain runnable that classifies a single utterance.
    Extracted into its own builder so tests can monkeypatch it.

    Strict JSON via Groq's `response_format` param. Groq supports it on
    most models including llama-3.3-70b-versatile."""
    classifier_model = os.environ.get(
        "JARVIS_GRAPH_CLASSIFIER_MODEL", "llama-3.3-70b-versatile"
    )
    return ChatGroq(
        model=classifier_model,
        temperature=0.0,
        max_tokens=64,
        model_kwargs={"response_format": {"type": "json_object"}},
    )


def classify_with_llm(
    text: str, history: list[BaseMessage]
) -> dict[str, Any]:
    """Run the strict-JSON classifier on `text`. `history` is the last
    few turns for context (use up to 4 to keep the prompt short).
    Returns {"route": str, "confidence": float}.

    On parse failure or LLM error: returns BANTER@0.3 (conservative —
    the speak_gate will still apply, so a misroute can't lie)."""
    chain = _build_classifier_chain()

    # Compose the input. SystemMessage carries the route definitions;
    # HumanMessage carries the user's utterance plus a short history.
    history_excerpt = "\n".join(
        f"  {m.type}: {getattr(m, 'content', '')[:120]}"
        for m in history[-4:]
    )
    user_block = (
        f"Recent turns:\n{history_excerpt}\n\nClassify: {text}"
        if history_excerpt
        else f"Classify: {text}"
    )

    try:
        resp = chain.invoke([
            SystemMessage(content=_CLASSIFIER_SYSTEM_PROMPT),
            HumanMessage(content=user_block),
        ])
        raw = (resp.content or "").strip()
        parsed = json.loads(raw)
        route = parsed.get("route")
        conf = float(parsed.get("confidence", 0.0))
        if route not in _ROUTE_NAMES:
            logger.warning(
                "[classify] LLM returned unknown route %r; defaulting to BANTER",
                route,
            )
            return {"route": "BANTER", "confidence": 0.3}
        return {"route": route, "confidence": conf}
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.warning(
            "[classify] could not parse LLM response: %s; defaulting BANTER",
            e,
        )
        return {"route": "BANTER", "confidence": 0.3}
    except Exception as e:
        logger.warning(
            "[classify] LLM call failed: %s: %s; defaulting BANTER",
            type(e).__name__, e,
        )
        return {"route": "BANTER", "confidence": 0.3}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_classify_llm.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/supervisor_graph/classify.py \
  src/voice-agent/tests/test_graph_classify_llm.py
git commit -m "supervisor-graph: strict-JSON LLM classifier (Groq response_format)"
```

---

### Task 5: `classify_node` — composes regex + LLM

**Files:**
- Modify: `src/voice-agent/supervisor_graph/classify.py`
- Test: `src/voice-agent/tests/test_graph_classify_node.py`

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_graph_classify_node.py
"""classify_node mutates JarvisState in place: sets `route` and
`route_confidence`. Regex match → TASK with confidence 1.0 (skip LLM).
Regex miss → call LLM."""
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")


def test_classify_node_regex_match_skips_llm():
    from supervisor_graph.classify import classify_node
    from supervisor_graph.state import initial_state

    state = initial_state(user_query="open a new tab")
    # Patch the LLM classifier; it MUST NOT be called when regex matches.
    with patch(
        "supervisor_graph.classify.classify_with_llm"
    ) as mock_llm:
        out = classify_node(state)
    assert mock_llm.called is False
    assert out["route"] == "TASK"
    assert out["route_confidence"] == 1.0


def test_classify_node_regex_miss_falls_back_to_llm():
    from supervisor_graph.classify import classify_node
    from supervisor_graph.state import initial_state

    state = initial_state(user_query="how are you")
    with patch(
        "supervisor_graph.classify.classify_with_llm",
        return_value={"route": "BANTER", "confidence": 0.85},
    ) as mock_llm:
        out = classify_node(state)
    assert mock_llm.call_count == 1
    assert out["route"] == "BANTER"
    assert out["route_confidence"] == 0.85
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_classify_node.py -v
```

Expected: `ImportError: cannot import name 'classify_node'`

- [ ] **Step 3: Add classify_node to classify.py**

Append to `src/voice-agent/supervisor_graph/classify.py`:

```python
def classify_node(state) -> dict:
    """LangGraph node. Regex first, LLM fallback. Returns the partial
    state dict (`route`, `route_confidence`) so LangGraph's reducer
    merges it into the parent state."""
    text = state.get("user_query") or ""
    if is_verb_initial_task(text):
        logger.info("[classify] regex matched TASK: %r", text[:80])
        return {"route": "TASK", "route_confidence": 1.0}

    history = state.get("messages") or []
    result = classify_with_llm(text, history)
    logger.info(
        "[classify] LLM route=%s conf=%.2f text=%r",
        result["route"], result["confidence"], text[:80],
    )
    return {"route": result["route"], "route_confidence": result["confidence"]}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_classify_node.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/supervisor_graph/classify.py \
  src/voice-agent/tests/test_graph_classify_node.py
git commit -m "supervisor-graph: classify_node composes regex + LLM"
```

---

## Phase 3 — Speak gate (the structural cure)

### Task 6: `speak_gate_node` refuses while pending tool/specialist

**Files:**
- Create: `src/voice-agent/supervisor_graph/speak_gate.py`
- Test: `src/voice-agent/tests/test_graph_speak_gate.py`

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_graph_speak_gate.py
"""speak_gate is the structural cure: it refuses to terminate while
pending_tool_calls is non-empty OR pending_specialist is set.

The test enumerates every refusal condition and the release condition.
A regression here re-opens the "JARVIS lies about completion" bug —
keep this suite green at all times."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_speak_gate_releases_on_clean_state():
    from supervisor_graph.speak_gate import speak_gate_node
    from supervisor_graph.state import initial_state

    state = initial_state()
    # No pending tools, no pending specialist → can speak.
    out = speak_gate_node(state)
    assert out["__route__"] == "release"


def test_speak_gate_blocks_on_pending_tool_calls():
    from supervisor_graph.speak_gate import speak_gate_node
    from supervisor_graph.state import initial_state

    state = initial_state()
    state["pending_tool_calls"] = ["call_abc123"]
    out = speak_gate_node(state)
    assert out["__route__"] == "block_for_tool"


def test_speak_gate_blocks_on_pending_specialist():
    from supervisor_graph.speak_gate import speak_gate_node
    from supervisor_graph.state import initial_state

    state = initial_state()
    state["pending_specialist"] = "browser"
    out = speak_gate_node(state)
    assert out["__route__"] == "block_for_specialist"


def test_speak_gate_blocks_when_both_pending():
    from supervisor_graph.speak_gate import speak_gate_node
    from supervisor_graph.state import initial_state

    state = initial_state()
    state["pending_tool_calls"] = ["call_xyz"]
    state["pending_specialist"] = "browser"
    # Tool takes precedence in the routing label so debugging is easier.
    out = speak_gate_node(state)
    assert out["__route__"] == "block_for_tool"


def test_speak_gate_decision_for_branch():
    """The graph's conditional edge reads `__route__` and routes:
       release           → END
       block_for_tool    → tool_node
       block_for_specialist → specialist (waits for it)
    Verify the decision function maps these correctly."""
    from supervisor_graph.speak_gate import (
        speak_gate_node, speak_gate_branch,
    )
    assert speak_gate_branch({"__route__": "release"}) == "release"
    assert speak_gate_branch({"__route__": "block_for_tool"}) == "block_for_tool"
    assert speak_gate_branch({"__route__": "block_for_specialist"}) == "block_for_specialist"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_speak_gate.py -v
```

Expected: `ModuleNotFoundError: No module named 'supervisor_graph.speak_gate'`

- [ ] **Step 3: Create speak_gate.py**

```python
# src/voice-agent/supervisor_graph/speak_gate.py
"""speak_gate — the structural cure for "JARVIS lies about completion".

The terminal speak path of the supervisor graph runs through this gate.
The gate inspects state and emits a routing decision. Three outcomes:

  release              — both pending lists empty → graph proceeds to END
                         (the assistant's final content has already been
                          emitted upstream; speak_gate does not synthesize
                          new content, it only decides "is it safe to leave
                          this turn?").
  block_for_tool       — pending_tool_calls non-empty → route back to
                         tool_node so the in-flight tool can complete.
  block_for_specialist — pending_specialist set → wait for the
                         specialist's task_done before proceeding.

What this prevents:

  - The supervisor LLM (or its fallback) emitting "Done, sir" while a
    tool_call has not been resolved.
  - Cross-stream lies where DeepSeek-the-fallback hallucinates
    completion in a NEW stream after Groq dropped a tool_call mid-way.

Both failure modes were live-observed 2026-05-04 and are the specific
bugs this gate exists to make impossible.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("supervisor_graph.speak_gate")


def speak_gate_node(state: dict) -> dict:
    """LangGraph node. Returns a routing-decision label in
    `__route__`. Pure function; never speaks; never mutates state
    fields the user sees."""
    pending_tools = state.get("pending_tool_calls") or []
    pending_spec = state.get("pending_specialist")

    if pending_tools:
        logger.warning(
            "[speak-gate] BLOCK pending_tool_calls=%s — routing back to tool_node",
            pending_tools,
        )
        return {"__route__": "block_for_tool"}

    if pending_spec:
        logger.warning(
            "[speak-gate] BLOCK pending_specialist=%r — waiting for task_done",
            pending_spec,
        )
        return {"__route__": "block_for_specialist"}

    logger.info("[speak-gate] release — no pending tools or specialist")
    return {"__route__": "release"}


def speak_gate_branch(state: dict) -> str:
    """Branch function used by `add_conditional_edges`. Returns one of
    'release', 'block_for_tool', 'block_for_specialist' so the graph
    can dispatch to the right next node."""
    route = state.get("__route__")
    if route in ("release", "block_for_tool", "block_for_specialist"):
        return route
    # Defensive default: release. Better to under-block than to deadlock.
    logger.warning(
        "[speak-gate] unknown __route__=%r; defaulting to release",
        route,
    )
    return "release"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_speak_gate.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/supervisor_graph/speak_gate.py \
  src/voice-agent/tests/test_graph_speak_gate.py
git commit -m "supervisor-graph: speak_gate node — structural cure for completion lies"
```

---

## Phase 4 — Dispatch nodes (TASK, BANTER, REASONING, EMOTIONAL)

### Task 7: TASK dispatch with `tool_choice="required"`

**Files:**
- Create: `src/voice-agent/supervisor_graph/dispatch.py`
- Test: `src/voice-agent/tests/test_graph_dispatch_task.py`

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_graph_dispatch_task.py
"""task_dispatch_node binds the supervisor's tool list to a Groq LLM
with tool_choice='required' so the model CANNOT emit free-form
completion text. The output AIMessage must always have tool_calls
and empty content. After emission, pending_tool_calls is populated."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")


def _fake_ai_message_with_tool_call(name: str, args: dict, call_id: str):
    """Build a LangChain AIMessage with a tool_call. The framework
    accepts dict-shape tool_calls and Pydantic ToolCall objects; we use
    the dict shape because that's what ChatGroq returns."""
    from langchain_core.messages import AIMessage
    return AIMessage(
        content="",
        tool_calls=[{
            "name": name,
            "args": args,
            "id": call_id,
            "type": "tool_call",
        }],
    )


def test_task_dispatch_emits_tool_call_and_marks_pending():
    from supervisor_graph.dispatch import task_dispatch_node
    from supervisor_graph.state import initial_state

    fake_msg = _fake_ai_message_with_tool_call(
        "transfer_to_browser",
        {"request": "open a new tab"},
        "call_abc123",
    )
    fake_llm = MagicMock()
    fake_llm.invoke = MagicMock(return_value=fake_msg)

    state = initial_state(user_query="open a new tab")

    with patch(
        "supervisor_graph.dispatch._build_task_llm",
        return_value=fake_llm,
    ):
        out = task_dispatch_node(state, tools=[MagicMock(name="transfer_to_browser")])

    # Must populate pending_tool_calls with the call_id.
    assert out["pending_tool_calls"] == ["call_abc123"]
    # Must append the AIMessage to messages.
    assert len(out["messages"]) == 1
    assert out["messages"][0].tool_calls[0]["name"] == "transfer_to_browser"
    # Must NOT emit free-form content (tool_choice=required guarantees this
    # at the API level; assert it for our recovery path).
    assert (out["messages"][0].content or "") == ""


def test_task_dispatch_uses_tool_choice_required():
    """Verify the LLM is invoked with tool_choice='required'. This is
    the structural lever that prevents the lying-supervisor failure
    mode at the API layer (Groq won't return content alongside the
    tool call)."""
    from supervisor_graph.dispatch import task_dispatch_node
    from supervisor_graph.state import initial_state

    captured_kwargs = {}

    class _RecordingLLM:
        def bind_tools(self, tools, tool_choice=None):
            captured_kwargs["tool_choice"] = tool_choice
            captured_kwargs["tools"] = tools
            return self

        def invoke(self, messages):
            return _fake_ai_message_with_tool_call(
                "transfer_to_browser", {"request": "x"}, "call_x"
            )

    state = initial_state(user_query="open a tab")

    with patch(
        "supervisor_graph.dispatch._build_task_llm",
        return_value=_RecordingLLM(),
    ):
        task_dispatch_node(state, tools=[MagicMock(name="transfer_to_browser")])

    assert captured_kwargs.get("tool_choice") == "required", (
        f"expected tool_choice='required', got {captured_kwargs.get('tool_choice')!r}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_dispatch_task.py -v
```

Expected: `ModuleNotFoundError: No module named 'supervisor_graph.dispatch'`

- [ ] **Step 3: Create dispatch.py with task_dispatch_node**

```python
# src/voice-agent/supervisor_graph/dispatch.py
"""Dispatch nodes for the supervisor graph.

One node per route. The TASK node is the load-bearing one — it forces
`tool_choice="required"` at the Groq API level so the LLM literally
cannot emit completion text. The BANTER/REASONING/EMOTIONAL nodes
are normal "speak" nodes that produce content.

Model choices:
  - TASK         → llama-3-groq-8b-tool-use (Groq's tool-tuned variant
                    that doesn't emit `<|python_tag|>` malformations).
                    Falls back to llama-3.3-70b-versatile via env.
  - BANTER       → llama-3.1-8b-instant (fastest; no tools attached so
                    the malformation surface is gone).
  - REASONING    → qwen3-32b (best for analysis; optional tools).
  - EMOTIONAL    → llama-4-scout-17b (warm tone).

All env-overridable via JARVIS_GRAPH_<ROUTE>_MODEL.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq

logger = logging.getLogger("supervisor_graph.dispatch")


def _build_task_llm():
    """Tool-dispatch LLM. Default: tool-tuned llama variant. Override
    via JARVIS_GRAPH_TASK_MODEL."""
    model = os.environ.get(
        "JARVIS_GRAPH_TASK_MODEL", "llama-3.3-70b-versatile"
    )
    return ChatGroq(model=model, temperature=0.3, max_tokens=512)


def task_dispatch_node(state: dict, tools: list[Any]) -> dict:
    """Force a tool_call. The supervisor cannot emit completion text
    on TASK turns; tool_choice='required' guarantees this at the API
    level. The output AIMessage's tool_calls populate
    `pending_tool_calls` so speak_gate refuses to fire until the
    matching ToolMessages arrive.

    This node is called with the supervisor's tool list bound — the
    graph builder (graph.py) injects the registered transfer_to_X
    tools.
    """
    user_query = state.get("user_query") or ""
    history = state.get("messages") or []

    llm = _build_task_llm()
    bound = llm.bind_tools(tools, tool_choice="required")

    sys_prompt = (
        "You are JARVIS's task-dispatch supervisor. The user just gave "
        "an imperative. Pick the right specialist via transfer_to_X "
        "and emit ONLY that tool call — never any text content. "
        "If unsure which specialist, pick the closest match."
    )

    msgs = [SystemMessage(content=sys_prompt)] + list(history) + [
        HumanMessage(content=user_query),
    ]

    try:
        response: AIMessage = bound.invoke(msgs)
    except Exception as e:
        # Caller (the graph) handles fallback. Re-raise here so the
        # graph's recovery edge fires.
        logger.warning(
            "[task-dispatch] LLM error: %s: %s", type(e).__name__, e,
        )
        raise

    # tool_calls is a list of dicts in LangChain shape:
    #   {"name": ..., "args": ..., "id": ..., "type": "tool_call"}
    tool_calls = response.tool_calls or []
    pending = [tc["id"] for tc in tool_calls if tc.get("id")]

    logger.info(
        "[task-dispatch] emitted %d tool_call(s): %s",
        len(tool_calls),
        ", ".join(tc.get("name", "?") for tc in tool_calls),
    )

    return {
        "messages": [response],
        "pending_tool_calls": pending,
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_dispatch_task.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/supervisor_graph/dispatch.py \
  src/voice-agent/tests/test_graph_dispatch_task.py
git commit -m "supervisor-graph: task_dispatch_node forces tool_choice=required"
```

---

### Task 8: BANTER / REASONING / EMOTIONAL speak nodes

**Files:**
- Modify: `src/voice-agent/supervisor_graph/dispatch.py`
- Test: `src/voice-agent/tests/test_graph_dispatch_speak.py`

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_graph_dispatch_speak.py
"""BANTER / REASONING / EMOTIONAL nodes emit content with no tools.
No tools = no malformation surface = no breaker thrash."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")


def _ai(content: str):
    from langchain_core.messages import AIMessage
    return AIMessage(content=content)


def test_banter_speak_emits_content():
    from supervisor_graph.dispatch import banter_speak_node
    from supervisor_graph.state import initial_state

    fake_llm = MagicMock()
    fake_llm.invoke = MagicMock(return_value=_ai("Just fine, sir."))

    with patch(
        "supervisor_graph.dispatch._build_banter_llm",
        return_value=fake_llm,
    ):
        out = banter_speak_node(initial_state(user_query="how are you"))

    assert len(out["messages"]) == 1
    assert "fine" in out["messages"][0].content.lower()
    # No tool calls — banter speaks freely.
    assert not (out["messages"][0].tool_calls or [])


def test_reasoning_speak_emits_content():
    from supervisor_graph.dispatch import reasoning_speak_node
    from supervisor_graph.state import initial_state

    fake_llm = MagicMock()
    fake_llm.invoke = MagicMock(
        return_value=_ai("Recursion is a function calling itself...")
    )
    with patch(
        "supervisor_graph.dispatch._build_reasoning_llm",
        return_value=fake_llm,
    ):
        out = reasoning_speak_node(initial_state(
            user_query="explain recursion"
        ))
    assert len(out["messages"]) == 1
    assert "recursion" in out["messages"][0].content.lower()


def test_emotional_speak_emits_content():
    from supervisor_graph.dispatch import emotional_speak_node
    from supervisor_graph.state import initial_state

    fake_llm = MagicMock()
    fake_llm.invoke = MagicMock(
        return_value=_ai("That sounds rough, sir. I'm here.")
    )
    with patch(
        "supervisor_graph.dispatch._build_emotional_llm",
        return_value=fake_llm,
    ):
        out = emotional_speak_node(initial_state(user_query="I'm tired"))
    assert len(out["messages"]) == 1
    assert "sir" in out["messages"][0].content.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_dispatch_speak.py -v
```

Expected: 3 ImportErrors.

- [ ] **Step 3: Append speak nodes to dispatch.py**

Append to `src/voice-agent/supervisor_graph/dispatch.py`:

```python
def _build_banter_llm():
    model = os.environ.get(
        "JARVIS_GRAPH_BANTER_MODEL", "llama-3.1-8b-instant"
    )
    return ChatGroq(model=model, temperature=0.6, max_tokens=160)


def _build_reasoning_llm():
    model = os.environ.get(
        "JARVIS_GRAPH_REASONING_MODEL", "qwen/qwen3-32b"
    )
    return ChatGroq(model=model, temperature=0.4, max_tokens=512)


def _build_emotional_llm():
    model = os.environ.get(
        "JARVIS_GRAPH_EMOTIONAL_MODEL",
        "meta-llama/llama-4-scout-17b-16e-instruct",
    )
    return ChatGroq(model=model, temperature=0.7, max_tokens=300)


_PERSONA = (
    "You are JARVIS, a dignified British butler. Address the user as "
    "'sir' sparingly — at most once per reply, only when natural. "
    "Speak in plain English; never use markdown, bullet lists, or "
    "emoji. Keep replies short for voice — one or two sentences."
)


def banter_speak_node(state: dict) -> dict:
    """Chitchat. No tools. Pure content."""
    return _speak_with(state, _build_banter_llm(),
                       extra_system="Reply briefly, casually, warmly.")


def reasoning_speak_node(state: dict) -> dict:
    """Explanation / analysis. No tools."""
    return _speak_with(state, _build_reasoning_llm(),
                       extra_system="Explain clearly. Use plain language.")


def emotional_speak_node(state: dict) -> dict:
    """Empathic acknowledgment. No tools."""
    return _speak_with(state, _build_emotional_llm(),
                       extra_system="Acknowledge feelings warmly; do not lecture.")


def _speak_with(state: dict, llm, *, extra_system: str) -> dict:
    """Common 'invoke an LLM with the persona + history + user_query'
    body for the no-tool speak nodes."""
    user_query = state.get("user_query") or ""
    history = state.get("messages") or []

    msgs = [
        SystemMessage(content=_PERSONA),
        SystemMessage(content=extra_system),
    ] + list(history) + [HumanMessage(content=user_query)]

    try:
        response = llm.invoke(msgs)
    except Exception as e:
        logger.warning(
            "[speak] LLM error: %s: %s", type(e).__name__, e,
        )
        raise

    return {"messages": [response]}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_dispatch_speak.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/supervisor_graph/dispatch.py \
  src/voice-agent/tests/test_graph_dispatch_speak.py
git commit -m "supervisor-graph: BANTER/REASONING/EMOTIONAL speak nodes (no tools)"
```

---

## Phase 5 — Specialist sub-graph + filler emission

### Task 9: Specialist node — filler-once + invoke specialist + clear pending

**Files:**
- Create: `src/voice-agent/supervisor_graph/specialist.py`
- Test: `src/voice-agent/tests/test_graph_specialist.py`

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_graph_specialist.py
"""specialist_node:
  1. emits a filler chunk ("One moment, sir.") exactly once
  2. invokes the existing RegistrySpecialist via the registered
     transfer tool — re-using the production specialists/agent.py
  3. clears pending_specialist + pending_tool_calls on completion.

The filler-once rule is enforced via state.handoff_filler_voiced."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")


def test_specialist_node_emits_filler_once():
    from supervisor_graph.specialist import specialist_node
    from supervisor_graph.state import initial_state

    state = initial_state(user_query="open a tab")
    state["pending_specialist"] = "browser"
    # Mock specialist runtime to return a clean summary.
    with patch(
        "supervisor_graph.specialist._run_specialist",
        return_value="Tab opened, sir.",
    ):
        out1 = specialist_node(state)
        # Filler should be in messages.
        contents = [getattr(m, "content", "") for m in out1["messages"]]
        assert any("moment" in c.lower() or "on it" in c.lower()
                   for c in contents)
        assert out1["handoff_filler_voiced"] is True

    # Second invocation in same state (rare; mostly defensive) must
    # NOT add another filler.
    state2 = initial_state(user_query="…")
    state2["pending_specialist"] = "browser"
    state2["handoff_filler_voiced"] = True
    with patch(
        "supervisor_graph.specialist._run_specialist",
        return_value="Tab opened, sir.",
    ):
        out2 = specialist_node(state2)
        contents = [getattr(m, "content", "") for m in out2["messages"]]
        assert not any("one moment" in c.lower() for c in contents), (
            "filler must be emitted at most once per handoff"
        )


def test_specialist_node_clears_pending_on_success():
    from supervisor_graph.specialist import specialist_node
    from supervisor_graph.state import initial_state

    state = initial_state()
    state["pending_specialist"] = "browser"
    state["pending_tool_calls"] = ["call_abc"]
    with patch(
        "supervisor_graph.specialist._run_specialist",
        return_value="Tab opened, sir.",
    ):
        out = specialist_node(state)
    assert out["pending_specialist"] is None
    assert out["pending_tool_calls"] == []
    assert out["last_tool_result"] == "Tab opened, sir."


def test_specialist_node_handles_specialist_failure():
    """If the specialist raises or returns None, the node must NOT
    leave pending_specialist set (would deadlock the graph). Instead
    it surfaces a failure summary as last_tool_result."""
    from supervisor_graph.specialist import specialist_node
    from supervisor_graph.state import initial_state

    state = initial_state()
    state["pending_specialist"] = "browser"
    state["pending_tool_calls"] = ["call_abc"]
    with patch(
        "supervisor_graph.specialist._run_specialist",
        side_effect=RuntimeError("specialist crashed"),
    ):
        out = specialist_node(state)
    assert out["pending_specialist"] is None
    assert out["pending_tool_calls"] == []
    assert "failed" in (out["last_tool_result"] or "").lower() \
        or "error" in (out["last_tool_result"] or "").lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_specialist.py -v
```

Expected: `ModuleNotFoundError: No module named 'supervisor_graph.specialist'`

- [ ] **Step 3: Create specialist.py**

```python
# src/voice-agent/supervisor_graph/specialist.py
"""specialist_node — the bridge from the graph to the existing
RegistrySpecialist machinery in `specialists/agent.py`.

Three responsibilities:
  1. Emit the non-committal filler ("One moment, sir.") exactly once
     per handoff — bridges the latency gap so the user hears a voice.
     The Sierra/Hamming/Vapi pattern: never claim completion before
     the work happens.
  2. Run the named specialist to completion via _run_specialist().
     The specialist still goes through its existing task_done gate
     (added 2026-05-04) so it cannot bail out without doing work.
  3. Clear pending_specialist + pending_tool_calls regardless of
     specialist outcome (success OR failure). Never leave the graph
     in a deadlocked pending state.

The actual specialist run is in _run_specialist() so tests can swap
it without standing up a full LiveKit AgentSession.
"""
from __future__ import annotations

import logging
import random
from typing import Optional

from langchain_core.messages import AIMessage, ToolMessage

logger = logging.getLogger("supervisor_graph.specialist")

# Non-committal fillers. NEVER include past-tense success language.
# All are < 1 second to synthesize via Groq Orpheus.
_FILLERS = (
    "One moment, sir.",
    "On it.",
    "Let me check.",
    "Looking now.",
)


def _pick_filler() -> str:
    return random.choice(_FILLERS)


def _run_specialist(name: str, request: str, state: dict) -> str:
    """Invoke the named specialist and return its final summary.

    For Phase 5 of this plan, this is a thin shim over the existing
    `RegistrySpecialist` mechanism. The graph constructs the
    specialist with the current chat_ctx and runs it in-process; the
    LiveKit AgentSession dispatches its tools normally.

    NOTE: in Phase 6 (graph-as-LLM adapter, Task 13) the specialist
    invocation is replaced by an inner LangGraph subgraph that wraps
    the same RegistrySpecialist as a node. Until then this shim is
    stubbed in tests via patch.
    """
    raise NotImplementedError(
        "Wired up by graph.py + llm_adapter.py in later tasks. "
        "Tests inject this via unittest.mock.patch."
    )


def specialist_node(state: dict) -> dict:
    """Run the in-flight specialist; emit filler-once; clear pending.

    The graph routes here when `pending_specialist` is set (typically
    set by task_dispatch_node when it emits a transfer_to_X tool
    call). On entry: set the filler if not already emitted. On exit:
    pending_specialist and pending_tool_calls are guaranteed empty —
    speak_gate will release.
    """
    name = state.get("pending_specialist")
    if not name:
        # Defensive — shouldn't happen given graph wiring.
        logger.warning("[specialist] called with no pending_specialist")
        return {}

    user_query = state.get("user_query") or ""
    output_messages: list = []

    # 1. Filler-once — bridges latency without lying.
    if not state.get("handoff_filler_voiced"):
        filler = _pick_filler()
        logger.info("[specialist] filler: %r → %s", filler, name)
        output_messages.append(AIMessage(content=filler))

    # 2. Run the specialist. Catch all exceptions; never deadlock.
    try:
        summary = _run_specialist(name, user_query, state)
        if not summary:
            summary = f"({name} specialist returned no summary)"
        logger.info("[specialist] %s done: %r", name, summary[:120])
    except Exception as e:
        summary = f"The {name} specialist failed: {type(e).__name__}: {e}"
        logger.warning("[specialist] %s failed: %s", name, e)

    # 3. Append the specialist's summary as a tool result the speak
    #    path can consume. Pair with the handoff's tool_call_id so
    #    pending_tool_calls clears cleanly.
    pending = state.get("pending_tool_calls") or []
    if pending:
        # The first pending id corresponds to the handoff that started
        # this specialist. Pair them.
        output_messages.append(ToolMessage(
            content=summary, tool_call_id=pending[0],
        ))

    return {
        "messages": output_messages,
        "pending_specialist": None,
        "pending_tool_calls": [],
        "last_tool_result": summary,
        "handoff_filler_voiced": True,
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_specialist.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/supervisor_graph/specialist.py \
  src/voice-agent/tests/test_graph_specialist.py
git commit -m "supervisor-graph: specialist_node — filler-once + run + clear pending"
```

---

## Phase 6 — Tool node + reflect

### Task 10: Wrap LangGraph's prebuilt `ToolNode` for direct tool calls

**Files:**
- Create: `src/voice-agent/supervisor_graph/tools.py`
- Test: `src/voice-agent/tests/test_graph_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_graph_tools.py
"""Direct (non-handoff) tool calls run through LangGraph's prebuilt
ToolNode. The node executes each pending tool_call and emits a
ToolMessage. After execution, our cleanup step removes the call_id
from pending_tool_calls so speak_gate releases."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_tool_cleanup_clears_pending_on_tool_messages():
    """Given a state with pending_tool_calls=['x', 'y'] and ToolMessages
    for both x and y in messages, the cleanup function returns a state
    update with pending_tool_calls=[]."""
    from langchain_core.messages import ToolMessage, AIMessage
    from supervisor_graph.tools import clear_resolved_pending

    state = {
        "messages": [
            AIMessage(content="", tool_calls=[
                {"name": "f", "args": {}, "id": "x", "type": "tool_call"},
                {"name": "g", "args": {}, "id": "y", "type": "tool_call"},
            ]),
            ToolMessage(content="ok-x", tool_call_id="x"),
            ToolMessage(content="ok-y", tool_call_id="y"),
        ],
        "pending_tool_calls": ["x", "y"],
    }
    out = clear_resolved_pending(state)
    assert out["pending_tool_calls"] == []


def test_tool_cleanup_keeps_unresolved():
    from langchain_core.messages import ToolMessage, AIMessage
    from supervisor_graph.tools import clear_resolved_pending

    state = {
        "messages": [
            AIMessage(content="", tool_calls=[
                {"name": "f", "args": {}, "id": "x", "type": "tool_call"},
                {"name": "g", "args": {}, "id": "y", "type": "tool_call"},
            ]),
            ToolMessage(content="ok-x", tool_call_id="x"),
            # 'y' is still in flight — no ToolMessage yet.
        ],
        "pending_tool_calls": ["x", "y"],
    }
    out = clear_resolved_pending(state)
    assert out["pending_tool_calls"] == ["y"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_tools.py -v
```

Expected: `ModuleNotFoundError: No module named 'supervisor_graph.tools'`

- [ ] **Step 3: Create tools.py**

```python
# src/voice-agent/supervisor_graph/tools.py
"""Tool execution utilities for the supervisor graph.

Direct (non-handoff) tool calls are dispatched by LangGraph's
prebuilt ToolNode. After the ToolNode runs, `clear_resolved_pending`
removes resolved call_ids from `pending_tool_calls`, which lets
speak_gate release.

Specialist handoffs (transfer_to_*) take a different path — they're
intercepted in the graph's branch logic so the specialist sub-graph
runs instead of ToolNode. See `graph.py` for the wiring.
"""
from __future__ import annotations

from langchain_core.messages import ToolMessage


def clear_resolved_pending(state: dict) -> dict:
    """Remove tool_call_ids from pending_tool_calls if a matching
    ToolMessage exists in messages. Idempotent."""
    pending: list[str] = list(state.get("pending_tool_calls") or [])
    if not pending:
        return {"pending_tool_calls": []}
    seen_ids = {
        m.tool_call_id for m in (state.get("messages") or [])
        if isinstance(m, ToolMessage)
    }
    remaining = [p for p in pending if p not in seen_ids]
    return {"pending_tool_calls": remaining}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_tools.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/supervisor_graph/tools.py \
  src/voice-agent/tests/test_graph_tools.py
git commit -m "supervisor-graph: tools.clear_resolved_pending"
```

---

## Phase 7 — Graph assembly + LiveKit LLM adapter

### Task 11: `build_graph()` wires the StateGraph end-to-end

**Files:**
- Create: `src/voice-agent/supervisor_graph/graph.py`
- Test: `src/voice-agent/tests/test_graph_assembly.py`

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_graph_assembly.py
"""Integration test: compile the full graph and run synthetic
turns through it end-to-end. No real Groq calls — every LLM is
patched."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")


def _ai(content: str = "", tool_calls=None):
    from langchain_core.messages import AIMessage
    return AIMessage(content=content, tool_calls=tool_calls or [])


def test_graph_compiles():
    from supervisor_graph.graph import build_graph
    g = build_graph(specialist_tools=[])
    assert g is not None


def test_graph_banter_path_end_to_end():
    """User says chitchat → classify routes BANTER → speak → END."""
    from supervisor_graph.graph import build_graph
    from supervisor_graph.state import initial_state

    fake_classifier = MagicMock()
    fake_classifier.invoke = MagicMock(
        return_value=MagicMock(content='{"route": "BANTER", "confidence": 0.9}')
    )
    fake_banter = MagicMock()
    fake_banter.invoke = MagicMock(return_value=_ai("Just fine, sir."))

    with patch(
        "supervisor_graph.classify._build_classifier_chain",
        return_value=fake_classifier,
    ), patch(
        "supervisor_graph.dispatch._build_banter_llm",
        return_value=fake_banter,
    ):
        g = build_graph(specialist_tools=[])
        # Trigger non-regex path so the LLM classifier fires.
        out = g.invoke(initial_state(user_query="how are you"))

    contents = [getattr(m, "content", "") for m in out["messages"]]
    assert any("fine" in c.lower() for c in contents)
    assert out["route"] == "BANTER"
    # speak_gate must release — no pending state.
    assert out["pending_tool_calls"] == []
    assert out["pending_specialist"] is None


def test_graph_task_with_handoff_path_end_to_end():
    """User says verb-initial TASK → regex routes TASK → dispatch
    emits transfer_to_browser → specialist runs → done."""
    from supervisor_graph.graph import build_graph
    from supervisor_graph.state import initial_state

    fake_task_llm_response = _ai("", tool_calls=[
        {"name": "transfer_to_browser",
         "args": {"request": "open a tab"},
         "id": "call_xyz",
         "type": "tool_call"},
    ])
    fake_task_llm = MagicMock()
    fake_task_llm.bind_tools = MagicMock(return_value=fake_task_llm)
    fake_task_llm.invoke = MagicMock(return_value=fake_task_llm_response)

    # Stub the transfer_to_browser tool — graph treats anything starting
    # with transfer_to_ as a specialist handoff and routes accordingly.
    fake_specialist_tool = MagicMock()
    fake_specialist_tool.name = "transfer_to_browser"

    with patch(
        "supervisor_graph.dispatch._build_task_llm",
        return_value=fake_task_llm,
    ), patch(
        "supervisor_graph.specialist._run_specialist",
        return_value="Tab opened, sir.",
    ):
        g = build_graph(specialist_tools=[fake_specialist_tool])
        out = g.invoke(initial_state(user_query="open a tab"))

    # Filler + final summary must both be present.
    contents = " ".join(
        getattr(m, "content", "") for m in out["messages"]
    ).lower()
    assert ("moment" in contents or "on it" in contents)
    assert "tab opened" in contents
    # speak_gate released cleanly.
    assert out["pending_specialist"] is None
    assert out["pending_tool_calls"] == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_assembly.py -v
```

Expected: `ModuleNotFoundError: No module named 'supervisor_graph.graph'`

- [ ] **Step 3: Create graph.py**

```python
# src/voice-agent/supervisor_graph/graph.py
"""build_graph() — assembles the supervisor StateGraph.

Topology:

    START
      ↓
    classify
      ↓ (route)
      ├─ BANTER → banter_speak → speak_gate → END
      ├─ EMOTIONAL → emotional_speak → speak_gate → END
      ├─ REASONING → reasoning_speak → speak_gate → END
      ├─ TASK → task_dispatch
      │     ↓ (does it carry transfer_to_*?)
      │     ├─ yes → set pending_specialist
      │     │       → specialist (filler + run + clear) → speak_gate → END
      │     └─ no  → tool_node → cleanup → speak_gate → END

The conditional branches are implemented with `add_conditional_edges`.
"""
from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, START, StateGraph

from .classify import classify_node
from .dispatch import (
    banter_speak_node,
    emotional_speak_node,
    reasoning_speak_node,
    task_dispatch_node,
)
from .specialist import specialist_node
from .speak_gate import speak_gate_branch, speak_gate_node
from .state import JarvisState
from .tools import clear_resolved_pending

logger = logging.getLogger("supervisor_graph.graph")


def _route_branch(state: dict) -> str:
    """Branch fn after classify_node — fans out to the right speak/dispatch."""
    return state.get("route") or "BANTER"


def _post_dispatch_branch(state: dict) -> str:
    """Branch fn after task_dispatch_node — was the tool a handoff
    (transfer_to_*) or a direct tool call?"""
    msgs = state.get("messages") or []
    if not msgs:
        return "no_op"
    last = msgs[-1]
    tcs = getattr(last, "tool_calls", None) or []
    for tc in tcs:
        name = tc.get("name", "")
        if name.startswith("transfer_to_"):
            # Handoff — set pending_specialist for the specialist node
            # to consume.
            spec_name = name[len("transfer_to_"):]
            state["pending_specialist"] = spec_name  # mutated in place;
            # LangGraph's reducer will merge on next node.
            return "specialist"
    return "tool_node"


def build_graph(*, specialist_tools: list[Any]):
    """Compile the supervisor graph. `specialist_tools` is the list
    of @function_tool transfer_to_X (and `delegate`) tools the
    supervisor's task_dispatch should bind."""

    g = StateGraph(JarvisState)

    # Nodes
    g.add_node("classify", classify_node)
    g.add_node("banter", banter_speak_node)
    g.add_node("reasoning", reasoning_speak_node)
    g.add_node("emotional", emotional_speak_node)
    g.add_node(
        "task_dispatch",
        lambda s: task_dispatch_node(s, tools=specialist_tools),
    )
    g.add_node("specialist", specialist_node)
    # tool_node is currently a no-op for direct (non-handoff) tools;
    # specialists are the dispatch path today. Direct tool execution
    # via LangGraph's prebuilt ToolNode is wired in a future task
    # when we add non-specialist tools. For now we just clear pending.
    g.add_node("tool_node", clear_resolved_pending)
    g.add_node("speak_gate", speak_gate_node)
    # No-op terminal for the rare WAITING / unknown route.
    g.add_node("no_op", lambda s: {})

    # Edges
    g.add_edge(START, "classify")
    g.add_conditional_edges(
        "classify",
        _route_branch,
        {
            "BANTER": "banter",
            "REASONING": "reasoning",
            "EMOTIONAL": "emotional",
            "TASK": "task_dispatch",
            "WAITING": "no_op",
        },
    )

    # Speak nodes go through the gate before END.
    for n in ("banter", "reasoning", "emotional"):
        g.add_edge(n, "speak_gate")

    # task_dispatch fans out: handoff → specialist; direct → tool_node.
    g.add_conditional_edges(
        "task_dispatch",
        _post_dispatch_branch,
        {
            "specialist": "specialist",
            "tool_node": "tool_node",
            "no_op": "no_op",
        },
    )

    # Specialist + tool_node converge at speak_gate.
    g.add_edge("specialist", "speak_gate")
    g.add_edge("tool_node", "speak_gate")
    g.add_edge("no_op", END)

    # speak_gate decides: release → END; otherwise loop.
    g.add_conditional_edges(
        "speak_gate",
        speak_gate_branch,
        {
            "release": END,
            "block_for_tool": "tool_node",
            "block_for_specialist": "specialist",
        },
    )

    return g.compile()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_assembly.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/supervisor_graph/graph.py \
  src/voice-agent/tests/test_graph_assembly.py
git commit -m "supervisor-graph: build_graph() — full StateGraph assembly"
```

---

### Task 12: Recovery — fallback-on-Groq-failure

**Files:**
- Modify: `src/voice-agent/supervisor_graph/dispatch.py`
- Test: `src/voice-agent/tests/test_graph_dispatch_fallback.py`

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_graph_dispatch_fallback.py
"""When the primary task_dispatch LLM fails (Groq rate-limit, tool
malformation, etc.), the node falls back to DeepSeek with the SAME
tool_choice=required, so the fallback CANNOT confabulate completion.
This is the cure for cross-stream lies (failure mode #5)."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")


def _ai_tool(name: str, args: dict, call_id: str):
    from langchain_core.messages import AIMessage
    return AIMessage(content="", tool_calls=[{
        "name": name, "args": args, "id": call_id, "type": "tool_call",
    }])


def test_task_dispatch_falls_back_on_primary_failure():
    """Primary raises → fallback runs and emits a clean tool_call."""
    from supervisor_graph.dispatch import task_dispatch_node
    from supervisor_graph.state import initial_state

    primary = MagicMock()
    primary.bind_tools = MagicMock(return_value=primary)
    primary.invoke = MagicMock(side_effect=RuntimeError("Failed to call a function"))

    fallback = MagicMock()
    fallback.bind_tools = MagicMock(return_value=fallback)
    fallback.invoke = MagicMock(return_value=_ai_tool(
        "transfer_to_browser", {"request": "open"}, "call_fb",
    ))

    state = initial_state(user_query="open a tab")

    with patch(
        "supervisor_graph.dispatch._build_task_llm",
        return_value=primary,
    ), patch(
        "supervisor_graph.dispatch._build_task_fallback_llm",
        return_value=fallback,
    ):
        out = task_dispatch_node(
            state, tools=[MagicMock(name="transfer_to_browser")],
        )

    assert out["pending_tool_calls"] == ["call_fb"]
    assert out["failed_providers"] == ["groq"]
    # Fallback was invoked, so it MUST have used tool_choice=required.
    bind_call = fallback.bind_tools.call_args
    assert bind_call.kwargs.get("tool_choice") == "required" \
        or (len(bind_call.args) >= 2 and bind_call.args[1] == "required")


def test_task_dispatch_re_raises_when_both_fail():
    """If primary and fallback both raise, propagate so the framework
    can show a graceful error to the user."""
    from supervisor_graph.dispatch import task_dispatch_node
    from supervisor_graph.state import initial_state

    primary = MagicMock()
    primary.bind_tools = MagicMock(return_value=primary)
    primary.invoke = MagicMock(side_effect=RuntimeError("groq down"))
    fallback = MagicMock()
    fallback.bind_tools = MagicMock(return_value=fallback)
    fallback.invoke = MagicMock(side_effect=RuntimeError("deepseek down"))

    state = initial_state(user_query="open a tab")

    import pytest
    with patch(
        "supervisor_graph.dispatch._build_task_llm",
        return_value=primary,
    ), patch(
        "supervisor_graph.dispatch._build_task_fallback_llm",
        return_value=fallback,
    ), pytest.raises(RuntimeError):
        task_dispatch_node(
            state, tools=[MagicMock(name="transfer_to_browser")],
        )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_dispatch_fallback.py -v
```

Expected: 2 failures (the fallback + raise paths don't exist yet).

- [ ] **Step 3: Add fallback to dispatch.py**

Modify `task_dispatch_node` in `src/voice-agent/supervisor_graph/dispatch.py`. Replace the existing implementation with the version below; also add the `_build_task_fallback_llm` builder.

```python
# Add near the other builders in dispatch.py:

def _build_task_fallback_llm():
    """DeepSeek fallback for TASK turns. Uses the same
    tool_choice='required' contract — the fallback CANNOT lie about
    completion either. Cures cross-stream hallucination (failure
    mode #5, live-observed 2026-05-04)."""
    from langchain_openai import ChatOpenAI  # DeepSeek is OpenAI-compat
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    base_url = os.environ.get(
        "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
    )
    return ChatOpenAI(
        model=os.environ.get("JARVIS_GRAPH_TASK_FALLBACK_MODEL", "deepseek-chat"),
        api_key=api_key,
        base_url=base_url,
        temperature=0.3,
        max_tokens=512,
    )
```

Replace the body of `task_dispatch_node` to add fallback handling:

```python
def task_dispatch_node(state: dict, tools: list[Any]) -> dict:
    """Force a tool_call. Primary: Groq. Fallback (on any exception):
    DeepSeek, also with tool_choice='required'. The fallback sees the
    SAME state — no partial assistant content has been appended yet,
    so it cannot confabulate completion.
    """
    user_query = state.get("user_query") or ""
    history = state.get("messages") or []
    failed: list[str] = list(state.get("failed_providers") or [])

    sys_prompt = (
        "You are JARVIS's task-dispatch supervisor. The user just gave "
        "an imperative. Pick the right specialist via transfer_to_X "
        "and emit ONLY that tool call — never any text content. "
        "If unsure which specialist, pick the closest match."
    )

    msgs = [SystemMessage(content=sys_prompt)] + list(history) + [
        HumanMessage(content=user_query),
    ]

    def _try(builder, provider_name: str):
        llm = builder()
        bound = llm.bind_tools(tools, tool_choice="required")
        return bound.invoke(msgs)

    response: AIMessage
    try:
        response = _try(_build_task_llm, "groq")
    except Exception as e:
        logger.warning(
            "[task-dispatch] primary (groq) failed: %s: %s — falling back to deepseek",
            type(e).__name__, e,
        )
        failed.append("groq")
        # Fallback: re-invoke with the SAME messages + SAME contract.
        # No partial assistant turn has been appended; fallback gets a
        # clean state and cannot lie about completion.
        try:
            response = _try(_build_task_fallback_llm, "deepseek")
        except Exception as e2:
            logger.error(
                "[task-dispatch] fallback (deepseek) ALSO failed: %s: %s",
                type(e2).__name__, e2,
            )
            raise

    tool_calls = response.tool_calls or []
    pending = [tc["id"] for tc in tool_calls if tc.get("id")]

    logger.info(
        "[task-dispatch] emitted %d tool_call(s): %s (failed_providers=%s)",
        len(tool_calls),
        ", ".join(tc.get("name", "?") for tc in tool_calls),
        failed,
    )

    return {
        "messages": [response],
        "pending_tool_calls": pending,
        "failed_providers": failed,
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_dispatch_fallback.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Verify pre-existing tests still pass**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_dispatch_task.py tests/test_graph_dispatch_speak.py tests/test_graph_assembly.py -v
```

Expected: 8 passed (2 task + 3 speak + 3 assembly).

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/supervisor_graph/dispatch.py \
  src/voice-agent/tests/test_graph_dispatch_fallback.py
git commit -m "supervisor-graph: task_dispatch falls back to DeepSeek under tool_choice=required"
```

---

## Phase 8 — LiveKit LLM adapter (graph behind AgentSession)

### Task 13: `JarvisSupervisorGraphLLM` — graph behind LiveKit `LLM`

**Files:**
- Create: `src/voice-agent/supervisor_graph/llm_adapter.py`
- Test: `src/voice-agent/tests/test_graph_llm_adapter.py`

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_graph_llm_adapter.py
"""The adapter exposes the compiled graph behind LiveKit's LLM
interface so AgentSession can drop it in unchanged. Drives the full
async-with / async-for protocol the framework uses."""
import asyncio
import os
import sys
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


def test_adapter_constructs_with_specialist_tools():
    from supervisor_graph.llm_adapter import JarvisSupervisorGraphLLM
    llm = JarvisSupervisorGraphLLM(specialist_tools=[])
    assert llm is not None


def test_adapter_chat_streams_banter_response():
    """End-to-end: invoke chat() and read its stream; verify that
    the banter content surfaces as ChatChunk content deltas."""
    from supervisor_graph.llm_adapter import JarvisSupervisorGraphLLM
    from langchain_core.messages import AIMessage

    fake_classifier = MagicMock()
    fake_classifier.invoke = MagicMock(
        return_value=MagicMock(content='{"route": "BANTER", "confidence": 0.9}')
    )
    fake_banter = MagicMock()
    fake_banter.invoke = MagicMock(return_value=AIMessage(content="Hello, sir."))

    with patch(
        "supervisor_graph.classify._build_classifier_chain",
        return_value=fake_classifier,
    ), patch(
        "supervisor_graph.dispatch._build_banter_llm",
        return_value=fake_banter,
    ):
        from livekit.agents import llm as agents_llm

        # Build a fake chat_ctx with a single user turn.
        chat_ctx = agents_llm.ChatContext()
        chat_ctx.add_message(role="user", content="hi")

        adapter = JarvisSupervisorGraphLLM(specialist_tools=[])
        stream = adapter.chat(chat_ctx=chat_ctx)

        async def collect():
            chunks = []
            async with stream:
                async for chunk in stream:
                    chunks.append(chunk)
            return chunks

        chunks = _run(collect())

    contents = "".join(
        (c.delta.content or "")
        for c in chunks
        if c.delta is not None
    )
    assert "hello" in contents.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_llm_adapter.py -v
```

Expected: `ModuleNotFoundError: No module named 'supervisor_graph.llm_adapter'`

- [ ] **Step 3: Create llm_adapter.py**

```python
# src/voice-agent/supervisor_graph/llm_adapter.py
"""LangGraph-as-LLM adapter for LiveKit AgentSession.

`JarvisSupervisorGraphLLM` extends `livekit.agents.llm.LLM`. Its
`chat()` runs the compiled supervisor graph and streams the resulting
assistant content back as `ChatChunk` deltas, which AgentSession
forwards to TTS just like any other LLM.

Why this works without changing AgentSession:

  - The framework drives turn timing (STT → LLM.chat → TTS).
  - The "LLM" is just an object with a `chat()` returning an async
    iterable of ChatChunk. The graph satisfies that contract: run
    the graph synchronously inside chat(), then yield one chunk per
    new AssistantMessage's content split across messages.

Trade-off: we don't stream tokens (the graph runs to completion
before any chunk is yielded). For voice that's fine — the audio
plays while the graph runs; users feel snappy because the FILLER
chunk goes out first.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, AsyncIterator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from livekit.agents import llm as agents_llm

from .graph import build_graph
from .state import initial_state

logger = logging.getLogger("supervisor_graph.llm_adapter")


def _ctx_to_lc_messages(chat_ctx: agents_llm.ChatContext) -> list:
    """Convert LiveKit ChatContext items to LangChain BaseMessages.
    Defensive about both dict and Pydantic shapes; LiveKit versions
    have shifted on this boundary."""
    out = []
    for item in getattr(chat_ctx, "items", []) or []:
        role = getattr(item, "role", None)
        content = getattr(item, "content", "") or ""
        if isinstance(content, list):
            content = " ".join(c if isinstance(c, str) else
                                getattr(c, "text", "") or "" for c in content)
        if role == "user":
            out.append(HumanMessage(content=content))
        elif role == "assistant":
            out.append(AIMessage(content=content))
        elif role == "system":
            out.append(SystemMessage(content=content))
        elif role == "tool":
            out.append(ToolMessage(
                content=content,
                tool_call_id=getattr(item, "tool_call_id", "") or "?",
            ))
    return out


class _GraphLLMStream:
    """Minimal async iterator yielding ChatChunk deltas from the graph
    output. LiveKit's contract: support `async with`, `async for`,
    `aclose`. See test_graph_llm_adapter for the exercised contract."""

    def __init__(self, chunks: list):
        self._chunks = chunks
        self._idx = 0
        self._closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._closed or self._idx >= len(self._chunks):
            raise StopAsyncIteration
        c = self._chunks[self._idx]
        self._idx += 1
        return c

    async def aclose(self):
        self._closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()


def _ai_messages_to_chunks(ai_messages: list) -> list:
    """Convert an iterable of AIMessages into a list of ChatChunks.
    Tool calls are NOT surfaced — the graph already executed them
    internally; TTS only needs the assistant content."""
    chunks = []
    for m in ai_messages:
        content = getattr(m, "content", "") or ""
        if not content:
            continue
        chunks.append(agents_llm.ChatChunk(
            id=f"graph_{uuid.uuid4().hex[:8]}",
            delta=agents_llm.ChoiceDelta(role="assistant", content=content),
        ))
    return chunks


class JarvisSupervisorGraphLLM(agents_llm.LLM):
    """Wraps a compiled supervisor StateGraph behind the LiveKit LLM
    contract. Construct once at agent startup; each user turn calls
    `chat()` which runs the graph fresh."""

    def __init__(self, *, specialist_tools: list[Any]):
        super().__init__()
        self._graph = build_graph(specialist_tools=specialist_tools)
        self._specialist_tools = specialist_tools

    def chat(self, *, chat_ctx, tools=None, **kwargs):  # noqa: D401
        """LiveKit calls this for each turn. Tools is the
        AgentSession's tool list — we ignore it because our graph has
        its own tool list bound at compile time."""
        # Extract user_query (last user turn) + history.
        lc_messages = _ctx_to_lc_messages(chat_ctx)
        user_query = ""
        history = []
        for m in reversed(lc_messages):
            if isinstance(m, HumanMessage):
                user_query = m.content
                # Everything before this is history.
                history = lc_messages[:lc_messages.index(m)]
                break

        state = initial_state(user_query=user_query)
        state["messages"] = history

        try:
            final_state = self._graph.invoke(state)
        except Exception as e:
            logger.exception("[graph] invoke failed: %s", e)
            # Surface a polite apology rather than crashing the turn.
            final_state = {
                "messages": [AIMessage(
                    content="My apologies, sir — something went wrong on my end."
                )]
            }

        # Pick out the AIMessages that were appended during the run
        # (everything past the original history).
        appended = (final_state.get("messages") or [])[len(history):]
        ai_messages = [m for m in appended if isinstance(m, AIMessage)]

        chunks = _ai_messages_to_chunks(ai_messages)
        if not chunks:
            chunks = [agents_llm.ChatChunk(
                id=f"graph_empty_{uuid.uuid4().hex[:8]}",
                delta=agents_llm.ChoiceDelta(role="assistant", content=""),
            )]
        return _GraphLLMStream(chunks)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_llm_adapter.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/supervisor_graph/llm_adapter.py \
  src/voice-agent/tests/test_graph_llm_adapter.py
git commit -m "supervisor-graph: JarvisSupervisorGraphLLM (LiveKit LLM adapter)"
```

---

## Phase 9 — Wire into JarvisAgent + feature flag

### Task 14: `JARVIS_LANGGRAPH_SUPERVISOR=1` swaps the supervisor LLM

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` (entrypoint() function)
- Test: `src/voice-agent/tests/test_graph_feature_flag.py`

- [ ] **Step 1: Write the failing test**

```python
# src/voice-agent/tests/test_graph_feature_flag.py
"""When JARVIS_LANGGRAPH_SUPERVISOR=1 is set, entrypoint() must build
the JarvisSupervisorGraphLLM and pass it to AgentSession in place of
the dispatcher. Test the construction path without standing up a real
LiveKit session."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key")
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")


def test_feature_flag_off_uses_legacy_supervisor():
    """Default behaviour: env var unset → existing dispatcher path.
    The flag is opt-in for the soak window."""
    import jarvis_agent
    with patch.dict(os.environ, {"JARVIS_LANGGRAPH_SUPERVISOR": "0"}):
        # Calling the helper that picks the supervisor LLM:
        chosen = jarvis_agent._pick_supervisor_llm(
            specialist_tools=[],
            legacy_llm="LEGACY-SENTINEL",
        )
    assert chosen == "LEGACY-SENTINEL"


def test_feature_flag_on_uses_graph_supervisor():
    import jarvis_agent
    from supervisor_graph.llm_adapter import JarvisSupervisorGraphLLM
    with patch.dict(os.environ, {"JARVIS_LANGGRAPH_SUPERVISOR": "1"}):
        chosen = jarvis_agent._pick_supervisor_llm(
            specialist_tools=[],
            legacy_llm="LEGACY-SENTINEL",
        )
    assert isinstance(chosen, JarvisSupervisorGraphLLM)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_feature_flag.py -v
```

Expected: `AttributeError: module 'jarvis_agent' has no attribute '_pick_supervisor_llm'`

- [ ] **Step 3: Add the picker helper to jarvis_agent.py**

Find the `entrypoint()` function (around line 5367 — the one starting with `async def entrypoint(ctx: JobContext)`). Add this helper ABOVE `entrypoint`:

```python
def _pick_supervisor_llm(*, specialist_tools, legacy_llm):
    """Feature-flagged supervisor LLM picker.

    JARVIS_LANGGRAPH_SUPERVISOR=1 → use the new LangGraph-state-shape
    supervisor (spec: 2026-05-04-supervisor-langgraph-design.md). The
    graph's structural cure prevents completion-claim lies — the
    supervisor literally cannot speak text on a TASK turn that has
    not yet observed a tool result.

    Default off through the soak window. Flip to on once telemetry
    confirms zero confab-detector drops on a 100-turn dev set.
    """
    if os.environ.get("JARVIS_LANGGRAPH_SUPERVISOR", "0") == "1":
        try:
            from supervisor_graph.llm_adapter import (
                JarvisSupervisorGraphLLM,
            )
            logger.info(
                "[supervisor] LangGraph state-shape supervisor active "
                "(JARVIS_LANGGRAPH_SUPERVISOR=1)"
            )
            return JarvisSupervisorGraphLLM(
                specialist_tools=specialist_tools,
            )
        except Exception as e:
            logger.exception(
                "[supervisor] LangGraph supervisor failed to construct; "
                "falling back to legacy dispatcher: %s", e,
            )
    return legacy_llm
```

Then in `entrypoint()`, find where `llm_arg` is assigned (around line 5484, currently `llm=llm_arg` in the `AgentSession(...)` call). Just before the AgentSession construction, wrap `llm_arg`:

```python
# Feature-flag the supervisor LLM. When JARVIS_LANGGRAPH_SUPERVISOR=1,
# the LangGraph state-shape supervisor takes over — see
# supervisor_graph/ and the design doc above. Default off.
llm_arg = _pick_supervisor_llm(
    specialist_tools=build_all_transfer_tools(),
    legacy_llm=llm_arg,
)
```

The `build_all_transfer_tools()` import should already be present near the top of the file (used by JarvisAgent). Verify with `grep -n build_all_transfer_tools src/voice-agent/jarvis_agent.py`.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_graph_feature_flag.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Run the entire voice-agent test suite to verify no regression**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/ -q --deselect tests/test_specialists_health.py::test_supervisor_has_persona_register_block 2>&1 | tail -10
```

Expected: ≥ all current passes + 18 new passes (state, classify, speak_gate, dispatch, fallback, specialist, tools, assembly, llm_adapter, feature_flag). 1 deselected (pre-existing persona test, unrelated).

- [ ] **Step 6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  src/voice-agent/jarvis_agent.py \
  src/voice-agent/tests/test_graph_feature_flag.py
git commit -m "supervisor-graph: feature-flag wiring (JARVIS_LANGGRAPH_SUPERVISOR)"
```

---

## Phase 10 — Live verification & soak

### Task 15: Live smoke test with the flag enabled

This task is manual + observational. It produces no code changes;
its commit is just the soak telemetry note.

**Files:**
- Create: `docs/superpowers/notes/2026-05-04-supervisor-graph-soak.md`

- [ ] **Step 1: Restart the agent service with the flag enabled**

```bash
sudo -u ulrich systemctl --user set-environment JARVIS_LANGGRAPH_SUPERVISOR=1
systemctl --user restart jarvis-voice-agent.service
sleep 5
systemctl --user is-active jarvis-voice-agent.service
```

Expected: `active`.

- [ ] **Step 2: Verify the new supervisor logs its activation**

```bash
journalctl --user -u jarvis-voice-agent.service -n 50 --no-pager 2>&1 | grep -E '\[supervisor\]|graph|prewarm|registered worker' | tail -10
```

Expected: a line like
`[supervisor] LangGraph state-shape supervisor active (JARVIS_LANGGRAPH_SUPERVISOR=1)`.

- [ ] **Step 3: Run a 5-turn dev-rig conversation script**

Drive the following turns through the voice-client:
1. *"Jarvis."*  → expected: "Yes, sir?" (bare-vocative; bypasses graph)
2. *"How are you?"* → expected: graph BANTER path; one short reply
3. *"What time is it?"* → expected: graph reasoning OR a real time-tool call (depending on tool wiring)
4. *"Open a new tab on the current browser and go to YouTube"* → expected: filler ("One moment, sir.") + specialist runs + summary ("Tab opened…" or actual outcome)
5. *"Thanks."* → expected: short BANTER reply

- [ ] **Step 4: Pull telemetry**

```bash
awk -F'"timestamp": "' 'NF>1 {ts=$2; sub(/".*/, "", ts); if (ts > "<RESTART-TIME>") print $0}' /tmp/jarvis-voice-agent.log | grep -E '\[supervisor\]|\[speak-gate\]|\[task-dispatch\]|\[specialist\]|\[classify\]|confab-detector|breaker:llm' | tail -50
```

Replace `<RESTART-TIME>` with the timestamp from Step 1's restart in
ISO format (e.g. `2026-05-04T19:00:00`).

Look for:
- ✅ `[speak-gate] release` lines on every successful turn
- ✅ `[task-dispatch] emitted N tool_call(s)` for the TASK turn
- ✅ `[specialist] filler:` then `[specialist] <name> done:` for the handoff
- ❌ Zero `[confab-detector] dropping` lines
- ❌ Zero `[breaker:llm] OPEN` (transient validation errors are uncounted by the breaker fix)

- [ ] **Step 5: Write the soak note**

Save the telemetry above into a note:

```bash
cat > /home/ulrich/Documents/Projects/jarvis/docs/superpowers/notes/2026-05-04-supervisor-graph-soak.md <<'EOF'
# Supervisor graph — initial soak

**Date:** 2026-05-04
**Flag:** JARVIS_LANGGRAPH_SUPERVISOR=1
**Driver:** 5-turn dev-rig script (see plan, Phase 10, Task 15)

## Telemetry highlights

(paste the grepped log block from Step 4 here)

## Verdict

Pass / Fail / Mixed — describe.

## Outstanding issues

(if any — track here, do NOT silently leave for later)

## Decision

[ ] Promote to default (flip env var in systemd unit)
[ ] Continue soak — re-run in 24h
[ ] Roll back — describe issue + plan
EOF
```

- [ ] **Step 6: Commit the note**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  docs/superpowers/notes/2026-05-04-supervisor-graph-soak.md
git commit -m "supervisor-graph: initial soak telemetry note"
```

---

### Task 16: Promote to default (after soak passes)

This task is **gated on Task 15 telemetry showing zero confab-detector
drops + zero unexpected breaker opens + clean handoffs**. Do NOT
execute until that's verified.

**Files:**
- Modify: `~/.config/systemd/user/jarvis-voice-agent.service` (add
  `Environment=JARVIS_LANGGRAPH_SUPERVISOR=1` line)

- [ ] **Step 1: Verify Task 15 verdict**

Re-read `docs/superpowers/notes/2026-05-04-supervisor-graph-soak.md`.
If the verdict is anything other than "Pass" with zero outstanding
issues, STOP — return to fixing those issues. Do NOT promote.

- [ ] **Step 2: Add the Environment line to the systemd unit**

```bash
sed -i '/^\[Service\]/a Environment=JARVIS_LANGGRAPH_SUPERVISOR=1' \
  ~/.config/systemd/user/jarvis-voice-agent.service
systemctl --user daemon-reload
```

- [ ] **Step 3: Restart the service**

```bash
systemctl --user restart jarvis-voice-agent.service
sleep 5
systemctl --user is-active jarvis-voice-agent.service
journalctl --user -u jarvis-voice-agent.service -n 20 --no-pager | \
  grep -E '\[supervisor\]' | tail -3
```

Expected: `active` + the LangGraph supervisor activation log line.

- [ ] **Step 4: Verify the env var is present in the running process**

```bash
systemctl --user show jarvis-voice-agent.service \
  -p Environment 2>&1 | grep JARVIS_LANGGRAPH_SUPERVISOR
```

Expected: `Environment=JARVIS_LANGGRAPH_SUPERVISOR=1`.

- [ ] **Step 5: Commit (the unit file change tracked outside git;
  document the flip in the soak note)**

Update `docs/superpowers/notes/2026-05-04-supervisor-graph-soak.md`:
mark `[x] Promote to default` and add a "Promotion timestamp" line.

```bash
cd /home/ulrich/Documents/Projects/jarvis && git add \
  docs/superpowers/notes/2026-05-04-supervisor-graph-soak.md
git commit -m "supervisor-graph: promote to default — soak passed"
```

---

## Self-review

Per the writing-plans skill, fresh-eyes pass on this plan against the spec.

### Spec coverage check

| Spec section | Covered by task(s) | Notes |
|--------------|----|----|
| §3.1 Topology — LangGraph-as-LLM behind AgentSession | Task 13 (llm_adapter) + Task 14 (feature flag) | ✅ |
| §3.2 JarvisState channels (12 fields) | Task 2 | ✅ All 12 in test_state_required_channels_present |
| §3.3 Node graph — classify → branch → dispatch → tool/specialist → speak_gate → END | Tasks 5, 7, 8, 9, 10, 11 | ✅ Tasks 11's `build_graph` wires the full topology |
| §3.4 classify contract (regex + strict-JSON LLM) | Tasks 3, 4, 5 | ✅ |
| §3.4 task_dispatch contract (tool_choice="required") | Task 7 (primary) + Task 12 (fallback) | ✅ |
| §3.4 tool_node | Task 10 (clear_resolved_pending) | ✅ for handoff path; direct-tool-execution via prebuilt ToolNode flagged as future work in graph.py docstring |
| §3.4 speak_gate (pending check) | Task 6 | ✅ |
| §3.4 specialist_subgraph | Task 9 | ✅ filler-once + run + clear pending + failure path |
| §3.4 reflect | Skipped — moved to "future work" since Phase 1 only needs single-tool-round per voice latency budget | Note added in graph.py |
| §3.5 Filler-phrase strategy | Task 9 | ✅ four non-committal fillers; flagged with handoff_filler_voiced |
| §3.6 Fallback on Groq failure (cross-stream lie cure) | Task 12 | ✅ tests cover both successful fallback + double-failure raise |
| §3.7 Checkpointing (SqliteSaver) | Skipped from this plan — checkpoint setup added when long-running multi-turn state is needed; first turn-graph runs are stateless. Tracked in design doc. | Acceptable for soak window; flag in soak note if turn-isolation issues emerge |
| §4 Files & components | Tasks 1, 2, 3-5, 6, 7-8, 9, 10, 11, 12, 13, 14 | ✅ |
| §5 Migration & rollback (feature flag) | Task 14 + Task 16 | ✅ |
| §6 Tests (unit + integration) | Tasks 1, 2, 3-5, 6, 7-8, 9, 10, 11, 12, 13, 14 | ✅ 18 test files, 30+ test cases |
| §7 Risks & mitigations | Implicit — feature flag (Task 14), comprehensive tests, soak (Task 15) | ✅ |
| §8 Success criteria | Task 15 (soak telemetry) | ✅ |

**Gaps:** §3.4 reflect node and §3.7 checkpointer are deferred. Both are
optional optimizations — the speak_gate already gives the structural
cure without them. Documented inline; not blocking the rebuild.

### Placeholder scan

- [x] No "TBD" / "TODO" / "implement later" / "fill in details" in any task body.
- [x] Every code block contains complete code.
- [x] Every test step shows the actual command + expected output.
- [x] Every commit step shows the exact `git add` + commit message.

### Type consistency

- [x] `JarvisState` field names match in §3.2 spec, Task 2 implementation, and Task 6/9/12 consumers (`pending_tool_calls`, `pending_specialist`, `last_tool_result`, `handoff_filler_voiced`, `failed_providers`, `retry_attempt`).
- [x] `Route` literal type used consistently: `BANTER`, `TASK`, `REASONING`, `EMOTIONAL`, `WAITING`.
- [x] `tool_choice="required"` (Groq's exact param value) used consistently in Task 7 and Task 12.
- [x] Feature flag name `JARVIS_LANGGRAPH_SUPERVISOR` used consistently across spec, Task 14 test, Task 14 helper, Task 16 promotion.
- [x] Environment variable model overrides use the `JARVIS_GRAPH_<ROUTE>_MODEL` pattern across Task 4, 7, 8.

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-04-supervisor-langgraph-implementation.md`.**

Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration, smaller blast radius if anything goes sideways.

2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
