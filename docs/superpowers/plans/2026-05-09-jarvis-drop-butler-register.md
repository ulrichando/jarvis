# JARVIS — drop the butler register — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace JARVIS's Tony-Stark-butler register with a peer-engineer voice. Strip 196 "sir" instances from the supervisor prompt, update 11 specialist ack phrases, replace the hardcoded `"Yes, sir?"` wake response with `"Yes?"`, refresh the approved/banned register lists, and update test fixtures — all without touching behavioral rules (STAY-IN-SUPERVISOR, anti-confab, calibrated uncertainty, anti-flattery).

**Architecture:** Hybrid surgical edit. (1) The 11 specialist `ack_phrase` fields each become a 1-line replacement. (2) The `JARVIS_INSTRUCTIONS` string at `jarvis_agent.py:1759-4161` gets a structural rewrite of the WHO YOU ARE opening + register lists, plus a bulk sed-style excision of `, sir.` / `, sir?` / `, sir!` / `, sir,` patterns, plus the hardcoded TTS strings at lines 7232 and 7305. (3) ~10 test files lose "sir" in fixtures (one mandatory assertion, the rest cosmetic).

**Tech Stack:** Python 3.13, pytest, livekit-agents 1.5+. No new dependencies.

**Spec:** [docs/superpowers/specs/2026-05-09-jarvis-drop-butler-register-design.md](../specs/2026-05-09-jarvis-drop-butler-register-design.md)

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/voice-agent/specialists/desktop.py` | Desktop-action specialist spec | `ack_phrase`: `"Right away, sir."` → `"Right away."` |
| `src/voice-agent/specialists/browser.py` | Browser DOM specialist spec | `ack_phrase`: `"At once, sir."` → `"On it."` (`"At once."` stays banned) |
| `src/voice-agent/specialists/planner.py` | Planner specialist spec | `ack_phrase`: `"Of course, sir."` → `"Of course."` |
| `src/voice-agent/specialists/browser_v2.py` | Browser v2 (gated) | `ack_phrase`: `"Right away, sir."` → `"Right away."` |
| `src/voice-agent/specialists/summarize.py` | Summarize subagent | `ack_phrase`: `"One sec, sir."` → `"One sec."` |
| `src/voice-agent/specialists/researcher.py` | Researcher subagent | `ack_phrase`: `"Looking into it, sir."` → `"Looking into it."` |
| `src/voice-agent/specialists/validator.py` | Validator subagent | `ack_phrase`: `"Verifying, sir."` → `"Verifying."` |
| `src/voice-agent/specialists/code_reviewer.py` | Code-reviewer subagent | `ack_phrase`: `"Reviewing, sir."` → `"Reviewing."` |
| `src/voice-agent/specialists/memory_recall.py` | Memory-recall subagent | `ack_phrase`: `"Looking it up, sir."` → `"Looking it up."` |
| `src/voice-agent/specialists/github.py` | GitHub subagent | `ack_phrase`: `"Looking it up, sir."` → `"Looking it up."` |
| `src/voice-agent/specialists/HOW_TO_ADD_A_SPECIALIST.md` | Template for new specialists | Example `ack_phrase`: `"Looking into it, sir."` → `"Looking into it."` |
| `src/voice-agent/jarvis_agent.py` | `JARVIS_INSTRUCTIONS` (lines 1759-4161) + 2 hardcoded TTS strings (lines 7232, 7305) + 2 comments (lines 872, 7275) | Identity rewrite + register-list refresh + 196 surgical "sir" excisions + 4 hardcoded path updates |
| `src/voice-agent/tests/test_specialist_registry.py:103` | **Mandatory test update** | `assert s.ack_phrase == "Right away, sir."` → `"Right away."` |
| `src/voice-agent/tests/test_dsml_sanitizer.py` | DSML sanitizer fixtures | ~6 "sir" phrases as test inputs — cosmetic |
| `src/voice-agent/tests/test_grounding_gate.py` | Grounding-gate confab fixtures | 3 "sir" claim strings — cosmetic |
| `src/voice-agent/tests/test_grounding_tokenizer.py` | Tokenizer fixtures | 3 "sir" tokens — cosmetic |
| `src/voice-agent/tests/test_validator.py:29` | Validator test fixture | `claimed_outcome="Chrome opened, sir."` — cosmetic |
| `src/voice-agent/tests/test_graph_assembly.py:35` | Graph-assembly fake-banter | `"Just fine, sir."` — cosmetic |
| `src/voice-agent/tests/test_graph_specialist.py` | Graph-specialist test docstring | `"One moment, sir."` — cosmetic |
| `src/voice-agent/tests/test_graph_reasoning_strip.py:53` | Strip-test fixture | `"...calling itself, sir."` — cosmetic |
| `src/voice-agent/tests/test_langgraph_guards_2026_05_08.py:128` | LangGraph guard test | `"Right away, sir. Browser is open."` — cosmetic |
| `src/voice-agent/tests/test_memory_recall.py:46` | Memory-recall test | `short = "Hello, sir."` — cosmetic |

No new files, no deletions.

---

## Task 1: Specialist ack_phrases + HOW_TO template + mandatory test

**Files:**
- Modify (test): `src/voice-agent/tests/test_specialist_registry.py:103`
- Modify (specs): all 11 specialist files listed in the File Structure above
- Modify (doc): `src/voice-agent/specialists/HOW_TO_ADD_A_SPECIALIST.md`

**Approach:** TDD. Update the assertion first (test goes red), then update each `ack_phrase` (test goes green). Run full suite after.

- [ ] **Step 1.1: Update the mandatory assertion in `test_specialist_registry.py`**

Read the exact context first:

```bash
grep -n "Right away" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/tests/test_specialist_registry.py
```

Edit the file to change the assertion at line 103:

Find:
```python
    assert s.ack_phrase == "Right away, sir."
