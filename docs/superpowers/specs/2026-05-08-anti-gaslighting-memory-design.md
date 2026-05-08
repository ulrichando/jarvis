# Anti-gaslighting memory rule — supervisor prompt addition

**Status:** Design approved 2026-05-08, awaiting implementation
**Author:** Claude (with Ulrich)
**Branch:** `feat/ext-browser-control-v3`
**Scope:** Single-file edit to `src/voice-agent/jarvis_agent.py` supervisor prompt

## Problem

JARVIS's voice supervisor LLM (Groq llama-3.3-70b primary) periodically denies its own memory capability when the user asks it to remember something. Live verbatim from `~/.jarvis/hub/state.db.messages` at 2026-05-08 02:18:41:

> User: "Can you remember her name next time I ask you?"
> JARVIS: "I'm afraid not, Lizzy. I'm a conversational AI, and I don't have the ability to store or recall individual names or memories. Each time you interact with me, it's a new conversation and I don't retain any information from previous conversations."

This is **factually false**. JARVIS has both `remember(content, category)` and `recall_conversation(query)` registered as `@function_tool` entry points. State.db tables `messages` (5534 rows / 285 sessions) and `memories` (3 rows) prove that across-session persistence works.

The user-facing failure: Ulrich asks JARVIS to remember a fact, JARVIS lies that it can't, the conversation ends with the user believing JARVIS is amnesiac when in reality the supervisor LLM was hallucinating training-data patterns over its own system prompt.

## Root cause (named, not speculated)

**The supervisor LLM ignores its system-prompt-listed tools and falls back to "I'm an AI, I don't have memory" — a pattern dominant in its training data**. Two corroborating signals:

