# ADR-001: Kimi K2.6 disabled as voice supervisor LLM (gated behind experimental flag)

- **Status:** accepted
- **Date:** 2026-05-05
- **Deciders:** `[ORCH]`, `[ARCH]`
- **Consulted:** `[ML]` (passive — model-tool-call interaction analysis)
- **Informed:** `[QA]`, `[INFRA]`

## Context

On 2026-05-04 the Kimi K2.6 family (`kimi-k2.6-instant/thinking/agent/swarm`) was registered as voice-supervisor candidates in `src/voice-agent/jarvis_agent.py` SPEECH_MODELS and `src/voice-agent/jarvis_voice_client.py` SPEECH_MODELS_AVAILABLE (commit 3b0178c). The four entries all point to the same upstream `kimi-k2.6` model on Moonshot's OpenAI-compatible endpoint; the Instant/Thinking/Agent/Swarm split is a client-side temperature preset, not separate APIs. The commit message asserted Kimi worked through the existing `deepseek_roundtrip.install()` patch.

On 2026-05-05 the user reported "voice intelligence broken / can't make normal conversation." Live-log inspection (`/tmp/jarvis-voice-agent.log`, sessions ~13:17–14:01 UTC) showed every Kimi-routed supervisor turn failing with:

```
openai.APIError: tool call validation failed: attempted to call tool
'web_search(query="...")' which was not in request.tools
```

Root cause: K2.6 has an opinionated set of *built-in* tools (notably `web_search`) that the model spontaneously invokes even when those tools are not registered in the request's `tools` array. Moonshot's API validates strictly and rejects the request before any chunk streams. The error wraps as `livekit.agents._exceptions.APIConnectionError("Connection error.")`, so the existing breaker validation-error filter (which checked `str(e)`) never matched, the breaker tripped at `fail_threshold=2`, and every subsequent turn for the cooldown returned `CircuitOpenError`. From the user's seat: silence on every turn.

## Decision

The four Kimi K2.6 voice entries are removed from the default tray-pickable set. They remain available behind the env var `JARVIS_KIMI_VOICE_EXPERIMENTAL=1` so a future, properly-integrated attempt has a flag to flip rather than re-introducing entries from scratch.

The active voice model on Ulrich's machine was reverted from `kimi-k2.6-instant` to `llama-3.3-70b-versatile` in `~/.jarvis/voice-model` (backup at `~/.jarvis/voice-model.bak.kimi-broken-2026-05-05`).

## Consequences

### Positive
- Voice intelligence restored on first restart after the change. Verified post-fix: `speech LLM: llama-3.3-70b-versatile (Groq · llama 3.3 70B)` and zero validation-error breaker trips in the post-restart window.
- The tray UI cannot accidentally route voice through a known-broken provider.
- A future Kimi integration has a single gate (`JARVIS_KIMI_VOICE_EXPERIMENTAL=1`) to flip when the underlying issue is solved.

### Negative
- Kimi K2.6 is unavailable as a voice supervisor on the desktop tray until the proper integration lands.
- The web-side Kimi K2.6 modes (`src/web` Instant/Thinking/Agent/Swarm handlers, commits `3178e0f`–`5c54865`) work fine because they expose the right tool surface; the voice/web parity gap is now explicit.

### Neutral / follow-up needed
- A proper re-integration requires either (a) registering shim/no-op tools matching K2.6's built-ins (`web_search`, possibly others — needs an enumerated list from Moonshot docs) so the model's spontaneous calls validate harmlessly, or (b) intercepting and stripping built-in tool_calls from the LLM stream server-side before they reach Moonshot. Option (a) is cheaper and more honest about the model's behavior.
- This ADR pairs with a parallel breaker fix (cause-chain walking) — see `03-STATE.md` Issue Register entry F-arch-002 — which would have *unstuck* the breaker even with Kimi misbehaving, but would not have fixed Kimi itself. Both fixes are needed.

## Alternatives considered

- **Option A: Remove Kimi voice entries entirely.** Rejected because future re-integration is plausible (web-side already works) and removing-then-re-adding loses the documentation trail of "we tried this; here's why it broke."
- **Option B: Keep entries enabled, document caveats in tray UI.** Rejected because the failure mode is silent at the user's seat (no audio output) and recovers only by manually changing `voice-model`. Discoverability of the cause is too low.
- **Option C: Register `web_search` as a no-op shim on the voice supervisor tool list.** Deferred — would need enumeration of all K2.6 built-in tools and a guarantee that Moonshot doesn't add new ones in K2.7. Reasonable v2 direction but premature now.

## Override / disagreement record

None.