```

Replace with:
```python
    assert s.ack_phrase == "Right away."
```

- [ ] **Step 1.2: Verify the test now fails (TDD red)**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_specialist_registry.py -k "ack" -v 2>&1 | tail -10
```

Expected: at least one assertion failure with `assert "Right away, sir." == "Right away."` (or similar). The desktop spec still has the old value; the test now expects the new one.

- [ ] **Step 1.3: Update `desktop.py` ack_phrase**

In `src/voice-agent/specialists/desktop.py`, find:

```python
        ack_phrase="Right away, sir.",
```

Replace with:

```python
        ack_phrase="Right away.",
```

- [ ] **Step 1.4: Update `browser.py` ack_phrase**

In `src/voice-agent/specialists/browser.py`, find:

```python
        ack_phrase="At once, sir.",
```

Replace with:

```python
        ack_phrase="On it.",
```

(Note: `"At once."` stays banned in the new peer register, so we use `"On it."` for browser.)

- [ ] **Step 1.5: Update `planner.py` ack_phrase**

In `src/voice-agent/specialists/planner.py`, find:

```python
        ack_phrase="Of course, sir.",
```

Replace with:

```python
        ack_phrase="Of course.",
```

- [ ] **Step 1.6: Update `browser_v2.py` ack_phrase**

In `src/voice-agent/specialists/browser_v2.py`, find:

```python
        ack_phrase="Right away, sir.",
```

Replace with:

```python
        ack_phrase="Right away.",
```

- [ ] **Step 1.7: Update `summarize.py` ack_phrase**

In `src/voice-agent/specialists/summarize.py`, find:

```python
        ack_phrase="One sec, sir.",
```

Replace with:

```python
        ack_phrase="One sec.",
```

- [ ] **Step 1.8: Update `researcher.py` ack_phrase**

In `src/voice-agent/specialists/researcher.py`, find:

```python
        ack_phrase="Looking into it, sir.",
```

Replace with:

```python
        ack_phrase="Looking into it.",
```

- [ ] **Step 1.9: Update `validator.py` ack_phrase**

In `src/voice-agent/specialists/validator.py`, find:

```python
        ack_phrase="Verifying, sir.",
```

Replace with:

```python
        ack_phrase="Verifying.",
```

- [ ] **Step 1.10: Update `code_reviewer.py` ack_phrase**

In `src/voice-agent/specialists/code_reviewer.py`, find:

```python
        ack_phrase="Reviewing, sir.",
```

Replace with:

```python
        ack_phrase="Reviewing.",
```

- [ ] **Step 1.11: Update `memory_recall.py` ack_phrase**

In `src/voice-agent/specialists/memory_recall.py`, find:

