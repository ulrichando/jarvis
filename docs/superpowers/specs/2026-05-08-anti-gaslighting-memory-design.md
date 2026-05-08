# Memory layer reliability — turn-boundary auto-extraction + recall force-routing

**Status:** Design v2 (pending re-approval) 2026-05-08, supersedes v1 banned-phrases approach
**Author:** Claude (with Ulrich)
**Branch:** `feat/ext-browser-control-v3`
**Scope:** Architectural shift in memory pipeline + targeted prompt addition + defense-in-depth detector

## Why a v2

v1 of this design (banned-phrases prompt addition) was rejected by Ulrich as unnatural, and three parallel research passes (verification + voice-framework specifics + community sources Quora/GitHub/StackOverflow/Reddit) converged on a single conclusion that **invalidates the prompt-only direction**:

> *"Mem0's own maintainers recommend bypassing function-tool registration."* — [github.com/mem0ai/mem0/issues/3999](https://github.com/mem0ai/mem0/issues/3999): the `function_tool` integration "requires manual tool invocation by the agent" and "agent must explicitly decide when to search/save memory." The maintainers recommend `MemoryService` / `PreloadMemoryTool` which **auto-injects relevant memories at the start of each turn without requiring any tool call**.

Every production memory system that works (Mem0, Zep, Character.AI, Pi) has moved memory **off the LLM's tool surface**. Asking Llama-3.3-70B to choose to call `remember()` is the failure mode, not the fix.

## Problem (unchanged from v1)

JARVIS's voice supervisor (Groq llama-3.3-70b primary) periodically denies its own memory capability when asked to remember something. Live verbatim from `~/.jarvis/hub/state.db.messages` at 2026-05-08 02:18:41:

> User: "Can you remember her name next time I ask you?"
> JARVIS: "I'm afraid not, Lizzy. I'm a conversational AI, and I don't have the ability to store or recall individual names or memories."

Hard data: across 285 sessions and 5534 logged messages, only **1 memory** has ever been saved through the voice path (May 6, the explicit "never restart" feedback Ulrich demanded). State.db has 3 memories total. Voice memory has effectively never worked.

## Research-driven root cause

The supervisor LLM ignores its registered `@function_tool` memory tools and falls back to "I'm a conversational AI" because:
1. **Metacognition limits** — published 2025 research ([arXiv 2509.21545](https://arxiv.org/abs/2509.21545)) shows Llama-class models exhibit "conservatism in context-/time-sensitive queries" — they default to the safer "I can't do that" disclosure even when they factually can.
2. **Training-data dominance** — millions of "I'm an AI assistant without memory" disclaimers in pretraining outweigh the system prompt's tool listing at inference time.
3. **Voice agents amplify the cost** — a 12-word capability denial spoken at 175 wpm is ~4 seconds of dead air, much more disruptive than the same string in a chat reply.

**Architectural insight from the research:** the LLM's reluctance to call memory tools is structural, not promptable. The fix has to bypass the LLM's choice rather than coerce it.

## Goal (success criterion — broadened from v1)

JARVIS reliably persists stable facts the user states, and never tells the user it can't remember when it factually can.

Concretely:
1. When user states a memorable fact ("we charge $600/6mo", "my wife's name is Lizzy"), it lands in `state.db.memories` within ~1s, every time. No dependence on the LLM choosing to call a tool.
2. When user asks a recall-shaped question ("what did I tell you about X?"), JARVIS retrieves from `state.db.memories` or `messages` and answers based on the result. Never replies with the gaslighting phrases.
3. When the LLM does emit a gaslighting phrase despite layers 1-2, a defense-in-depth detector intercepts and re-rolls the turn.

Out of scope (NOT goals of this fix):
- Multi-modal memory (face recognition memories, image attachments).
- Cross-user memory sharing.
- Memory editing/forgetting via voice (already handled by `forget()` tool).

## Design — three layers, defense-in-depth

### Layer 1 (PRIMARY): Auto-extraction on turn boundary

**The single highest-leverage change.** Mirrors the Mem0/Zep/Character.AI production pattern.

**Where:** Hook into the existing telemetry write path in [src/voice-agent/pipeline/turn_telemetry.py](../../src/voice-agent/pipeline/turn_telemetry.py) (which already writes every turn to SQLite). Add an extraction step that runs after the user turn completes but before / in parallel with the supervisor LLM call.

**What it does:** A small, fast LLM (Groq llama-3.1-8b-instant — cheap, low-latency) sees the user transcript and answers a single classification question: "Does this transcript contain a stable fact about the user, their work, their preferences, or operational context that should be remembered? If yes, output the fact in the format `<category>: <one-sentence summary>`. If no, output `SKIP`."

If the answer isn't `SKIP`, the parsed `<category, content>` pair is written directly to `state.db.memories` via the same `_publish_event` path as the existing `remember()` tool — bypassing the supervisor LLM entirely.

**Trigger phrases the classifier learns from few-shot examples:**
- "we charge X" / "I have N students" / "we teach Python, JS, Lua" → `project`
- "my wife's name is Lizzy" / "I run Pretva" → `user`
- "every time we try X, Y happens" → `feedback`

**Why a separate small LLM rather than regex:** the live failure conversation showed memorable facts mixed with conversational drift ("the ones in a dish rack", ambient TV) — a regex over-fires; a small LLM judges context. The 8B at Groq inference rates is ~50–80ms per call; runs in parallel with the main supervisor.

**Failure mode:** if the small LLM extractor itself fails (rate limit, schema reject), the user-visible behavior degrades to current state — the supervisor *might* call `remember()` itself, or not. No regression beyond status quo.

### Layer 2 (SECONDARY): Recall force-routing for explicit recall queries

When the user asks a recall-shaped question, route deterministically to `recall_conversation()` via `tool_choice` forcing — **don't let the supervisor's metacognition-conservatism reject the call**.

**Where:** Extend [src/voice-agent/pipeline/turn_router.py](../../src/voice-agent/pipeline/turn_router.py) which already classifies BANTER/TASK/REASONING/EMOTIONAL. Add a parallel `RECALL` category with a regex pre-filter:

```python
_RECALL_PATTERNS = [
    r"\b(?:do you|can you|did i)\s+(?:remember|recall)",
    r"\bwhat (?:did|do) (?:i|we) (?:tell|say|talk).*you",
    r"\bwhat'?s? my\s+\w+(?:'s)?\s+name",
    r"\bremember when (?:i|we)",
]
```

When matched, the router sets `tool_choice={"type":"function","function":{"name":"recall_conversation"}}` for that single turn. **Critical caveat from [LiveKit issue #4671](https://github.com/livekit/agents/issues/4671): `tool_choice` persists across turns when set on `generate_reply()` in LiveKit Agents — must be explicitly reset to `"auto"` after the forced call.**

**Why not always-force:** would break banter and follow-up turns. The classifier-then-force pattern is what Groq's own docs recommend ([Groq tool-use blog](https://groq.com/blog/introducing-llama-3-groq-tool-use-models)).

### Layer 3 (DEFENSE-IN-DEPTH): Output-rail denial detector

**JARVIS-original — not in any published framework, but academically grounded.**

**Where:** New module `src/voice-agent/sanitizers/denial_detector.py`. Hooks into the same LLMStream patch surface used by other sanitizers (`pycall`, `dsml`, `tool_name`, `handoff_text`).

**What it does:** Watches the supervisor's outgoing assistant text. If it matches:
```python
_DENIAL_RE = re.compile(
    r"\b(?:I'?m|I am)\s+(?:just\s+)?an?\s+(?:AI|conversational|language model|computer program)"
    r".*?\b(?:can(?:'t|not)|don'?t|won'?t)\b.*?\b(?:remember|recall|retain|store|memory|memorize)",
    re.IGNORECASE | re.DOTALL,
)
```
…AND no `remember()` / `recall_conversation()` tool call fired this turn, the detector:
1. Suppresses the assistant text (don't voice it).
2. Logs `[denial-detector] suppressed gaslighting reply: <text[:120]>`.
3. Re-rolls the turn with `tool_choice` forced to `recall_conversation`.

**Academic precedent:** [Google ADK reflect-and-retry plugin](https://google.github.io/adk-docs/plugins/reflect-and-retry/) and the [Reflect, Retry, Reward paper (HuggingFace 2505.24726)](https://huggingface.co/papers/2505.24726) — both apply the pattern to tool *errors*; we'd be the first to apply it to capability *denials*.

**Failure mode:** the detector regex might miss novel denial phrasings ("As a language model, I lack persistent memory"). Mitigation: log every supervisor text output to a separate sample for ongoing pattern tuning.

### Supporting layer: targeted prompt addition (NOT the primary fix)

The original v1 banned-phrases section is dropped. Replaced with a much shorter, naturally-phrased addition that mirrors what Anthropic auto-injects with their memory tool:

```
**═══ YOU HAVE MEMORY ═══**

You have `remember(content, category)` and `recall_conversation(query)` 
as tools. State.db persists across sessions. ASSUME INTERRUPTION: chat
context resets every session — anything not in `remember()` is gone.
Saying you can't remember is factually wrong; treat the tools as real.

When the user states a stable fact (pricing, team, family, values), 
either trust the auto-extractor (Layer 1) to capture it, or call 
`remember()` yourself. Either way, never tell the user "I can't 
remember" — you can.
```

**Why keep this:** even with Layers 1-3, the LLM still produces voice replies. Without this minimal anchor, the supervisor will say things like "I'll remember that for our conversation" when the auto-extractor has *actually* persisted it. This block keeps voice replies honest about the persisted state.

## Architecture diagram

```
User speaks → STT → user_input_transcribed
                          │
            ┌─────────────┼─────────────────────┐
            ▼             ▼                     ▼
   Layer 1 extractor  Layer 2 router    Supervisor LLM
   (small LLM,        (regex + tool_     (with prompt anchor
    fact extraction)   choice forcing)    + Layer 3 denial
            │             │               detector on output)
            ▼             ▼                     │
   state.db.memories  recall_conversation       │
                       forced if matched         │
            └─────────────┼─────────────────────┘
                          ▼
                       TTS → user
```

## Implementation phases

### Phase 1 — minimal anchor (today, ~30 min)
Just the supporting-layer prompt addition. Short, honest, not banned-phrases. No code changes.

### Phase 2 — Layer 1 auto-extraction (this week, ~2 hours)
- New `pipeline/memory_extractor.py` — async function called from `on_user_turn_completed`.
- Few-shot prompt for llama-3.1-8b-instant with 5–8 examples.
- Writes via existing `_publish_event("memory.value.upserted", ...)` path; no new infra.
- Telemetry: log extraction outcomes to compare auto-extracted vs LLM-extracted memory rates.

### Phase 3 — Layer 2 recall force-routing (this week, ~1 hour)
- Extend `turn_router.py` with `_RECALL_PATTERNS` + `tool_choice` forcing.
- Reset `tool_choice` to `"auto"` after the forced call (LiveKit #4671 mitigation).
- Tests for the regex patterns.

### Phase 4 — Layer 3 denial detector (this week, ~2 hours)
- New `sanitizers/denial_detector.py` — installs as `LLMStream._run` patch like other sanitizers.
- Suppresses gaslighting output, re-rolls with forced tool_choice.
- Telemetry: count denial-detector triggers per session — should trend to ~0 as Layer 1 effectiveness grows.

## Verification

### Phase 1 (prompt anchor)
- `JARVIS_INSTRUCTIONS` contains "YOU HAVE MEMORY" verbatim.
- 800+ existing tests pass.
- Live: ask "do you have memory?" — JARVIS should NOT reply "I'm a conversational AI..."

### Phase 2 (extractor)
- Smoke: state "we charge $600 for 6 months" → row appears in `state.db.memories` within 2s with category `project`.
- Telemetry: new column `memory_auto_extracted` (bool) in `turns` table tracks per-turn extractor outcome.
- Target: ≥80% of conversations with stable facts get at least one auto-extracted memory.

### Phase 3 (recall force-routing)
- Test: "what's my wife's name?" → matches `_RECALL_PATTERNS` → `recall_conversation` fires → answer based on retrieval.
- Test: "okay" / "yes please" / "thanks" → does NOT match patterns, normal route.
- Confirm `tool_choice` reset after each forced call (no persistence across turns).

### Phase 4 (denial detector)
- Unit test: feed denial-shaped string into detector → suppression triggers.
- Unit test: feed legitimate refusal ("I can't generate physical money") → does NOT trigger.
- Live: counts of `[denial-detector] suppressed` should drop to near-zero after Phases 1-3 land.

## Risks

- **Layer 1 false positives** — extractor saves trivia like "I'm thirsty." Mitigation: include negative few-shot examples (ephemeral statements should `SKIP`); cap memories at 500 chars; ban-list ephemeral words ("today", "right now", "currently").
- **Layer 1 latency** — extra 50-80ms per turn calling 8B. Mitigation: run in parallel with supervisor LLM, not sequentially.
- **Layer 2 false negatives** — recall-shaped queries the regex misses. Mitigation: log unmatched user transcripts containing "remember"/"recall"/"told you" for monthly pattern review.
- **Layer 2 cross-turn persistence bug** — LiveKit #4671. Mitigation: explicit `tool_choice="auto"` reset after each forced call; integration test for reset.
- **Layer 3 over-trigger** — detector fires on legitimate refusals. Mitigation: regex requires both the AI-self-reference AND the memory-specific verb; legitimate refusals like "I can't open a tab" don't match.
- **Prompt anchor over-grows the system prompt** — adds ~400 chars to ~110K. Negligible budget impact.

## What we're explicitly NOT doing

- NOT swapping models. Research showed `Llama-3-Groq-70B-Tool-Use` would help marginally but doesn't fix the proactive-memory problem; Groq itself recommends router → tool-use model only on tool-needed turns.
- NOT using `tool_choice="required"` blanket. Breaks banter (LiveKit issue confirms cross-turn persistence makes this dangerous).
- NOT using Reflexion-style multi-step self-correction. Recent practitioner reports show it amplifies confident wrongness on tool errors.
- NOT moving to MemGPT/Letta architecture. Their tool-based memory works because they have a stateful ReAct loop between user turns; JARVIS doesn't and won't.
- NOT fine-tuning. Breaks Groq hosting, expensive, and the auto-extraction pattern works without it.

## Why now (and why this scope)

Original v1 success criterion ("JARVIS stops gaslighting") was scoped tight under the assumption that prompt-only could fix it. Research definitively showed prompt-only is the path everyone abandoned. The architectural shift (Phase 2 + 3) is ~3 hours of work that follows the proven production pattern; the marginal added scope is justified by the strength of the evidence.

Phase 1 (prompt anchor) ships first as a hedge: zero-risk, immediate, gives some improvement even if Phases 2-4 land later. Phases 2-4 then compound — each layer covers a different failure mode and they don't conflict.

## Sources cited in this design

- [Mem0 issue #3999 — recommend MemoryService over function tools](https://github.com/mem0ai/mem0/issues/3999) ← strongest signal
- [Mem0 + LiveKit integration docs](https://docs.mem0.ai/integrations/livekit)
- [Zep + LiveKit blog](https://blog.getzep.com/zep-livekit/)
- [Anthropic Memory Tool docs (ASSUME INTERRUPTION)](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)
- [LiveKit Agents #4671 — tool_choice persists across turns](https://github.com/livekit/agents/issues/4671)
- [Groq tool-use models blog](https://groq.com/blog/introducing-llama-3-groq-tool-use-models)
- [Evidence for Limited Metacognition in LLMs (arXiv 2509.21545)](https://arxiv.org/abs/2509.21545)
- [Google ADK reflect-and-retry plugin](https://google.github.io/adk-docs/plugins/reflect-and-retry/)
- [Reflect, Retry, Reward paper](https://huggingface.co/papers/2505.24726)
- [HN — GPT-5 system prompt authenticity disputed](https://news.ycombinator.com/item?id=44832990)
- [Lost in the Middle (Liu et al., arXiv 2307.03172)](https://arxiv.org/abs/2307.03172)
- [LangChain few-shot tool-calling study](https://www.langchain.com/blog/few-shot-prompting-to-improve-tool-calling-performance)

## Sources we initially cited but should NOT rely on as primary

- "GPT-5 leaked system prompt with bio tool trigger" — [HN dispute](https://news.ycombinator.com/item?id=44832990); demote to inspirational, not authoritative.
- "Instruction Hierarchy paper supports position-matters" — wrong citation; that paper is about role hierarchy.
- "30-40% Llama 3.3 70B proactive tool ceiling" — no primary source; folk wisdom.
