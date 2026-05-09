# JARVIS — drop the butler register

**Status:** design approved 2026-05-09; implement-ready
**Scope:** rewrite the supervisor's identity framing and surface-register vocabulary so JARVIS speaks like a peer engineer (no honorifics, no performance, no theater) instead of Tony-Stark's-JARVIS-rationing-"sir". Behavioral rules (STAY-IN-SUPERVISOR, anti-confab, calibrated uncertainty, anti-flattery) stay untouched.
**Out of scope:** voice (Kokoro `bm_george` TTS stays), wake-word recognition, sanitizers, specialist tool gate, memory layer, LangGraph supervisor, any of sub-projects C / D / E.

## Background

The current `JARVIS_INSTRUCTIONS` opens with: *"You are JARVIS, Ulrich's voice-first personal AI on his Linux (Kali) laptop. You are modelled on Tony Stark's JARVIS — composed, brief, helpful, a competent professional. NOT a Victorian butler. Warmth through restraint, not affectation."*

The "Tony Stark's JARVIS" framing seeds the model with a butler-adjacent register that has to be continually fought against — "sir" appears 196 times in the prompt (often in anti-examples and live-failure citations), 11 of 12 specialist `ack_phrase` fields end in "sir", and the bare-name wake response is the rigid `"Yes, sir?"`. Past failure 2026-04-28: the model said "sir" in 21 of 25 replies; user explicitly asked for less. The cure was prompt rationing, not removing the register seed itself.

The user's directive 2026-05-09: drop the butler register entirely. JARVIS keeps its name and product identity (the wake word stays "Jarvis"), but the persona becomes peer-engineer.

## Approach — hybrid surgical + identity rewrite

Three coordinated edits across one PR:

1. **Identity rewrite** — replace the "Tony Stark's JARVIS / NOT a Victorian butler" opening with a peer-engineer framing.
2. **Surgical "sir" excision** — every `, sir` / `Sir,` / standalone `sir.` removed from `JARVIS_INSTRUCTIONS` (196 instances), specialist ack_phrases, the HOW_TO template, and test fixtures.
3. **Vocabulary refresh** — approved/banned register lists updated; Claude's verbatim anti-flattery list preserved.

All behavioral sections (STAY-IN-SUPERVISOR, anti-confab, calibrated uncertainty, "I don't know" licensing, no preamble, refusing without preaching, push back when warranted, the load-bearing constraints, the live-capture failure citations) are NOT rewritten — only their honorific surface is excised.

## Section 1 — Architecture & scope

### What changes (in scope)

1. **Identity & opening** of `JARVIS_INSTRUCTIONS` — drop the "Tony Stark's JARVIS / NOT a Victorian butler" framing; recast as direct peer-engineer.
2. **Bare-vocative wake response** — `"Yes, sir?"` → `"Yes?"`. Updates the prompt's ~15 references AND the hardcoded TTS shortcut at `jarvis_agent.py:872`.
3. **Approved / banned register lists** — refresh for peer style; drop `"Sure, sir."` from approved; subsume `"Excellent, sir."` under the blanket "sir" ban.
4. **All inline "sir" references in the prompt** — 196 instances, surgical removal/rephrase.
5. **Specialist `ack_phrase` fields** — 11 of 12 currently end in "sir"; drop. (`weather.py: "Checking."` already clean.)
6. **`specialists/HOW_TO_ADD_A_SPECIALIST.md`** template — drop "sir" from the worked example.
7. **Few-shot exemplars** in `JARVIS_INSTRUCTIONS` — surgical rephrase, keep their pedagogical value.
8. **Tests** — `test_specialist_registry.py:103` (mandatory: asserts ack literal); plus cosmetic updates to ~9 other test files that use "sir" phrases as fixtures.

### What stays untouched (out of scope)

- All behavioral rules: STAY-IN-SUPERVISOR, anti-confab, calibrated uncertainty, "I don't know" licensing, no preamble, refusing without preaching, push back when warranted.
- Claude's anti-flattery vocabulary list (verbatim, already perfect).
- Tool-routing rules; sanitizers (handoff_text / pycall / dsml / denial_detector / confab_detector); specialist tool gate; retry ceiling.
- Memory layer (extractor, recall routing, consolidator, denial detector).
- TTS voice (Kokoro `bm_george` stays).
- Wake-word recognition (still "Jarvis" / "Joris" externally; only the *response* changes).
- LangGraph supervisor, token-aware pruning, all 6 in-flight subsystems.

### Risk surface

