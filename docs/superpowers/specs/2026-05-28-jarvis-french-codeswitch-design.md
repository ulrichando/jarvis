# JARVIS French/English Code-Switch — Design

**Date:** 2026-05-28
**Status:** Approved by user, ready for implementation plan
**Scope:** Restore JARVIS's ability to speak French. Auto-detect per turn; no menu clicks.

## Background

JARVIS used to respond in French when Ulrich spoke French, and in English when Ulrich spoke English. Two changes silently locked the system to English-only:

1. **`prompts/soul.md:5`** declares `"English only."` as a hard persona rule. The LLM honors it across providers.
2. **`providers/stt.py`** pins both STT chains (Groq Whisper Large v3 Turbo and Deepgram Nova-3) to `language="en"`. Even if the LLM wanted to mirror French, the upstream transcript would already be mis-transcribed as English.

The TTS layer is the third dependency: `canopylabs/orpheus-v1-english` (the current Groq Orpheus voice for both Troy and Austin) is an English-only model. EdgeTTS (Microsoft Edge-TTS, already used as the Orpheus fallback per `providers/edge_tts.py`) supports French voices natively (`fr-FR-HenriNeural`, `fr-FR-DeniseNeural`, etc.) with no new dependency.

## Goals

- User speaks French → JARVIS replies in French, voiced through a French TTS.
- User speaks English → unchanged behavior (Orpheus / Troy).
- Switching is automatic per turn. No UI clicks, no environment variable, no command word.
- Kill-switch available to revert to English-only without redeploy.

## Non-goals

- Languages other than French and English. (YAGNI; add later if needed.)
- Tray "Language" submenu. (User chose auto-detect over explicit toggle.)
- Mid-utterance language switching. The language code is captured at user-turn start and rides through the LLM + TTS as one unit.

## Architecture

```
User speech ──► STT (Whisper or Deepgram, lang=None)
                       │
                       ▼
            transcript + detected_lang_code
                       │
                       ▼
            LangContext.set(detected_lang_code)   ◄─── stored on session
                       │
                       ▼
            LLM (soul.md says "match user's language")
                       │
                       ▼
                  reply text
                       │
                       ▼
            DispatchingTTS.pick(route, lang=LangContext.get())
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
   lang == "en":               lang == "fr":
   Orpheus + Troy              EdgeTTS + fr-FR-HenriNeural
```

**Single source of truth:** the STT-detected language for the user's most recent turn drives both LLM language (via soul.md) and TTS voice (via dispatcher). We do not run a separate classifier on the LLM reply — the LLM honors soul.md, and the TTS picks voice from the same STT-derived signal regardless.

## Component Changes

### 1. `providers/stt.py` — unpin language

| Line | Current | New |
|------|---------|-----|
| 96 (Whisper)  | `language="en"`  | `language=None`  |
| 169 (Deepgram) | `language="en"`  | `language=None`  |

Both Whisper Large v3 Turbo and Deepgram Nova-3 do high-quality language ID natively when `language=None`. Confidence floor (see Edge Cases) is enforced at the consumer, not in the STT call.

### 2. `pipeline/lang_context.py` — new module

New tiny module owning per-session language state.

```python
class LangContext:
    """Session-scoped most-recent-detected user language.

    Default "en". Updated by the STT result hook. Read by the TTS
    dispatcher at pick() time. Thread-safe (single asyncio loop;
    plain attribute access).
    """
    def __init__(self, default: str = "en") -> None:
        self._lang = default
        self._confidence = 1.0

    def set(self, lang: str, confidence: float = 1.0) -> None:
        # Confidence floor — see Edge Cases. Below 0.6, keep previous.
        if confidence < 0.6:
            return
        self._lang = lang
        self._confidence = confidence

    def get(self) -> str:
        return self._lang
```

Held on the agent session, not a global. One per LiveKit room so concurrent sessions don't cross-contaminate.

### 3. `pipeline/dispatching_tts.py` — language axis

Current shape:
```python
class DispatchingTTS:
    def pick(self, route: str) -> Any: ...
```

New shape:
```python
class DispatchingTTS:
    def pick(self, route: str, lang: str = "en") -> Any:
        if lang == "fr":
            return self._fr_tts          # EdgeTTS fr-FR-HenriNeural
        return self._en_pick(route)      # existing Orpheus chain unchanged
```

`self._fr_tts` is constructed at build time from `providers/edge_tts.EdgeTTS(voice="fr-FR-HenriNeural")`. Single instance, reused.

### 4. `prompts/soul.md:5` — language rule