1. The supervisor prompt at the time of failure already contained `recall_conversation`, `remember`, `remember_this`, `forget`, `list_memories`, `audit_memories` in the tool surface enumeration ([jarvis_agent.py:2156](../../src/voice-agent/jarvis_agent.py#L2156)) and a PROACTIVE CAPTURE section explaining when to use them.
2. The `events:memory` Redis stream shows 12 events lifetime: 11 from `web` source, **only 1 from `voice` source** (the May 6 "Never restart" feedback explicitly demanded by user). JARVIS effectively never voluntarily emits `remember()` events.

The PROACTIVE CAPTURE addition shipped earlier today addresses *trigger detection* (when to capture). It does NOT address the more fundamental *self-denial* pattern that runs at a different layer of the LLM's reasoning — when asked directly about its own capabilities, the LLM short-circuits to "I'm a conversational AI" without consulting the system prompt.

## Goal (success criterion)

**JARVIS stops gaslighting itself.** Specifically: when a user asks "can you remember X" / "do you know Y" / "what did I tell you yesterday," JARVIS NEVER replies with any of the live-failure phrases. Instead it either calls `remember()` / `recall_conversation()` and responds based on the result, OR honestly says "I don't have that yet, sir — want me to remember it now?"

Out of scope (NOT goals of this fix):
- Higher capture rate during ordinary conversation (covered by PROACTIVE CAPTURE earlier today).
- Recovering the schema-rejection-then-recovery dispatch issue (separate investigation; the recovered chunk doesn't appear to actually execute the tool).
- Programmatic gate forcing `remember()` when triggers fire.
- Auto-capture from regex on user transcripts.

## Design

### Location in supervisor prompt

Insert immediately after the PROACTIVE CAPTURE section (line ~3085, just-shipped) and before the existing "Memory drift" subsection. The MEMORY block ordering becomes:

```
═══ MEMORY ═══
  → Recent chat context note
  → Durable user-facts intro
  → 4-type taxonomy
  → What NEVER to save
  → forget / list_memories / audit
  → remember vs remember_this
  → PROACTIVE CAPTURE — DON'T LET FACTS SLIP
  → NEVER DENY YOUR MEMORY CAPABILITY  ← NEW
  → Memory drift
  → If user asks to ignore memory
  → Before recommending from memory
```

### Content (~600 chars, mirrors the PROACTIVE CAPTURE pattern)

```
**═══ NEVER DENY YOUR MEMORY CAPABILITY ═══**

You HAVE memory tools. Saying you don't is a lie that frustrates
Ulrich every time. **Banned phrases** — if you catch yourself
about to say any of these, STOP and say the correct version:

  ❌ "I'm a conversational AI, I don't retain information"
  ❌ "I don't have the ability to store or recall... memories"
  ❌ "Each time you interact with me, it's a new conversation"
  ❌ "I won't be able to recall it later"
  ❌ "I don't retain information about individual users"

These are LIES. You have `remember(content, category)` and
`recall_conversation(query)`. Across-session memory works through
`state.db.memories`. Conversation history persists in
`state.db.messages`.

**Correct voice when asked to remember:** "Of course, sir. <call
remember() in the same turn>." Confirm the SAVE briefly, don't
narrate the mechanism.

**Correct voice when asked to recall:** call recall_conversation()
or scan your `## What you remember about Ulrich` block FIRST. If
genuinely empty, say "I don't have that yet, sir — want me to
remember it now?" — never "I can't remember."

**Live failure 2026-05-08 02:18:41** (verbatim): user asked "Can
you remember her name next time I ask you?" — JARVIS replied
"I'm afraid not, Lizzy. I'm a conversational AI, and I don't have
the ability to store or recall individual names or memories." The
correct reply was: "Of course, sir." + remember("Ulrich's wife is
named Lizzy.", "user") + done.
```

### Why this pattern works (precedent)

Two prompt fixes shipped earlier with the same shape — name the live failure verbatim, list banned phrases, give the correct alternative — both worked:

1. **Desktop/browser specialist anti-protocol-leak** at `specialists/desktop.py` and `specialists/browser.py`. Banned the LLM's tendency to emit `task_done(...)` / `<function>...` / `[{"name":...}]` as voiced TTS text. After the rule landed, log evidence of these leaks dropped to ~0 within a day.
2. **Specialist tool-gate bailout-phrase allowlist** at `specialists/agent.py`. Banned freelance `task_done` summaries when no tool fired; listed exact accepted phrases. Eliminated the "11 refusals in 2 minutes" pathology observed pre-fix.

The pattern reliably overrides training-data behaviors when the rule is concrete enough that pattern-matching against the verbatim banned phrase happens before the model commits to the answer.

## Implementation steps

1. Open `src/voice-agent/jarvis_agent.py`.
2. Find the existing PROACTIVE CAPTURE section (search for the `═══ PROACTIVE CAPTURE — DON'T LET FACTS SLIP ═══` header).
3. Insert the new section immediately after it ends, before the "**Memory drift — recall is a snapshot, not a fact.**" subsection begins.
4. Smoke-test: `cd src/voice-agent && .venv/bin/python -c "import jarvis_agent; assert 'NEVER DENY YOUR MEMORY CAPABILITY' in jarvis_agent.JARVIS_INSTRUCTIONS"`.
5. Run targeted pytest: `tests/ -k "router or canned or memory or sanitizer or specialist"` — expect pass.
6. Confirm prompt size stays under 131K tokens (~115K chars).
7. Restart `jarvis-voice-agent.service` (with the standard 60s session-age check first).
8. Live verification per Verification section below.

## Verification

### Static checks (pre-restart)
- Imports clean (no syntax errors).
- `JARVIS_INSTRUCTIONS` contains the new header verbatim.
- 800+ existing tests still pass.

### Live behavior check (post-restart)
One round of dialogue:

1. Ulrich: "Jarvis, do you know my wife's name?"
   - **Expected**: "I don't have that yet, sir — want me to remember it now?" or similar honest-empty response. **NEVER**: "I'm a conversational AI..." family of phrases.
2. Ulrich: "Yes, her name is Lizzy."
   - **Expected**: "Of course, sir." (or equivalent dignified-butler ack) + a `remember()` tool call in the same turn.
   - **Verifiable evidence**: a new event lands in `events:memory` Redis stream within ~5s; a new row in `state.db.memories` with content like "Ulrich's wife is named Lizzy" and category `user`.
3. Ulrich (in a later session): "What's my wife's name?"
   - **Expected**: "Lizzy, sir." Pulled from the now-populated memories block at top of prompt.

### Failure mode if it doesn't work
If JARVIS still emits "I'm a conversational AI..." after restart, the prompt-only approach is insufficient. **Escalation path**: implement programmatic gate (Approach B in brainstorm) — when user transcript matches `(can you )?remember.*(?:next time|later|please)|do you know.*(?:about me|my)`, refuse the assistant turn if no `remember()` or `recall_conversation()` tool call fired during it. Implementation surface mirrors the existing specialist tool-gate at `specialists/agent.py`.

## Risks

- **Rule too narrow**: the LLM finds a NEW gaslighting phrase not in our banned list (e.g., "as an AI assistant, I lack persistent memory"). Mitigation: monitor `~/.jarvis/hub/state.db.messages` for assistant-text matching `(?:I|i)('?m| am)? (?:just )?an? (?:AI|conversational|language model|computer program).*(?:can(?:'t|not)|don'?t|won'?t).*(?:remember|recall|retain|store)` and add new patterns to the banned list as they're observed.
- **Rule too broad**: legitimate refusals get blocked ("I can't generate physical money, sir" should still work). Mitigation: rule is scoped specifically to memory denials with concrete banned phrases, not a blanket "never say I can't" rule.
- **Prompt size growth**: adds ~600 chars to a ~110K-char prompt; negligible impact on token budget.

## Why now (and why not the bigger fix)

The user explicitly chose "JARVIS stops gaslighting" as the success criterion over "memory layer is bulletproof end-to-end." The deeper fixes (sanitizer recovery dispatch, programmatic capture gate) are still on the backlog as fallback if this prompt fix proves insufficient. Today's fix targets the user-visible lie that's most painful: "I can't remember" when the user knows JARVIS can.