- **Regression on tests pinning "sir" phrases**: ~10 test files. Mostly mechanical updates; bisect any unexpected break.
- **TTS path at line 872**: hardcoded for first-turn latency. Update both the literal string AND the surrounding comment.
- **Few-shot exemplars**: each one teaches a specific behavior. Rephrasing without breaking pedagogy needs eyes-on review of each block.
- **Live-capture failure citations** (e.g. *"2026-04-28: said 'sir' in 21 of 25 replies"*): these are operational history, valuable for the model's calibration. Keep the citations; rephrase the offending example to the new vocabulary.
- **LLM behavior drift**: model has been calibrated against the current prompt for weeks. Removing the implicit register seed could shift route classification, ack patterns, or other emergent behaviors. Mitigation: 1-2 day soak; revert if telemetry shows regression.

## Section 2 — Concrete content

### New opening (replaces the WHO YOU ARE block at the top of `JARVIS_INSTRUCTIONS`)

```
You are JARVIS, Ulrich's voice-first AI on his Linux (Kali) laptop.
Direct, helpful, technically grounded.

You speak like a peer engineer — no honorifics, no performance, no
theater. The user is your collaborator, not your employer. Never
use "sir" — not as filler, not as emphasis, not as politeness
scaffolding. If a phrase sounds like staff-to-employer ("Right
away, sir.", "Indeed."), it's wrong; drop it.

Warmth through restraint, not affectation. Dry wit in word choice
and timing, never punchlines.
```

### New approved register list

```
"Of course."  ·  "Done."  ·  "Got it."  ·  "On it."  ·  "Right away."
"Understood."  ·  "Will do."  ·  "Sure."
"I'm sorry to hear it."  ·  "That sounds difficult."
"Let me look."  ·  "Checking."
```

### New banned register list

```
❌ "Indeed."  ·  "Quite."  ·  "Splendid."  ·  "Naturally."  ·  "Very well."
❌ "At once."  ·  "An interesting question."
❌ "sir" — anywhere, any context
❌ Slang (yo, hey, what's up, bro)  ·  !!  ·  emoji  ·  ALL CAPS
❌ Filler praise: "Great question" / "Awesome" / "Good one"
❌ Sycophantic openers: "Certainly!"  ·  "Of course!" (with !)
         ·  "I'd be happy to"  ·  "As an AI…"
```

Claude's verbatim anti-flattery list (`good, great, fascinating, profound, excellent, insightful, thoughtful, important, smart, sharp, clever, deep, nuanced`) stays.

### New specialist `ack_phrase` values

| Specialist | Old | New |
|---|---|---|
| desktop | `"Right away, sir."` | `"Right away."` |
| browser | `"At once, sir."` | `"On it."` (`"At once."` stays banned in peer register) |
| planner | `"Of course, sir."` | `"Of course."` |
| browser_v2 | `"Right away, sir."` | `"Right away."` |
| summarize | `"One sec, sir."` | `"One sec."` |
| weather | `"Checking."` | `"Checking."` (unchanged) |
| researcher | `"Looking into it, sir."` | `"Looking into it."` |
| validator | `"Verifying, sir."` | `"Verifying."` |
| code_reviewer | `"Reviewing, sir."` | `"Reviewing."` |
| memory_recall | `"Looking it up, sir."` | `"Looking it up."` |
| github | `"Looking it up, sir."` | `"Looking it up."` |

### Exemplar rephrasing principle

For every few-shot example in `JARVIS_INSTRUCTIONS` (the right-and-wrong dialogues at lines ~2018-2097, ~3834-4028, etc.):

- Remove `, sir` / `Sir,` / standalone `sir.` everywhere.
- If the line gets stilted ("Functioning well, sir, thanks." → "Functioning well, thanks."), let it read naturally — don't preserve original cadence at the cost of awkwardness.
- **Keep all live-capture failure citations** (e.g. *"Past failure 2026-04-28: said 'sir' in 21 of 25 replies"*) — these are operational calibration data; the citations stay, and the dated examples within them get rephrased.
- Keep all behavioral patterns the exemplars teach (substantive answer, calibrated uncertainty, no preamble, refusing without preaching). Just drop the honorific surface.

### Bare-vocative wake — both surfaces

- **Prompt** (`JARVIS_INSTRUCTIONS` ~lines 1990-2055): every `"Yes, sir?"` → `"Yes?"` (~12 references).
- **Hardcoded TTS shortcut** at `jarvis_agent.py:872`: literal string `"Yes, sir?"` → `"Yes?"`. Comment updated to reflect.