Current line:
> `literally — every word matters. English only.`

New line:
> `literally — every word matters. Reply in the user's language. If they speak French, reply in French; if English, English. Same register either way — peer engineer, not butler.`

The replacement keeps "every word matters" and adds the language mirroring rule inline. No other prompt sections need updating — the language rule is one sentence.

### 5. `pipeline/turn_router.py` — wire the lang axis

At the existing TTS dispatch call site, pass `LangContext.get()` as the `lang` argument:

```python
# Before:
tts = dispatcher.pick(route)
# After:
tts = dispatcher.pick(route, lang=session.lang_ctx.get())
```

The STT result handler in `jarvis_agent.py` already fires on every user transcript — add a one-line `session.lang_ctx.set(event.language, event.confidence)` call there. The `language` and `confidence` fields are standard on LiveKit's STT events.

## Edge Cases

- **Confidence floor (0.6).** Below this, `LangContext.set` is a no-op — short utterances ("hi" / "merci") that bounce detection won't flip the voice. Threshold validated empirically in test_lang_context.py.
- **Mixed-language utterance.** STT picks the dominant language. The voice for the reply follows. No mid-reply switching.
- **Unknown language code.** Anything other than `"en"` or `"fr"` falls back to English voice. Reply still respects soul.md (which doesn't restrict to a fixed list).
- **Session start with no STT events yet.** `LangContext` defaults to `"en"`. Tray icon / direct-mode bins inherit the default until the first transcript lands.
- **Direct-mode bins (Gemini / OpenAI).** Out of scope for v1 — Gemini Live and OpenAI Realtime own their own STT and TTS; their language handling is provider-internal. If we want code-switch parity there later, that's a follow-up spec.

## Telemetry

Store `user_lang` per turn in `~/.local/share/jarvis/turn_telemetry.db` (new column on the `turns` table, default `"en"` for backward compat). Lets us see code-switch frequency without trawling transcripts.

## Test plan

Full pytest suite (currently 2703 passed + 1 skipped) must remain green. New tests:

1. `tests/test_lang_context.py`
   - Default is `"en"`.
   - `set("fr", 0.9)` → `get() == "fr"`.
   - `set("fr", 0.5)` (below floor) → `get()` unchanged from prior value.
   - Multiple updates stick.

2. `tests/test_dispatching_tts_lang.py`
   - `pick(route="TASK", lang="en")` returns the Orpheus chain.
   - `pick(route="TASK", lang="fr")` returns the EdgeTTS adapter with `voice="fr-FR-HenriNeural"`.
   - `pick(route="TASK", lang="de")` returns the English fallback (no German voice configured).
   - All four routes (BANTER/TASK/REASONING/EMOTIONAL) honor the lang axis.

3. `tests/test_stt_lang_passthrough.py`
   - When STT event arrives with `language="fr"`, the session's `LangContext.get()` becomes `"fr"`.
   - When confidence is below the floor, `LangContext` does NOT update.

4. Manual smoke test (after merge):
   - Speak English: "Hey Jarvis, what time is it?" → English reply, Troy voice.
   - Speak French: "Salut Jarvis, quelle heure est-il?" → French reply, fr-FR-HenriNeural voice.
   - Alternate utterances: confirm voice flips per turn.

## Rollback

Single env kill-switch: `JARVIS_LANG_AUTODETECT=0`. When set:
- STT reverts to `language="en"` (gated read in `providers/stt.py`).
- DispatchingTTS ignores the `lang` argument and always picks the English chain.
- soul.md edit stays in place — the LLM is told to match the user's language regardless, but with English transcripts forced upstream, replies will be English by construction.

Allows a panic-revert without redeploying or reverting commits.

## File touch list

- `src/voice-agent/providers/stt.py` — modified (2 lines)
- `src/voice-agent/pipeline/lang_context.py` — new
- `src/voice-agent/pipeline/dispatching_tts.py` — modified (~10 lines, additive)
- `src/voice-agent/pipeline/turn_router.py` — modified (1 line at TTS dispatch site)
- `src/voice-agent/jarvis_agent.py` — modified (1 line at STT result handler)
- `src/voice-agent/prompts/soul.md` — modified (1 line)
- `src/voice-agent/tests/test_lang_context.py` — new
- `src/voice-agent/tests/test_dispatching_tts_lang.py` — new
- `src/voice-agent/tests/test_stt_lang_passthrough.py` — new
- `~/.local/share/jarvis/turn_telemetry.db` schema migration (additive column, default `"en"`)

Estimated effort: ~2-3 hours including tests + manual smoke.
