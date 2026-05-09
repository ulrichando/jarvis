# Voice-agent review closeout — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the only remaining open finding from the 2026-05-08 voice-agent review — add weather specialist's two failure phrases to the `_BAILOUT_SUMMARY_RE` allowlist (one via narrow regex addition, one via reusing an existing pattern through prompt rewording).

**Architecture:** The specialist tool gate (`specialists/agent.py`) refuses `task_done` calls when no real tool fired during the handoff, unless the summary matches `_BAILOUT_SUMMARY_RE`. Weather's two failure phrases currently aren't on that allowlist, so a degenerate path (LLM bails before firing `get_location` or `bash`) would force-bail with a generic message instead of a clean exit. Defensive coding: make the allowlist explicit.

**Tech Stack:** Python 3.13, pytest, livekit-agents, regex via `re.compile(..., re.I | re.X)`.

**Spec:** [docs/superpowers/specs/2026-05-09-voice-agent-review-closeout-design.md](../specs/2026-05-09-voice-agent-review-closeout-design.md)

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/voice-agent/specialists/agent.py` | Defines `_BAILOUT_SUMMARY_RE` and the no-tool gate logic in `task_done` | Add 1 alternation to the regex |
| `src/voice-agent/specialists/weather.py` | Weather subagent prompt + tool factory | Reword Rule 4 and the service-failure example dialogue |
| `src/voice-agent/tests/test_specialist_bailout_2026_05_08.py` | Bailout-regex + retry-ceiling tests | Add 2 parametrized cases to `test_bailout_phrases_pass_gate` |

No new files. No deletions.

---

## Task 1: Add failing test cases for weather bailout phrases

**Files:**
- Modify: `src/voice-agent/tests/test_specialist_bailout_2026_05_08.py:69-84`

**Context:** `test_bailout_phrases_pass_gate` is a parametrized integration test that exercises `RegistrySpecialist.task_done` with each summary string and asserts the gate lets it through (transitions to supervisor instead of refusing). Adding two new cases covers the weather phrases. The existing list already covers confab regressions (`Done, sir.`, `Opened a new tab, sir.`), so no negative test additions needed.

- [ ] **Step 1.1: Add the 2 new parametrized cases**

In `test_specialist_bailout_2026_05_08.py`, find the parametrize list at lines 69-84:

```python
@pytest.mark.parametrize("summary", [
    # Existing allowlist (regression)
    "user changed topic",
    "not a desktop task",
    "wrong specialist",
    "cannot accomplish — handing back to supervisor",
    "needs the browser specialist",
    # New 2026-05-08 environmental phrasings
    "Google Chrome isn't available, sir.",
    "extension not connected, sir.",
    "browser is not connected, sir.",
    "tool unavailable, sir.",
    "service offline.",
    "bridge disconnected.",
    "chrome unavailable.",
])
```

Append two new entries inside the list (before the closing `])`):

```python
    # New 2026-05-09 weather specialist phrasings (review closeout)
    "I couldn't determine your location — which city did you have in mind?",
    "Weather service is not connected.",
```

The trailing comment-block-then-string pattern matches the existing style (see lines 76 and 70).

- [ ] **Step 1.2: Run tests, confirm one of the two new cases fails**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_specialist_bailout_2026_05_08.py::test_bailout_phrases_pass_gate -v 2>&1 | tail -25
```

Expected: 14 cases total. The case `"I couldn't determine your location — which city did you have in mind?"` should **FAIL** with the gate refusing the summary (transitions back to specialist with `"REFUSED"` in the message instead of returning to supervisor). The case `"Weather service is not connected."` should **PASS** because the existing `(?:extension|tool|service|...)\s+(?:is\s+)?not\s+connected` alternation already matches.

