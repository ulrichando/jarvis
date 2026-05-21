# JARVIS Soul — design (Hermes-style identity layer)

**Date:** 2026-05-20
**Status:** approved (design); implementation pending
**Scope:** `src/voice-agent/` only

## Motivation

Hermes gives its agent a "soul" via `SOUL.md` — one user-editable file that becomes
**slot #1 of the system prompt** (the primary identity), cleanly separated from
operational config (`AGENTS.md`), with injection-scanning, truncation, a hardcoded
fallback, and a `/personality` overlay.

JARVIS already has a *richer* persona than Hermes, but it is **tangled**: identity
lives inside a 1417-line `prompts/supervisor.md` monolith that mixes "who JARVIS is"
with all tool-routing/handoff/ops rules. There is no single, clean, human-editable
identity layer.

This design ports Hermes' clean identity layer onto JARVIS's existing reality —
**without rewriting the (battle-tuned) persona content**. It is a structural move,
not a personality transplant.

## Tier model

| Tier | File | Mutable | Role |
|---|---|---|---|
| **Soul** (NEW) | `prompts/soul.md` | git-only | Identity, voice, character — **slot #1** |
| Invariants | `prompts/anchor_rules.md` | git-only | Immutable hard constraints — **unchanged** |
| Operations | `prompts/supervisor.md` | git-only | Tool routing / handoffs / tool mechanics — **slimmed** |
| Volatile id | `runtime_id_block` (built per session) | per-session | Active provider/model/date/session — **unchanged** |

Mirrors Hermes' stable-identity / ops / volatile split.

## Extraction map

**Dividing test:** *would this rule change if JARVIS's personality changed (→ soul)
or if its tools changed (→ supervisor)?*

### Moves to `soul.md` (voice / character / values)
`WHO YOU ARE` · `SUBSTANTIVE ENGAGEMENT` · `TASK BREVITY` · `CALIBRATED UNCERTAINTY` ·
`WHEN INPUT IS UNCLEAR` · `PUSH BACK WHEN WARRANTED` · `DIPLOMATICALLY HONEST + HANDLING
CRITICISM` · `TREATING ULRICH AS AN ADULT` · `TECHNICAL DEPTH` · `VOICE TEXTURE` ·
`CURIOSITY + ENGAGING WITH ULRICH'S DOMAINS` · `NO HEDGING. ACT, OR STAY SILENT.` ·
`AMBIGUITY OWNED + ETHICAL ENGAGEMENT` · `LENGTH + NO PREAMBLE` · `ACKNOWLEDGMENT
VOCABULARY` · `FEW-SHOT EXEMPLARS`

### Stays in `supervisor.md` (ops / safety / mechanism)
`NEVER WRITE THESE AS REPLY TEXT` (output-format safety; also anchor A-0004) ·
`HANDOFF DISCIPLINE` · `IS THIS DIRECTED AT YOU?` · `WAKE-VOCATIVE` · `DECIDING THE
RESPONSE` · `ROUTE TAGS` · `TOOL ROUTING` · `set_screen_share` · `computer_use vs
desktop` · `PLAN MODE` · `TASK TRACKING` · `CLARIFYING WITH OPTIONS` · `BACKGROUND
MONITORS` · `GIT WORKTREES` · `CODE SEARCH` · `NEVER DELEGATE UNDERSTANDING` · `AFTER A
TOOL OR HANDOFF` · `POST-HANDOFF HONESTY` · `ACTION HONESTY` · `INTERRUPTION HANDLING` ·
`MUTE / WAKE-UP` · all `MEMORY` / `STALE PRIOR-SESSION` / `PROACTIVE CAPTURE` / `YOU
HAVE MEMORY` / `SESSION MEMORY` · `LOCATION QUESTIONS` · `AMBIGUOUS REQUESTS` ·
`TOOL-CALL CHAINING` · `MULTITASK / TASK FRAMING` · `BEHAVIORAL LEARNING` · `USER
PREFERENCES`

### Borderline calls
- `DECIDING THE RESPONSE`, `ROUTE TAGS` carry voice *and* routing — **stay** in
  supervisor (routing scaffolds); they may cross-reference soul rather than duplicate.
- `ACTION HONESTY`, `POST-HANDOFF HONESTY` are coupled to the confab-detector —
  **stay** as mechanism; the honesty *value* already moves via `DIPLOMATICALLY HONEST`.

The move is **faithful**: section text is relocated verbatim, relative order preserved.

