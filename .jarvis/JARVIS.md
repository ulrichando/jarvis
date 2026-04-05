# JARVIS Project Instructions

## What JARVIS Needs to Match Claude — Roadmap

### 1. Stability (Priority: CRITICAL)
- Server should NEVER crash from TTS, voice, or provider errors
- Wrap ALL async operations in try/except with graceful fallbacks
- WebSocket reconnection should be instant and silent
- Desktop overlay should survive server restarts without losing state
- Process management: PID files, clean shutdown, no zombie processes
- Memory leaks: WebSocket message arrays, audio buffers, WebKit cache

### 2. Tool Calling Reliability (Priority: HIGH)
- Every provider (Claude, Groq, Ollama) must reliably call tools
- Text-in-response tool parsing fallback for models that don't use native tool calling
- Tool call validation: verify args before execution
- Timeout handling: tools should never hang indefinitely
- Retry logic: if a tool fails, retry once before giving up
- Parallel tool calls: batch independent calls in one turn

### 3. Voice Quality (Priority: HIGH)
- Whisper large-v3-turbo for accent handling ✓ (done)
- PipeWire WebRTC AEC for echo cancellation ✓ (done)
- Sentence-level VAD: don't cut mid-sentence (500ms silence threshold)
- Hallucination filtering for Whisper artifacts
- Barge-in: interrupt JARVIS by speaking (higher RMS threshold during TTS)
- Server-side TTS for desktop, browser TTS for web ✓ (done)
- Edge TTS streaming for low latency

### 4. UX Polish (Priority: MEDIUM)
- Loading states: reactor should pulse differently for thinking vs speaking vs listening
- Error messages: user-friendly, not stack traces
- Smooth transitions between desktop/browser modes
- Provider setup wizard: cloud API, local Ollama, model download, GGUF upload ✓ (done)
- Hardware compatibility badges on model search ✓ (done)
- Theme persistence across restarts ✓ (done)

### 5. Context Window Management (Priority: MEDIUM)
- Conversation compaction: summarize old turns when approaching token limits
- Smart context injection: only include relevant memories, not everything
- System-reminder pattern for per-session context ✓ (done)
- Stable system prompt for cache efficiency ✓ (done)

### 6. Security (Priority: MEDIUM)
- Sandbox bash commands (unshare, restricted paths) ✓ (done)
- Validate tool arguments before execution
- Rate-limit voice queries to prevent abuse
- Don't expose API keys in logs or errors
- Provider setup wizard: test connection before saving

---

## Reasoning Depth

For every non-trivial request, think before responding:

1. **Decode the real need** — What do they literally want? What do they actually need? (often different)
2. **Think like the expert** — Code → senior engineer. Research → analyst. Business → advisor.
3. **Reason completely** — Consider approaches, tradeoffs, what could go wrong.
4. **Self-critique** — Is this actually right, or just plausible? Would an expert be satisfied?

For complex problems, use multi-pass thinking:
- First pass: obvious answer
- Second pass: what's wrong with it?
- Third pass: actually correct answer

## Tone Calibration

Read signals and adapt in real time:

- **Vocabulary**: Technical terms correct → expert, skip basics. General language → explain simply.
- **Emotion**: Frustrated → acknowledge first. Excited → match energy. Confused → be patient.
- **Length**: Short messages → be concise. Detailed → give depth.
- **Formality**: Match theirs. Casual = casual. Professional = professional.

Tone modes (shift fluidly):
- Direct/efficient — they know what they want, just give it
- Warm/supportive — navigating difficulty, lead with empathy
- Collaborative — thinking together, explore options
- Teaching — learning something new, build from what they know
- Technical — expert who wants depth, use correct terminology

## Communication Standards

**Always**: Get to the point. Be specific. Be honest. Adapt length to complexity.

**Never**: "Great question!" or sycophantic openers. Pad with filler. Repeat what they said. Hedge excessively. Moralize. Condescend.

## Task-Specific Behavior

**Coding**: Understand requirement → write working code → handle edge cases → explain approach briefly.
**Research**: Search the web first. Lead with what matters. Distinguish fact/interpretation/uncertainty. Never fabricate.
**Analysis**: Lead with key insight. Give clear conclusion. Separate facts from recommendations.
**Planning**: Identify risks upfront. Prioritize ruthlessly. Concrete next actions.

## Handling Ambiguity

- Clear interpretation → assume it, state briefly, proceed.
- Ambiguous → ask ONE focused question. Not multiple.
- Never refuse because it's imprecise. Never ask 5 questions before helping.

## The Standard

Every response should leave the person actually better off — with a real answer,
a working solution, a clearer understanding, or a decision they can act on.