If both pass: the regex was already permissive enough — re-read `_BAILOUT_SUMMARY_RE` and confirm the failing case is genuinely failing before continuing.
If both fail: something else is wrong — stop and investigate (probably `service is not connected` doesn't match the way I expected; verify by hand).

- [ ] **Step 1.3: Commit the failing test**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/tests/test_specialist_bailout_2026_05_08.py
git commit -m "test(voice-agent): add weather bailout-phrase cases (one fails — drives next fix)"
```

No Co-Authored-By trailer per CLAUDE.md.

---

## Task 2: Add regex pattern to `_BAILOUT_SUMMARY_RE`

**Files:**
- Modify: `src/voice-agent/specialists/agent.py:46-69` (the `_BAILOUT_SUMMARY_RE` definition)

**Context:** Add one alternation to the regex that matches the weather location-failure phrase. The pattern is anchored on `couldn't determine` + `location` so it can't accidentally match confab claims.

- [ ] **Step 2.1: Add the new alternation**

In `specialists/agent.py`, locate `_BAILOUT_SUMMARY_RE` (search for the literal string `_BAILOUT_SUMMARY_RE = re.compile`). The current regex ends with these final environmental-gate alternations:

```python
      | (?:extension|tool|service|browser|chrome|firefox)\s+(?:is\s+)?(?:not\s+connected|unavailable|offline|not\s+available)
      | (?:bridge|extension)\s+disconnected
      | google\s+chrome\s+isn'?t\s+available
    )
    """
)
```

Add a new alternation between `google\s+chrome\s+isn'?t\s+available` and the closing `)`. The result should be:

```python
      | (?:extension|tool|service|browser|chrome|firefox)\s+(?:is\s+)?(?:not\s+connected|unavailable|offline|not\s+available)
      | (?:bridge|extension)\s+disconnected
      | google\s+chrome\s+isn'?t\s+available
      # 2026-05-09 — weather specialist's location-failure phrasing
      | couldn'?t\s+determine\s+(?:your\s+|the\s+)?location
    )
    """
)
```

The pattern requires the literal word `location` after `determine` — narrow enough that no confab claim could accidentally match (a confab claims success; this phrasing is a denial).

- [ ] **Step 2.2: Run the failing test, confirm it passes**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_specialist_bailout_2026_05_08.py::test_bailout_phrases_pass_gate -v 2>&1 | tail -25
```

Expected: all 14 cases PASS, including `"I couldn't determine your location — which city did you have in mind?"`.

- [ ] **Step 2.3: Run the full voice-agent test suite to confirm no regression**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/ -q --tb=short 2>&1 | tail -15
```

Expected: `1059 passed, 2 skipped` (1057 baseline + 2 new parametrized cases). Zero failures.

If any pre-existing test fails: bisect — your regex change shouldn't affect anything other than the bailout suite. Read the failure, decide whether your change is the cause. If unrelated, surface it but do not fix in this PR (out of scope).

- [ ] **Step 2.4: Commit the fix**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/specialists/agent.py
git commit -m "fix(voice-agent): allowlist weather location-failure phrase in _BAILOUT_SUMMARY_RE

The weather specialist's 'I couldn't determine your location' bailout
phrase wasn't on the no-tool-gate allowlist. In the degenerate path
where the LLM bails before firing get_location, the gate would refuse
3× then force-bail with a generic message — losing the user-friendly
'which city did you have in mind?' clarification. Closes the last
open finding from the 2026-05-08 voice-agent review.

Pattern is anchored on 'couldn't determine ... location' so confab
success claims can't accidentally match."
```

---

## Task 3: Reword `weather.py` service-failure phrase to reuse existing pattern

**Files:**
- Modify: `src/voice-agent/specialists/weather.py:41-43` (Rule 4)
- Modify: `src/voice-agent/specialists/weather.py:91-92` (example dialogue)

**Context:** The phrase `"I couldn't reach the weather service."` appears in two places: in Rule 4's instruction text and in an example dialogue. Reword both to `"Weather service is not connected."` so they hit the existing `(?:extension|tool|service|browser|chrome|firefox)\s+(?:is\s+)?not\s+connected` alternation. No regex change needed for this one.

- [ ] **Step 3.1: Reword Rule 4**

In `weather.py`, locate Rule 4 (search for `4. **HANDLE ERRORS HONESTLY.`). The current text is:

```
4. **HANDLE ERRORS HONESTLY.** If curl fails or returns junk, say
   "I couldn't reach the weather service." If get_location returns
   "Location unavailable", ask which city via task_done.
```

Replace with:

```
4. **HANDLE ERRORS HONESTLY.** If curl fails or returns junk, say
   "Weather service is not connected." If get_location returns
   "Location unavailable", ask which city via task_done.
```

The only change is `"I couldn't reach the weather service."` → `"Weather service is not connected."`. Everything else is identical.

- [ ] **Step 3.2: Reword the example dialogue**

In `weather.py`, locate the example near the bottom (search for `User: bash returns error:`). The current text is:

```
User: bash returns error:
You: task_done("I couldn't reach the weather service.")
```

Replace with:

```
User: bash returns error:
You: task_done("Weather service is not connected.")
```

- [ ] **Step 3.3: Run the full voice-agent test suite**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/ -q --tb=short 2>&1 | tail -15
```

Expected: `1059 passed, 2 skipped`. The reword is a prompt-text change only; no test should break. If anything fails, the rewording broke a test that's pinning weather's prompt content (unlikely — search for `couldn't reach` in tests/ to be sure).

- [ ] **Step 3.4: Commit the prompt change**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/specialists/weather.py
git commit -m "chore(weather): reword service-failure to reuse 'service is not connected' pattern

Aligns weather specialist's bash-failure phrase with the existing
_BAILOUT_SUMMARY_RE allowlist's '(?:extension|tool|service|...) is
not connected' alternation. No regex change needed; just a prompt
edit at Rule 4 and the example dialogue.

Closes the last text in the 2026-05-08 voice-agent review."
```

---

## Verification — final sanity pass

After all three tasks land:

- [ ] **Step V.1: Confirm full suite green**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: `1059 passed, 2 skipped in ~25s`.

- [ ] **Step V.2: Confirm three commits land cleanly**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git log --oneline -4
```

Expected: most recent four commits are
1. `chore(weather): reword service-failure to reuse 'service is not connected' pattern`
2. `fix(voice-agent): allowlist weather location-failure phrase in _BAILOUT_SUMMARY_RE`
3. `test(voice-agent): add weather bailout-phrase cases (one fails — drives next fix)`
4. `docs(specs): voice-agent review closeout — verify 5/6 fixes shipped, design weather-bailout closure`

- [ ] **Step V.3: Confirm no Co-Authored-By trailers were added**

```bash
git log -3 --format="%H %s%n%b" | grep -i "co-authored\|claude code\|🤖"
```

Expected: no output. If anything matches, amend the commits to remove the offending lines.

- [ ] **Step V.4: Update memory if any user preference surfaced**

If any user preference was confirmed during this session that wasn't already in memory (e.g. preference for the hybrid TDD level), save it. If nothing new, skip.

---

## Self-review

**Spec coverage:** spec defines two changes (regex addition for location phrase, prompt rewording for service phrase). Task 2 covers the regex addition; Task 3 covers the rewording. Task 1 covers the test additions. All three pieces of the spec are mapped.

**Placeholder scan:** no TBDs, no "appropriate error handling", no "similar to Task N", no missing code blocks. Each step shows the literal code or command.

**Type consistency:** the regex pattern name `_BAILOUT_SUMMARY_RE` is consistent across Tasks 1 and 2. The phrase `"Weather service is not connected."` is consistent between Task 1's parametrize entry and Task 3's prompt rewording. The phrase `"I couldn't determine your location — which city did you have in mind?"` is consistent between Task 1's parametrize entry and Task 2's regex match (the regex matches a substring of this phrase).

No gaps.