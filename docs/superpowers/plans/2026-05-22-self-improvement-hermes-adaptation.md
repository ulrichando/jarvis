# Self-Improvement: Adapt Hermes's Logic into JARVIS Natively — Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Make JARVIS's (rebuilt) self-improvement loop faithfully reflect Hermes's *actual* review/authoring logic — porting the prompt richness, anti-garbage rules, skill-vs-memory guidance, live skill-awareness, and umbrella-consolidation doctrine — while keeping JARVIS's **conservative** stance and **guarded auto-apply** (user decision 2026-05-22: "rich + conservative").

**Architecture:** Pure prompt/heuristic ports into JARVIS's existing files — no new runtime, no AIAgent fork (JARVIS keeps its one-shot Groq reviewer). Substrate-specific Hermes pieces (forked AIAgent, iteration-nudge counters, full-transcript review) are NOT ported.

**Source (read for exact text) → target:**
- `hermes/agent/background_review.py:45-145` (`_SKILL_REVIEW_PROMPT`) → `src/voice-agent/pipeline/skill_review.py::_REVIEW_PROMPT` (line ~387)
- `hermes/agent/curator.py:330-445` (`CURATOR_REVIEW_PROMPT`) → `src/voice-agent/pipeline/curator.py::_CONSOLIDATION_PROMPT` (line ~633)
- `hermes/agent/prompt_builder.py:179-186` (`SKILLS_GUIDANCE`) → `src/voice-agent/prompts/supervisor.md` + `src/voice-agent/pipeline/prompt_builder.py`

**Hard constraints (every task):**
- KEEP JARVIS's conservative bias — do NOT port Hermes's "be ACTIVE — Nothing-to-save is not the default" eager-authoring line. The autonomous path auto-applies silently with a weak llama-8b reviewer; eager bias would spam the store.
- Do NOT change the auto-apply gating (`autonomous_review_turn` stays as-is; CLI stays double-gated). Curator consolidation stays **suggestion-only** (`JARVIS_CURATOR_CONSOLIDATION` gate unchanged).
- ZERO `hermes` tokens in any ported text (rewrite JARVIS-native).
- Off-by-default invariants and `import jarvis_agent` + full suite must stay green.
- Don't touch `soul.md` (persona/voice). Skill-authoring guidance is OPS → `supervisor.md`.

---

## Task 1: Enrich the turn-review prompt (`skill_review.py::_REVIEW_PROMPT`)

Fold Hermes's `_SKILL_REVIEW_PROMPT` substance into JARVIS's existing prompt, keeping JARVIS's "be CONSERVATIVE / most turns warrant nothing" framing and the JSON-only output contract.

**ADD (adapted JARVIS-native, no hermes tokens):**
1. **Signal list** — name the things that warrant action: user corrected your *style/tone/format/verbosity* (frustration signals like "stop doing X", "too verbose", "just give me the answer", explicit "remember this") → first-class **skill** signals; user corrected your *workflow/approach* → encode as a pitfall/step in the governing skill; a non-trivial technique/fix/workaround emerged; a skill that was used turned out wrong/outdated → patch it.
2. **"Do NOT capture" anti-garbage block** (this is the high-value anti-poison guard — port faithfully): environment-dependent failures (missing binaries, "command not found", fresh-install errors); **negative claims about tools** ("browser tools don't work", "X is broken") — these harden into self-cited refusals; session-specific transient errors that resolved; one-off task narratives. "If a tool failed because of setup state, capture the FIX, never 'this tool doesn't work'."
3. **Skill-vs-memory guidance** — "Memory captures *who the user is* (persona, preferences, durable facts); skills capture *how to do this class of task*. When the user corrects how you handled a task, embed the correction into the skill that governs that task — not just memory."
4. Keep the existing FORBIDDEN meta-paraphrase list + JSON output schema + "be conservative" + the three proposal kinds.

- [ ] **Step 1:** Read `skill_review.py:387-431` (current prompt) + `hermes/agent/background_review.py:45-145` (source).
- [ ] **Step 2:** Write/extend a test in `tests/test_skill_review.py` (or the existing skill-review test file) asserting the prompt now contains the anti-garbage guidance (e.g. `assert "command not found" in _REVIEW_PROMPT or "negative claim" in _REVIEW_PROMPT.lower()`) and still contains "conservative" + the JSON contract. RED first.
- [ ] **Step 3:** Edit `_REVIEW_PROMPT` — add the four blocks above, JARVIS-native. Keep it one f-string with the same `{route}/{subagent}/{user_text}/{jarvis_text}` tail + `OUTPUT:`.
- [ ] **Step 4:** Run `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q -k "skill_review or review"` → green; `grep -i hermes` the file → none.
- [ ] **Step 5:** Commit `feat(self-improve): enrich turn-review prompt with signal list + anti-garbage rules + skill-vs-memory guidance (conservative kept)`.

---

## Task 2: Enrich curator consolidation prompt (`curator.py::_CONSOLIDATION_PROMPT`)

Port Hermes's umbrella-consolidation doctrine; keep it **suggestion-only** (gated by `JARVIS_CURATOR_CONSOLIDATION`, unchanged).