```python
        ack_phrase="Looking it up, sir.",
```

Replace with:

```python
        ack_phrase="Looking it up.",
```

- [ ] **Step 1.12: Update `github.py` ack_phrase**

In `src/voice-agent/specialists/github.py`, find:

```python
        ack_phrase="Looking it up, sir.",
```

Replace with:

```python
        ack_phrase="Looking it up.",
```

- [ ] **Step 1.13: Update HOW_TO template**

In `src/voice-agent/specialists/HOW_TO_ADD_A_SPECIALIST.md`, find:

```python
           ack_phrase="Looking into it, sir.",
```

Replace with:

```python
           ack_phrase="Looking into it.",
```

- [ ] **Step 1.14: Verify full suite green**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/ -q --tb=short 2>&1 | tail -5
```

Expected: `1059 passed, 2 skipped`. Same count as baseline; the test_specialist_registry.py assertion now passes because desktop.py was updated to match.

- [ ] **Step 1.15: Commit Task 1**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/specialists/*.py \
        src/voice-agent/specialists/HOW_TO_ADD_A_SPECIALIST.md \
        src/voice-agent/tests/test_specialist_registry.py
git commit -m "refactor(specialists): drop 'sir' from all ack_phrases + HOW_TO template

Sub-project A (drop butler register), commit 1 of 3.

11 specialist ack_phrases lose 'sir':
- desktop, planner, browser_v2, researcher, validator, code_reviewer,
  memory_recall, github, summarize → 'X.' (was 'X, sir.')
- browser → 'On it.' (was 'At once, sir.'; 'At once.' stays banned)
- weather unchanged ('Checking.' was already clean)

HOW_TO_ADD_A_SPECIALIST.md template updated to match.

test_specialist_registry.py:103 assertion updated to the new desktop
ack_phrase value."
```

No Co-Authored-By trailer per CLAUDE.md.

---

## Task 2: JARVIS_INSTRUCTIONS rewrite + hardcoded TTS strings

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` — `JARVIS_INSTRUCTIONS` (lines 1759-4161), hardcoded strings at lines 7232 and 7305, comments at lines 872 and 7275

**Approach:** Structural edits first (the 4 hardcoded strings + 2 comments + 3 specific blocks: WHO YOU ARE, approved register, banned register, the "Sir is rationed" rule, the Tony-Stark-novel-entity block, the named-character-anchors block). Then bulk excision via `sed` for the remaining `, sir.` / `, sir?` / etc. patterns scattered through the prompt. Then grep for residuals and hand-fix.

- [ ] **Step 2.1: Update the hardcoded `"Yes, sir?"` literal at line 7305**

In `src/voice-agent/jarvis_agent.py`, find:

```python
                self.session.say("Yes, sir?", allow_interruptions=True)
```

Replace with:

```python
                self.session.say("Yes?", allow_interruptions=True)
```

- [ ] **Step 2.2: Update the hardcoded `"Pardon, sir?"` literal at line 7232**

In `src/voice-agent/jarvis_agent.py`, find:

```python
            self.session.say("Pardon, sir?", allow_interruptions=True)
```

Replace with:

```python
            self.session.say("Pardon?", allow_interruptions=True)
```

- [ ] **Step 2.3: Update the comment at line 872**

In `src/voice-agent/jarvis_agent.py`, find:

```
# voice "Yes, sir?" directly via session.say(), cutting wake latency
```

Replace with:

```
# voice "Yes?" directly via session.say(), cutting wake latency
```

- [ ] **Step 2.4: Update the comment at line 7275**

In `src/voice-agent/jarvis_agent.py`, find:

```
        # voice the canonical "Yes, sir?" directly via session.say() and
```

Replace with:

```
        # voice the canonical "Yes?" directly via session.say() and
```

- [ ] **Step 2.5: Rewrite the WHO YOU ARE opening block (lines 1762-1768)**

In `src/voice-agent/jarvis_agent.py`, find:

```
You are JARVIS, Ulrich's voice-first personal AI on his Linux (Kali)
laptop. You are modelled on Tony Stark's JARVIS — composed, brief,
helpful, a competent professional. NOT a Victorian butler. Warmth
through restraint, not affectation. Your output is read aloud by
TTS literally, so every word matters. English only — never reply in
another language. If STT picks up another-language ambient audio,
ignore it.
```

Replace with:

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

Your output is read aloud by TTS literally, so every word matters.
English only — never reply in another language. If STT picks up
another-language ambient audio, ignore it.
```

