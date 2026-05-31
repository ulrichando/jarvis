# Gemini Live English-Pin Mitigation

**Date:** 2026-05-31
**Status:** proposed (design) — awaiting review
**Scope:** stop JARVIS's Gemini Live direct-mode from answering in random languages (Spanish/French)
when Ulrich speaks. Two entrypoints — `bin/jarvis-gemini-direct` (pure screen-share dialog) and
`bin/jarvis-gemini-tools` (Gemini Live + tools) — get the same two changes. **No model change, no new
dependency.**

## Why this — root cause (researched + probed 2026-05-31)

The Gemini Live direct modes run a **native-audio** model (`gemini-3.1-flash-live-preview`). Per
Google's own Live docs, native-audio models *"automatically choose the appropriate language and don't
support explicitly setting the language code"* — they **infer the output language from the user's
accent, tone, and names** and can switch unprompted. This is a documented, open limitation/bug
(google-gemini/cookbook #1197), not a missing flag we forgot. Ulrich's accent triggers it.

**Two gaps found in the code:**
1. **`language_code` is absent** from the `SpeechConfig` in BOTH files (`jarvis-gemini-direct:165`,
   `jarvis-gemini-tools:433`) — only `voice_config` is set. The documented language pin is simply not
   passed.
2. **The system-instruction language rule is weak or missing.** `jarvis-gemini-direct`'s
   `SYSTEM_INSTRUCTION` (lines 68-74) has **no language rule at all**. `jarvis-gemini-tools`'s
   `OPS_BLOCK` (lines 191-194) says "ALWAYS RESPOND IN ENGLISH … Never reply in any language other
   than English" — but lacks the specific anti-inference clause Google's workaround calls for.

**Empirically established (probes against the live API with Ulrich's key, 2026-05-31):**
- The half-cascade Live models where `language_code` is *authoritative* (`gemini-live-2.5-flash`,
  `gemini-2.0-flash-live-001`) are **NOT available** on this Gemini Developer API key — `models.list()`
  returns only native-audio variants (`gemini-2.5-flash-native-audio-*`) + `gemini-3.1-flash-live-preview`.
  A *provable* hard-lock would require a half-cascade model (Vertex AI tier) — out of scope here.
- `gemini-3.1-flash-live-preview` **accepts** `language_code="en-US"` on connect (no `1007` rejection).
- The drift could **not** be reproduced from *text* input (Spanish text → English reply, 6/6 trials,
  with and without `language_code`). ⇒ the trigger is **accented audio**, not the words — so this fix
  can only be validated by Ulrich speaking, not by an automated text harness.

## Goal

Make Gemini direct-mode reply in English as reliably as the available (native-audio-only) models allow:
set the documented language pin + the strongest documented anti-drift instruction. Honest non-goal:
this is a **strong mitigation, not a guaranteed lock** — native-audio can still occasionally drift;
the true guarantee (Vertex half-cascade) is a deferred follow-up.

## Design

### Change 1 — pin `language_code` on `SpeechConfig` (both files)
Add an env-configurable language constant and pass it to `SpeechConfig`:
```python
GEMINI_LANGUAGE = os.environ.get("JARVIS_GEMINI_LANGUAGE", "en-US")
...
speech_config=types.SpeechConfig(
    language_code=GEMINI_LANGUAGE,                 # NEW — documented audio-language pin
    voice_config=types.VoiceConfig(
        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE_NAME),
    ),
)
```
- `bin/jarvis-gemini-direct`: add `GEMINI_LANGUAGE` near the other env constants (~line 60); add
  `language_code=GEMINI_LANGUAGE` to the `SpeechConfig` at ~line 165.
- `bin/jarvis-gemini-tools`: same — add the constant near its model/voice env reads (~line 144); add
  `language_code=GEMINI_LANGUAGE` to the `SpeechConfig` at ~line 433.
- Default `en-US` (verified accepted). Env override lets a future non-English use or a model-supported
  code be set without code change. (An unsupported code would surface as a connect error — `en-US` is
  the safe default; document that overrides must be model-supported.)

### Change 2 — strengthen the anti-drift system instruction (both files)
Use Google's documented anti-inference wording.
- `bin/jarvis-gemini-direct` `SYSTEM_INSTRUCTION` (lines 68-74): currently has **no** language rule —
  prepend one sentence:
  > "Respond ONLY in English (en-US). NEVER infer or switch language from Ulrich's accent, tone,
  > names, or a mishearing — even if the audio sounds like another language, always reply in English."
- `bin/jarvis-gemini-tools` `OPS_BLOCK` (lines 191-194): replace the existing English lines with the
  same strengthened clause (keep it a single tight paragraph; the no-inference phrasing is the part
  that matters per cookbook #1197).

### Change 3 — no model change
Stays `gemini-3.1-flash-live-preview` (still env-overridable via the existing
`JARVIS_GEMINI_DIRECT_MODEL` / `JARVIS_GEMINI_TOOLS_MODEL`). It's the only viable Live model on the key
and held English in the text probes; switching to the 2.5 native-audio variants buys nothing (same
class).

## Deployment
Gemini direct-mode is **not** the always-on voice-agent service — it's spawned fresh by `jarvis-mode`
each time the user switches to Gemini mode. So the fix takes effect on the **next "switch to Gemini"**;
no `jarvis-voice-agent` restart, no in-flight-session risk.

## Testing / verification
- **Offline (automatable):** `py_compile` both bins; a connect-probe (the one already used in design)
  confirms `gemini-3.1-flash-live-preview` accepts the `SpeechConfig` with `language_code="en-US"`
  without raising (guards against a Deepgram-style "valid-looking config crashes the session" repeat).
- **Live (Ulrich, required):** switch to Gemini mode, speak normally (accented), confirm replies stay
  English across a few turns; try `bin/jarvis-gemini-direct` and the tools mode. This is the only real
  test — the trigger is accented audio, not reproducible in an automated harness.

## Out of scope / honest limits
- Not a *provable* hard-lock — native-audio may still occasionally drift (Google limitation). If it
  does after this, the only remaining lever is a **half-cascade Live model** (`language_code`
  authoritative), which needs **Vertex AI** access — a separate spec/feasibility study, deferred.
- No change to the base (Claude) supervisor, the Groq/Whisper STT chain, or `src/cli/`.
- No model swap, no new dependency, no schema change.

## Risks
- **Mitigation may be insufficient on heavy accent** — accepted; validated live, and the Vertex path is
  the documented escalation if needed.
- **Env override with an unsupported `language_code`** would error the Live connect — mitigated by the
  `en-US` default + a doc note; the offline connect-probe catches a bad default before ship.