## `soul.md` format rules
- Use the existing `═══ SECTION ═══` header style (no `##` prefix).
- **Must NOT** use the reserved learned-rules tier headers
  `## ═══ ANCHOR|CORE|ACCEPTED|STAGED|ARCHIVED ═══` (matched by
  `pipeline/evolution/schema.py::TIER_HEADER_RE`). The moved section names don't
  collide, so this is automatic; documented to prevent future drift.

## Loader — `load_soul()` (in `pipeline/prompt_builder.py`, next to `load_learned_rules`)

```
SOUL_PATH_DEFAULT  = _PROMPTS_DIR / "soul.md"            # git-tracked canonical
SOUL_PATH_OVERRIDE = ~/.jarvis/SOUL.md                   # optional user override
MAX_SOUL_CHARS     = 16000                               # truncation cap
DEFAULT_SOUL       = "<hardcoded minimal identity>"      # last-resort fallback
```

Resolution order:
1. `~/.jarvis/SOUL.md` exists & non-empty → scan for injection → if blocked, fall
   through; else truncate + use (replaces default).
2. `prompts/soul.md` exists & non-empty → scan + truncate + use.
3. Else `DEFAULT_SOUL`.

`_scan_soul()` + `_truncate_soul()` port Hermes'
`prompt_builder._scan_context_content` (threat-pattern regexes + invisible-unicode
check) and `_truncate_content`. A blocked override logs a warning and falls back to
the git default — flagged content is never injected.

Read **once at import / session start** (same lifecycle as `supervisor.md`), so the
upstream prompt cache stays warm; no per-turn cost.

## Assembly wiring (`jarvis_agent.py`)

```
SOUL = load_soul()                                       # near line 927
JARVIS_INSTRUCTIONS = (_PROMPTS_DIR / "supervisor.md").read_text(...)   # ops-only now
...
# line 4992:
instructions_prefix = SOUL + "\n\n" + JARVIS_INSTRUCTIONS + runtime_id_block
```

Soul leads the assembled prompt → identity-first, matching Hermes and preserving the
"persona at top" invariant.

## Evolution-system interaction (verified)

`pipeline/evolution/store.py` writes **only** `~/.jarvis/learned_rules.md` (atomic
temp+replace) and refuses anchor-tier writes. `supervisor.md` and `soul.md` are
categorically outside its write path. **No evolution code change is required** — the
earlier "add soul.md to a never-touch denylist" framing was incorrect; no such denylist
exists. The only requirement is the format rule above.

## Test impact (verified)

- **Update one test:** `tests/test_subagents_health.py` (the `WHO YOU ARE` / register
  test, ~lines 365-390) asserts those strings are `in JARVIS_INSTRUCTIONS` at offset
  `< 200`. Repoint at the loaded `SOUL` (and assert the supervisor assembles `SOUL`
  first). Its true invariant — persona at top of the system prompt — is preserved.
- **No other test** couples a moved section (verified by grep across `tests/`).
  `test_memory_anchor.py` references memory sections that **stay** in supervisor — unaffected.
- **New `tests/test_soul.py`:** resolution order (override > default > fallback);
  malicious override is blocked and falls back; truncation cap; `DEFAULT_SOUL` when
  both files missing; `soul.md` uses no reserved tier headers; **prompt-parity** —
  every moved section header is present in `SOUL` and absent from `supervisor.md`.

## Out of scope (YAGNI / project rules)
- No persona **content** rewrite — faithful relocation only. Depth-enrichment is a
  separate later pass.
- `/personality` temporary-overlay feature — deferred.
- Cross-surface (CLI / desktop) — `src/cli/` is off-limits; desktop is separate.
  `soul.md` is designed so it *could* be shared later, but no other tree is touched.
- Subagent prompts — unchanged; soul governs the supervisor (the voice the user hears).

## Verification plan
- New + updated tests pass.
- Full suite green: `cd src/voice-agent && .venv/bin/python -m pytest tests/` (~25s).
- No service restart required for the change. If validated live, check
  `~/.local/share/jarvis/turn_telemetry.db` recency first (don't restart within 60s of
  an active turn).

## Risks & mitigations
- **Reordering shifts behavior** even with identical content → keep soul first
  (identity-first is correct) and preserve relative order of moved sections.
- **Splice order** → confirm the full `initial_instructions` assembly order
  (learned_rules / memory / breaker) when editing; soul prepends to `instructions_prefix`.
- **Truncation cap too low** clips a long user override → start at 16k chars (the moved
  persona is well under this); log when truncation fires.
