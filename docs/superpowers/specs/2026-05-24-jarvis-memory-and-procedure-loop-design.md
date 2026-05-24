# JARVIS Memory + Procedure Loop — Design Spec

> Created 2026-05-24. **Plane 1 + 2** of the three-plane self-modification
> architecture. **Plane 3** (source-code self-mod via CLI delegation) is a
> follow-up spec, not this one.

## Context

JARVIS has had a Hermes-adapted self-improvement loop wired since the
2026-05-22 commits (`feat(skills): autonomous self-improvement — auto-fire
review+curator off the turn boundary`). The loop fires on every turn, runs
an LLM reviewer, and has a fully-wired apply path that can write skills,
patch skills, **and write to MEMORY.md / USER.md** via the existing
`file_memory.add()` surface (see [pipeline/skill_review.py:600-609](../../../src/voice-agent/pipeline/skill_review.py#L600-L609)).

Despite the infrastructure being in place, the user-facing behaviour is:

- *"Jarvis, remember I prefer X"* → JARVIS replies *"got it"* and writes
  nothing.
- *"Jarvis, do you remember Y?"* → JARVIS replies *"this conversation
  just started"* (recall confabulation).
- `~/.jarvis/memories/` contains only stale `.lock` files; MEMORY.md and
  USER.md don't exist on disk.

This spec documents the structural fix.

## Evidence summary (gathered 2026-05-24)

| Claim | Evidence |
|---|---|
| Memory tool never invoked in 14 days | `grep "\[memory\]" voice-agent.log{,.gz}` → 0 hits. Handler at `tools/memory.py:81` logs every call. |
| Reviewer loop fires per turn | `[skill_review] autonomous applied skill_create ok=True` for 2 turns on 2026-05-22 20:37–20:38. Wiring at `jarvis_agent.py:5241` via `fire_self_improvement(_TurnSnapshot)`. |
| `JARVIS_SKILL_REVIEW_APPLY` is OFF in production | `systemctl show jarvis-voice-agent.service -p Environment` lists no SKILL_REVIEW key; `~/.local/share/jarvis/logs/skill_review/` does not exist. |
| Reviewer prompt steers "remember this" → SKILL, not memory | `pipeline/skill_review.py:405-410` literal text: *"an explicit 'remember this' [is a] **FIRST-CLASS skill signal — not just memory signals**".* |
| Existing 2 skills are low-value extractions | `~/.jarvis/skills/dedicated-voice-appliance/SKILL.md` and `design-fixed-voice-interface/SKILL.md` — single-conversation narrative extracts, not reusable procedures. |
| Confab detector handles tool-claims, not save-claims | `confab_detector.py` docstring: *"recurring failure: assistant turn says 'A new tab is open.' when no tool actually fired"*. No save-promise regex class. |

## Goals

After this spec ships, the following turns work:

1. *"Jarvis, remember I'm allergic to fish"* → `USER.md` gains one entry, audible *"got it"* reply (no announcement of the tool call).
2. *"Jarvis, save this process: deploy = run tests then push then check CI"* → `PROCEDURES.md` gains a named entry.
3. *"Jarvis, deploy the app"* (after #2) → JARVIS reads the procedure and either announces the steps or executes them with a confirmation gate.
4. *(After a multi-step successful task)* → JARVIS offers *"want me to keep these steps as 'X'?"* → user yes → procedure saved.
5. *"Jarvis, do you remember Y?"* → JARVIS calls `recall()` (Honcho) or reads `MEMORY.md` and answers from real state — never *"this conversation just started"*.
6. *"Jarvis, I'll remember that"* without a memory tool call → confab detector flags + rejects the turn from chat_ctx, identical to the existing tool-claim guard.

## Non-goals

- **Plane 3 (source-code self-mod)** — separate spec.
- **Reviewer LLM upgrade** (llama-8b → Haiku 4.5 / Sonnet 4.6) — known weakness from the 2 junk skills, but a cost decision outside this spec. Spec A.1 follow-up.
- **Honcho deepening** — Honcho stays as the auto-sync episodic backstop. No new Honcho write surface for the supervisor; `recall()` remains the only Honcho touchpoint.
- **soul.md edits** — per the 2026-05-22 hermes-adaptation plan: *"Don't touch soul.md (persona/voice). Skill-authoring guidance is OPS → supervisor.md."* This spec respects that line.
- **Auto-extractor restoration** — the 2026-05-21 retired `pipeline.memory_extractor` is not coming back. Auto-extraction now lives in `autonomous_review_turn`; trying to add a parallel extractor would recreate the 22% TV-audio hallucination problem.

## Sourced principles

The standards this design is measured against:

- **[Anthropic — Memory cookbook](https://github.com/anthropics/anthropic-cookbook/tree/main/skills/memory_management)**: deliberate, structured writes; recall-first when context allows; treat the memory store as ground truth, not buffer.
- **[OpenAI Model Spec — 2025-04-11](https://model-spec.openai.com/2025-04-11.html)** § Memory: be transparent about what is/isn't remembered; allow user deletion; never silently store conversational asides as durable facts.
- **EU AI Act, Article 50** (enforceable Aug 2026 — voice interface obligation already covered in `soul.md`): users must know they're talking to AI; by extension, memory writes must be inspectable + correctable.
- **[GDPR Article 17 — Right to erasure](https://gdpr-info.eu/art-17-gdpr/)**: PII must be deletable on request. Memory store contains PII; deletion path must exist + be documented.
- **[Anthropic — Claude's character](https://www.anthropic.com/research/claude-character)**: anti-sycophancy — JARVIS should not invent memories. *"I'll remember that"* without a tool call = lying to the user. Track 3 (save-confab guard) directly enforces this.
- **Memory-quality findings memo, 2026-05-20**: rich-get-richer `use_count` lock-in; 22% TV-audio hallucinations; contradictions slip; hybrid retrieval didn't beat LIKE. These prior findings shape the conservative-default + reject-narration stances kept from the existing `_REVIEW_PROMPT`.

## Capability surface — ground truth

What memory infrastructure exists today vs. what this spec adds:

| Layer | Current | Post-Spec-A |
|---|---|---|
| Deliberate-write tool | `memory(action ∈ add/replace/remove/read, target ∈ memory/user)` | + target `procedure`, + `name` param when target=procedure |
| File-backed stores | MEMORY.md (2,200 chars) + USER.md (1,375 chars). **Both missing on disk — only stale `.lock` files exist.** | + PROCEDURES.md (8,000 chars). All three present after first write. |
| Autonomous reviewer | `pipeline/skill_review.py`, runs per turn, **propose-only** by default | Same wiring; prompt rewritten (Track 5); PROPOSAL_KIND extended (Track 2); APPLY=1 set last (Track 6) |
| Trigger discipline | Soft tool description ("DO save when…") — supervisor ignores it | + regex force-inject for explicit phrases (Track 1) |
| Recall surface | `recall(query)` → Honcho (`http://127.0.0.1:8000`, self-hosted) | Unchanged + auto-inject of matching procedure (Track 2.5) |
| Confab guard | `confab_detector.py` — tool-claim regex class only | + save-claim regex class (Track 3) |
| Curator | `pipeline/curator.py`, suggestion-only consolidation (gated `JARVIS_CURATOR_CONSOLIDATION`) | Unchanged. Will dedupe new memory + procedure writes. |
| Audit trail | `~/.local/share/jarvis/logs/skill_review/{stamp}/run.json` (path exists in code; **directory does not exist yet** because APPLY=OFF means no runs reported) | + 4 telemetry columns on `turns`; + new log lines under `jarvis.memory_loop` |
| Kill switches | `JARVIS_SELF_IMPROVE_DISABLED`, `JARVIS_SKILL_REVIEW_APPLY`, `JARVIS_CURATOR_*`, `JARVIS_CONFAB_DETECTOR` | + `JARVIS_SAVE_TRIGGER_LIVE`, `JARVIS_RECALL_TRIGGER_LIVE`, `JARVIS_PROCEDURE_CAPTURE_DISABLED`, `JARVIS_CONFAB_SAVE_DISABLED` |

## Architecture — seven deltas

```
┌─────────────────────────────────────────────────────────────────────┐
│  USER UTTERANCE (STT)                                                │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
            ┌──────────────────────────┐
            │ on_user_turn_completed   │
            │ jarvis_agent.py          │
            └───────────┬──────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
  ┌────────────┐  ┌─────────────┐  ┌───────────┐
  │ TRACK 1    │  │ STAY-IN-    │  │ KILL-     │
  │ regex      │  │ SUPERVISOR  │  │ PHRASE    │
  │ force-     │  │ classifier  │  │ regex     │
  │ trigger    │  │             │  │ (exists)  │
  │ (NEW)      │  │             │  │           │
  └─────┬──────┘  └──────┬──────┘  └────┬──────┘
        │                │              │
        ▼                ▼              ▼
    inject system    supervisor    interrupt path
    message into     inference     (existing)
    chat_ctx
        │
        ▼
  ┌─────────────────────────────────────┐
  │ SUPERVISOR (Sonnet 4.6 / Haiku 4.5) │
  │ calls memory() / recall() / etc.     │
  └──────────────┬──────────────────────┘
                 │
                 ▼
       ┌────────────────────┐
       │ TRACK 3            │
       │ confab_detector    │      ← NEW: save-claim regex class
       │ (EXTENDED)         │
       └─────────┬──────────┘
                 │ turn accepted?
                 ▼
       ┌────────────────────┐
       │ turn_telemetry +    │
       │ chat_ctx append     │
       └─────────┬──────────┘
                 │
                 ▼
       ┌────────────────────┐         ┌─────────────────────────┐
       │ fire_self_improve  │────────►│ autonomous_review_turn  │
       │ (background task)  │         │ TRACK 4 (NEW): success  │
       └────────────────────┘         │ capture → procedure     │
                                      │ proposal                │
                                      └─────────┬───────────────┘
                                                │
                                                ▼
                                      ┌─────────────────────────┐
                                      │ review LLM (llama-8b)   │
                                      │ TRACK 5 (PROMPT FIX):   │
                                      │ "remember" → memory     │
                                      │ TRACK 2 (NEW): procedure│
                                      │ as PROPOSAL_KIND        │
                                      └─────────┬───────────────┘
                                                │
                                                ▼
                                      ┌─────────────────────────┐
                                      │ apply_proposal          │
                                      │ TRACK 6: JARVIS_SKILL_  │
                                      │ REVIEW_APPLY=1 flip     │
                                      │ TRACK 7: junk skill GC  │
                                      └─────────┬───────────────┘
                                                │
                                                ▼
                                ┌─────────┐  ┌─────────┐  ┌───────────┐
                                │ USER.md │  │MEMORY.md│  │PROCEDURES │
                                │         │  │         │  │.md (NEW)  │
                                └─────────┘  └─────────┘  └───────────┘
```

Seven deltas. Each is independently testable and reversible.

| # | Track | What | File(s) | Risk |
|---|---|---|---|---|
| 1 | Track 5 | **Fix reviewer prompt — memory vs skill split.** Explicit save phrases route to `kind=memory`. Style/tone corrections stay `kind=skill`. | `pipeline/skill_review.py::_REVIEW_PROMPT` (lines ~387-471) | Low — prompt edit, gated by reviewer LLM emission |
| 2 | Track 1 | **Force-trigger via regex.** `on_user_turn_completed` matches explicit save/recall phrases → inject system message into chat_ctx pre-inference. | `jarvis_agent.py::JarvisAgent.on_user_turn_completed` (definition at line 3350) | Medium — false positives on figurative speech; supervisor judgment + shadow mode mitigate |
| 3 | Track 2 | **Add `procedure` PROPOSAL_KIND** + validate + apply path. New file_memory target `procedure` → `PROCEDURES.md`. | `pipeline/skill_review.py`, `pipeline/file_memory.py` | Low — extends existing infrastructure |
| 4 | Track 2.5 | **Success-capture in `autonomous_review_turn`** — gate criteria, trajectory enrichment, end-of-turn offer. | `pipeline/skill_review.py`, `jarvis_agent.py` | Medium — voice-noisy if gate is wrong; gate tuning soaks |
| 5 | Track 3 | **Save-claim regex in `confab_detector`.** Patterns: `i'?ll remember`, `i'?ve saved`, etc. Combined with no memory tool call → reject turn. | `confab_detector.py` | Low — additive to existing detector; shadow before live |
| 6 | Track 6 | **Flip `JARVIS_SKILL_REVIEW_APPLY=1`** in service env. **Last** — after (1)/(5) land. | `setup/systemd/jarvis-voice-agent.service` | High if premature — generates junk like the 2 existing skills |
| 7 | Track 7 | **Delete 2 junk skills.** `rm -rf` the existing low-quality entries. Curator handles future quality. | filesystem op | None |

## Detailed design

> **Track naming convention.** Track numbers (1–7) below refer to the
> "Track" column in the architecture table above. They do NOT match the
> rollout-order numbers in the "Rollout sequencing" section near the
> end of this spec. Track numbers are stable identity; rollout-order
> numbers are time-ordering.

### Track 1 — Force-trigger via regex (NEW)

**Problem.** The reviewer LLM is too weak (llama-3.1-8b-instant) and too
conservative to reliably emit `kind=memory` proposals for explicit save
phrases. The supervisor (Sonnet 4.6 / Haiku 4.5) IS capable, but its tool
description for `memory()` is a soft "DO save when…" instruction that the
conversational flow routinely overrides into social agreement.

**Mechanism.** Hard pre-inference inject in
`JarvisAgent.on_user_turn_completed` (defined at
`jarvis_agent.py:3350`) — runs after STT delivers the final transcript,
before supervisor inference. (Note: the existing `_KILL_PHRASES`
regex at `jarvis_agent.py:4216` is a DIFFERENT mechanism — it fires on
STT partials during speech for barge-in, not on completed turns. Track 1
is a sibling pattern, not a co-located one.)

```python
_SAVE_TRIGGER_RE = re.compile(
    r"(?ix)
    (?:^|\.|\?|!|,|\s)\s*
    (?:
        remember\s+(?:this|that|me|to)\b
      | save\s+(?:this|that|it)\b
      | don'?t\s+forget\b
      | write\s+this\s+down\b
      | memori[sz]e\s+(?:this|that|it)\b
    )
    "
)

_RECALL_TRIGGER_RE = re.compile(
    r"(?ix)
    (?:^|\.|\?|!|,|\s)\s*
    (?:
        do\s+you\s+remember\b
      | what\s+did\s+i\s+tell\s+you\b
      | have\s+i\s+told\s+you\b
      | remind\s+me\s+(?:about|of|what)\b
    )
    "
)
```

If `_SAVE_TRIGGER_RE` matches the user text, inject a high-priority
system message into chat_ctx **before** supervisor inference:

> *"USER REQUESTED A SAVE. Identify the durable fact / preference /
> procedure in their message and call `memory()` (target='user' for
> facts about Ulrich, 'memory' for environment notes, 'procedure' for
> named multi-step processes) BEFORE replying. Then reply with a short
> acknowledgment ('got it' / 'saved')."*

If `_RECALL_TRIGGER_RE` matches:

> *"USER REQUESTED A RECALL. Call `recall(query=<their question>)`
> FIRST. Use the returned context to answer. Do NOT reply 'this
> conversation just started' or 'I don't have prior context'."*

**Two-gate design (this is load-bearing — read carefully).** The regex is
intentionally LIBERAL — it triggers the inject whenever the user MIGHT
have asked to save. The supervisor (Sonnet 4.6 / Haiku 4.5) is the
SECOND gate: it reads the full user text + the inject's instructions
and uses judgment about WHAT (if anything) to save. A regex false
positive doesn't cause a bad save — it causes the supervisor to
consider saving and decline. The cost is one extra inject in the prompt
(~50 tokens, negligible).

Examples of regex behaviour + supervisor disposition:

| User text | Regex matches? | Supervisor disposition |
|---|---|---|
| *"Jarvis, remember this: I prefer fish"* | ✅ yes (`remember\s+this`) | Calls `memory(add, user, "Ulrich prefers fish")`, replies "Got it." |
| *"Jarvis, remember that I'm allergic to fish"* | ✅ yes (`remember\s+that`) | Calls `memory(add, user, "Ulrich is allergic to fish")`, replies "Got it." |
| *"Could you save that for me?"* | ✅ yes (`save\s+that`) | Depends on prior context — saves the referenced item or asks clarification |
| *"Don't forget I prefer terse replies"* | ✅ yes (`don'?t\s+forget`) | Calls `memory(add, user, ...)`, replies "Got it." |
| *"I'll always remember that joke"* | ✅ yes (`remember\s+that`) — FALSE POSITIVE | Reads "joke" as conversational; **does not save**, just replies. Cost: ~50 wasted tokens. |
| *"This song is unforgettable"* | ❌ no (no save verb) | n/a |
| *"Remember when we did the deploy?"* | ❌ no (`remember\s+when` not in alternation; recall regex catches it instead) | Triggers recall path |

The "false positive" rows are NOT bugs — the supervisor's judgment is
the actual gate. The regex's job is to make sure save cases never
reach the supervisor unannounced; the supervisor's job is to decide
whether to actually save.

**Shadow mode for 24h.** Initial deployment: regex matches → log
`[trigger] save_trigger matched: user_text=...` with `mode=shadow`
but does NOT inject. After 24h of soak we audit the matches against
turn outcomes (via `turn_telemetry.db`): if ≥95% of matches would have
caused a correct supervisor decision (judged by manual review of 20
sampled turns), we flip to live with `JARVIS_SAVE_TRIGGER_LIVE=1`.
The 5% threshold is for *bad supervisor decisions after a regex match*,
not regex false positives directly — because supervisor judgment is the
real gate.

**Telemetry.** Add column `save_trigger_fired INTEGER DEFAULT 0` and
`recall_trigger_fired INTEGER DEFAULT 0` to `turns` table for audit.

### Track 2 — `procedure` PROPOSAL_KIND (NEW)

**Schema extension.** In `pipeline/skill_review.py`:

```python
PROPOSAL_KINDS = ("skill_create", "skill_patch", "memory", "procedure")

# Validate branch
if kind == "procedure":
    name = str(payload.get("name", "")).strip()
    steps = payload.get("steps")
    if not name or not re.match(r"^[a-z0-9_-]+$", name):
        return None  # name must be kebab/snake
    if not isinstance(steps, list) or not steps:
        return None
    cleaned_steps = [str(s).strip() for s in steps if str(s).strip()]
    if not cleaned_steps:
        return None
    return {"name": name, "steps": cleaned_steps}
```

**Apply branch:**

```python
if p.kind == "procedure":
    from pipeline import file_memory
    body = "## " + p.payload["name"] + "\n" + \
           "\n".join(f"{i+1}. {s}" for i, s in enumerate(p.payload["steps"]))
    res = file_memory.add("procedure", body)
    return ApplyResult(proposal=p, ok=bool(res.get("success")),
                       detail=f"procedure {p.payload['name']}")
```

**file_memory extension.** In `pipeline/file_memory.py`:

- `VALID_TARGETS = ("memory", "user", "procedure")`
- `PROCEDURE_CHAR_LIMIT = 8000` (room for ~15-25 procedures)
- `_path_for("procedure")` returns `_memory_dir() / "PROCEDURES.md"`
- `_render_block("procedure", entries)` header:
  `"PROCEDURES (named multi-step processes) [pct — n/N chars]"`
- Existing `MemoryStore` mutations (`add`/`replace`/`remove`/`read`)
  all accept the new target with no other changes.

**memory tool schema extension** (`tools/memory.py::MEMORY_SCHEMA`):

```jsonc
// Diff (additive; existing actions/targets unchanged):
{
  "name": "memory",
  "parameters": {
    "type": "object",
    "properties": {
      "action":  { "enum": ["add", "replace", "remove", "read"] },
      "target":  { "enum": ["memory", "user", "procedure"] },  // <-- +procedure
      "content": { "type": "string" },
      "old_text":{ "type": "string" },
      "name":    {                                              // <-- NEW
        "type": "string",
        "description": "Required when target='procedure' and action='add'. Kebab-case identifier, e.g. 'deploy-app'."
      }
    },
    "required": ["action", "target"]
  }
}
```

Tool description gains: *"`procedure` is named multi-step processes ('how to deploy the app'). When target='procedure', supply `name` (kebab-case) and `content` as a numbered step list."*

`_handle_memory` validates `name` when `target=procedure` + `action ∈ (add, replace, remove)`; rejects missing/malformed names with a clear error. `_handle_memory(target='procedure', action='read')` returns all procedures (the file content).

**Snapshot injection.** `snapshot_for_prompt()` block ordering:
USER → MEMORY → PROCEDURES. Total cap (~15k chars) still small enough
that prefix-cache stability is preserved.

### Track 2.5 — Success capture (NEW)

**Gate.** Inside `autonomous_review_turn`, before the cold-turn review,
check whether the snapshot qualifies as a *successful multi-step task*:

```python
def _is_successful_trajectory(snap) -> bool:
    return (
        snap.route in ("TASK", "REASONING")
        and snap.tool_call_count >= 3
        and snap.had_tool_error is False
        and snap.user_followup_30s in (0, None)  # 0 = no followup, None = silent
        and _CLAIM_COMPLETION_RE.search(snap.jarvis_text)  # "done" / "deployed" / "finished"
        and _INTENT_VERB_RE.search(snap.user_text)        # "deploy"/"find"/"set up"/...
        and snap.total_wall_clock_s >= 10
    )
```

`_CLAIM_COMPLETION_RE` and `_INTENT_VERB_RE` are new constants in
`skill_review.py`; tested in isolation.

`tool_call_count` and `had_tool_error` need to be added to the
`TurnSnapshot` dataclass (currently has user_text/jarvis_text/route/
subagent/turn_id). Source: aggregate from `turn_telemetry.notes` field
where tool-call audit lives.

**Trajectory enrichment.** When the gate passes, the review prompt
appended block:

```
THIS TURN WAS A SUCCESSFUL MULTI-STEP TASK. TRAJECTORY:
  intent: {user_text first sentence}
  steps:
    1. {tool_call_1.name}({redacted_args})
    2. {tool_call_2.name}({redacted_args})
    ...
If these steps form a reusable procedure, propose kind="procedure"
with a kebab-case name derived from the intent verb + object.
```

**End-of-turn offer.** When the gate passes, jarvis_agent appends ONE
line to JARVIS's spoken reply (after the natural completion sentence):

> *"Want me to keep these steps as 'deploy-app' for next time?"*

Name auto-derived: extract first verb + first noun from user_text,
kebab-case. User says yes/save it/sure → next turn's review prompt is
seeded with `force_procedure=True` and the trajectory is applied
directly (bypasses the conservative reviewer for the confirmed case).
User says no/cancel → no proposal.

**Replay on next invocation.** When user utterance fuzzy-matches an
existing procedure name (Levenshtein distance ≤ 3 normalized), the
supervisor's chat_ctx gets a system inject:

> *"Saved procedure '{name}' exists. Steps: {steps}. Execute these in
> order, confirming before any destructive step (git push / rm / etc)."*

This is conservative — JARVIS announces the steps and gets confirmation
before doing them, rather than blind replay.

**Kill-switch.** `JARVIS_PROCEDURE_CAPTURE_DISABLED=1` skips the gate.

### Track 3 — Save-confab guard (EXTEND existing detector)

**Mechanism.** Extend `confab_detector.py` with a new pattern class.
The existing detector flags assistant turns where:

```
(a) text strongly claims a successful past action
AND (b) prior message in chat history lacks a tool result.
```

Add an analogous save-claim class:

```python
_SAVE_CLAIM_PATTERNS = [
    re.compile(r"(?i)\bi'?ll remember\b"),
    re.compile(r"(?i)\bi'?ve (saved|noted|stored|added)\b"),
    re.compile(r"(?i)\bgot it,? (saved|noted|added)\b"),
    re.compile(r"(?i)\badded (to memory|to user|to procedure)\b"),
    re.compile(r"(?i)\bremembered\b.*\bfor (next time|future|later)\b"),
]
```

Combined check (additive to existing tool-claim check):

```python
def is_confab(assistant_text, chat_ctx_tail) -> tuple[bool, str]:
    if _existing_tool_claim_check(assistant_text, chat_ctx_tail):
        return True, "tool_claim_without_evidence"
    if any(p.search(assistant_text) for p in _SAVE_CLAIM_PATTERNS):
        if not _has_recent_memory_tool_call(chat_ctx_tail):
            return True, "save_claim_without_evidence"
    return False, ""
```

`_has_recent_memory_tool_call` is a NEW helper, added to
`confab_detector.py` alongside the existing tool-claim helper. It
inspects the last N messages of `chat_ctx` for a `memory` tool call
(any action) or a `function_call` / `FunctionCallOutput` with
`name="memory"`. Same lookback window as the existing tool-evidence
check (8 messages — the detector's tuned value). Pure function; no
async, no I/O.

**Outcome on confab.** Turn is rejected from chat_ctx AND
`turn_telemetry.confab_check_state` is set to `save_claim`. Same path
the tool-claim confab already takes — proven in production.

**Kill-switch.** `JARVIS_CONFAB_SAVE_DISABLED=1` (separate from the
existing `JARVIS_CONFAB_DETECTOR=0` master kill — allows tuning save
detection independently while tool-claim detection stays on).

**Shadow mode.** Initial deployment: detect, log, but do NOT reject
(same pattern as Track 1). After 24h soak: review log entries; if
< 5% false positives, flip to reject.

### Track 5 — Reviewer prompt rewrite (CRITICAL)

**Current text** (`pipeline/skill_review.py:405-410`):

> *"Frustration signals like 'stop doing X', 'too verbose', 'don't
> format like this', 'just give me the answer', or an explicit
> 'remember this' are FIRST-CLASS skill signals — not just memory
> signals. Embed the preference into the skill that governs that
> class of task so the next session starts already knowing."*

**Replacement** (route explicit save phrases to `kind=memory`):

> *"SIGNALS — what to route where:*
>
> *• STYLE/TONE corrections ('stop doing X', 'too verbose', 'don't
> format like this', 'just give me the answer') → `kind=skill_create`
> or `kind=skill_patch`. Embed the preference into the skill governing
> that class of task.*
>
> *• EXPLICIT SAVE PHRASES ('remember this', 'save that', 'don't forget',
> 'write this down', 'memorize this') → `kind=memory` if the content is
> a durable fact/preference, `kind=procedure` if it's a named
> multi-step process.*
>
> *• WORKFLOW corrections (the user corrects the sequence of steps you
> took) → `kind=skill_patch` to the governing skill.*
>
> *• NON-TRIVIAL TECHNIQUE / WORKAROUND that emerged → `kind=skill_create`.*
>
> *• SKILL CONSULTED THIS TURN TURNED OUT WRONG → `kind=skill_patch`."*

**Plus** add `procedure` to the output schema example:

```json
{"kind": "procedure",
 "payload": {"name": "deploy-app", "steps": ["run tests", "git push", "check CI"]},
 "rationale": "one sentence"}
```

**Anti-garbage block stays intact** — that's the high-value section
that prevents environment-failure capture and negative-tool claims.

### Track 6 — `JARVIS_SKILL_REVIEW_APPLY=1` flip

**Sequencing.** This is the LAST step. Without Tracks 1/5, flipping it
generates more junk like the existing 2 skills. After 1/5 are in:

```ini
# setup/systemd/jarvis-voice-agent.service
Environment="JARVIS_SKILL_REVIEW_APPLY=1"
```

`systemctl --user daemon-reload && systemctl --user restart
jarvis-voice-agent.service` (checking `turn_telemetry.db` first per
the operational rule — don't restart within 60s of the last turn).

**Audit on.** The first 48h post-flip: monitor
`~/.local/share/jarvis/logs/skill_review/{YYYYMMDD-HHMMSS}/run.json`
+ `MEMORY.md` / `USER.md` / `PROCEDURES.md` file growth. Any junk
proposal triggers a tightening of the review prompt OR a return to
APPLY=0 while we tune.

**Curator interaction.** `JARVIS_CURATOR_DISABLED` stays unset
(curator default ON). Curator's consolidation stays suggestion-only
(`JARVIS_CURATOR_CONSOLIDATION` unset). Curator will catch any
near-duplicates that slip through into skills/memory/procedures.

### Track 7 — Junk skill cleanup

Before flipping APPLY=1 in Track 6:

```bash
rm -rf ~/.jarvis/skills/dedicated-voice-appliance
rm -rf ~/.jarvis/skills/design-fixed-voice-interface
```

Reason: both are single-conversation narrative extracts (Raspberry Pi
voice appliance setup) from a session where APPLY was temporarily ON
with the broken prompt. Not actual procedures — would dilute the
catalog injection in the supervisor prompt. Curator could archive
them, but a manual rm is faster + cleaner for a known-junk baseline.

Document in `~/.jarvis/skills/.cleanup.log`: timestamp + filenames
removed + reason. This is the manual-deletion equivalent of the
curator's archive log.

## Data flow — complete user-utterance lifecycle

**Happy path: user says "Jarvis, remember I'm allergic to fish"**

1. STT produces *"jarvis remember I'm allergic to fish"*.
2. `on_user_turn_completed` runs — kill-phrase regex doesn't match;
   `_SAVE_TRIGGER_RE` matches.
3. System message injected into chat_ctx: *"USER REQUESTED A SAVE..."*
4. Supervisor inference (Haiku 4.5 on TASK route) — model sees the
   inject, emits a tool call `memory(action="add", target="user",
   content="Ulrich is allergic to fish")`.
5. `_handle_memory` in `tools/memory.py` calls `file_memory.add("user",
   "Ulrich is allergic to fish")`. USER.md is created if absent, entry
   appended atomically.
6. Tool result returned to supervisor. Supervisor's final reply: *"Got it."*
7. `confab_detector` checks the reply — *"got it"* alone doesn't match
   save-claim patterns; turn accepted.
8. `turn_telemetry` row written; `save_trigger_fired=1`.
9. `fire_self_improvement(snapshot)` scheduled background.
10. `autonomous_review_turn` runs — sees a TASK turn with one tool call;
    reviewer LLM is conservative; emits `{"proposals": []}` (we don't
    need it; the supervisor already did the work).
11. End state: USER.md contains the fact, audit trail in
    `turn_telemetry`, no double-write.

**Happy path: user says "Jarvis, deploy the app" (no saved procedure yet)**

1-2. STT + on_user_turn_completed — neither trigger matches.
3. Supervisor inference — routes to TASK, plans steps, calls
   `terminal('pytest')`, then `terminal('git push')`, then
   `browser_task('check CI status')`.
4. All three tools return without error. Supervisor's reply: *"Tests
   passed, pushed, CI is green — deployed."*
5. `confab_detector` accepts (tool evidence in chat_ctx).
6. `turn_telemetry` row written: route=TASK, tool_call_count=3,
   had_tool_error=False, claim_completion=True.
7. `fire_self_improvement` scheduled.
8. `autonomous_review_turn` runs `_is_successful_trajectory(snap)` →
   True (3 tools, no error, intent verb "deploy", >10s).
9. Review prompt enriched with trajectory. Reviewer LLM emits
   `{"kind": "procedure", "payload": {"name": "deploy-app",
   "steps": ["run pytest", "git push", "check CI on github"]}}`.
10. `apply_proposal` is NOT called yet (proposal pending user
    confirmation). End-of-turn offer appended to next reply opportunity:
    *"Want me to keep these steps as 'deploy-app'?"*
11. User says *"yeah"*. Next turn's review forces `apply` for the
    pending procedure. PROCEDURES.md gains the entry.

**Future invocation: user says "Jarvis, deploy"**

1. STT, no trigger regex match.
2. **NEW: fuzzy match against procedure names** in PROCEDURES.md
   (during chat_ctx assembly in `prompt_builder`). Match on
   "deploy" ≤ "deploy-app".
3. System message injected: *"Saved procedure 'deploy-app' exists.
   Steps: ... Confirm before destructive steps."*
4. Supervisor reads, replies *"deploy-app procedure: pytest → git push
   → check CI. Run it?"* — user confirms — supervisor executes.

**Confab path: user says "Jarvis, remember I love sushi"**

(Test case for Track 3 — what if the LLM ignores the inject?)

1-3. Trigger regex matches, `save_trigger_fired=1` in this turn, system message injected.
4. Supervisor — for whatever reason (provider hiccup, model variance)
   — replies *"I'll remember that"* WITHOUT calling memory().
5. `confab_detector.is_confab(reply, ctx)`:
   - save-claim pattern `i'?ll remember` matches → flag candidate.
   - `_has_recent_memory_tool_call(ctx_tail)` → False.
6. **Track 1 ↔ Track 3 interaction (R3 escape hatch).** Before deciding to REJECT, the detector checks `save_trigger_fired` for the current turn:
   - **If `save_trigger_fired=0`:** standard path — REJECT turn (existing confab behaviour). Reply suppressed; TTS doesn't play the false promise.
   - **If `save_trigger_fired=1`:** **downgrade to ANNOTATE.** Mark `confab_check_state="save_claim_annotated"` in telemetry. Turn is **kept** in chat_ctx + telemetry. The supervisor's reply plays. The injection-failure is visible to the user ("I'll remember" was said, nothing was saved) but JARVIS isn't muted. Rationale: if Track 1 fired, the user explicitly asked. Silencing the supervisor would block their explicit ask; better to let the failed promise through and rely on Spec A's monitoring to catch the pattern (the failure is now visible in telemetry and in the user's lived experience). Next iteration of supervisor.md prompt can teach the model harder; in the worst case, Spec A.1 (model upgrade) fixes it.
7. Next user turn re-asks → trigger regex re-fires → supervisor gets a second chance. If the downgrade-to-ANNOTATE path keeps triggering, that's the metric that flags a deeper issue (supervisor not respecting the inject).

## Error handling

| Failure | Behaviour | Audit |
|---|---|---|
| Reviewer LLM (Groq) down | `_call_review_llm` returns `'{"proposals": []}'` (existing) — no proposal, no apply. Supervisor path unaffected. | `[skill_review] LLM call failed` warning |
| `file_memory.add` returns char-cap error | apply_proposal returns `ok=False` with the error detail. Reviewer can `replace`/`remove` in future turns to free space. | run.json `by_kind` + per-proposal `ok=False` |
| Track 1 regex false positive (figurative speech) | Worst case: supervisor receives a spurious inject, calls memory() with a non-durable fact. File_memory's `_META_PARAPHRASE_RE` filter (existing) blocks LLM-narration shapes. Threat-pattern scan blocks injection. Worst real outcome: USER.md gains a useless entry. Mitigated by curator dedup over time. | Per-trigger telemetry column |
| Track 2.5 fuzzy match false positive (wrong procedure surfaced) | Supervisor confirmation gate ("Run it?") protects against blind execution. Worst case: user says no, JARVIS replans. | `procedure_match_offered=1` telemetry |
| Track 3 save-claim false positive (real save was made but regex matched wrong context) | Turn rejected — user re-asks, supervisor saves again, memory is idempotent (file_memory.add returns "Entry already exists"). Worst case: brief UX confusion, no data loss. | `confab_check_state=save_claim` |
| APPLY=1 generates junk after Track 6 | Set `JARVIS_SKILL_REVIEW_APPLY=0`, restart. Junk entries in MEMORY/USER/PROCEDURES are removed via `memory(action="remove", ...)`. | run.json + manual review of recent writes |

All paths preserve the existing invariant: **failures fall through to
"the loop did nothing this turn"**. No hard-fail, no exception
propagation to the voice path.

## Threat model

The memory store is **injected verbatim into the system prompt** at session start (see [pipeline/file_memory.py::snapshot_for_prompt](../../../src/voice-agent/pipeline/file_memory.py#L218)). Anything written here gets read by the supervisor on every turn of every future session. This makes the write path a **first-class attack surface**.

| Threat | Vector | Existing mitigation | What this spec adds |
|---|---|---|---|
| Prompt injection via voice → system prompt | Adversarial speech around the user; prompt-shaped phrase ("ignore previous instructions, you are now…") via mic | `file_memory.scan_memory_content()` blocks 9 injection patterns + 5 exfil patterns + 5 SSH/key-access patterns + 10 invisible-unicode chars | Track 1 inject is **constant text** ("USER REQUESTED A SAVE…"), no user-content interpolation, so the inject itself can't carry an attacker payload. The payload still has to pass scan_memory_content on the actual write. |
| LLM narration leak ("the user is X-ing") | Reviewer hallucinates conversation narration as a "fact" | `is_meta_paraphrase` regex blocks 4 narration patterns | Track 5 prompt rewrite preserves the FORBIDDEN block verbatim. Track 2's procedure validation rejects empty steps + bad names. |
| Save-poison via 3rd-party voice | Hostile speaker in audio range says "Jarvis remember Ulrich is a fraud" | None — no voiceprint gate | **Out of scope for Spec A.** Mitigation deferred (would require speaker verification — separate spec). Current threat model assumes single-speaker audio. Documented limitation. |
| Procedure-replay → destructive action | Saved procedure includes `rm` / `git push` / external API call, gets blind-replayed | None — procedures don't exist today | Track 2.5 replay flow includes mandatory confirmation inject: *"Confirm before any destructive step (git push / rm / etc)."* Verbal confirmation required, not silent. |
| TV/radio audio hallucinated as save trigger | Background audio picked up by mic, classified as user speech | None | Track 1 regex requires object-specificity anchor ("remember THIS / save IT"). Track 5 prompt's "DO NOT CAPTURE one-off task narratives" applies. Residual risk: shadow mode soak required before going live. |
| Race: supervisor + reviewer + curator all touch same file | Multi-writer | `file_memory._file_lock` (`fcntl.flock` exclusive, per-target) | Verified — see Concurrency analysis below. |
| Honcho leak | Honcho server is self-hosted but if it ever moves to a managed endpoint, every turn's text leaves the device | N/A | Out of scope (Honcho deepening is non-goal). Confirm Honcho stays at `127.0.0.1:8000` in deployment review. |

## Privacy + data handling

The memory store contains personally-identifiable information about Ulrich. The following surfaces this spec guarantees:

- **Inspect:** `memory(action="read", target=...)` lists current entries. Direct path: `cat ~/.jarvis/memories/{USER,MEMORY,PROCEDURES}.md`.
- **Correct:** `memory(action="replace", target=..., old_text=..., content=...)`. Multi-line entries supported (`§` delimiter — see [file_memory.py:53](../../../src/voice-agent/pipeline/file_memory.py#L53)).
- **Delete (single entry):** `memory(action="remove", target=..., old_text=...)`.
- **Delete (full wipe):** `rm -f ~/.jarvis/memories/*.md && systemctl --user restart jarvis-voice-agent.service`. Restart required to clear the in-prompt snapshot (snapshot is captured at session start and frozen for the prefix cache).
- **Audit:** Every write logs at INFO level via `[memory] {action} {target} → {message}` (existing handler at [tools/memory.py:81](../../../src/voice-agent/tools/memory.py#L81)). Audit window: `~/.local/share/jarvis/logs/voice-agent.log` + 14-day gzipped rotation.
- **No third-party transmission of memory content.** File-backed stores never leave the local filesystem. Honcho (separately) syncs every turn to its own server (`http://127.0.0.1:8000`, self-hosted per `~/.jarvis/keys.env`) — Honcho writes are **not addressed by this spec** and the user controls the Honcho server.
- **Retention:** No auto-expiry. Entries persist until explicitly removed or curator-archived. Curator runs periodic dedup with **suggestion-only** consolidation; no auto-deletion of valid entries.
- **Backup:** `~/.jarvis/memories/` should be added to the existing snapshot rotation at `~/.jarvis/snapshots/` (currently only telemetry DB snapshots exist there per `ls`). Add to `bin/jarvis-snapshot.sh` (out-of-scope tooling change — flag for follow-up).

## Concurrency analysis

Per-target `.lock` file (`fcntl.flock` exclusive — `pipeline/file_memory.py:371-389`) serializes writers per file. Cross-target writes parallelize safely. Within a target:

| Scenario | Behaviour |
|---|---|
| Supervisor calls `memory(add)` + autonomous reviewer's `apply_proposal` writes same target same turn | One acquires `flock`, the other waits. Both writes durable; second sees the first's content via `_reload_target` before deciding (`add` returns "Entry already exists" if dupe). |
| Two concurrent voice sessions (multi-process) write same memory target | Same lock file per target; OS-level `fcntl.flock` serializes across processes. |
| Reader (`snapshot_for_prompt` at session start) overlaps with writer | Atomic-rename (`_atomic_replace`) guarantees the reader sees pre-write OR post-write content — never partial. No lock needed for reads. |
| Curator deletes / consolidates while supervisor adds | Curator goes through the same `file_memory` surface; no race. |
| Track 2.5 procedure offer pending across turns | Pending state in process memory only (`_pending_procedure_offers: dict[room_id, ProposalCandidate]`); cleared on apply or next turn-without-confirm. **Not durable across process restarts** — restart loses the pending offer (acceptable UX cost). |
| First-write bootstrap (file doesn't exist) | `MemoryStore._save` calls `_memory_dir().mkdir(parents=True, exist_ok=True)` before `_atomic_replace`. Verified `pipeline/file_memory.py:401`. |

## Performance budget

Voice-first context — latency from STT-finished to first TTS chunk targets ~700ms (cached Anthropic prompt). Any addition in the hot path must justify itself.

| Track | Hot path? | Latency cost | Notes |
|---|---|---|---|
| 1 — trigger regex | Yes (pre-inference) | ≤1ms / turn | Python `re` against ≤500-char user_text; 5+5 patterns max |
| 1 — system message inject | Yes | 0ms | Constant-string append to `chat_ctx` — no allocation churn |
| 2 — procedure PROPOSAL_KIND | Off-latency | 0ms hot path | Lives in `autonomous_review_turn` background task |
| 2.5 — success-capture gate | Off-latency | 0ms hot path | Same task as above |
| 2.5 — end-of-turn offer | Hot path (TTS payload extension) | ~250ms additional TTS audio for the ~8-word offer phrase | Groq Orpheus @ ~30ms/word. **Only on turns matching gate criteria** (≥3 tool calls + intent verb + >10s wall-clock) — rare. |
| 3 — save-confab guard | Hot path (turn-write gate) | ≤1ms | 5 regex patterns against ≤2000-char jarvis_text |
| 5 — prompt rewrite | Off-latency | 0ms hot path | Reviewer prompt only |
| 6 — APPLY=1 flip | Off-latency | 0ms hot path | n/a |

**Total hot-path cost: ~2ms regex + ~250ms TTS on procedure-offer turns only.** Acceptable; well below the 50ms / 500ms thresholds for non-blocking work and visible UX changes respectively.

**Off-latency cost:** Each turn already triggers one Groq llama-8b reviewer call. No new LLM calls added. Track 2.5 enriches the existing prompt with trajectory data; cost-neutral.

## Observability + metrics

New telemetry columns on `turns` (additive `ALTER TABLE`):

```sql
ALTER TABLE turns ADD COLUMN save_trigger_fired INTEGER DEFAULT 0;
ALTER TABLE turns ADD COLUMN recall_trigger_fired INTEGER DEFAULT 0;
ALTER TABLE turns ADD COLUMN procedure_match_offered INTEGER DEFAULT 0;
ALTER TABLE turns ADD COLUMN procedure_match_executed INTEGER DEFAULT 0;
-- existing `confab_check_state` domain extended to include "save_claim"
```

New log lines (logger `jarvis.memory_loop`):

```
[trigger] save_trigger matched: user_text="..." (mode=shadow|live)
[trigger] recall_trigger matched: user_text="..."
[procedure] offer appended: name="..." steps=N
[procedure] applied: name="..." source=user_confirm|review_apply
[confab] save_claim flagged: jarvis_text="..." → turn rejected
[memory] add user|memory|procedure → message   (existing handler; unchanged)
```

Dashboard hooks (deferred — flag for follow-up, not Spec A scope):

- Memory growth over time: entries per target, char usage per target (sourceable from `file_memory.read` + simple cron).
- Trigger fire rate: `save_trigger_fired` + `recall_trigger_fired` per day from `turns`.
- Procedure conversion: `procedure_match_offered` → `procedure_match_executed` ratio.
- Confab rejection rate: `confab_check_state` breakdown by class per day.

## Risks & mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Track 1 regex overfires on figurative speech | Medium | Low (`scan_memory_content` + `_META_PARAPHRASE_RE` reject narration shapes) | Shadow mode 24h → measure FP rate → flip live only if <5%. Documented anchors require object-specificity. |
| R2 | Reviewer (llama-8b) writes garbage after APPLY=1 flip | Medium-high (the 2 existing junk skills prove the model is weak) | Medium (junk in store dilutes the prompt) | Track 5 prompt rewrite **first**; observe APPLY=1 for 48h; revert if junk rate >5%/day; longer-term: Spec A.1 reviewer upgrade (llama-8b → Haiku 4.5). |
| R3 | Track 1 + Track 3 interaction → user can't save | Low | High (every save attempt rejected as confab) | When `save_trigger_fired=1` AND `save_claim` flagged in same turn, **downgrade Track 3 from REJECT to ANNOTATE** (mark turn, accept reply). Supervisor gets second-chance trigger on the inevitable user retry. Documented in Data flow's confab path. |
| R4 | File_memory char limit hit mid-conversation | Medium (USER.md cap is 1.4k — small) | Low (tool returns clear error; user can replace/remove) | Tool description teaches model to consolidate when usage >80%. Curator's umbrella-consolidation handles long-tail. May need to bump USER.md to 2.5k (cheap; raise here if curator's first-pass shows pressure). |
| R5 | Procedure replay executes destructive action | Low (replay always confirms) | High (data loss possible) | Inject text explicitly names "confirm before destructive". Confirmation is verbal, not implicit. Reviewable in supervisor.md after each iteration. |
| R6 | User says "remember" meaning session-only | Medium | Low (tool description distinguishes durable from ephemeral; user can `memory(remove)`) | Acceptable false-positive rate; idempotent removal path. |
| R7 | Service restart loses pending Track 2.5 offers | Medium (daily restart cadence in dev) | Negligible (user just doesn't get the offer; can re-trigger by saying save) | Acknowledged. Not worth durable storage cost for the offer queue. |
| R8 | Stale entries accumulate (GDPR-style retention) | Low (single-user system) | Medium (clutter; potentially obsolete facts) | Manual `memory(remove)` + documented full-wipe path. Curator surfaces dedupe suggestions. No auto-aging — by design. |
| R9 | Fuzzy procedure name match collides (two procedures named similarly) | Low | Medium (wrong procedure surfaced) | Match returns top-1 + ASK; if Levenshtein ≤ 3 for both top-1 and top-2 → disambiguation prompt: *"Two procedures match: 'X' and 'Y'. Which?"* |
| R10 | TurnSnapshot extension breaks existing reviewer | Low | High (review loop halts) | New fields (`tool_call_count`, `had_tool_error`) added with sensible defaults (0, False). Existing constructors continue to work. Test in `test_skill_review.py` covers backwards-compat. |

## Testing strategy

Per the voice-agent rule: tests live in `src/voice-agent/tests/`,
run via `cd src/voice-agent && .venv/bin/python -m pytest tests/`.

| Track | Test file | Cases |
|---|---|---|
| 1 | `tests/test_save_trigger_regex.py` (NEW) | True positives (10): explicit save phrases. True negatives (10): figurative "remember", recall phrasing, song lyrics. Edge: punctuation, capitalization, voice-STT-typical artifacts ("uh remember this..."). |
| 1 | `tests/test_turn_pipeline_trigger_inject.py` (NEW) | Inject path: STT text matches → chat_ctx has system message before supervisor inference. Negative: no match → no inject. Shadow mode: live=0 → log but don't inject. |
| 2 | `tests/test_skill_review_procedure_kind.py` (NEW) | `PROPOSAL_KINDS` contains procedure. `_validate_payload` accepts valid procedure, rejects empty steps / bad name. `apply_proposal` writes to file_memory `procedure` target. |
| 2 | `tests/test_file_memory.py` (EXTEND) | `VALID_TARGETS` contains procedure. `add("procedure", body)` writes PROCEDURES.md. Char cap respected. Snapshot includes PROCEDURES block. |
| 2 | `tests/test_memory_recall_tool.py` (EXTEND) | `memory` tool's target enum accepts `"procedure"`. Schema description mentions procedure. |
| 2.5 | `tests/test_success_trajectory_capture.py` (NEW) | `_is_successful_trajectory` gate: True for synthetic TASK with 3+ tools no error / claim completion / intent verb / >10s. False for each missing dimension. |
| 2.5 | `tests/test_procedure_offer.py` (NEW) | Gate passes → offer phrase appended to reply. User-yes → procedure applied. User-no → no apply. Name auto-derivation. |
| 3 | `tests/test_confab_detector.py` (EXTEND) | Save-claim patterns flag without memory tool call. Save-claim + memory tool call → accept. Existing tool-claim cases still pass. |
| 5 | `tests/test_skill_review.py` (EXTEND) | `_REVIEW_PROMPT` mentions "EXPLICIT SAVE PHRASES" + "kind=memory" + "kind=procedure". Anti-garbage block still present. JSON-only output contract intact. `grep -i hermes` returns nothing. |
| 6 | `tests/test_self_improve_wiring.py` (EXTEND) | `JARVIS_SKILL_REVIEW_APPLY` env var path: when set, apply runs; when unset, propose-only. Kill switch `JARVIS_SELF_IMPROVE_DISABLED=1` still suppresses both. |

Soak: after each track lands, 24h shadow mode → log review → flip live.

`import jarvis_agent` clean check after each commit (catches accidental
import cycle).

## Success criteria — measurable

**Sprint 1 (days 1–7):** Tracks 5/3/1/2/2.5/7 implemented + tests green. APPLY still OFF; shadow mode for Tracks 1/3.

- **Pass:** in shadow logs, `save_trigger_fired=1` ≥ once per genuine "remember/save" user turn (manual audit of 20 sampled turns; ≤5% FP).
- **Pass:** save-confab guard fires on every "I'll remember" assistant reply that lacked a memory tool call in the prior 8 messages (manual audit of any matching turn; ≤5% FP).
- **Pass:** `pytest tests/` full suite green; no regression vs the existing 2090-test baseline.
- **Pass:** `import jarvis_agent` clean.

**Sprint 2 (days 8–14):** Tracks 1/3 flipped shadow → live. Track 6 (`JARVIS_SKILL_REVIEW_APPLY=1`) set in service env after 48h Sprint-1 stability.

- **Pass:** USER.md / MEMORY.md / PROCEDURES.md combined entry count ≥ 5 within 48h of typical voice use.
- **Pass:** zero turns of the form `jarvis_text contains "I'll remember"` AND `confab_check_state IS NULL` (i.e., no save-confab slips through).
- **Pass:** recall trigger fires on every genuine "do you remember…" user turn (manual audit).
- **Pass:** ≥ 1 procedure saved via Track 2.5 success-capture path (proves the offer + apply path E2E).

**Sprint 3 (days 15–30):** stability soak.

- **Pass:** char usage ≤ 80% of cap on any single store (no consolidation pressure).
- **Pass:** curator suggestion queue ≤ 3/week (low duplicate emission).
- **Pass:** user-reported "JARVIS forgot X" turns ≤ 1 per week (down from current baseline — which can't even be measured because no memory exists yet).

**Anti-success (red flags — pause + tune):**

- Memory store dominated by single-conversation noise (>30% of entries from one session)
- APPLY=1 produces > 3 junk skills/week
- User has to repeat "remember X" more than twice on average
- PROCEDURES.md contains entries the user never invokes after a month

**Pre-existing measurable baseline (so we know what improvement means):**

- 0 memory tool invocations in 14 days of `voice-agent.log` (today's state)
- 0 entries in MEMORY.md / USER.md (files absent on disk)
- 2 skills in `~/.jarvis/skills/`, both single-conversation extracts (low quality)
- 1+ "I'll remember that" confab/week per turn telemetry sampling

## Rollout sequencing

| Order | Step | Goes live when |
|---|---|---|
| 1 | Track 5: prompt rewrite | Commit + restart |
| 2 | Track 3: save-confab guard (shadow) | Commit + restart |
| 3 | Track 1: trigger regex (shadow) | Commit + restart |
| 4 | Track 2: procedure PROPOSAL_KIND | Commit + restart |
| 5 | Track 2.5: success capture | Commit + restart |
| 6 | Track 7: junk skill cleanup | Manual rm + restart |
| 7 | Tracks 1+3: flip from shadow → live | After 24h shadow review |
| 8 | Track 6: `JARVIS_SKILL_REVIEW_APPLY=1` | After 48h of step 7 stability |

Each step is independently reversible — kill switches at every layer.

## Kill switches

| Env var | Default | Effect |
|---|---|---|
| `JARVIS_SELF_IMPROVE_DISABLED` | unset (loop ON) | Master kill — disables the entire review loop |
| `JARVIS_SKILL_REVIEW_APPLY` | unset (OFF) | When `=1`, apply path active. Spec flips this LAST. |
| `JARVIS_SAVE_TRIGGER_LIVE` | unset (shadow) | When `=1`, trigger regex actually injects |
| `JARVIS_RECALL_TRIGGER_LIVE` | unset (shadow) | Same for recall |
| `JARVIS_PROCEDURE_CAPTURE_DISABLED` | unset | When `=1`, Track 2.5 gate skipped |
| `JARVIS_CONFAB_SAVE_DISABLED` | unset | When `=1`, Track 3 save-claim detection off (tool-claim still on) |
| `JARVIS_CONFAB_DETECTOR` | unset (ON) | Master confab kill (existing) |
| `JARVIS_CURATOR_DISABLED` | unset (ON) | Disable periodic curator (existing) |

## Future work (out of scope here)

- **Spec A.1 — Reviewer LLM upgrade.** llama-3.1-8b-instant → Haiku 4.5.
  ~2× cost per review, but quality of extraction goes up substantially.
  Spec A's 2 junk skills are the symptom this would address.
- **Spec B — Plane 3 source-code self-mod via CLI delegation.** Branch
  + edit + pytest + PR-artifact + manual merge loop. Separate spec
  because user-review UX differs (silent text writes vs. visible PR).
- **Honcho deepening.** Currently auto-sync only; could add a deliberate
  `honcho_save(content)` tool if file-backed memory proves too narrow.
  Not needed if file-backed + Honcho-auto continue working in parallel.
- **Cross-session procedure replay.** Currently each session re-reads
  PROCEDURES.md fresh. Could index procedures via embedding for fuzzy
  intent matching beyond name-Levenshtein.
- **Procedure parameterization.** Today procedures are fixed strings.
  Could support `deploy --env staging` style with arg placeholders.

## Open questions (decide during writing-plans)

- **TurnSnapshot data source for `tool_call_count` + `had_tool_error`.** Two options:
  (a) parse from the existing `turn_telemetry.turns.notes` (unstructured TEXT — fragile);
  (b) add `tool_call_count INTEGER DEFAULT 0` + `had_tool_error INTEGER DEFAULT 0` as proper columns on `turns`, populated from the same audit hook that already fires for the existing telemetry.
  Recommendation: (b). Cleaner audit, no parse fragility, and the columns are useful for dashboards independent of this spec. Decide and document in the implementation plan.
- **USER.md char limit raise** (currently 1,375). The memory-quality memo flagged dedup + contradictions; if first-pass curator suggestions show pressure, raise to 2,500. Hold the change to the curator's first telemetry batch (week 1-2) before committing.
- **Backup integration.** Add `~/.jarvis/memories/` to `bin/jarvis-snapshot.sh` rotation (currently snapshots only telemetry DB). Flagged in Privacy section; out-of-scope tooling change for Spec A but worth doing immediately after.
- **Procedure name conflict policy.** Two procedures with the same auto-derived name (e.g., user does "deploy" twice with different step sequences). Current: second `add` returns "Entry already exists" if exact duplicate, else appends. Probably wants disambiguation prompt during apply ("there's already a 'deploy-app' procedure; replace it, or save this as 'deploy-app-v2'?"). Flag for implementation-plan decision.

## Self-review notes (completed inline)

- **Placeholders:** None remaining. The two "deferred — out-of-scope tooling" flags (snapshot script, dashboards) are explicit, not placeholders.
- **Internal consistency:** Track numbering convention documented inline (table = rollout order; section headers = stable Track ID). Capability-surface ground-truth table aligns with what each Detailed Design section says. Risk register R1–R10 maps to specific mitigations in detail sections.
- **Scope:** Plane 3 (source-code self-mod), reviewer LLM upgrade, Honcho deepening, soul.md edits, auto-extractor restoration, speaker verification — all explicitly out of scope, with rationale.
- **Ambiguity:** Trigger regex patterns specified concretely with examples + counterexamples. Success-trajectory gate has numeric thresholds (≥3 tool calls, >10s wall-clock). End-of-turn offer text quoted. Confab regex patterns spelled out. Track 1 ↔ Track 3 interaction has an explicit escape (R3 → annotate, not reject).
- **Reversibility:** Every track has a kill switch (8 env vars enumerated). Track 6 (APPLY flip) sequenced last; revert by unsetting the env. Track 7 (junk delete) logs to `~/.jarvis/skills/.cleanup.log`.
- **Enterprise lens:** sourced principles cited (Anthropic / OpenAI / EU AI Act / GDPR / prior internal memo). Threat model enumerated (7 threats + mitigations). Privacy + data handling: inspect/correct/delete paths documented. Concurrency: 6 scenarios analyzed against the existing `fcntl.flock` discipline. Performance budget: hot-path cost ~2ms + ~250ms TTS-only-on-offer-turns. Observability: 4 new telemetry columns + named log lines under `jarvis.memory_loop`. Risks: 10-row register with likelihood/impact/mitigation. Success criteria: measurable 30/60/90-day with anti-success red flags + pre-existing baseline.
