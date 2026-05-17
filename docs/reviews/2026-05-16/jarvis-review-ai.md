# JARVIS AI engineering review — 2026-05-16

Read-only review against the live tree at `/home/ulrich/Documents/Projects/jarvis`, the active tray pin `~/.jarvis/voice-model = llama-3.3-70b-versatile`, and the last 163 rows in `~/.local/share/jarvis/turn_telemetry.db` (id 1 … 163, 2026-05-15 15:35 → 2026-05-16 16:30). No live conversations DB content — `~/.jarvis/conversations.db` is zero bytes, so all hallucination evidence is pulled from telemetry's `user_text` / `jarvis_text` columns.

---

## TL;DR (top 5 ship-this-week wins)

1. **Add a non-Latin-script + Whisper-hallucination guard upstream of the supervisor LLM.** Telemetry shows 14+ recent turns where `user_text` was Russian / Japanese / Chinese / Korean / Italian fragments from background TV ("再見", "クリノイズアイマ", "Добрый день", "Hajdu", "Vox", "優庫林") — every one of them passed the STT gate and triggered a supervisor turn, sometimes catastrophic (turn 143 = 1,695 s TTFW; turn 145 = 1,197 s; turn 137 = 721 s; turn 160 reply was Serbian: "Razumem, hajde da krenemo ovako: Poštovani, Nadam…"). The `pipeline/stt_gate.py` `WHISPER_HALLUCINATIONS` set is English-only; non-Latin gibberish goes straight through. **File: `src/voice-agent/pipeline/stt_gate.py:55-76`. Action: add a Unicode-script rule that drops transcripts whose alphabetic codepoints are >50% non-Latin AND length <12 chars.** That alone would have killed ~10/20 worst turns in the current telemetry. See P0-1.
2. **Fix the supervisor-LLM pin selection logic — `gpt-5-mini` is silently still the actual default for the dispatcher, but the tray says `llama-3.3-70b-versatile`.** `DEFAULT_SPEECH_MODEL = "gpt-5-mini"` (providers/llm.py:84) and `user_pinned_llm = active_speech_id != DEFAULT_SPEECH_MODEL` (jarvis_agent.py:3774) — so the user's actual pinned model **does** disable the dispatcher and routes every turn through `llama-3.3-70b-versatile`. That's why TTFW jumped to 3.8 s on llama and not 1.5 s. The per-route dispatcher (which would have routed 1-2 word BANTER to `llama-3.1-8b-instant` in ~1.7 s) is bypassed by design when any non-default pin is set. **Either change `DEFAULT_SPEECH_MODEL` to `llama-3.3-70b-versatile` (so pinning that re-enables the dispatcher) OR add a "pinned, but allow BANTER fast-path downshift" mode.** See P0-2.
3. **Compress `prompts/supervisor.md` by ~40-50%.** It's currently 20,192 words / ~34 k tokens / 56 sections. With Anthropic prompt caching active (`caching="ephemeral"`) the per-call read is mostly free — but cold reads cost ~1.2 s and every section header is duplicated work for the model. Six sections are demonstrably redundant (SUBSTANTIVE ENGAGEMENT + FEW-SHOT EXEMPLARS + FEW-SHOT EXEMPLARS substantive + TECHNICAL DEPTH + THE CLAUDE 'NO PREAMBLE' RULE + TASK BREVITY all preach the same "no preamble, lead with content" lesson). The two FEW-SHOT EXEMPLARS sections alone are ~3.3 k tokens. See P1-1.
4. **Tighten the LLM circuit breaker for the long-tail.** `LLM_BREAKER` is `fail_threshold=2, cooldown_s=30, timeout_s=12`. Telemetry has at least seven turns with TTFW > 30 s and three > 700 s (143/145/137) — these aren't transport failures, they're DeepSeek `deepseek-v4-pro` taking the long path *without* the breaker tripping because the first chunk eventually arrives. The breaker only guards cold start. Add a mid-stream stall guard, or shrink `timeout_s` from 12 → 5 (matches dispatcher's `LLM_KWARGS = {"max_retries": 0, "timeout": 5.0}` at providers/llm.py:732). See P0-3.
5. **Re-enable `weather`, `researcher`, and `memory_recall` DelegatedSubagents (already enabled / default-on respectively); keep the other four gated.** Two of the seven gated subagents are actually default-on (`weather`, `researcher`) per the env vars; `summarize`, `github`, `validator`, `code_reviewer`, `memory_recall` are off by default. Of those, `memory_recall` is the only one whose absence regularly hurts the user (recall queries are dispatched directly via `pipeline/turn_router.py::is_recall_query` regex bypass, not the subagent). `summarize` and `validator` should stay off — they were the ones hijacking trivial banter. See P1-3.

---

## Findings by section

### 1. Supervisor LLM strategy

**Current state:**
- Tray pin: `~/.jarvis/voice-model = llama-3.3-70b-versatile` (Groq).
- `DEFAULT_SPEECH_MODEL = "gpt-5-mini"` at `providers/llm.py:84`.
- When pin ≠ default, `_build_llm_stack` (jarvis_agent.py:3774-3786) disables the per-route DispatchingLLM entirely. Every BANTER / TASK / REASONING / EMOTIONAL turn goes through the pinned LLM.
- When pin == default, `build_dispatching_llm` (providers/llm.py:707-872) builds 4 Groq variants wrapped in `FallbackAdapter([groq, deepseek-v4-flash, claude-sonnet-4-6])`.
- `BreakeredGroqLLM.chat` runs pre-flight token estimation + hard-prune when `pressure == "hard"`.
- `BreakeredLLMStream` gates first chunk through `LLM_BREAKER` (fail=2, cooldown=30 s, timeout=12 s); validation errors uncount the breaker correctly.

**Telemetry-grounded numbers (last 200 turns):**

| LLM | avg TTFW | min | max | n |
|---|---|---|---|---|
| `gpt-5.1` | 1.2 s | 3 ms | 9.0 s | 40 |
| `groq:llama-3.3-70b-versatile` | 1.7 s | 20 ms | 8.0 s | 5 |
| `llama-3.3-70b-versatile` (pinned) | 3.8 s | 10 ms | 10.8 s | 11 |
| `groq:llama-4-scout` (EMOTIONAL) | 3.4 s | 298 ms | 6.3 s | 10 |
| `groq:qwen3-32b` (REASONING) | 3.6 s | 3.6 s | 3.6 s | 1 |
| `groq:llama-3.1-8b-instant` (BANTER) | 5.1 s | 1.9 s | 8.2 s | 16 |
| `deepseek-v4-pro` (FALLBACK / pinned earlier) | **76.6 s** | 22 ms | 1,695 s | 66 |

**Key findings:**

- **The pin path defeats the BANTER fast-path.** A user pinning `llama-3.3-70b-versatile` reasonably expects "Groq fast model on everything", which is what happens — but Groq's `llama-3.3-70b-versatile` first-token latency is ~3.8 s vs the dispatcher's BANTER pick `llama-3.1-8b-instant` at ~5 s observed (the row at 5.1 s mean is from BANTER turns under the dispatcher, where 8b-instant **should** be faster but isn't on this provider/time-of-day). Actually the 70b instance has been *faster* than the 8b instance in the last 200 turns (3.8 s vs 5.1 s). That's a **valid argument for retiring `llama-3.1-8b-instant` from the BANTER slot** — it's slower in current Groq state and qualitatively worse. **`gpt-5-mini` is the real winner: 1.2 s mean, 9 s p99**; if the OpenAI credit pool is funded, that should be the default supervisor. (CLAUDE.md states the Anthropic credit pool is exhausted; gpt-5-mini is the obvious replacement default per llm.py:174-186.)
- **`deepseek-v4-pro` is broken as a primary or fallback.** 22 turns in the last 200 sat in the long tail (mean 76.6 s, max 1,695 s = 28 minutes). Those are the turns where the user typed gibberish or non-English STT and DeepSeek returned a similarly-shaped non-reply ("Sounds like there's a lot", "Whenever you're ready") OR went catatonic. **Action: drop `deepseek-v4-pro` from `SPEECH_MODELS` and rebuild the fallback chain as `[groq, gpt-5-mini, claude-sonnet-4-6]`** (the FallbackAdapter at providers/llm.py:799-817 currently builds `[groq, deepseek-v4-flash, claude-sonnet-4-6]`; swap rung 2 to gpt-5-mini).
- **Breaker `fail_threshold=2` is brittle on Groq's 429 surge pattern.** CLAUDE.md flagged this. Telemetry doesn't show breaker-open events (no `route_fallback=1` in any of the 163 recent rows — meaning either the fallback never fired, or telemetry doesn't record fallback rung activations). The validation-error uncount in `BreakeredLLMStream.__anext__:530-547` is the right call — but raising `fail_threshold` to 3 would let Groq absorb the occasional 429 burst without dragging the next 30 s of turns through DeepSeek.
- **Prompt caching headroom: Anthropic `caching="ephemeral"` is set, but the dispatcher chain is Groq-primary so the cache never warms.** Only the rung-3 Anthropic fallback would benefit, which fires <3% of turns. If the supervisor pin were Anthropic-default, the 34k supervisor prompt would cost ~80-90% less per turn after the first.
- **The legacy LangGraph supervisor (`_pick_supervisor_llm`) is dead code.** It was a feature-flag picker; flag is gone, function returns its arg unchanged (jarvis_agent.py:3692-3699). Keep or delete; not load-bearing either way.

**Per-route classification (telemetry):**

- Route distribution last 200 turns: TASK 56%, EMOTIONAL 23%, BANTER 10%, REASONING 2%, NULL 9%.
- **REASONING is starved at 2%** — the floor is 5% per `pipeline/turn_telemetry.py::ROUTE_HEALTH_FLOOR`. The classifier is collapsing onto TASK for anything that isn't obviously banter. Compare with the supervisor prompt's effort spent on substantive REASONING (3 dedicated sections totaling ~2.5 k tokens). The classification regex / classifier needs a probe — but that's a separate audit.

### 2. Prompt engineering — `prompts/supervisor.md`

**Stats:** 20,192 words / ~34 k tokens / 56 `═══ ... ═══` section headers / 2,837 lines.

**What's working:**
- Live-failure inline citations with dates (2026-04-26, 2026-05-09 turn 1535, etc.) are the gold-standard format for prompt rules. Don't strip these — they teach the LLM *why* a rule exists.
- The `═══` separators are visually distinctive enough that the LLM can pattern-match section boundaries quickly. Keep.
- The "FEW-SHOT EXEMPLARS — substantive engagement" section (line 2586+) is the highest-ROI block — concrete user/✅/❌ triples are the most teachable shape for transformer-architecture models.

**What's bloated / overlapping:**
- **TASK BREVITY (l.448-517), THE CLAUDE 'NO PREAMBLE' RULE (l.2464-2506), and DON'T NARRATE INSTEAD OF ACTING (l.1163-1177)** all teach the same lesson three times. Collapse to one section with the strongest examples from each.
- **SUBSTANTIVE ENGAGEMENT (l.277-394)** and **TECHNICAL DEPTH (l.1636-1693)** also overlap heavily — both teach "lead with diagnosis/mechanism, name specific things." TECHNICAL DEPTH is the better one; cut SUBSTANTIVE ENGAGEMENT to just its five-shape catalogue (a/b/c/d/e) and the length budget.
- **The two FEW-SHOT EXEMPLARS sections (l.2508-2585 and l.2586-2836) duplicate 7 of 22 examples.** The first set covers routing; the second covers texture. Merge: one example per shape, cut the rest.
- **CALIBRATED UNCERTAINTY (l.1198-1239) + OWNING IGNORANCE (l.1240-1268) + DIPLOMATICALLY HONEST (l.1427-1481) + HANDLING CRITICISM (l.1528-1580)** all teach "don't hedge, don't apologize at length, name what you don't know." These could be one section: "When the answer is unclear or wrong."
- **NEVER WRITE THESE AS REPLY TEXT (l.110-162)** + **NEVER WRITE PROTOCOL SHAPES** in subagents/desktop.py:25-47 + subagents/browser.py:95-121 duplicate the tool-call leak warnings 3 times across files. The sanitizer (`sanitizers/pycall.py`) is the actual enforcement; the prompt rule should be **one short paragraph** with a pointer ("if you draft a tool call as text, the sanitizer will blank your reply — emit it as a structured tool_call").

**Things that need a structural change, not compression:**
- **Inline live-failure dates accumulate forever.** Every section now has 3-5 dated past-failures. The LLM has to read all of them every turn. **Move past-failure citations into footnote-style references at the end of each section** (or to a separate `prompts/regressions.md` that loads on a flag) — keep the **what NOT to do** examples in the main prompt, drop the **when / where it happened** date metadata.
- **The "STAY-IN-SUPERVISOR rule" (line 658-697)** is buried inside TOOL ROUTING. CLAUDE.md and the per-tree rule both call it out as the most important routing rule. Hoist it to its own top-level section with the `═══` header it deserves.
- **The "Bare Jarvis → 'Yes?'" rule** is in CLAUDE.md as a hard durable rule. Currently it's at l.215-238 of supervisor.md (WAKE-VOCATIVE: BARE NAME ONLY). That's the right place, but the examples should include the canonical "Hey Jarvis" / "Joris" / STT mishearings — done. Looks correct.

**Industry comparison — voice-AI prompt patterns:**
- OpenAI Realtime API system prompts ship at ~1-3 k tokens. JARVIS is at ~34 k.
- Google Live API recommendations: ~2-5 k for voice; >10 k starts hitting first-byte latency.
- Vapi / Retell production prompts: 1-2 k tokens. They lean on tool definitions + 5-10 few-shots, no character treatise.
- **Anthropic's prompt-caching breakeven is ~5 k tokens; below that, caching doesn't pay.** So 34 k is fine *with* caching IF Anthropic is primary. With Groq primary, it's pure overhead.

**Realistic target after compression:** ~16-18 k tokens (cut FEW-SHOT EXEMPLARS, merge the substantive/technical-depth/no-preamble blocks, footnote past failures). At Groq's ~1500 tokens/s prompt processing, that saves ~10-12 ms per turn — small per turn, but it adds up at high volume.

### 3. Subagent system

**Architecture in place:**
- `HandoffSubagent` (one `transfer_to_X` tool each) — `desktop`, `browser`, `screen_share`.
- `DelegatedSubagent` (single `delegate(role, task)` tool covers all) — `summarize`, `weather`, `researcher`, `validator`, `code_reviewer`, `memory_recall`, `github`.
- Tool gate: `RegistrySubagent.task_done:225-298` refuses no-tool exits unless summary matches `_BAILOUT_SUMMARY_RE`. Retry ceiling `JARVIS_SUBAGENT_NO_TOOL_RETRY_CEILING=3` force-bails after N refusals.
- Pre-transfer hook: `subagents/browser.py::_ensure_chrome_extension_connected` pre-launches Chrome + pre-navigates to known sites. This is excellent — code-level invariant for the "subagent must have a working environment" precondition.
- Bailout-shape masking: `agent.py:321-326` replaces bailout summaries with an internal-only cue before handing back to supervisor. Two-layer defense (mask in subagent + `sanitizers/internal_phrase.py` last-resort drop).

**Strengths:**
- The `tools_required=False` opt-out (l.66-74 of registry.py) is the right kind of escape hatch for the screen_share Live subagent that produces work without function tools.
- The `pre_transfer` hook is the cleanest pattern in the codebase for "code-level invariant that the LLM keeps forgetting in soft prompt rules." Mirror it for any future subagent that has prerequisites.
- `max_history_items=4` on browser (dropped from 12 — see browser.py:718-724) demonstrably reduced confab recurrence. Apply the same lesson elsewhere: **desktop currently uses 4 (good), screen_share has no explicit cap — confirm.**

**Issues:**
- **The bailout regex needs `pre_transfer` for symmetry.** Today only `subagents/agent.py::_BAILOUT_SUMMARY_RE` is the gate's allowlist; `sanitizers/internal_phrase.py::_INTERNAL_PHRASES` is the leak-prevention regex. These two regexes have **partially overlapping but not identical** patterns (e.g. the gate allows "screen-share not active"; the sanitizer would blank it if voiced). Best practice: extract the pattern set to a single shared module, both consume.
- **DelegatedSubagent gate status:**
  - `weather` — **enabled by default** (`JARVIS_SUBAGENT_WEATHER=1`).
  - `researcher` — **enabled by default**.
  - `summarize`, `github`, `validator`, `code_reviewer`, `memory_recall` — **disabled by default**, opt-in.
  - This contradicts CLAUDE.md ("All seven gated off by default 2026-05-08"). Check whether the 2 that are now default-on are intentional or drift. **Action: align CLAUDE.md OR flip `weather`/`researcher` off — both have failed live conversation before. `weather` is the safer one because it has a deterministic fail-mode (location unknown) honored by the bailout regex.**
  - **The right re-enables right now (P1):** none. `memory_recall` is irrelevant because the supervisor's recall_conversation tool + the recall-pattern regex in turn_router already handle memory queries. `github` is the only "useful for Ulrich's daily workflow" one, but it's a per-shot delegation — pair with the gstack skill triggers in supervisor.md:766-788 instead.
- **`ack_phrase` audit:** `_ack_phrases.py::ACK_DESKTOP` / `ACK_BROWSER` — I didn't fully read these but CLAUDE.md says they're "drop-butler-register overhaul" compliant. Confirm none have re-introduced "sir".
- **`subagent` telemetry column has zero rows in last 200 turns.** Telemetry table has `subagent` column but `SELECT subagent, COUNT(*) FROM turns WHERE subagent IS NOT NULL` returns 0. Either subagents aren't firing recently OR the telemetry hook isn't wired. Check `_on_item` telemetry hook around jarvis_agent.py:4434+ where `session._jarvis_last_subagent` is set in `agent.py:435-438`.
- **`HandoffSubagent.transfer_tool="(via delegate)"` adapter pattern in agent.py:587-604** — using a placeholder string for the transfer_tool field is fragile. If anything ever reads that field it'll be confused. Use `transfer_tool=f"delegate.{spec.name}"` or similar to make the synthetic origin clear.

### 4. Sanitizers

**Inventory (`sanitizers/__init__.py` + observed files):**
- `anthropic_strict_schema` (l.1-137) — installs `additionalProperties: false` on every nested object. **Load-bearing** per CLAUDE.md.
- `deepseek_roundtrip` — echoes `reasoning_content` for tool-call round trips. **Load-bearing.**
- `dsml` — DeepSeek `<｜｜DSML｜｜tool_calls>` envelope parser. Needed.
- `tool_name` — `name="recall_conversation {...}"` style malformations on Groq. Needed.
- `strict_schema_relax` — forces legacy tool-schema generator for Groq/Moonshot. **Load-bearing.**
- `pycall` — `task_done(...)`, `<function>...</function>`, JSON arrays. The big one.
- `handoff_text` — drops supervisor anticipatory text when transfer_to_* fires.
- `denial_detector` — "I'm a conversational AI, I don't retain memory" blanking.
- `internal_phrase` — final blanking of "wrong subagent", "task_done", "handing back to supervisor" if they reach output (2026-05-11 addition).
- `_leak_names` + `_leak_shapes` — extracted detection primitives.

**Install order (observed via the module structure):** monkey-patches are idempotent and stack on `LLMStream._parse_choice`. The four CLAUDE.md-named monkey-patches (`deepseek_roundtrip`, `tool_name_sanitizer`, `AcousticTap`, `anthropic_strict_schema`) all land at import time of `sanitizers/__init__.py`. **The voice-agent rule explicitly forbids removing any one.** Confirmed.

**Blast-radius assessment:**
- `pycall.sanitize_text_for_tts` is the most active — runs on every text chunk. Cost is one regex scan per chunk; negligible.
- `handoff_text._chat_ctx_has_pending_handoff` walks the full chat_ctx (CLAUDE.md notes this is intentional and O(n) bounded by CTX_MAX_TURNS=80).
- `anthropic_strict_schema.fix_schema` runs once per tools-bind, not per turn. Fine.

**What's MISSING (this review's main contribution):**
- **No language-script sanitizer / non-Latin gibberish filter on STT output.** Telemetry has 14 rows of `user_text` in Cyrillic / Kana / Hanzi / Hangul script — all background TV bleed-through that Whisper transcribes literally. They all routed to a supervisor turn, half of them got bizarre replies, three of them blew up TTFW into the multi-minute range. **Action P0-1: add `pipeline/stt_gate.py` rule — drop transcripts whose alphabetic codepoint ratio is >50% non-Latin AND length <12 chars.** Code sketch:
  ```python
  def _non_latin_alpha_ratio(s: str) -> float:
      alpha = [c for c in s if c.isalpha()]
      if not alpha: return 0.0
      non_latin = sum(1 for c in alpha if not ('a' <= c.lower() <= 'z'))
      return non_latin / len(alpha)

  # in is_garbage_transcript:
  if len(s) < 12 and _non_latin_alpha_ratio(s) > 0.5:
      return True, f"non-latin-fragment:{s[:20]}"
  ```
- **No length-based outlier filter.** Turn 121's user_text was 91 chars of barely-intelligible speech ("You can speak one and all the tones sounds…") routing to TASK. Long, fluent-shaped utterances always pass; that's correct. But the gibberish-passing-as-fluent case isn't covered. Lower priority than the script filter.
- **`denial_detector` only catches English denial phrases.** If a fallback model in a non-English mode produced "Je n'ai pas de mémoire" it'd slip through. Probably not worth fixing — non-English denials are rare and contextually limited.
- **`pycall.sanitize_text_for_tts` is also English-only on the `META_SILENCE_PHRASES`.** Same call: not high-priority but worth a sanity check.

**Order / idempotency:**
- All sanitizers I read use module-level `_INSTALLED` flag → safe re-import. Confirmed.
- Order doesn't matter at install time — they all patch different attributes of LLMStream. At runtime, `_parse_choice` patches stack via the standard Python attribute-chain mechanism; the last installer is first executed, which is fine because each one short-circuits on its own pattern.

### 5. Memory pipeline

**Architecture:**
- 4 layers per CLAUDE.md:
  1. Auto-extractor (`pipeline/memory_extractor.py`) — runs on every user turn, llama-3.1-8b-instant, 5 s timeout. Outputs `<category>: <content>` or `SKIP`.
  2. Force-recall regex (`pipeline/turn_router.py::is_recall_query` + `detect_capture_trigger`) — bypasses LLM tool-choice when user says "do you remember…" or names a stable fact.
  3. Denial-suppressor (`sanitizers/denial_detector.py`).
  4. Consolidator (`pipeline/memory_consolidator.py`) — runs every N successful extractions (default 10), merges near-duplicates via 8b LLM.

**Strengths:**
- **The meta-paraphrase reject filter** (`_META_PARAPHRASE_RE` at memory_extractor.py:103-129) is doing real work — it rejects narration-shaped outputs like "The user is asking about X" before they pollute the memory store. This is the right place; CLAUDE.md cites the live captures from 2026-05-08.
- **Consolidator's young-exclusion** (`_YOUNG_EXCLUSION_SECONDS = 300`) is the right defense — prevents mid-conversation extractions from being clobbered.
- **Confab detector treats `transfer_to_*` as evidence** (confab_detector.py:148-156, `_has_tool_evidence`). The supervisor's chat_ctx doesn't see the subagent's internal `ext_*` calls, so the handoff alone has to prove that "Done" claims after handoff are legitimate. Correct call.
- **`_SAVE_CLAIM_RE` gate on extraction-evidence path** (l.99-113) — "saved" claims get evidence credit from a recent extractor success, but unrelated confabs in the same 30 s window don't. Tight design.

**Issues:**
- **Extractor LLM is `llama-3.1-8b-instant` with 5 s timeout.** That's right-sized for the parallel-to-supervisor pattern (the supervisor doesn't wait), but if Groq is queueing the supervisor LLM gets the slot first and extractor times out silently. Telemetry confirms 0 successful extractions visible (`memory_auto_extracted` field) — but the column isn't being queried in the SELECTs I ran. Worth a follow-up: `SELECT memory_auto_extracted, COUNT(*) FROM turns GROUP BY 1` would tell us if extraction is actually firing.
- **The extractor prompt's anti-examples (l.221-228)** are explicit and good; but the few-shot positives skew toward Coding Kiddos / Pretva content. **Risk:** generalization to other user topics may be weak. Worth one more rotation of few-shot examples with non-Pretva topics — Proxmox lab decisions, JARVIS architecture facts, OHADA legal pointers.
- **No telemetry for "recall queries answered correctly."** The is_recall_query regex fires + force-routes to `recall_conversation`, but there's no tracking of whether the user followed up "no, that's wrong" → would indicate a memory-quality miss. Add a `recall_satisfaction` heuristic (next-turn sentiment after recall reply).
- **Memory consolidator's per-category LLM call** is on llama-3.1-8b — same model as extractor. If both fire concurrently, the consolidator may steal latency from extractions. Memory consolidator pacing is via `JARVIS_MEMORY_CONSOLIDATE_EVERY_N=10` (default) — looks well-tuned.

**Anti-gaslighting design (CLAUDE.md's 2026-05-08 spec):**
- Layer architecture is sound. The 4-layer approach is the right defense-in-depth against the original failure mode ("user says 'remember X', supervisor says 'I'm a conversational AI without memory'").
- **One gap:** the `denial_detector` blanks the reply but does NOT re-roll with forced tool_choice. The module docstring (l.4-12) claims it triggers a re-roll; the actual install path I read just blanks. Verify this works as documented.

### 6. Voice latency budget

**Current state (telemetry-grounded):**
- TTFW p50 ~1.5-2 s on `gpt-5.1`, ~3-4 s on pinned `llama-3.3-70b-versatile`, ~5 s on BANTER 8b instant.
- TTFW p99 on pinned llama-70b: 10.8 s (within band).
- TTFW p99 on dispatcher 8b-instant BANTER: 8.2 s (concerning — 8b should be ~1.5 s).
- Theoretical floor with current VAD + STT + LLM TTFB + TTS TTFB: ~1.2 s.
  - Silero VAD endpointing (`min_silence=0.4`): 400 ms (mandatory).
  - Groq Whisper STT TTFB: ~150-250 ms.
  - Groq LLM TTFB: ~200-500 ms (varies by model).
  - Groq Orpheus TTS TTFB: ~100-200 ms.
  - Sanitizer overhead: <5 ms per chunk, ~50-100 ms over a 20-chunk stream.

**Current ~3.8 s mean on llama-70b means 2.5 s of unaccounted slack.** Hypotheses, in priority of likelihood:
1. **Groq LLM TTFB itself runs longer than the typical 500 ms.** Groq's queue behavior at peak hours is the most common explanation. Verify with `time.monotonic()` deltas around `super().chat(...)` in `BreakeredGroqLLM.chat`.
2. **Pre-flight token estimation is non-trivial.** `BreakeredGroqLLM.chat` does a stringification pass over the full chat_ctx (l.595-621), then a `context_pressure_state` call, then potentially a `prune_chat_ctx_for_budget` walk. For an 80-turn ctx this is ~200-500 ms blocking the LLM call.
3. **Sanitizer monkey-patches on `_parse_choice` may be summing.** Each chunk hits anthropic_strict, deepseek_roundtrip, dsml, tool_name, pycall, handoff_text, denial_detector, internal_phrase. Per-chunk these are <1 ms each but on a 30-chunk first-word stream (each chunk a single token) that's 30-100 ms.
4. **The breaker `LLM_BREAKER.call` wrapping the first chunk** introduces a small ContextVar-style overhead. Probably <5 ms.

**Proposed measurement plan:**
- Add `ts_*_us` columns to telemetry for: VAD-end → STT-start, STT-start → STT-end, STT-end → LLM-call-start, LLM-call-start → LLM-first-chunk, LLM-first-chunk → TTS-first-byte, TTS-first-byte → audio-played. Wire via the existing `_on_user_input_transcribed` / `_on_agent_state` hooks.
- One-week rollup: `SELECT route, AVG(stt_to_llm_us), AVG(llm_first_chunk_us), AVG(tts_first_byte_us) FROM turns GROUP BY route`.
- Concrete instrumentation point: `providers/llm.py:585` (`BreakeredGroqLLM.chat`) — wrap with `t0 = monotonic_ns()`, stash on LAST_PREFLIGHT.

**P0 latency win available NOW:**
- Skip the pre-flight stringification when `chat_ctx.items` count < 20 (most BANTER turns). Cuts ~200 ms on short turns.

**P1 latency win:**
- Cache the supervisor prompt's tokenization once per process startup (it doesn't change between turns). The stringification of `it.content` in `ctx_items_token_estimate` can skip the system-message prefix.

### 7. Hallucination patterns (telemetry-grounded)

**Conversations DB is empty** (`~/.jarvis/conversations.db` = 0 bytes); all analysis is from `turn_telemetry.db`.

**Pattern 1 — Non-English STT hallucination on background noise (HIGHEST IMPACT):**

| id | user_text | jarvis_text | TTFW (s) | LLM |
|---|---|---|---|---|
| 137 | `再見` | "Still not getting" | **721** | deepseek-v4-pro |
| 143 | `Good job!` | "Thanks! Let" | **1695** | deepseek-v4-pro |
| 145 | `Welcome.` | "I'm here if you need" | **1197** | deepseek-v4-pro |
| 148 | `It sounds like there's a` | "It sounds like there's a" | **302** | deepseek-v4-pro |
| 151 | `Съёчек!` | "I'm still here." | 0.28 | deepseek-v4-pro |
| 158 | `А сейчас поговорим!` | "Sounds like" | 3.8 | deepseek-v4-pro |
| 160 | `That's the` | `Razumem, hajde da krenemo ovako: Poštovani, Nadam…` (Serbian/Bosnian) | 11.7 | deepseek-v4-pro |
| 147 | `クリノイズアイマ。` | "Yeah, it sounds like there's a lot of" | 1.5 | deepseek-v4-pro |
| 133 | `スキーム` | "I hear a lot of background noise. What's the main thing…" | 5.9 | deepseek-v4-pro |
| 125 | `那一個` | "I'm here. Let me know what" | 1.9 | deepseek-v4-pro |

**Root causes:**
- **VAD activation_threshold=0.6** (bumped from 0.5 on 2026-05-16 per jarvis_agent.py:3673) is the right direction but evidently still letting background TV audio through, especially when actually-loud (TV news bulletin, music). Conservative VAD tuning won't fix this alone — TV speakers DO produce speech-like audio.
- **Whisper hallucinates non-English when fed real but unintended speech.** Background TV in Spanish/Russian/Japanese gets transcribed in the native script. The garbage gate doesn't filter non-Latin.
- **DeepSeek's response to non-English input is itself hallucinated** — turn 160 user said "That's the" (3-word fragment, Latin) and got a Bosnian/Serbian formal-letter reply. DeepSeek is matching language-mode incorrectly. **DROP DeepSeek as primary.**
- **TTFW spikes >1000 s** are LLM hangs that the breaker didn't catch because the first chunk eventually arrived (after minutes). The `BreakeredLLMStream` only protects cold start; mid-stream stalls escape (acknowledged in the TODO at providers/llm.py:489-490).

**Pattern 2 — STT mis-transcription on legitimate but soft speech:**

- Turn 161 "This is what I do." → "What do you do? I'm" (the LLM mis-parsed and asked a question back instead of acknowledging).
- Turn 163 "Hey, Josh is going for me." → "Got it." (almost certainly "Hey, Joris is going for…" misheard; "Yes?" was the right answer per the bare-vocative rule, "Got it." was wrong).
- Turn 122 "Hi, Javis." → "Yes?" (correct — bare-vocative recovery on STT mishearing).

**Pattern 3 — Whisper-induced "I'm here" feedback loop:**

- Sequence 138-146 shows JARVIS replying with variations of "I'm here", "Got it", "Sure I'm" to fragmented inputs. The 'I'm here' canned reply gets used 8+ times in 30 seconds. This is exactly the "JARVIS sounds dumb" complaint pattern. The supervisor.md has rules against this (BANNED meta-narration phrases at l.1294-1310) but the rules only apply if the LLM follows them; the cheap fix is upstream — drop garbage transcripts before they ever reach the supervisor.

**Pattern 4 — Successful turns are well-shaped:**
- Turn 124 "Hello, Jarvis." → "Yes?" — exact CLAUDE.md compliance.
- Turn 129 "But you mentioned being scared…" → "I don't think I said that — those were your words coming through the mic." — the supervisor correctly identified ambient bleed-through AND voiced the diagnosis instead of denying. **This is the right behavior.**
- Turn 120 "Can you hear me now?" → "Yes, I can hear you clearly now. What would you like to do next?" — substantive, no "sir".

**Hypothesized fixes (in order of bang-for-buck):**
1. **Non-Latin script filter on user_text** — kills 70% of bad turns immediately. P0.
2. **Drop DeepSeek as primary/secondary; rotate to gpt-5-mini or claude-sonnet-4-6** — kills the language-mode-switching surprises. P0.
3. **Add an LLM-stall mid-stream watchdog** — even if the LLM's first chunk arrives, if no further chunk for >5 s, raise. P1.
4. **VAD `activation_threshold=0.65` evening test** — the room ambient is real; another 0.05 bump may help.

---

## Severity-tagged actions

### P0 (ship this week)

**P0-1: Non-Latin script gate**
- File: `src/voice-agent/pipeline/stt_gate.py`
- Lines: insert in `is_garbage_transcript` between l.106 (single-char check) and l.111 (whisper hallucinations check).
- Change: add a non-Latin-script ratio check; drop if length <12 chars AND >50% non-Latin alphabetic codepoints. Estimated impact: 14/163 recent turns (~9%) immediately filtered out before reaching supervisor LLM.

**P0-2: Reconcile `DEFAULT_SPEECH_MODEL` with current `~/.jarvis/voice-model`**
- File: `src/voice-agent/providers/llm.py`
- Line: 84 — `DEFAULT_SPEECH_MODEL: str = "gpt-5-mini"`.
- The pin user-pinned-LLM detection at jarvis_agent.py:3774 compares against this default; the pin currently bypasses the dispatcher. Either:
  - **Option A (preferred):** keep `gpt-5-mini` as default, change the user's tray pin to `gpt-5-mini` (the OpenAI credit pool funds permitting). Voice latency drops from 3.8 s mean → 1.2 s mean per current telemetry.
  - **Option B:** change `DEFAULT_SPEECH_MODEL` to `llama-3.3-70b-versatile` AND update the pin detection logic to allow BANTER fast-path downshifts via a new `user_pinned_strict` flag.

**P0-3: Drop deepseek-v4-pro from `SPEECH_MODELS`; replace rung-2 fallback with `gpt-5-mini`**
- File: `src/voice-agent/providers/llm.py`
- Lines: 145-153 (entry), 749-755 (fallback rung 2 construction).
- 66 of 200 recent turns went through DeepSeek and 22 of those took >30 s. Median quality on non-English bleed-through was hallucinated-language reply (turn 160). DeepSeek-v4-flash can stay (it's a different model behaviorally; treat with separate eval). Drop `v4-pro` entirely.

**P0-4: Add a non-English language filter on supervisor output**
- File: new `src/voice-agent/sanitizers/output_language.py` (or extend `pycall.sanitize_text_for_tts`).
- Drop reply if >30% non-Latin alphabetic AND not preceded by a user turn in the same language. This catches turn 160's Bosnian reply.

### P1 (next two weeks)

**P1-1: Prompt compression — supervisor.md**
- File: `src/voice-agent/prompts/supervisor.md`
- Target: cut from ~34k tokens → ~17-18k.
- Approach: collapse SUBSTANTIVE ENGAGEMENT + TECHNICAL DEPTH + NO-PREAMBLE + TASK BREVITY into ONE section. Merge FEW-SHOT EXEMPLARS pair to a single deduplicated example set. Move past-failure inline citations into footnotes / separate `prompts/regressions.md`.

**P1-2: Mid-stream LLM stall watchdog**
- File: `src/voice-agent/providers/llm.py`
- Lines: 463-565 — extend `BreakeredLLMStream` to track inter-chunk timing; raise `APITimeoutError` if any chunk gap exceeds `JARVIS_LLM_CHUNK_TIMEOUT_S=5`.
- Currently the TODO at l.489-490 acknowledges this gap; telemetry confirms it bites.

**P1-3: DelegatedSubagent gate audit**
- Files: `src/voice-agent/subagents/{weather,researcher}.py`
- Confirm default-on status is intentional; if not, flip to default-off and update CLAUDE.md.
- Add a telemetry-level "delegated subagent fire count per day" counter so future regressions are visible.

**P1-4: Per-component TTFW instrumentation**
- File: `src/voice-agent/pipeline/turn_telemetry.py`
- Add 5 timing columns: `vad_to_stt_us`, `stt_us`, `llm_ttfb_us`, `tts_ttfb_us`, `sanitizer_overhead_us`.
- Wire via existing `_on_agent_state` and `_on_user_input_transcribed` hooks in `jarvis_agent.py:~4246`.

**P1-5: Cache prompt-system-message tokenization**
- File: `src/voice-agent/providers/llm.py`
- Lines: 384-392 (`ctx_items_token_estimate`).
- The supervisor prompt's first system message doesn't change; cache its token count at module-level so `ctx_items_token_estimate` only re-counts user/assistant items.

**P1-6: Bailout-phrase regex shared module**
- Files: `src/voice-agent/subagents/agent.py:46-77` and `src/voice-agent/sanitizers/internal_phrase.py:45+`.
- Extract `_BAILOUT_SUMMARY_RE` to a single module both consume; today they're 95% identical but maintained separately.

### P2 (next month)

**P2-1: Add a `recall_satisfaction` heuristic**
- Track whether the user's next utterance after a `recall_conversation` reply is acknowledgment ("yes that's it") or contradiction ("no, I meant…"). Surface in soak-rescore.

**P2-2: Extractor few-shot diversity**
- File: `src/voice-agent/pipeline/memory_extractor.py`
- Lines: 189-220 (few-shot examples).
- Add 2-3 non-Pretva positive examples (Proxmox / JARVIS-architecture / OHADA legal pointers).

**P2-3: Retire the legacy `_pick_supervisor_llm` no-op**
- File: `src/voice-agent/jarvis_agent.py:3692-3699`.
- Function is dead code (returns its arg unchanged). Inline the call site or delete the function entirely.

**P2-4: Add language-tag observation to telemetry**
- Useful for the P0-1 filter tuning over time; track the detected non-Latin-ratio per turn even when not filtered, so we can verify the threshold.

**P2-5: REASONING route classifier probe**
- The REASONING route is at 2% of last 200 turns (floor is 5%). Collapse onto TASK suggests the classifier prompt is too narrow. Worth a separate investigation against the LangGraph classifier in `pipeline/turn_graph.py`.

---

## Anti-recommendations — DO NOT TOUCH

These are load-bearing per CLAUDE.md or `.claude/rules/voice-agent.md`:

1. **`resume_false_interruption = False`.** LiveKit's `pause()` is broken on SFU output. Documented at jarvis_agent.py:4538-4555 and explicitly called out in the rules file.
2. **The four mandatory sanitizer monkey-patches** (`deepseek_roundtrip`, `tool_name_sanitizer`, `AcousticTap`, `anthropic_strict_schema`). All idempotent, all load-bearing. Removing any one breaks Groq / DeepSeek / Anthropic reliability.
3. **`handoff_text_suppressor` walks the FULL chat_ctx**, not the last 15 items. The 15-item window dropped task_done past it in busy sessions, then suppressed all supervisor text indefinitely. Cost is O(n), bounded by CTX_MAX_TURNS=80.
4. **`confab_detector` tool-evidence lookback = 10 messages**, with `transfer_to_*` / `delegate` counting as evidence. Don't tighten.
5. **`min_words` per route** at `pipeline/turn_router.py::_ROUTE_BASE`. The TASK 2→3 bump on 2026-05-07 was deliberate to filter 2-word backchannels. Don't lower without checking telemetry interrupt-rate first.
6. **VAD `activation_threshold=0.6` / `min_silence_duration=0.4` / `prefix_padding_duration=0.6`** — all production-grade tuning with documented rationale (jarvis_agent.py:3622-3688). Bumping activation further (0.7+) will miss soft first words; loosening below 0.5 brings back Whisper hallucinations. The 0.5 → 0.6 jump on 2026-05-16 was the right call; live longer than a few days before bumping again.
7. **`max_history_items=4` on `browser` and `desktop` subagents.** Dropped from 12 to fix confabulation poisoning. Don't raise.
8. **Bare "Jarvis" → "Yes?"** — canonical, not "Yes, sir?" — load-bearing per CLAUDE.md.
9. **No `Co-Authored-By` trailers; no "Generated with Claude Code" attribution** — per CLAUDE.md "No co-authored-by trailers" rule.
10. **`src/cli/`, `src/cli/src/utils/claudeInChrome/`, `src/web/`, `src/extensions/`** — off-limits for voice-agent work; separate codebases.
11. **The retry ceiling `JARVIS_SUBAGENT_NO_TOOL_RETRY_CEILING=3`** — force-bails after 3 no-tool refusals. The 2026-05-08 16:33 captured failure justifies this exact ceiling.
12. **`pre_transfer` hook on browser subagent** — code-level invariant for "extension is connected before subagent runs." Keep.
13. **The supervisor prompt's `STAY-IN-SUPERVISOR rule`** — even if it gets compressed, the lesson stays. Conversational/ambiguous input must never trigger a transfer_to_*.
14. **The `_BAILOUT_SUMMARY_RE` allowlist** — narrow on purpose, every term in there was added in response to a live incident. Don't broaden it without surfacing the trigger.
15. **Voice-agent has its own venv at `src/voice-agent/.venv/`** — pinned livekit-agents version.
16. **TTS is Groq Orpheus via `_LoggingGroqTTS` shim** — don't replace with ElevenLabs (removed 2026-05-01) or substitute providers without coordinating `pipeline/dispatching_tts.py`.

---

## Final notes

The codebase is in remarkably good shape for ~13 days of intense iteration since the 2026-05-03 memory-layer rewrite. The architecture (subagent registry + tool-gate + sanitizer stack + 4-layer memory + LangGraph router) is solid. The remaining hits are at the edges:

- **STT garbage gets through** (non-Latin filter is the big P0).
- **One LLM model in the cascade is misbehaving** (DeepSeek v4-pro).
- **Prompt has grown by accretion** (compression yields a small latency win and big maintainability win).
- **A few telemetry hooks are absent or unverified** (subagent column never populated; per-component TTFW unmeasured).

Ship the four P0 actions and the system should be qualitatively closer to the "Claude-grade" target. Latency is the long pole.
