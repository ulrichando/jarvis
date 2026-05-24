# Skill loop: give the voice supervisor a full use+author skill capability (Hermes parity)

**Status:** spec
**Date:** 2026-05-20
**Why now:** The voice agent already ships a *read-only* skill subsystem (`pipeline/skills_loader.py` + `tools/skill_runner.py`, added 2026-05-11) but it's pull-only and can't author. Hermes's value isn't the file format — it's the closed loop: an always-on awareness index + an authoring tool + a prompt directive that together let the agent *grow its own skills*. This is part of the ongoing Hermes→JARVIS port (cf. `tools/file_safety.py`, ported from Hermes today). User wants the full loop (use + author) in a new JARVIS-native skill library, seeded by recreating ~5 Hermes skills.

## Goal

Turn the existing skill subsystem into a full **use + author** loop, tuned for a real-time voice agent:

1. **Always-on awareness** — a compact `## Skills` index in the supervisor prompt so authored skills actually get used (today they're invisible unless the model calls `list_skills()`).
2. **Authoring** — one `skill_manage(action, …)` tool the supervisor can call to create/patch/edit/delete JARVIS-native skills, gated by an **offer+confirm** conversational protocol.
3. **Seed** — recreate ~5 voice-relevant Hermes skills as JARVIS-native to prove the loop end-to-end; the offer+confirm loop grows the rest over time.

Non-goals (the OUT list): porting all ~170 Hermes skills; any change to `src/cli/`; changing `run_skill`'s body-loader semantics; touching the four monkey-patches, tray, AEC, or memory layer.

## Where it stands today — the existing read-only system

| Piece | File | What it does |
|---|---|---|
| Discovery + registry | `src/voice-agent/pipeline/skills_loader.py` | `Skill` dataclass (`name`/`description`/`when_to_use`/`body`/`path`/`raw_frontmatter`); discovers `skills/<name>/SKILL.md` (bundled) + `~/.jarvis/skills/<name>/SKILL.md` (user, overrides bundled); hand-rolled frontmatter parser (no PyYAML); `SKILLS` singleton; `reload_skills()`; `JARVIS_SKILLS_PATHS` env override for test isolation. |
| Read tools | `src/voice-agent/tools/skill_runner.py` | `list_skills()` (enumerate `name — when_to_use`); `run_skill(name)` (returns the body as a turn-scoped instruction). |
| Prompt mention | `prompts/supervisor.md:286-290` | Describes `list_skills`/`run_skill` as **voice-discoverable only** — no passive index. |
| Shipped skills | `skills/git-status/`, `skills/system-stats/` | 2 example skills. `~/.jarvis/skills/` does not exist yet (0 user skills). |
| Tests | `tests/test_skills_loader.py`, `tests/test_skill_runner.py` | Existing coverage to extend. |

**The three gaps vs. Hermes's loop:** (1) no always-on awareness — skills are invisible until `list_skills()` is called; (2) no authoring — the registry is read-only, there's no `skill_manage` equivalent; (3) no self-improvement directive in the prompt.

## Awareness model — hybrid progressive disclosure (decided)

Three models were considered; **hybrid progressive disclosure** chosen:

- **A. Hybrid (chosen)** — compact always-on index (`name — when_to_use`, one line each) injected into the supervisor prompt; full body on demand via the *existing* `run_skill`; a **light** directive. Hermes's exact shape, mapped onto code that already exists. ~125 tokens for ~6 skills.
- **B. Pure pull** (rejected) — keep `list_skills`-only awareness, add authoring only. Cheapest, but preserves the "model forgets skills exist" weakness — undercuts the authoring loop.
- **C. Full Hermes push** (rejected) — rich index + "scan before every reply" mandatory directive. Max adherence, but makes the supervisor deliberate about skills on *every* turn (incl. "Jarvis, mute"), adding latency and fighting the STAY-IN-SUPERVISOR rule; 2-3× the token cost.

The chosen directive is deliberately **light** — "if a listed skill fits the task, load it with `run_skill(name)` first" — not Hermes's heavy mandatory-scan language, to respect voice latency and STAY-IN-SUPERVISOR.

## Architecture — small, isolated units

```
  READ side (mostly exists)                 WRITE side (new)
  ─────────────────────────                 ─────────────────────────
  skills_loader.py   ── SKILLS ──┐          skills_authoring.py
   (discovery, parse, reload)    │           validate_skill_markdown()
        │                        │           render_skill_md()
        │                        │           create/patch/edit/delete_user_skill()
        ▼                        │                  │ (writes ~/.jarvis/skills/ only,
  _build_skills_index_block()    │                  │  through file_safety, then reload)
   (compact index → prompt)      │                  ▼
        │                        └──────────  tools/skill_runner.py
        ▼                                       skill_manage(action,…)  ← NEW tool
  prompts/supervisor.md                         list_skills() / run_skill()  (unchanged)
   (light use directive +
    offer+confirm author directive)
```

| Unit | File | Change | Responsibility |
|---|---|---|---|
| Discovery/registry | `pipeline/skills_loader.py` | unchanged | Stays pure-read. |
| **Authoring core** | `pipeline/skills_authoring.py` | **NEW** | `validate_skill_markdown()`, `render_skill_md()`, `create_user_skill()`, `patch_user_skill()`, `edit_user_skill()`, `delete_user_skill()`. Pure logic. Depends on loader (parse/reload) + `file_safety` (write guard). |
| **Author tool** | `tools/skill_runner.py` | extend | Add `skill_manage(action,…)` `@function_tool`; thin wrapper over the authoring core; calls `reload_skills()` after a successful write. |
| **Prompt index** | `jarvis_agent.py` | extend | New `_build_skills_index_block()`; append to `initial_instructions` in `_build_initial_prompt_state` (parallel to `memory_block`/`breaker_block`). Register `skill_manage` in the tools list next to `list_skills`/`run_skill`. |
| **Directives** | `prompts/supervisor.md` | extend | Light use directive + offer+confirm authoring/patch/delete directive (exact phrases below). |
| **Seed skills** | `skills/<name>/SKILL.md` | **NEW** ×~5 | Committed, bundled, recreated from Hermes in JARVIS vocabulary. Added via Write + git — **not** by the tool. |

Authoring lives in its own module, *not* in `skills_loader.py`, so the loader keeps its single read-only purpose — write logic can change without touching discovery.

## The `skill_manage` tool

One multi-action tool (Hermes parity; smaller tool surface than two discrete tools; reliable with Sonnet 4.6):

```
skill_manage(action, name, description?, when_to_use?, body?,
             old_string?, new_string?, replace_all?)

  action = "create"  needs: name, description, when_to_use, body
                      → validate → file_safety → atomic write
                        ~/.jarvis/skills/<name>/SKILL.md → reload

  action = "patch"   needs: name, old_string, new_string [, replace_all]
                      → load existing user skill → exact replace
                      → re-validate whole file → write → reload

  action = "edit"    needs: name, body  (full body rewrite; frontmatter preserved
                      unless description/when_to_use also supplied)
                      → re-validate → write → reload

  action = "delete"  needs: name
                      → confirm target is a USER skill (not shipped)
                      → realpath under ~/.jarvis/skills/ → move dir to
                        ~/.jarvis/.skills-trash/<name>-<UTCstamp>/ → reload
```

Returns a short, voice-safe confirmation or a clear one-line error. All actions operate on **user skills only** (`~/.jarvis/skills/`).

## Data flow

**Use (read):** session start → `_build_initial_prompt_state` (`jarvis_agent.py:4977`) → `_build_skills_index_block()` reads `SKILLS` → compact `## Skills` block appended to `initial_instructions` (`jarvis_agent.py:5025`) → supervisor passively aware → on a matching task calls `run_skill(name)` → body returned as turn-scoped instruction → executes with existing tools.

**Author (offer+confirm):** supervisor finishes a hard/iterative task → (per directive) voices "Want me to save that as a skill?" → user says yes → `skill_manage(action="create", …)` → `create_user_skill()` validates → `file_safety` check → atomic write → `reload_skills()` → returns "Saved." Registry updates **immediately** (so `run_skill` sees it this session); the passive prompt index refreshes on the existing rule-reload path / next session (acceptable; matches Hermes's cached-loader behavior).

**Patch / edit:** "the X skill steered me wrong" → offer to fix → on yes, `skill_manage(action="patch"|"edit", …)`.

**Delete:** explicit user request → "Delete the X skill?" → on yes, `skill_manage(action="delete", name="X")` → recoverable move to `.skills-trash`.

## Invariants / load-bearing decisions

1. **Write target is `~/.jarvis/skills/` ONLY.** The tool never writes the bundled repo `skills/` (committed source). Seed skills are added by a human/Claude via file Write + git, never by the tool. Mirrors Hermes (`skill_manage`→`~/.hermes`; in-repo skills via plain file write).
2. **Validate before write; atomic write; rollback on fail.** Ported from `hermes/tools/skill_manager_tool.py::_validate_frontmatter` + limits:
   - name `^[a-z0-9][a-z0-9-]*$`, ≤ 64 chars (`MAX_NAME_LENGTH`) — also the path-traversal defense
   - description ≤ 1024 chars (`MAX_DESCRIPTION_LENGTH`)
   - total file ≤ 100 000 chars (`MAX_SKILL_CONTENT_CHARS`)
   - frontmatter parses (loader's hand-rolled parser, **no PyYAML**) + has `name` + `description` + non-empty body
   - `when_to_use` recommended; falls back to `description` (matches loader behavior)
3. **Reuse `file_safety.write_denial_message()`** exactly like `tools/file_write.py:60`. Same guard, same mic-injection threat model. `~/.jarvis/skills/` is not on the denylist; `~/.jarvis/local-api-token.env` and the voice-agent `.env` are — so the guard already blocks the dangerous escapes.
4. **Delete is recoverable + confined.** Move to `~/.jarvis/.skills-trash/` (outside discovery root → no loader change), refuse shipped skills, realpath-confirm under `~/.jarvis/skills/` before touching. No hard `rm`.
5. **Compact, statically-capped index.** Line = `- <name> — <when_to_use truncated ~100 chars>`. Cap total (~40 skills / ~3 000 chars) with a "…+N more — ask to list skills" overflow line. Empty string when no skills (zero steady-state cost — same convention as `memory_block`/`breaker_block`). The preflight estimator (`tools/token_estimation.py`) already counts the system prompt, so no dynamic feedback loop is needed.
6. **Offer+confirm is enforced by the prompt, not code.** The tool is safe to call anytime (worst case: a recoverable file the user can delete). Delete additionally requires the confirm phrasing per the directive.
7. **Confab-safe.** "Saved."/"Deleted." are backed by the `skill_manage` `tool_result`, satisfying the confab detector (10-message tool-evidence lookback) with no special-casing.
8. **Sanitizer-safe.** Tool results are short confirmations; they flow through the existing tool-name/leak sanitizers like every other tool. The four monkey-patches are untouched.
9. **Offer frequency is calibrated HIGH to respect the user's noise-aversion.** CLAUDE.md is explicit that proactive follow-up offers ("want me to schedule…?") read as noisy. The save-as-skill offer is the same class. The directive therefore: offers only after *genuinely hard, repeatable* tasks, at most once, never repeats a declined offer in-session, and is phrased as one short clause — never a trailing paragraph. If in doubt, don't offer (the user can always say "save that as a skill" unprompted). This threshold should be tuned down further if live capture shows over-offering.

## Prompt directives (exact text to add to `prompts/supervisor.md`)

Use directive (in/near the existing skills section, lines 286-290):
> If the **## Skills** index below lists a skill that fits the user's task, call `run_skill(name)` first and follow its recipe — don't improvise a worse version.

Authoring directive (offer+confirm):
> Rarely — only after a genuinely hard, multi-step task that worked and is clearly *repeatable* — you MAY offer **once**, in one short clause: "Want me to save that as a skill?" Never offer after trivial, one-shot, or conversational turns. Only call `skill_manage(action='create', …)` after the user agrees. If a skill you loaded had wrong steps, offer **once** to fix it and call `skill_manage(action='patch', …)` on yes. Delete a skill only on an explicit request, after confirming by name. Never write a skill behind the user's back, and never repeat an offer the user has already declined this session.

The `═══ NEVER WRITE PROTOCOL SHAPES AS REPLY TEXT ═══` convention already covers leaked tool-call text; no new sanitizer needed.

## Error handling

| Failure | Behavior |
|---|---|
| Validation fail (bad name/oversize/missing field) | Error string, **no file written**; supervisor voices a brief reason. |
| Disk/permission fail | Caught; atomic temp+rename → no partial file; error returned. |
| `file_safety` denial | Returns the denial message; no write. |
| Patch/edit target missing or is a shipped skill | Error listing available **user** skills. |
| Name collision with a shipped skill (create) | Allowed (user overrides shipped per discovery order); tool result **warns** it's shadowing. |
| Delete a shipped skill | Refused with a clear message (shipped skills are read-only source). |
| `reload_skills()` fails after write | File is on disk; registry stale → logged; skill appears next session. |

## Testing (TDD — tests first)

- **NEW `tests/test_skills_authoring.py`** — validator (good/bad frontmatter, oversize, bad/traversal name); `create_user_skill` (correct path+content, atomic, reload picks up); `patch`/`edit` (exact match, missing target, re-validation, shipped-skill refusal); `delete` (moves to `.skills-trash`, refuses shipped, realpath confinement); write-target safety (refuses repo `skills/` + `file_safety`-denied paths). Isolation via `JARVIS_SKILLS_PATHS` → tmp dir; never touches real `~/.jarvis`.
- **NEW `tests/test_skills_index_block.py`** — `_build_skills_index_block` empty/compact/overflow-cap/`when_to_use` truncation.
- **Extend `tests/test_skill_runner.py`** — `skill_manage` happy + error paths per action.
- **Extend `tests/test_skills_loader.py`** — each seed `SKILL.md` parses + passes the new validator.
- Full suite stays green: `cd src/voice-agent && .venv/bin/python -m pytest tests/` (~25 s, 800+ tests).

## Seed skills (~5; final list confirmed against the live tool surface during impl)

Recreated from Hermes equivalents in JARVIS voice + tool vocabulary. **Rule: never author a skill that calls a tool JARVIS lacks.**

| Skill | Hermes source | JARVIS recipe uses |
|---|---|---|
| `web-research` | research/ | JARVIS web tools / researcher subagent |
| `note-capture` | note-taking/ | file write / memory |
| `systematic-debugging` | software-development/ | bash + the debug discipline, voice-scoped |
| `media-control` | smart-home / media | `bash` + dbus/mpris (the loader's own spotify example) |
| `code-review` | software-development/ | `run_jarvis_cli` |

## Verification before "done"

1. pytest green (full suite).
2. Manual end-to-end: `skill_manage(action='create')` a temp skill → confirm it appears in `list_skills` → runs via `run_skill` → (next session/refresh) shows in the prompt index → `skill_manage(action='delete')` → confirm it's gone from `list_skills` and lands in `.skills-trash`.
3. Restart `jarvis-voice-agent.service` only after checking `~/.local/share/jarvis/turn_telemetry.db` (latest `ts_utc` ≥ 60 s old) per the operational rule; otherwise ask first.

## Files touched (summary)

```
NEW   src/voice-agent/pipeline/skills_authoring.py
NEW   src/voice-agent/tests/test_skills_authoring.py
NEW   src/voice-agent/tests/test_skills_index_block.py
NEW   src/voice-agent/skills/<~5 seed skills>/SKILL.md
EDIT  src/voice-agent/tools/skill_runner.py        (+ skill_manage)
EDIT  src/voice-agent/jarvis_agent.py              (_build_skills_index_block + registration)
EDIT  src/voice-agent/prompts/supervisor.md        (use + offer/confirm directives)
EDIT  src/voice-agent/tests/test_skill_runner.py   (extend)
EDIT  src/voice-agent/tests/test_skills_loader.py  (extend)
UNCHANGED  pipeline/skills_loader.py, the 4 monkey-patches, tray, AEC, memory layer
```