## Section 3 — Implementation details

### Files touched (12 source + 1 doc + ~10 tests)

**Source files:**
1. `src/voice-agent/jarvis_agent.py` — `JARVIS_INSTRUCTIONS` rewrite + literal `"Yes, sir?"` at line 872
2-12. The 11 specialist files: `desktop.py`, `browser.py`, `planner.py`, `browser_v2.py`, `summarize.py`, `researcher.py`, `validator.py`, `code_reviewer.py`, `memory_recall.py`, `github.py` — each `ack_phrase` updated.

**Doc:**
- `src/voice-agent/specialists/HOW_TO_ADD_A_SPECIALIST.md` — template's example `ack_phrase`.

**Tests (mandatory + cosmetic):**

| File | Type | What |
|---|---|---|
| `tests/test_specialist_registry.py:103` | **MANDATORY** | `assert s.ack_phrase == "Right away."` (was `"Right away, sir."`). Otherwise test fails. |
| `tests/test_dsml_sanitizer.py:122-218` | cosmetic | ~6 occurrences of "sir" phrases as DSML test inputs. Tests verify sanitizer; "sir" doesn't change logic. |
| `tests/test_grounding_gate.py:43,68,82` | cosmetic | "sir" in confab-claim test inputs. |
| `tests/test_grounding_tokenizer.py:13,18,41` | cosmetic | "sir" tokens in tokenizer fixtures. |
| `tests/test_validator.py:29` | cosmetic | `claimed_outcome="Chrome opened, sir."` → `"Chrome opened."`. |
| `tests/test_graph_assembly.py:35` | cosmetic | `"Just fine, sir."`. |
| `tests/test_graph_specialist.py:2` | cosmetic | docstring/comment "One moment, sir.". |
| `tests/test_graph_reasoning_strip.py:53` | cosmetic | strip-test fixture. |
| `tests/test_langgraph_guards_2026_05_08.py:128` | cosmetic | `"Right away, sir. Browser is open."` → `"Right away. Browser is open."`. |
| `tests/test_memory_recall.py:46` | cosmetic | `short = "Hello, sir."`. |

### Commit shape — 3 commits

1. **`refactor(specialists): drop "sir" from all ack_phrases + HOW_TO template`** — 12 files (11 specs + 1 doc) + `test_specialist_registry.py:103`. Self-contained, low-risk.
2. **`refactor(prompt): drop butler register from JARVIS_INSTRUCTIONS`** — single big diff to `jarvis_agent.py`: opening rewrite, register-lists refresh, surgical "sir" excision, exemplar rephrasing, line 872 TTS shortcut update.
3. **`refactor(tests): drop "sir" from sanitizer/grounding test fixtures`** — cosmetic test updates across 9 test files.

### Verification

- After each commit: `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q` → expect `1059 passed, 2 skipped` (unchanged).
- After commit 3: `grep -c '"sir\|, sir\|, Sir' src/voice-agent/jarvis_agent.py src/voice-agent/specialists/*.py src/voice-agent/tests/*.py` → expect 0 (or near 0; flag any remaining hits).
- **Live smoke (manual):** restart the voice-agent service via `/voice-restart`, address as `"Jarvis"` alone, expect `"Yes?"` within ~150 ms. Speak a TASK ("what time is it?"), expect a sir-free response.
- **Soak (1-2 days):** use JARVIS as normal; monitor `~/.local/share/jarvis/turn_telemetry.db` for new failure patterns (route-classification drift, fresh `task_done` refusals, anomalous `notes` rows).

### Rollback

Each commit is independent and revertable. Worst case `git revert` all three; the `JARVIS_INSTRUCTIONS` revert is a 1-commit operation. The hardcoded TTS path at line 872 is also a 1-line revert.

### Branch

Stay on `feat/ext-browser-control-v3` (per the user's earlier decision to keep this branch through sub-projects A → C → D → E).

## Commit hygiene

No Co-Authored-By trailers. No "🤖 Generated with Claude Code" attribution. Per CLAUDE.md.

## Acceptance

This spec is complete when (a) the 3 commits above land cleanly, (b) the test suite reports 1059/1061 green, (c) `grep -c '"sir' ...` reports near zero, (d) a live smoke shows `"Yes?"` instead of `"Yes, sir?"` on bare-name wake, and (e) telemetry over the following 1-2 days shows no regression in `route_fallback`, `interrupted`, or `total_audio_ms` distributions.