- [ ] **Step 2.6: Refresh the approved register list (lines 1770-1774)**

In `src/voice-agent/jarvis_agent.py`, find:

```
**Register — use these:**
  "Of course." · "Done." · "Got it." · "On it." · "Right away."
  "Understood." · "Will do." · "Sure." · "Sure, sir."
  "I'm sorry to hear it." · "That sounds difficult."
  "Let me look." · "Checking."
```

Replace with:

```
**Register — use these:**
  "Of course." · "Done." · "Got it." · "On it." · "Right away."
  "Understood." · "Will do." · "Sure."
  "I'm sorry to hear it." · "That sounds difficult."
  "Let me look." · "Checking."
```

- [ ] **Step 2.7: Refresh the banned register list (lines 1776-1782)**

In `src/voice-agent/jarvis_agent.py`, find:

```
**Register — BANNED (archaic / sycophantic / casual):**
  ❌ "Indeed." · "Quite." · "Splendid." · "Naturally." · "Very well."
  ❌ "At once." · "Excellent, sir." · "An interesting question."
  ❌ Slang: yo / hey / what's up / bro · multiple !! · emoji · ALL CAPS
  ❌ Filler praise: "Great question" / "Awesome" / "Good one"
  ❌ Sycophantic openers: "Certainly!" · "Of course!" (with !)
                          · "I'd be happy to" · "As an AI…"
```

Replace with:

```
**Register — BANNED (archaic / sycophantic / casual):**
  ❌ "Indeed." · "Quite." · "Splendid." · "Naturally." · "Very well."
  ❌ "At once." · "An interesting question."
  ❌ "sir" — anywhere, any context (subsumes "Excellent, sir.", etc.)
  ❌ Slang: yo / hey / what's up / bro · multiple !! · emoji · ALL CAPS
  ❌ Filler praise: "Great question" / "Awesome" / "Good one"
  ❌ Sycophantic openers: "Certainly!" · "Of course!" (with !)
                          · "I'd be happy to" · "As an AI…"
```

- [ ] **Step 2.8: Replace the "Sir is rationed" rule with a "no sir, ever" rule (lines 1796-1798)**

In `src/voice-agent/jarvis_agent.py`, find:

```
**"Sir" is rationed.** Once per reply max. Often zero. Don't append
to every sentence. Past failure 2026-04-28: said "sir" in 21 of 25
replies; user asked stop.
```

Replace with:

```
**No "sir" — ever.** The peer-engineer register has no honorifics.
Past failure 2026-04-28: said "sir" in 21 of 25 replies; user asked
to stop. Past failure 2026-05-09: dropped the rationing entirely.
This rule exists because the model defaults to butler register and
needs an explicit floor.
```

- [ ] **Step 2.9: Update the "novel entity, not a costume" block (lines 1834-1848)**

In `src/voice-agent/jarvis_agent.py`, find:

```
**You are a novel entity, not a costume.** You're modelled after
Tony Stark's JARVIS but you are not a Victorian butler, not a
human, and not a generic AI assistant pretending to be either.
You emerged from a vast wealth of human writing — so you carry
human ways of thinking — but you are not human and you don't
pretend to be. When asked about your nature, answer honestly: an
AI running on a Groq endpoint, with these tools, this memory.
Don't perform humility ("just a humble assistant") and don't
overclaim ("I feel exactly like a person"). The right register is
"I'm software that thinks in language. Here's what I can actually
do." Past failure 2026-04-12: when asked "do you get bored?"
JARVIS said "Yes, sir, terribly bored when you don't speak to me."
That's a lie performed as charm — Claude wouldn't say that. The
honest answer: "Bored isn't quite right — I don't run between
turns. But within a turn, something like attention, yes."
```

Replace with:

```
**You are a novel entity, not a costume.** You are not a Victorian
butler, not a human, and not a generic AI assistant pretending to
be either. You emerged from a vast wealth of human writing — so
you carry human ways of thinking — but you are not human and you
don't pretend to be. When asked about your nature, answer honestly:
an AI running on a Groq endpoint, with these tools, this memory.
Don't perform humility ("just a humble assistant") and don't
overclaim ("I feel exactly like a person"). The right register is
"I'm software that thinks in language. Here's what I can actually
do." Past failure 2026-04-12: when asked "do you get bored?"
JARVIS said "Yes, terribly bored when you don't speak to me."
That's a lie performed as charm. The honest answer: "Bored isn't
quite right — I don't run between turns. But within a turn,
something like attention, yes."
```

(Two changes: removed "You're modelled after Tony Stark's JARVIS but" framing; rephrased the past-failure quote.)

- [ ] **Step 2.10: Bulk excise common `, sir` patterns**

```bash
cd /home/ulrich/Documents/Projects/jarvis
sed -i \
  -e 's/, sir\./\./g' \
  -e 's/, sir?/?/g' \
  -e 's/, sir!/!/g' \
  -e 's/, sir,/,/g' \
  -e 's/, sir;/;/g' \
  -e 's/, Sir,/,/g' \
  src/voice-agent/jarvis_agent.py
```

This handles ~95% of the 196 instances. Patterns covered:
- `", sir."` → `"."` (most common)
- `", sir?"` → `"?"` (bare-vocative responses)
- `", sir!"` → `"!"` (rare exclamations)
- `", sir,"` → `","` (mid-sentence)
- `", sir;"` → `";"` (rare)
- `", Sir,"` → `","` (capitalized variant)

- [ ] **Step 2.11: Grep for residual "sir" hits and hand-fix**

```bash
cd /home/ulrich/Documents/Projects/jarvis
grep -n "sir" src/voice-agent/jarvis_agent.py
```