**ADD (JARVIS-native):** frame the pass as umbrella-building not pairwise-dedup ("a library of many narrow one-session skills is a failure; group prefix clusters into class-level umbrellas"); "do NOT use `use=0` as a reason to skip — usage counters are not evidence of value"; the three modes (merge-into-umbrella / new-umbrella / demote-to-reference). Do NOT port the "fewer than 10 archives = stopped too early" aggressive quota (JARVIS's library is small + the reviewer is weaker). Keep output as suggestions (no auto-apply).

- [ ] **Step 1:** Read `curator.py:633-652` (current) + `hermes/agent/curator.py:330-445` (source).
- [ ] **Step 2:** Test in `tests/test_curator.py` asserting the consolidation prompt mentions umbrella/class-level grouping + that consolidation stays suggestion-only (no mutation without `JARVIS_CURATOR_CONSOLIDATION`). RED.
- [ ] **Step 3:** Edit `_CONSOLIDATION_PROMPT`. Keep suggestion-only.
- [ ] **Step 4:** `pytest -k curator` green; `grep -i hermes` none.
- [ ] **Step 5:** Commit `feat(self-improve): umbrella-consolidation doctrine in curator prompt (suggestion-only kept)`.

---

## Task 3: Live skill-awareness — catalog injection + authoring nudge (biggest functional gap)

Right now the live supervisor has `skill_manage`/`skill_view`/`skills_list` wired but is **blind** to its skill library (no catalog in the prompt) and is never told to author/patch. Fix both.

**3a — Skill catalog injection (`pipeline/prompt_builder.py`):** inject a compact catalog (each skill's `name` + `when_to_use`/`description`, from `skills_loader.SKILLS` / the loader's list) into the supervisor prompt so JARVIS knows what exists to load/patch. Cap the size (e.g. names + one-line each, truncate to a char budget); keep it stable within a session (build once, like the memory snapshot) so the prefix cache isn't churned.

**3b — Authoring nudge (`prompts/supervisor.md`, ops section):** add Hermes's `SKILLS_GUIDANCE` adapted JARVIS-native + **framed as silent/off-band**: "After a complex multi-step task, save the approach as a skill via `skill_manage` (do this silently — never narrate tool calls in your spoken reply). When you load/consult a skill and find it wrong or outdated, patch it via `skill_manage`." Must NOT cause tool-call text to leak into TTS (respect the existing "NEVER WRITE PROTOCOL SHAPES AS REPLY TEXT" rule).

- [ ] **Step 1:** Read `pipeline/prompt_builder.py` (find the supervisor-prompt assembly + how the memory block is injected as a model) + `prompts/supervisor.md` + `pipeline/skills_loader.py` (the `SKILLS` data shape: name + when_to_use).
- [ ] **Step 2:** Test in `tests/test_prompt_builder.py` (or similar): a built supervisor prompt contains a skill-catalog section when skills exist, and is empty/absent when none; size is bounded. RED.
- [ ] **Step 3a:** Add a `_build_skill_catalog()` (bounded, stable) + inject it into the prompt assembly.
- [ ] **Step 3b:** Add the silent authoring-nudge block to `supervisor.md` (ops/routing area, NOT soul.md).
- [ ] **Step 4:** `pytest -k "prompt or builder or supervisor"` green; `import jarvis_agent` clean; `grep -i hermes` the touched files → none.
- [ ] **Step 5:** Commit `feat(self-improve): inject skill catalog + silent skill-authoring nudge into supervisor prompt`.

---

## Task 4: Final verification + restart

- [ ] **Step 1:** Full suite `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q` → green (no regressions vs the 2090 baseline + new tests).
- [ ] **Step 2:** `import jarvis_agent` clean; `tests/test_no_duplicate_tools.py` green.
- [ ] **Step 3:** Confirm gating unchanged: `JARVIS_SELF_IMPROVE_DISABLED` still suppresses both; curator consolidation still gated by `JARVIS_CURATOR_CONSOLIDATION`; autonomous auto-apply behavior unchanged (conservative).
- [ ] **Step 4:** `grep -rinE 'hermes' pipeline/skill_review.py pipeline/curator.py pipeline/prompt_builder.py prompts/supervisor.md` → none.
- [ ] **Step 5:** Restart decision — check `turn_telemetry.db` latest `ts_utc`; if >60s idle, restart `jarvis-voice-agent.service` + confirm clean boot. The richer prompts take effect on the next background review / next session prompt build.

---

## Self-review notes
- **Scope coverage:** review-prompt richness (T1) + anti-garbage (T1) + skill-vs-memory (T1) + consolidation doctrine (T2) + live skill-awareness (T3) = all four exploration gaps (G1/G4/G5/G6). G2 (full-transcript) + G7 (support-file packaging) are explicitly OUT (need a telemetry-schema change / skills_authoring extension — separate work).
- **Conservative-decision honored:** no "be ACTIVE" line; auto-apply + consolidation gates unchanged.
- **Risk control:** T3b framed silent/off-band to avoid TTS tool-call leaks; catalog injection bounded + session-stable for prefix-cache safety.
