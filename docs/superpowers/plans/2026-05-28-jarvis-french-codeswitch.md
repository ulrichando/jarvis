# JARVIS French/English Code-Switch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore JARVIS's French replies — auto-detect user's language per turn via STT, mirror it in the LLM reply, route TTS to a French voice when needed.

**Architecture:** STT runs unpinned (`language=None`) so Whisper/Deepgram return the detected language code on every transcript. A small `LangContext` object on the session carries the most-recent detected language. The TTS dispatcher takes the language as a new axis: English → existing Orpheus (Troy/Austin), French → EdgeTTS `fr-FR-HenriNeural`. The LLM is told via soul.md to match the user's language. Single env kill-switch (`JARVIS_LANG_AUTODETECT=0`) reverts everything to English-only without redeploy.

**Tech Stack:** Python 3.13, livekit-agents, Groq Whisper Large v3 Turbo, Deepgram Nova-3 (STT), Groq Orpheus + Microsoft Edge-TTS (TTS), Anthropic Claude / Groq llama (LLM).

**Spec:** [docs/superpowers/specs/2026-05-28-jarvis-french-codeswitch-design.md](../specs/2026-05-28-jarvis-french-codeswitch-design.md)

**Pre-flight:** Run `cd src/voice-agent && .venv/bin/python -m pytest tests/ --ignore=tests/test_memory_injection_no_bump.py -q` to confirm the suite is green before starting. Current expected: 2703 passed, 1 skipped (the ignored test imports `src/hub/server.py` which isn't checked into this tree).

---

### Task 1: LangContext module

A per-session holder for the most-recently-detected user language. Defaults to `"en"`. Updates from STT events. Read by the TTS dispatcher at pick-time.

**Files:**
- Create: `src/voice-agent/pipeline/lang_context.py`
- Test: `src/voice-agent/tests/test_lang_context.py`

- [ ] **Step 1: Write the failing test**

Create `src/voice-agent/tests/test_lang_context.py`:

```python
"""LangContext — per-session most-recent-detected user language.

Confidence floor protects against 1-word utterances ("hi" / "merci")
bouncing the voice — STT's language ID isn't reliable on tiny inputs.
"""
from __future__ import annotations

from pipeline.lang_context import LangContext


def test_default_is_english():
    ctx = LangContext()
    assert ctx.get() == "en"


def test_default_override():
    ctx = LangContext(default="fr")
    assert ctx.get() == "fr"


def test_set_above_floor_sticks():
    ctx = LangContext()
    ctx.set("fr", confidence=0.9)
    assert ctx.get() == "fr"


def test_set_below_floor_is_noop():
    """Confidence below the floor (0.6) does not update.

    Short utterances ("hi" / "merci") often have low-confidence
    detection that flip-flops; the floor keeps the voice steady."""
    ctx = LangContext()
    ctx.set("fr", confidence=0.5)
    assert ctx.get() == "en"  # unchanged


def test_set_at_floor_sticks():
    ctx = LangContext()
    ctx.set("fr", confidence=0.6)
    assert ctx.get() == "fr"


def test_multiple_updates_track_latest():
    ctx = LangContext()
    ctx.set("fr", confidence=0.9)
    ctx.set("en", confidence=0.95)
    ctx.set("fr", confidence=0.8)
    assert ctx.get() == "fr"


def test_default_confidence_is_max():
    """set() called without confidence keyword arg should accept the
    update (used by callers that don't have per-event confidence)."""
    ctx = LangContext()
    ctx.set("fr")
    assert ctx.get() == "fr"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_lang_context.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'pipeline.lang_context'`.

- [ ] **Step 3: Write the implementation**

Create `src/voice-agent/pipeline/lang_context.py`:

```python
"""LangContext — per-session most-recent-detected user language.

Default "en". Updated by the STT result hook in jarvis_agent.py.
Read by the TTS dispatcher at pick() time in turn_dispatcher.py
and turn_graph.py.

Single asyncio loop per session, plain attribute access is
thread-safe enough — no locks needed.
"""
from __future__ import annotations


__all__ = ["LangContext"]


# Confidence floor — short utterances ("hi" / "merci") often produce
# low-confidence language IDs that flip-flop. Below this floor the
# update is silently dropped, keeping the voice steady.
_CONFIDENCE_FLOOR = 0.6


class LangContext:
    """Per-session most-recent-detected user language.

    Construct one per agent session and stash it on the session
    (e.g., `session.lang_ctx = LangContext()`). The STT result
    handler calls `set(lang, confidence)` on each transcript; the
    TTS dispatcher calls `get()` at pick() time.
    """

    def __init__(self, default: str = "en") -> None:
        self._lang = default

    def set(self, lang: str, confidence: float = 1.0) -> None:
        if confidence < _CONFIDENCE_FLOOR:
            return
        self._lang = lang

    def get(self) -> str:
        return self._lang
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_lang_context.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/lang_context.py src/voice-agent/tests/test_lang_context.py
git commit -m "feat(voice-agent): LangContext — per-session most-recent user language

Holds the most-recently-detected user language from STT. Default 'en'.
Confidence floor (0.6) drops low-confidence updates so short utterances
don't flip the voice. Read by the TTS dispatcher at pick() time so the
voice matches whatever the user just spoke.

Standalone unit — no wiring yet. Subsequent commits hook it to the STT
result handler and the TTS dispatcher."
```

---

### Task 2: DispatchingTTS language axis

Extend `DispatchingTTS.pick()` to accept a `lang` argument. When `lang == "fr"` and a French inner is configured, return it; otherwise fall back to the existing English route lookup.

**Files:**
- Modify: `src/voice-agent/pipeline/dispatching_tts.py`
- Test: `src/voice-agent/tests/test_dispatching_tts_lang.py`

- [ ] **Step 1: Write the failing test**

Create `src/voice-agent/tests/test_dispatching_tts_lang.py`:

```python
"""DispatchingTTS — language axis on pick().

en + any route → route's English inner (existing behaviour).
fr + any route → the single French inner (EdgeTTS fr-FR-HenriNeural).
Unknown lang (de, es, etc.) → falls back to English. The LLM still
respects soul.md but the voice stays English until we add more
locales — YAGNI for v1.
"""
from __future__ import annotations

from pipeline.dispatching_tts import DispatchingTTS


class _StubTTS:
    def __init__(self, label: str) -> None:
        self.voice_id = label

    def __repr__(self) -> str:
        return f"<StubTTS {self.voice_id}>"


def _make_dispatcher(fr_inner=None):
    inners = {
        "BANTER":    _StubTTS("en:austin"),
        "TASK":      _StubTTS("en:troy"),
        "REASONING": _StubTTS("en:troy"),
        "EMOTIONAL": _StubTTS("en:daniel"),
    }
    return DispatchingTTS(
        inners=inners,
        fallback=_StubTTS("en:fallback"),
        fr_inner=fr_inner,
    )


def test_en_picks_english_route_inner():
    d = _make_dispatcher()
    picked = d.pick("TASK", lang="en")
    assert picked.voice_id == "en:troy"


def test_en_default_lang_is_backward_compatible():
    """Existing callers passing only route= must still work — lang
    defaults to 'en'."""
    d = _make_dispatcher()
    picked = d.pick("TASK")
    assert picked.voice_id == "en:troy"


def test_fr_returns_fr_inner_regardless_of_route():
    fr = _StubTTS("fr:henri")
    d = _make_dispatcher(fr_inner=fr)
    for route in ["BANTER", "TASK", "REASONING", "EMOTIONAL"]:
        picked = d.pick(route, lang="fr")
        assert picked is fr, f"route={route} did not get fr_inner"


def test_fr_without_fr_inner_falls_back_to_english():
    """If build_dispatching_tts failed to construct fr_inner (e.g.,
    EdgeTTS import error), fr requests should not crash — fall back
    to the English route."""
    d = _make_dispatcher(fr_inner=None)
    picked = d.pick("TASK", lang="fr")
    assert picked.voice_id == "en:troy"


def test_unknown_lang_falls_back_to_english():
    fr = _StubTTS("fr:henri")
    d = _make_dispatcher(fr_inner=fr)
    picked = d.pick("TASK", lang="de")
    assert picked.voice_id == "en:troy"


def test_last_route_and_voice_id_updated():
    """Telemetry breadcrumbs the dispatcher exposes for the metrics
    span — must still be set on both en and fr paths."""
    fr = _StubTTS("fr:henri")
    d = _make_dispatcher(fr_inner=fr)
    d.pick("BANTER", lang="en")
    assert d.last_route == "BANTER"
    assert d.last_voice_id == "en:austin"
    d.pick("REASONING", lang="fr")
    assert d.last_route == "REASONING"
    assert d.last_voice_id == "fr:henri"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_dispatching_tts_lang.py -v
```

Expected: errors — `TypeError: __init__() got an unexpected keyword argument 'fr_inner'` AND `pick() got unexpected keyword argument 'lang'`.

- [ ] **Step 3: Write the implementation**

Replace `src/voice-agent/pipeline/dispatching_tts.py` entirely with:

```python
"""Dispatching TTS wrapper. Sibling pattern of DispatchingLLM.

The integration step in jarvis_agent.py constructs four inner TTS
instances (one per route) and assembles them here. The voice_id
attribute is a duck-typed convenience for telemetry; real LiveKit
TTS instances expose the voice somewhere on themselves and can be
adapted.

Language axis (2026-05-28 spec): pick() takes a lang code in
addition to route. fr → single French inner (EdgeTTS); other →
existing English route lookup. Falling back to English on unknown
lang keeps the dispatcher safe when build_dispatching_tts couldn't
construct fr_inner (e.g., EdgeTTS network error at startup).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class DispatchingTTS:
    inners: dict[str, Any]
    fallback: Any
    fr_inner: Optional[Any] = None
    last_route: Optional[str] = None
    last_voice_id: Optional[str] = None

    def pick(self, route: str, lang: str = "en") -> Any:
        if lang == "fr" and self.fr_inner is not None:
            inner = self.fr_inner
        else:
            inner = self.inners.get(route, self.fallback)
        self.last_route = route
        self.last_voice_id = getattr(inner, "voice_id", repr(inner))
        return inner
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_dispatching_tts_lang.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Run the rest of the suite to make sure no callers broke**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/ --ignore=tests/test_memory_injection_no_bump.py -q
```

Expected: 2709 passed (was 2703 + 7 new test_lang_context + 6 new test_dispatching_tts_lang = 2716; allow ±10 for environment drift, but no FAIL).

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/pipeline/dispatching_tts.py src/voice-agent/tests/test_dispatching_tts_lang.py
git commit -m "feat(voice-agent): DispatchingTTS language axis

pick(route, lang='en') — backwards compatible. lang='fr' returns the
single French inner regardless of route; lang='en' (or any other code)
returns the route's English inner. None of the existing call sites
pass lang yet — they default to 'en' and behave exactly as before.

Subsequent commits add the French inner construction in providers/tts.py
and the LangContext.get() pass-through at the dispatcher call sites."
```

---

### Task 3: Build the French EdgeTTS inner

Wire up `build_dispatching_tts()` to construct a French EdgeTTS instance and pass it as `fr_inner` to the returned `DispatchingTTS`.

**Files:**
- Modify: `src/voice-agent/providers/tts.py`
- Test: extend `src/voice-agent/tests/test_dispatching_tts_lang.py`

- [ ] **Step 1: Find the return statement of build_dispatching_tts**

```bash
grep -n "return DispatchingTTS" src/voice-agent/providers/tts.py
```

Expected: a single match in `build_dispatching_tts()`. Note the line number for the next step.

- [ ] **Step 2: Write the failing test**

Append to `src/voice-agent/tests/test_dispatching_tts_lang.py`:

```python
def test_build_dispatching_tts_constructs_fr_inner(monkeypatch, tmp_path):
    """build_dispatching_tts() should attach a French EdgeTTS instance
    as fr_inner. The voice defaults to fr-FR-HenriNeural; overridable
    via JARVIS_FR_EDGE_VOICE."""
    from providers.tts import build_dispatching_tts

    d = build_dispatching_tts()
    assert d.fr_inner is not None, (
        "build_dispatching_tts must construct a French EdgeTTS inner"
    )
    # The EdgeTTS instance's voice_id is set by build_dispatching_tts
    # to a `edge:fr-…` shape so the metrics span tells English/French
    # apart at a glance.
    vid = getattr(d.fr_inner, "voice_id", "")
    assert vid.startswith("edge:fr-"), (
        f"fr_inner voice_id should start with 'edge:fr-', got {vid!r}"
    )


def test_build_dispatching_tts_respects_fr_voice_env(monkeypatch):
    """Override the French voice via JARVIS_FR_EDGE_VOICE."""
    from providers.tts import build_dispatching_tts

    monkeypatch.setenv("JARVIS_FR_EDGE_VOICE", "fr-FR-DeniseNeural")
    d = build_dispatching_tts()
    assert d.fr_inner is not None
    vid = getattr(d.fr_inner, "voice_id", "")
    assert "fr-FR-Den" in vid, (
        f"override voice should appear in voice_id, got {vid!r}"
    )
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_dispatching_tts_lang.py::test_build_dispatching_tts_constructs_fr_inner -v
```

Expected: AssertionError on `d.fr_inner is not None` — current `build_dispatching_tts` doesn't pass `fr_inner`.

- [ ] **Step 4: Implement — modify the bottom of build_dispatching_tts in providers/tts.py**

Inside `build_dispatching_tts()`, just before the final `return DispatchingTTS(...)`, insert:

```python
    # French inner — EdgeTTS with a French voice. Constructed once,
    # used by DispatchingTTS.pick(route, lang='fr') regardless of
    # route. Defaults to fr-FR-HenriNeural (male, standard French);
    # override via JARVIS_FR_EDGE_VOICE.
    fr_voice = os.environ.get("JARVIS_FR_EDGE_VOICE", "fr-FR-HenriNeural")
    try:
        _fr_inner = edge_tts_plugin.EdgeTTS(voice=fr_voice)
        _fr_inner.voice_id = f"edge:{fr_voice[:18]}…"
    except Exception as e:
        logger.warning(
            f"[dispatch] French edge_tts construction failed ({e}); "
            f"fr will fall back to English chain"
        )
        _fr_inner = None
```

Then modify the `return DispatchingTTS(...)` call to pass `fr_inner=_fr_inner`. If the current signature is positional, append `fr_inner=_fr_inner` as a keyword argument.

- [ ] **Step 5: Run both new tests**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_dispatching_tts_lang.py -v
```

Expected: 8 passed (6 from Task 2 + 2 from this task).

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/providers/tts.py src/voice-agent/tests/test_dispatching_tts_lang.py
git commit -m "feat(voice-agent): wire EdgeTTS fr-FR-HenriNeural as DispatchingTTS.fr_inner

build_dispatching_tts() now constructs a single French EdgeTTS
instance and attaches it as fr_inner. DispatchingTTS.pick(route,
lang='fr') will return it; en is unchanged. Voice override:
JARVIS_FR_EDGE_VOICE (default fr-FR-HenriNeural). Construction errors
are logged and degrade gracefully — fr_inner=None falls back to
English without crashing.

The call sites in turn_dispatcher.py and turn_graph.py still pass
only route; the language axis arrives in a later commit."
```

---

### Task 4: Unpin STT language (gated by env kill-switch)

Replace the hardcoded `language="en"` on both Whisper (`providers/stt.py:96`) and Deepgram (`providers/stt.py:169`) with `_stt_language()` — a tiny helper that returns `None` (auto-detect) by default, or `"en"` when `JARVIS_LANG_AUTODETECT=0`.

**Files:**
- Modify: `src/voice-agent/providers/stt.py`
- Test: `src/voice-agent/tests/test_stt_lang_passthrough.py`

- [ ] **Step 1: Write the failing test**

Create `src/voice-agent/tests/test_stt_lang_passthrough.py`:

```python
"""STT language unpinning + kill-switch env.

Default: language=None (Whisper / Deepgram auto-detect).
JARVIS_LANG_AUTODETECT=0 → language='en' (revert to pre-spec behavior
without a redeploy)."""
from __future__ import annotations

from providers import stt as stt_mod


def test_stt_language_default_is_none(monkeypatch):
    monkeypatch.delenv("JARVIS_LANG_AUTODETECT", raising=False)
    assert stt_mod._stt_language() is None


def test_stt_language_killswitch_pins_english(monkeypatch):
    monkeypatch.setenv("JARVIS_LANG_AUTODETECT", "0")
    assert stt_mod._stt_language() == "en"


def test_stt_language_killswitch_truthy_pins_english(monkeypatch):
    """Any non-zero truthy value also disables — common convention."""
    monkeypatch.setenv("JARVIS_LANG_AUTODETECT", "false")
    assert stt_mod._stt_language() == "en"
    monkeypatch.setenv("JARVIS_LANG_AUTODETECT", "off")
    assert stt_mod._stt_language() == "en"


def test_stt_language_explicit_one_enables_autodetect(monkeypatch):
    monkeypatch.setenv("JARVIS_LANG_AUTODETECT", "1")
    assert stt_mod._stt_language() is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_stt_lang_passthrough.py -v
```

Expected: `AttributeError: module 'providers.stt' has no attribute '_stt_language'`.

- [ ] **Step 3: Implement the helper in providers/stt.py**

Near the top of `src/voice-agent/providers/stt.py` (after the imports), add:

```python
def _stt_language():
    """Return the STT language pin.

    None → auto-detect (Whisper and Deepgram both support this and
    return the detected lang code on the transcript event).

    'en' → kill-switch path, set when JARVIS_LANG_AUTODETECT is any
    falsy string (0, false, off, no, ''). Reverts to pre-spec
    behavior without a redeploy.
    """
    raw = os.environ.get("JARVIS_LANG_AUTODETECT", "1").strip().lower()
    if raw in ("0", "false", "off", "no", ""):
        return "en"
    return None
```

Make sure `import os` is already at the top of the module (it should be).

- [ ] **Step 4: Replace the two language="en" call sites**

At `providers/stt.py:96`, change:

```python
    return BreakeredGroqSTT(model="whisper-large-v3-turbo", language="en")
```

to:

```python
    return BreakeredGroqSTT(model="whisper-large-v3-turbo", language=_stt_language())
```

At `providers/stt.py:169` (Deepgram), change:

```python
            language="en",
```

to:

```python
            language=_stt_language(),
```

- [ ] **Step 5: Run all STT tests**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_stt_lang_passthrough.py tests/test_stt_chain.py tests/test_stt_fallback.py -v 2>&1 | tail -20
```

Expected: all green. (test_stt_chain and test_stt_fallback may not exist — that's fine; the new test_stt_lang_passthrough is the gate.)

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/providers/stt.py src/voice-agent/tests/test_stt_lang_passthrough.py
git commit -m "feat(voice-agent): STT auto-detect language (kill-switch JARVIS_LANG_AUTODETECT=0)

Both Whisper Large v3 Turbo and Deepgram Nova-3 now run with
language=None — they auto-detect on every transcript and surface the
code on the SpeechEvent. JARVIS_LANG_AUTODETECT=0 reverts to
language='en' for a panic-revert without redeploying.

The detected language will flow into LangContext via the STT result
handler in a later commit; this commit only changes what STT does."
```

---

### Task 5: Soul.md language rule

Replace the one-line `"English only."` rule with a language-matching rule that tells the LLM to mirror the user's language.

**Files:**
- Modify: `src/voice-agent/prompts/soul.md:5`

- [ ] **Step 1: Apply the edit**

Open `src/voice-agent/prompts/soul.md` and at line 5, replace:

```
literally — every word matters. English only.
```

with:

```
literally — every word matters. Reply in the user's language. If they speak French, reply in French; if English, English. Same register either way — peer engineer, not butler.
```

- [ ] **Step 2: Sanity-check no other "English only" instances**

```bash
grep -in "english only" src/voice-agent/prompts/*.md
```

Expected: zero matches. If any remain, the spec missed them — surface to user before continuing.

- [ ] **Step 3: Run the prompt-related tests**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_prompt_builder.py tests/test_soul_loader.py -v 2>&1 | tail -10
```

Expected: all green. (If these test files don't exist, skip — soul.md loading is tested indirectly elsewhere.)

- [ ] **Step 4: Commit**

```bash
git add src/voice-agent/prompts/soul.md
git commit -m "feat(voice-agent): soul.md — reply in the user's language (drop English-only rule)

Single-line rule replacement. Was 'English only.', now tells the LLM
to mirror the user's language with the same register. No other prompt
files reference an English-only constraint — verified by grep.

Effect alone: LLM may reply in French if the user speaks French, but
the STT transcript is still upstream and the TTS voice still English.
Subsequent commits wire LangContext into the STT/TTS path so the
whole turn matches end-to-end."
```

---

### Task 6: Wire LangContext into the agent session + STT result handler

Construct a `LangContext` per session and store it on the session object. Update it inside the existing `@session.on("user_input_transcribed")` handler at `jarvis_agent.py:5091` with the language and confidence from the STT event.

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py`
- Test: extend `src/voice-agent/tests/test_stt_lang_passthrough.py`

- [ ] **Step 1: Locate the session construction site**

```bash
grep -n "session = AgentSession\|session: AgentSession\|AgentSession(" src/voice-agent/jarvis_agent.py | head -5
```

Note the line of the `AgentSession(...)` constructor invocation. The `session.lang_ctx = LangContext()` line goes immediately after the session is constructed.

- [ ] **Step 2: Write the failing test (behavior of the handler)**

Append to `src/voice-agent/tests/test_stt_lang_passthrough.py`:

```python
import types


def _make_event(language, confidence=0.9, is_final=True, transcript="bonjour"):
    """Mimic a LiveKit user_input_transcribed event shape — duck-typed
    attribute access is what the handler uses."""
    return types.SimpleNamespace(
        language=language,
        confidence=confidence,
        is_final=is_final,
        transcript=transcript,
    )


def test_handler_updates_lang_context_on_high_confidence_french():
    """The STT result handler should call session.lang_ctx.set(lang,
    confidence) when language and confidence are present on the event."""
    from pipeline.lang_context import LangContext
    from jarvis_agent import _update_lang_from_stt_event

    ctx = LangContext()
    ev = _make_event(language="fr", confidence=0.92)
    _update_lang_from_stt_event(ctx, ev)
    assert ctx.get() == "fr"


def test_handler_no_op_when_language_missing():
    """STT plugins that don't surface a language field — handler must
    not crash; LangContext stays at its prior value."""
    from pipeline.lang_context import LangContext
    from jarvis_agent import _update_lang_from_stt_event

    ctx = LangContext()
    ev = _make_event(language=None, confidence=0.9)
    _update_lang_from_stt_event(ctx, ev)
    assert ctx.get() == "en"  # unchanged


def test_handler_no_op_below_confidence_floor():
    from pipeline.lang_context import LangContext
    from jarvis_agent import _update_lang_from_stt_event

    ctx = LangContext()
    ev = _make_event(language="fr", confidence=0.4)
    _update_lang_from_stt_event(ctx, ev)
    assert ctx.get() == "en"


def test_handler_no_op_when_confidence_missing():
    """Some events omit confidence; default to 1.0 (above floor) so
    the language still propagates."""
    from pipeline.lang_context import LangContext
    from jarvis_agent import _update_lang_from_stt_event

    ctx = LangContext()
    ev = types.SimpleNamespace(language="fr", is_final=True, transcript="bonjour")
    _update_lang_from_stt_event(ctx, ev)
    assert ctx.get() == "fr"
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_stt_lang_passthrough.py::test_handler_updates_lang_context_on_high_confidence_french -v
```

Expected: `ImportError: cannot import name '_update_lang_from_stt_event' from 'jarvis_agent'`.

- [ ] **Step 4: Add the helper near the top of jarvis_agent.py (module-level)**

Find a quiet spot at module scope (e.g., right after the LangContext import — see Step 5). Add:

```python
def _update_lang_from_stt_event(ctx, ev) -> None:
    """Update a LangContext from a user_input_transcribed event.

    Handles three event-shape quirks:
      - language attr may be missing or None (some STT plugins don't
        surface it). Skip — keep previous lang.
      - confidence attr may be missing. Default 1.0 — accept the
        language since we have no signal to reject it.
      - language is anything truthy → pass through; LangContext's
        confidence floor handles low-confidence drops.
    """
    lang = getattr(ev, "language", None)
    if not lang:
        return
    conf = getattr(ev, "confidence", 1.0)
    try:
        ctx.set(lang, confidence=float(conf))
    except (TypeError, ValueError):
        ctx.set(lang)
```

- [ ] **Step 5: Add the LangContext import + session construction**

Near the top of `jarvis_agent.py` where other pipeline imports live (search for `from pipeline.`), add:

```python
from pipeline.lang_context import LangContext
```

Then locate the `AgentSession(...)` construction (from Step 1) and immediately after the session is assigned, add:

```python
    session.lang_ctx = LangContext()
```

- [ ] **Step 6: Hook the existing user_input_transcribed handler at line ~5091**

Read jarvis_agent.py:5091-5110 to see the existing `_on_user_input(ev)` handler body. Add one line at the very top of the handler body (before the try/except for audio_silence_watchdog):

```python
        _update_lang_from_stt_event(session.lang_ctx, ev)
```

The full handler becomes:

```python
    @session.on("user_input_transcribed")
    def _on_user_input(ev) -> None:
        _update_lang_from_stt_event(session.lang_ctx, ev)
        try:
            from resilience import audio_silence_watchdog as _asw
            _asw.mark_audio_activity()
        except Exception:
            pass
        if getattr(ev, "is_final", True):
            # ... existing body unchanged ...
```

- [ ] **Step 7: Run the tests**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_stt_lang_passthrough.py -v
```

Expected: 8 passed (4 from Task 4 + 4 from this task).

- [ ] **Step 8: Commit**

```bash
git add src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_stt_lang_passthrough.py
git commit -m "feat(voice-agent): wire LangContext into session + STT result handler

Each AgentSession gets its own LangContext (default 'en'). The
user_input_transcribed handler calls _update_lang_from_stt_event,
which reads ev.language + ev.confidence and updates the context if
language is present (any non-empty value passes through; the
confidence floor inside LangContext handles low-confidence drops).

Side effects so far: LangContext is populated per turn but nothing
reads it yet — the TTS dispatcher pass-through arrives in the next
commit."
```

---

### Task 7: Pass `lang` from LangContext into every dispatch_tts.pick() call

Update every `dispatch_tts.pick(route)` call site to also pass `lang=session.lang_ctx.get()`. Five call sites total: four in `pipeline/turn_dispatcher.py` (lines 313, 331, 386, 546) and one in `pipeline/turn_graph.py:218`.

**Files:**
- Modify: `src/voice-agent/pipeline/turn_dispatcher.py`
- Modify: `src/voice-agent/pipeline/turn_graph.py`

- [ ] **Step 1: Confirm the call sites still match**

```bash
grep -n "dispatch_tts.pick\|tts_dispatcher.pick" src/voice-agent/pipeline/turn_dispatcher.py src/voice-agent/pipeline/turn_graph.py
```

Expected:
- `pipeline/turn_dispatcher.py:313` — `session._tts = dispatch_tts.pick("TASK")`
- `pipeline/turn_dispatcher.py:331` — `fast_tts = dispatch_tts.pick("BANTER")`
- `pipeline/turn_dispatcher.py:386` — `fast_tts = dispatch_tts.pick("REASONING")`
- `pipeline/turn_dispatcher.py:546` — `new_tts = dispatch_tts.pick(route)`
- `pipeline/turn_graph.py:218` — `new_tts = tts_dispatcher.pick(route)`

If any line numbers drifted, adjust the edits below accordingly.

- [ ] **Step 2: Write a smoke test**

Create `src/voice-agent/tests/test_lang_dispatch_wiring.py`:

```python
"""Smoke-test that the dispatcher call sites pass lang from
LangContext.

We don't try to construct a real LiveKit session — instead, patch
DispatchingTTS.pick to capture its kwargs and call the dispatcher
function with a stub session that has a lang_ctx returning 'fr'."""
from __future__ import annotations

import types
from unittest.mock import MagicMock

from pipeline.dispatching_tts import DispatchingTTS
from pipeline.lang_context import LangContext


def _spy_dispatcher():
    spy = MagicMock(spec=DispatchingTTS)
    return spy


def test_turn_graph_passes_lang_from_session_lang_ctx():
    """turn_graph.py's TTS dispatch should call pick(route, lang=…)
    using session.lang_ctx.get()."""
    from pipeline import turn_graph

    ctx = LangContext()
    ctx.set("fr", confidence=0.9)
    session = types.SimpleNamespace(lang_ctx=ctx, _tts=None)
    tts_spy = _spy_dispatcher()
    llm_spy = MagicMock()
    llm_spy.pick = MagicMock(return_value=object())
    tts_spy.pick = MagicMock(return_value=object())

    # Use the same private helper turn_graph.py uses to dispatch;
    # search for a function named `_swap_route` or `swap_route` in
    # turn_graph.py and call it with our stubs.
    swap = getattr(turn_graph, "_swap_route_for_test", None)
    if swap is None:
        # Fall back: directly inspect that the source contains the
        # lang=… keyword on the .pick call. This is a guardrail that
        # protects against regressions even when we can't run the
        # full swap in isolation.
        import inspect
        src = inspect.getsource(turn_graph)
        assert "tts_dispatcher.pick(route, lang=" in src, (
            "turn_graph.py must pass lang=… to tts_dispatcher.pick"
        )
    else:
        swap(session, "BANTER", tts_dispatcher=tts_spy, llm_dispatcher=llm_spy)
        tts_spy.pick.assert_called_with("BANTER", lang="fr")
```

```python
def test_turn_dispatcher_passes_lang_to_all_pick_calls():
    """Source-level guardrail — every dispatch_tts.pick(...) in
    turn_dispatcher.py must include a lang= kwarg sourced from
    session.lang_ctx.get()."""
    from pipeline import turn_dispatcher
    import inspect, re

    src = inspect.getsource(turn_dispatcher)
    pick_calls = re.findall(r"dispatch_tts\.pick\([^)]*\)", src)
    assert pick_calls, "no dispatch_tts.pick(...) calls found — has the module been refactored?"
    for call in pick_calls:
        assert "lang=" in call, (
            f"dispatch_tts.pick call missing lang= kwarg: {call}"
        )
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_lang_dispatch_wiring.py -v
```

Expected: both tests FAIL — the call sites haven't been modified yet.

- [ ] **Step 4: Patch the four turn_dispatcher.py call sites**

Apply these four edits in `src/voice-agent/pipeline/turn_dispatcher.py`:

1. Line ~313 — `session._tts = dispatch_tts.pick("TASK")` → `session._tts = dispatch_tts.pick("TASK", lang=session.lang_ctx.get())`

2. Line ~331 — `fast_tts = dispatch_tts.pick("BANTER")` → `fast_tts = dispatch_tts.pick("BANTER", lang=session.lang_ctx.get())`

3. Line ~386 — `fast_tts = dispatch_tts.pick("REASONING")` → `fast_tts = dispatch_tts.pick("REASONING", lang=session.lang_ctx.get())`

4. Line ~546 — `new_tts = dispatch_tts.pick(route)` → `new_tts = dispatch_tts.pick(route, lang=session.lang_ctx.get())`

If `session` isn't already in scope at any of these lines, locate it from the surrounding function signature — every dispatch happens inside a handler that has access to `session`.

- [ ] **Step 5: Patch the turn_graph.py call site**

`src/voice-agent/pipeline/turn_graph.py:218` — `new_tts = tts_dispatcher.pick(route)` → `new_tts = tts_dispatcher.pick(route, lang=session.lang_ctx.get())`. Same scoping note as above.

- [ ] **Step 6: Run the smoke test**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_lang_dispatch_wiring.py -v
```

Expected: 2 passed.

- [ ] **Step 7: Run the full suite (regression check)**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/ --ignore=tests/test_memory_injection_no_bump.py -q
```

Expected: prior count + 4 new tests from this task. No FAILs.

- [ ] **Step 8: Commit**

```bash
git add src/voice-agent/pipeline/turn_dispatcher.py src/voice-agent/pipeline/turn_graph.py src/voice-agent/tests/test_lang_dispatch_wiring.py
git commit -m "feat(voice-agent): pass LangContext.get() into every dispatch_tts.pick

Five call sites: four in pipeline/turn_dispatcher.py (lines 313, 331,
386, 546) and one in pipeline/turn_graph.py:218. Each now passes
lang=session.lang_ctx.get() alongside route. en/fr alternation now
works end-to-end:

  user speaks French → STT detects fr → LangContext.set('fr', conf) →
  next turn's dispatcher call returns EdgeTTS fr-FR-HenriNeural →
  French audio.

Source-level guardrails in test_lang_dispatch_wiring.py prevent a
future refactor from silently dropping the lang= kwarg."
```

---

### Task 8: Telemetry — user_lang column on the turns table

Persist the detected user language per turn. Useful for spotting code-switch patterns later without re-running the audio.

**Files:**
- Modify: `src/voice-agent/pipeline/turn_telemetry.py`
- Test: `src/voice-agent/tests/test_turn_telemetry_user_lang.py`

- [ ] **Step 1: Locate the schema and the write path**

```bash
grep -nE "CREATE TABLE.*turns|INSERT INTO turns|ALTER TABLE turns" src/voice-agent/pipeline/turn_telemetry.py | head -10
```

Note the schema definition and the insert call.

- [ ] **Step 2: Write the failing test**

Create `src/voice-agent/tests/test_turn_telemetry_user_lang.py`:

```python
"""turn_telemetry schema must include user_lang. Additive column —
default 'en' so back-compat with rows written before the migration."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path


def test_turns_table_has_user_lang_column():
    """Open a fresh telemetry DB and confirm user_lang is in the
    turns table schema with a default of 'en'."""
    from pipeline.turn_telemetry import _ensure_schema

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "telemetry.db"
        conn = sqlite3.connect(db_path)
        _ensure_schema(conn)
        cols = {row[1]: row for row in conn.execute("PRAGMA table_info(turns)")}
        assert "user_lang" in cols, "turns table missing user_lang column"
        # Default should be 'en' (column 4 in PRAGMA table_info is dflt_value)
        default = cols["user_lang"][4]
        assert "en" in str(default), (
            f"user_lang default should be 'en', got {default!r}"
        )
        conn.close()


def test_existing_db_gets_user_lang_added_idempotently():
    """If the column already exists, _ensure_schema must be a no-op
    (no exception). Migration is run on every voice-agent startup."""
    from pipeline.turn_telemetry import _ensure_schema

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "telemetry.db"
        conn = sqlite3.connect(db_path)
        _ensure_schema(conn)  # first run — creates everything
        _ensure_schema(conn)  # second run — must not raise
        cols = {row[1] for row in conn.execute("PRAGMA table_info(turns)")}
        assert "user_lang" in cols
        conn.close()
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_turn_telemetry_user_lang.py -v
```

Expected: assertion failure or import error — the column doesn't exist yet.

- [ ] **Step 4: Update the schema in pipeline/turn_telemetry.py**

Add `user_lang` to the CREATE TABLE statement. Locate the existing CREATE TABLE for `turns` and add the column with `TEXT DEFAULT 'en'`. Then, in the same `_ensure_schema` function (or whatever runs at startup), add an idempotent ALTER:

```python
    try:
        conn.execute("ALTER TABLE turns ADD COLUMN user_lang TEXT DEFAULT 'en'")
    except sqlite3.OperationalError:
        # Column already exists — fine.
        pass
```

This handles upgrades for existing telemetry DBs in the field.

- [ ] **Step 5: Update the INSERT statement to write user_lang**

Find the `INSERT INTO turns` site and add `user_lang` to the column list. If turn-write callers don't have the language, default to `"en"` in the helper signature — the few existing callers won't need to change.

- [ ] **Step 6: Run the tests**

```bash
cd src/voice-agent
.venv/bin/python -m pytest tests/test_turn_telemetry_user_lang.py tests/test_turn_telemetry.py -v 2>&1 | tail -15
```

Expected: 2 new tests pass; existing telemetry tests still green.

- [ ] **Step 7: Migrate the live DB**

Voice-agent restart on next deploy runs `_ensure_schema` and adds the column to `~/.local/share/jarvis/turn_telemetry.db`. No manual migration needed — verify after restart with:

```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db "PRAGMA table_info(turns)" | grep user_lang
```

- [ ] **Step 8: Commit**

```bash
git add src/voice-agent/pipeline/turn_telemetry.py src/voice-agent/tests/test_turn_telemetry_user_lang.py
git commit -m "feat(voice-agent): telemetry user_lang column on turns table

Additive column with default 'en'. _ensure_schema runs on every
voice-agent startup; idempotent ALTER picks up the column on existing
deployments without manual migration. Lets us spot French/English
code-switch patterns at the SQL level without trawling transcripts."
```

---

### Task 9: Manual smoke test + cleanup

Restart the voice-agent so all the wiring takes effect, then exercise both languages end-to-end.

- [ ] **Step 1: Confirm no active session before restart**

```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db "SELECT ts_utc, strftime('%s','now')-strftime('%s',ts_utc) AS sec_ago FROM turns ORDER BY id DESC LIMIT 1"
```

If `sec_ago` is under 60, ask the user before restarting. Otherwise proceed.

- [ ] **Step 2: Restart voice-agent**

```bash
systemctl --user restart jarvis-voice-agent.service
sleep 4
systemctl --user is-active jarvis-voice-agent.service
```

Expected: `active`.

- [ ] **Step 3: Verify the telemetry migration applied**

```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db "PRAGMA table_info(turns)" | grep user_lang
```

Expected: a row showing the user_lang column with default 'en'.

- [ ] **Step 4: Live English turn**

User says: "Hey Jarvis, what time is it?"

Expected:
- Reply in English, Troy voice (Orpheus).
- `sqlite3 ~/.local/share/jarvis/turn_telemetry.db "SELECT ts_utc, user_lang, route FROM turns ORDER BY id DESC LIMIT 1"` → `user_lang=en`.

- [ ] **Step 5: Live French turn**

User says: "Salut Jarvis, quelle heure est-il?"

Expected:
- Reply in French, fr-FR-HenriNeural voice (EdgeTTS).
- Most-recent turn → `user_lang=fr`.

- [ ] **Step 6: Live alternation**

Alternate utterances (en → fr → en). Each turn's voice should match the language detected on that turn's user input. No mid-utterance switching.

- [ ] **Step 7: Verify the kill-switch**

```bash
systemctl --user set-environment JARVIS_LANG_AUTODETECT=0
systemctl --user restart jarvis-voice-agent.service
sleep 4
```

Speak French — reply should be ENGLISH (STT is re-pinned, the transcript is in English regardless of the user's actual language). Then:

```bash
systemctl --user unset-environment JARVIS_LANG_AUTODETECT
systemctl --user restart jarvis-voice-agent.service
```

- [ ] **Step 8: Final commit (no-op if no changes; sanity)**

```bash
git status
```

If clean, no commit needed. If any tweaks fell out of the smoke (e.g., a wiring nit), commit them with a tight message.

---

## Self-Review

Coverage map vs. spec sections:

| Spec section | Task(s) |
|---|---|
| 1. providers/stt.py — unpin language | Task 4 |
| 2. pipeline/lang_context.py — new module | Task 1 |
| 3. pipeline/dispatching_tts.py — language axis | Task 2 + Task 3 |
| 4. prompts/soul.md:5 — language rule | Task 5 |
| 5. Wire LangContext into agent + STT result | Task 6 |
| 5. Wire LangContext into TTS dispatch sites | Task 7 |
| Telemetry — user_lang column | Task 8 |
| Kill-switch JARVIS_LANG_AUTODETECT=0 | Task 4 (helper) + Task 9 (verified) |
| Test plan — test_lang_context.py | Task 1 |
| Test plan — test_dispatching_tts_lang.py | Task 2, 3 |
| Test plan — test_stt_lang_passthrough.py | Task 4, 6 |
| Manual smoke test | Task 9 |

No spec section is uncovered. No `TBD` / `TODO` / "implement later" anywhere. Every code step shows actual code. File paths are absolute or repo-relative throughout. Function names (`LangContext`, `_stt_language`, `_update_lang_from_stt_event`, `_ensure_schema`) match across tasks.

Estimated total effort: 2-3 hours for someone with the codebase in hand.