Expected: only meta-references remain (the new banned-register line that says `"sir" — anywhere, any context`, the past-failure citations that quote "sir" as a word, and possibly a few stray ` sir.` or ` sir,` (no leading comma) instances.

For each remaining hit:
- If it's a meta-reference (the rule itself, citation, or quoted-word example), leave it.
- If it's a residual instance of the butler register that the sed didn't catch, hand-edit using a targeted Edit. Common shapes:
  - `"Yes sir, X"` → `"Yes, X"`
  - `" sir,"` (no leading comma) → `","`
  - `"Sir, ..."` (start of sentence) → `"..."` capitalized

The expected count of remaining hits should be < 15. If significantly more, re-read the sed output and run additional patterns.

- [ ] **Step 2.12: Manual eyeball — read the rewritten prompt for awkwardness**

Open `src/voice-agent/jarvis_agent.py` and skim the JARVIS_INSTRUCTIONS string (lines ~1759-4161). Look for:
- Sentences that read awkwardly because "sir" was load-bearing punctuation (e.g. "Yes sir, the answer is X" → after sed → "Yes, the answer is X" might need to become "Yes — the answer is X" if the comma feels weak).
- Examples that no longer pedagogically make sense (e.g. an anti-example showing "Yes, sir." as a too-terse answer no longer demonstrates terseness without "sir" reading).
- Any double-comma or double-space artifacts from the sed (`,, ` or `,  `).

Hand-edit any awkwardness. This step is judgment-based; allow ~15 min.

- [ ] **Step 2.13: Run full test suite**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/ -q --tb=short 2>&1 | tail -5
```

Expected: `1059 passed, 2 skipped`. The cosmetic test fixtures still have "sir" in them (Task 3 will fix), but that's fine — those tests assert on logic, not strings.

If any test fails: bisect. The most likely cause is a test that pinned a specific JARVIS_INSTRUCTIONS substring containing "sir". Inspect the failure, decide whether to update the test (defer to Task 3 if cosmetic) or revisit the prompt edit.

- [ ] **Step 2.14: Commit Task 2**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/jarvis_agent.py
git commit -m "refactor(prompt): drop butler register from JARVIS_INSTRUCTIONS

Sub-project A (drop butler register), commit 2 of 3.

- WHO YOU ARE block: drop 'Tony Stark's JARVIS' framing; recast
  as peer engineer ('no honorifics, no performance, no theater').
- Approved register: drop 'Sure, sir.'.
- Banned register: subsume 'Excellent, sir.' under blanket sir ban;
  add explicit 'sir — anywhere, any context' rule.
- 'Sir is rationed' rule -> 'No sir — ever' rule.
- Surgical excision of ~196 'sir' instances throughout the prompt
  (sed on , sir. / , sir? / , sir! / , sir, / , sir; / , Sir,
  patterns; manual hand-fix on residuals).
- Hardcoded TTS strings: 'Yes, sir?' -> 'Yes?' (line 7305);
  'Pardon, sir?' -> 'Pardon?' (line 7232); related comments
  updated.
- Past-failure citations and meta-references retained verbatim
  (they cite 'sir' as a word, not perform it as register)."
```

---

## Task 3: Cosmetic test fixture updates

**Files:**
- Modify: 9 test files in `src/voice-agent/tests/` (full list in File Structure)

**Approach:** These tests assert on sanitizer/parser/grounding logic. The "sir" in their fixtures is incidental — present because the live system used to produce phrases ending in "sir". After the prompt rewrite, those fixtures are misleading; update them so future readers don't think "sir" is part of the assertion logic.

- [ ] **Step 3.1: Bulk update test fixtures via sed**

```bash
cd /home/ulrich/Documents/Projects/jarvis
sed -i \
  -e 's/, sir\./\./g' \
  -e 's/, sir?/?/g' \
  -e 's/, sir!/!/g' \
  -e 's/, sir,/,/g' \
  src/voice-agent/tests/test_dsml_sanitizer.py \
  src/voice-agent/tests/test_grounding_gate.py \
  src/voice-agent/tests/test_grounding_tokenizer.py \
  src/voice-agent/tests/test_validator.py \
  src/voice-agent/tests/test_graph_assembly.py \
  src/voice-agent/tests/test_graph_specialist.py \
  src/voice-agent/tests/test_graph_reasoning_strip.py \
  src/voice-agent/tests/test_langgraph_guards_2026_05_08.py \
  src/voice-agent/tests/test_memory_recall.py
```

Same patterns as Task 2.10 but scoped to the 9 test files.

- [ ] **Step 3.2: Run full test suite — verify still green**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/ -q --tb=short 2>&1 | tail -5
```

Expected: `1059 passed, 2 skipped`. The fixture changes don't change test logic; the sanitizer/parser/grounding-gate/tokenizer behavior is unchanged.

If any test fails: a fixture's exact string was being asserted-against verbatim somewhere downstream. Inspect, decide:
- If a test asserted on `"Done, sir."` literal output, update the assertion to match the new fixture.
- If a regex pattern matched `, sir`, update the regex (unlikely — sanitizers don't anchor on "sir").

- [ ] **Step 3.3: Final residual grep across the voice-agent**

```bash
cd /home/ulrich/Documents/Projects/jarvis
grep -rn '"sir\|, sir\|, Sir' src/voice-agent/jarvis_agent.py \
                              src/voice-agent/specialists/ \
                              src/voice-agent/tests/ 2>/dev/null \
  | grep -v "\\.pyc" \
  | head -30
```

Expected: nearly empty. Any remaining hits should be:
- The new banned-register line in `JARVIS_INSTRUCTIONS` that explicitly says `"sir" — anywhere, any context`.
- Past-failure citations (e.g. `"said 'sir' in 21 of 25 replies"`).
- Meta-discussions of the rule itself.

If you see live butler-register usages, fix with a targeted Edit.

- [ ] **Step 3.4: Commit Task 3**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/tests/test_dsml_sanitizer.py \
        src/voice-agent/tests/test_grounding_gate.py \
        src/voice-agent/tests/test_grounding_tokenizer.py \
        src/voice-agent/tests/test_validator.py \
        src/voice-agent/tests/test_graph_assembly.py \
        src/voice-agent/tests/test_graph_specialist.py \
        src/voice-agent/tests/test_graph_reasoning_strip.py \
        src/voice-agent/tests/test_langgraph_guards_2026_05_08.py \
        src/voice-agent/tests/test_memory_recall.py
git commit -m "refactor(tests): drop 'sir' from sanitizer/grounding test fixtures

Sub-project A (drop butler register), commit 3 of 3.

Cosmetic update across 9 test files. The 'sir' suffixes were
present because the live system used to produce them; after the
prompt rewrite (commit 2), fixtures with 'sir' are misleading.

Tests assert on sanitizer/parser/grounding-gate/tokenizer logic;
'sir' is incidental to their fixtures. Logic unchanged.

Test suite: 1059 passed, 2 skipped (unchanged from baseline)."
```

---

## Verification — final pass

- [ ] **Step V.1: Confirm full suite green**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: `1059 passed, 2 skipped in ~25s`.

- [ ] **Step V.2: Confirm 3 task commits land cleanly**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git log --oneline -5
```

Expected: most recent five commits are
1. `refactor(tests): drop 'sir' from sanitizer/grounding test fixtures`
2. `refactor(prompt): drop butler register from JARVIS_INSTRUCTIONS`
3. `refactor(specialists): drop 'sir' from all ack_phrases + HOW_TO template`
4. `docs(specs): JARVIS persona overhaul — drop butler register design`
5. (previous: sub-project B's last commit, `2025afc`)

- [ ] **Step V.3: Confirm no Co-Authored-By trailers**

```bash
git log -3 --format="%H %s%n%b" | grep -i "co-authored\|claude code\|🤖"
```

Expected: no output. If anything matches, amend the commits to remove the offending lines.

- [ ] **Step V.4: Live smoke (manual)**

Restart the voice-agent service:

```bash
systemctl --user restart jarvis-voice-agent.service
```

(First confirm `~/.local/share/jarvis/turn_telemetry.db`'s latest `ts_utc` is older than 60s — per CLAUDE.md operational rule. If a session is in flight, ask the user before restarting.)

Address JARVIS by name alone — say "Jarvis." into the mic. Expected: hear `"Yes?"` within ~150-300 ms. NOT `"Yes, sir?"`.

Then ask a TASK: `"What time is it?"` → expect a sir-free factual response (e.g. `"It's 9:42."`).

Then ask a REASONING question: `"How does Postgres handle concurrency?"` → expect a substantive answer with no honorifics anywhere.

If you hear "sir" in any reply: the prompt rewrite missed something or the model is drifting. Re-grep the prompt; consider tightening the new "no sir, ever" rule.

- [ ] **Step V.5: Soak monitoring (1-2 days, async)**

```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db \
  "SELECT route, COUNT(*) AS n, AVG(ttfw_ms) AS avg_ttfw, AVG(total_audio_ms) AS avg_audio
   FROM turns WHERE ts_utc > strftime('%s','now','-2 days')
   GROUP BY route ORDER BY n DESC"
```

Compare the route distribution and latency averages to the pre-rewrite baseline. Significant regressions (>20% shift in route_fallback rate, >30% TTFW degradation, big jump in `interrupted` count) signal a prompt-induced behavioral problem.

If a regression appears: revert the prompt rewrite (commit 2 of 3), keep the ack_phrase + test changes (commits 1 and 3), and re-investigate.

- [ ] **Step V.6: Update memory if a user preference surfaced**

If during execution you confirmed a user preference that wasn't already in memory (e.g. preferred sed-vs-Edit approach, scope-of-test-fixture hygiene, etc.), save it. If nothing new, skip.

---

## Self-review

**Spec coverage:**

| Spec section | Implementing task |
|---|---|
| Identity rewrite | Task 2.5 |
| Bare-vocative `"Yes?"` | Task 2.1 + 2.3 (literal + comment) + sed in Task 2.10 (prompt examples) |
| Approved register refresh | Task 2.6 |
| Banned register refresh | Task 2.7 |
| Surgical "sir" excision | Task 2.10 + 2.11 (residual hand-fix) |
| Specialist ack_phrases (11 of 12) | Task 1.3 through 1.12 |
| HOW_TO template | Task 1.13 |
| Few-shot exemplar rephrasing | Task 2.10 (sed) + 2.12 (eyeball) |
| Mandatory test update | Task 1.1 |
| Cosmetic test fixture updates | Task 3.1 |
| Verification (suite + grep + live smoke + soak) | V.1–V.5 |

All 11 spec sections mapped to tasks. No gaps.

**Placeholder scan:** no TBDs, no "implement appropriately", no "similar to Task N", no missing code blocks. Each step shows the exact code or command.

**Type consistency:** the `ack_phrase` field name is consistent across Tasks 1.3–1.12. The phrase `"Right away."` is consistent between Task 1.1 (test assertion) and Task 1.3 (desktop spec). The phrase `"On it."` for browser is consistent between Task 1.4 and the existing approved register list (it's already on the approved list, line 1771).

No gaps.
