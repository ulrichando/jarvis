# Voice-agent — close out the 2026-05-08 review

**Status:** design approved 2026-05-09; implement-ready
**Scope:** verify each finding from `2026-05-08-voice-agent-desktop-tauri-review.md` against current `main`, then close the only remaining open caveat (weather specialist's failure phrases vs. `_BAILOUT_SUMMARY_RE`).
**Out of scope:** persona overhaul (drop butler register), voice-tool audit execution, subagent re-enable, broader architecture sweep, desktop-tauri findings. Each gets its own spec.

## Background — verification of the 2026-05-08 review

The review found 1 🔴 + 4 🟡 + 1 ⚠ in the voice-agent. As of 2026-05-09, five of six are already closed in code; the test suite reports `1057 passed, 2 skipped, 0 failed in 24.4s` (39 new tests since the review).

| # | Finding | Location | Status | Closing commit |
|---|---|---|---|---|
| 1 | 🔴 `_NO_TOOL_RETRY_CEILING` cached at module import | `specialists/agent.py` | Fixed via `_no_tool_retry_ceiling()` runtime-read function at line 85-86 | `9a11df9` |
| 2 | 🟡 `_SAVE_CLAIM_RE` extraction-evidence gate missing | `confab_detector.py` | Fixed; regex defined at line 92-106 and consulted at line 281 | `2f62415` |
| 3 | 🟡 `_META_PARAPHRASE_RE` over-rejects hedged facts | `pipeline/memory_extractor.py` | Fixed; narrowed to subject-anchored prefixes at line 103-129 | `2f62415` |
| 4 | 🟡 `_LAST_EXTRACTION_SUCCESS_AT` undocumented mutable global | `pipeline/memory_extractor.py` | Fixed; concurrency comment at line 35-42 | `d10c499` |
| 5 | 🟡 `importlib.reload` test smell | `tests/test_specialist_bailout_2026_05_08.py` | Fixed; reload removed (verified by `grep importlib.reload` returning empty) | (incidental, with #1) |
| 6 | ⚠ Weather specialist's failure phrases not in `_BAILOUT_SUMMARY_RE` | `specialists/weather.py:89,92` + `specialists/agent.py` | **Open — closed by this spec** | (this PR) |

## Problem (finding #6)

`weather.py` instructs the LLM to call `task_done` with two phrases that are not on the `_BAILOUT_SUMMARY_RE` allowlist:

- Line 89: `"I couldn't determine your location — which city did you have in mind?"`
- Line 92: `"I couldn't reach the weather service."`

In practice, both invocations follow at least one real tool call (`get_location` or `bash`) so the no-tool gate already passes. The risk is theoretical: if the LLM ever bails directly to `task_done` without firing a tool first (e.g. parsing the user's request as out-of-scope), the gate refuses, the retry counter increments, and on the third refusal the gate force-bails with the generic "Cannot accomplish — handing back to supervisor" string — losing the weather specialist's user-friendly clarification prompt.

Defensive coding: make the phrases explicit allowlist members so the gate honors a clean exit even in the degenerate path.

## Solution — Approach 3 (hybrid)

1. **Add a narrow regex pattern to `_BAILOUT_SUMMARY_RE`** in `specialists/agent.py`:

   ```
   couldn'?t\s+determine\s+(?:your\s+)?location
   ```

   Matches `"I couldn't determine your location"` and minor variants (`"couldn't determine the location"`, etc.). Doesn't match a confab claim of success because confabs assert action, not denial.

2. **Reword the service-failure phrase in `weather.py`** from `"I couldn't reach the weather service."` to `"Weather service is not connected."` to reuse the existing `(?:extension|tool|service|browser|chrome|firefox)\s+(?:is\s+)?not\s+connected` alternation. Zero new regex surface.

   Two call sites: Rule 4 at line 41-43 and the example dialogue at line 92. Both updated to the new phrase.

The friendly clarification at line 89 is preserved verbatim (the regex addition handles it). Only the service-failure phrase is reworded.

## Code changes

| File | Change | LOC |
|---|---|---|
| `src/voice-agent/specialists/agent.py` | Add 1 alternation to `_BAILOUT_SUMMARY_RE` regex | +1 |
| `src/voice-agent/specialists/weather.py` | Reword Rule 4 (line 41-43) + example at line 92 to `"Weather service is not connected."` | ~2 |
| `src/voice-agent/tests/test_specialist_bailout_2026_05_08.py` | Add 4 new test cases (positive matches + negative regressions) | +~30 |

## Tests

Hybrid TDD per the agreed plan: failing-test-first for the regex tweak.

1. **Positive:** `_BAILOUT_SUMMARY_RE.search("I couldn't determine your location — which city did you have in mind?")` is truthy.
2. **Positive:** `_BAILOUT_SUMMARY_RE.search("Weather service is not connected.")` is truthy (sanity — exercises the existing `service is not connected` pattern).
3. **Negative regression:** `_BAILOUT_SUMMARY_RE.search("I've opened a new tab")` is falsy.
4. **Negative regression:** `_BAILOUT_SUMMARY_RE.search("Done, sir.")` is falsy.

Run sequence: write tests first → confirm test #1 fails → land the regex pattern → confirm all 4 tests pass → run full suite (`cd src/voice-agent && .venv/bin/python -m pytest tests/ -q`) → expect `1061 passed, 2 skipped` (1057 baseline + 4 new tests).

## Verification

- `pytest -k "bailout or weather"` — focused run
- `pytest tests/` — full suite, expect 1061/1063
- Manual eyeball of `_BAILOUT_SUMMARY_RE` after edit: regex still scans cleanly for human reading; no inadvertent over-broadening (each new alternative is anchored on `couldn't`/`is not connected`, neither of which a confab uses).

## Risks

- **False positive on non-weather specialist saying "couldn't determine your location"**: the only specialist that does this is `weather` itself (gated off by default). Other specialists don't use the phrase. Risk is negligible.
- **Future specialist adopts the wording for an unrelated bailout**: by definition, that's the desired behavior — adding a phrase to the allowlist *is* permission to bail with it. If a confab ever claims "I couldn't determine your location" as a fake-success line, it would mean the LLM is denying the action it claims to have done — incoherent, unlikely.

## Commit shape

Single PR, branch `feat/ext-browser-control-v3` (current). Two commits:

1. `test(voice-agent): add weather bailout-phrase tests (failing)` — per TDD
2. `fix(voice-agent): allowlist weather bailout phrases in _BAILOUT_SUMMARY_RE`

No Co-Authored-By trailer, no Claude attribution per CLAUDE.md.
