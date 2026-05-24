# JARVIS Memory + Procedure Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make JARVIS actually save what the user asks him to save (memories, preferences, and named multi-step procedures), and stop confabulating "I'll remember" without doing it.

**Architecture:** Extend the already-wired autonomous self-improvement loop (`pipeline/skill_review.py` fires per turn via `fire_self_improvement` from `jarvis_agent.py:5241`) with a new `procedure` PROPOSAL_KIND, fix the reviewer prompt that mis-routes "remember this" to skills instead of memory, force-trigger an inject for explicit save phrases via a new regex in `JarvisAgent.on_user_turn_completed:3350`, extend `confab_detector.looks_like_confabulation` with a save-claim class, and finally flip `JARVIS_SKILL_REVIEW_APPLY=1` last so the reviewer's writes actually land. R3 escape (Track 1 ↔ Track 3 interaction) is load-bearing — when `save_trigger_fired=1`, Track 3 downgrades from REJECT to ANNOTATE so the supervisor's reply isn't silenced.

**Tech Stack:** Python 3.13, LiveKit Agents framework, SQLite (turn telemetry), pytest, atomic-file-write + `fcntl.flock` per-target (existing `pipeline/file_memory.py`).

**Spec:** [`docs/superpowers/specs/2026-05-24-jarvis-memory-and-procedure-loop-design.md`](../specs/2026-05-24-jarvis-memory-and-procedure-loop-design.md)

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `src/voice-agent/pipeline/skill_review.py` | MODIFY | Reviewer prompt rewrite; add `procedure` PROPOSAL_KIND; extend `TurnSnapshot`; add success-capture gate; trajectory-enrichment prompt block |
| `src/voice-agent/pipeline/file_memory.py` | MODIFY | Add `procedure` target; `PROCEDURE_CHAR_LIMIT = 8000`; extend `MemoryStore` to handle procedure entries + snapshot block |
| `src/voice-agent/tools/memory.py` | MODIFY | Add `procedure` to target enum; add `name` param; validate name when target=procedure; update tool description |
| `src/voice-agent/confab_detector.py` | MODIFY | Add `_SAVE_CLAIM_PATTERNS` list; add `_has_recent_memory_tool_call` helper; extend `looks_like_confabulation` with save-claim class |
| `src/voice-agent/jarvis_agent.py` | MODIFY | `on_user_turn_completed`: add `_SAVE_TRIGGER_RE` + `_RECALL_TRIGGER_RE` matching + system-message inject + telemetry write; track pending procedure offers; append end-of-turn offer; handle confirmation on next turn |
| `src/voice-agent/pipeline/prompt_builder.py` | MODIFY | Add `build_procedure_catalog_block(procedures)`; fuzzy match user utterance against procedure names; inject saved procedure steps when match found |
| `src/voice-agent/pipeline/turn_telemetry.py` | MODIFY | Migration: 6 new columns on `turns` (additive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`-equivalent — use try/except duplicate column) |
| `setup/systemd/jarvis-voice-agent.service` | MODIFY | Add `Environment="JARVIS_SKILL_REVIEW_APPLY=1"` (last task, after 48h soak) |
| `src/voice-agent/tests/test_save_trigger_regex.py` | NEW | Trigger regex true/false positives |
| `src/voice-agent/tests/test_turn_pipeline_trigger_inject.py` | NEW | Inject path through `on_user_turn_completed` |
| `src/voice-agent/tests/test_skill_review_procedure_kind.py` | NEW | `procedure` PROPOSAL_KIND validate + apply |
| `src/voice-agent/tests/test_file_memory.py` | MODIFY | Procedure target round-trip; char cap; snapshot block |
| `src/voice-agent/tests/test_memory_recall_tool.py` | MODIFY | Tool schema procedure target + name param validation |
| `src/voice-agent/tests/test_success_trajectory_capture.py` | NEW | `_is_successful_trajectory` gate |
| `src/voice-agent/tests/test_procedure_offer.py` | NEW | End-of-turn offer + confirmation flow |
| `src/voice-agent/tests/test_confab_detector.py` | MODIFY | Save-claim class added |
| `src/voice-agent/tests/test_skill_review.py` | MODIFY | Reviewer prompt mentions new signal routing + procedure |
| `src/voice-agent/tests/test_self_improve_wiring.py` | MODIFY | `JARVIS_SKILL_REVIEW_APPLY` gate respects env |
| `src/voice-agent/tests/test_prompt_builder.py` | MODIFY | Procedure-catalog block + intent-match injection |
| `src/voice-agent/tests/test_turn_telemetry_migration.py` | NEW | Schema has new columns after init |

---

## Task 1: Reviewer prompt rewrite (Track 5 — must land FIRST)

**Why first:** Track 6 (APPLY=1 flip) will be the last step; before flipping, the reviewer must emit `kind=memory` for save phrases, not `kind=skill`. The 2 existing junk skills prove what happens with the current prompt.

**Files:**
- Modify: `src/voice-agent/pipeline/skill_review.py:387-471` (the `_REVIEW_PROMPT` constant)
- Test: `src/voice-agent/tests/test_skill_review.py` (extend)

- [ ] **Step 1: Write the failing test**

Add to `src/voice-agent/tests/test_skill_review.py` (append to existing test file):

```python
def test_review_prompt_routes_explicit_save_to_memory():
    """Track 5: explicit save phrases must route to kind=memory, not kind=skill_create."""
    from pipeline.skill_review import _REVIEW_PROMPT
    prompt_lower = _REVIEW_PROMPT.lower()
    # Explicit save phrases must steer to memory
    assert "explicit save phrases" in prompt_lower
    assert "remember this" in prompt_lower
    assert "save that" in prompt_lower
    assert "kind=memory" in prompt_lower or '"kind": "memory"' in _REVIEW_PROMPT
    # And procedure routing for named multi-step processes
    assert "kind=procedure" in prompt_lower or '"kind": "procedure"' in _REVIEW_PROMPT
    # Style/tone corrections still route to skills (preserved Hermes guidance)
    assert "style" in prompt_lower and "tone" in prompt_lower
    assert "skill_create" in prompt_lower or "skill_patch" in prompt_lower
    # Anti-garbage block preserved
    assert "command not found" in prompt_lower
    assert "negative claims" in prompt_lower
    # FORBIDDEN narration block preserved
    assert "the user is" in prompt_lower
    assert "the conversation has shifted" in prompt_lower
    # JSON-only contract intact
    assert "Output JSON ONLY" in _REVIEW_PROMPT


def test_review_prompt_no_hermes_tokens():
    """JARVIS-native — no hermes references in the prompt."""
    from pipeline.skill_review import _REVIEW_PROMPT
    assert "hermes" not in _REVIEW_PROMPT.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_skill_review.py::test_review_prompt_routes_explicit_save_to_memory -v
```

Expected: FAIL with `assert "explicit save phrases" in prompt_lower` — current prompt doesn't contain it.

- [ ] **Step 3: Rewrite `_REVIEW_PROMPT` in `pipeline/skill_review.py`**

Replace the `_REVIEW_PROMPT = """..."""` block at lines 387-471 with:

```python
_REVIEW_PROMPT = """You are JARVIS's self-improvement reviewer. You read \
ONE completed conversation turn (what the user said, what JARVIS replied, \
and which subagent/route handled it) and decide whether it reveals \
something worth durably saving.

You may propose ONLY these moves (nothing else):
  - "skill_create": a REUSABLE multi-step workflow worth saving as a \
skill (a repeatable procedure JARVIS could follow again next time).
  - "skill_patch": a targeted fix/addition to an existing skill (only if \
you can name an exact existing skill and an exact old→new string).
  - "memory": a DURABLE fact about the user, their preferences, their \
projects, or feedback on how JARVIS should behave.
  - "procedure": a NAMED multi-step process the user asked to save \
("save this process", "remember how to deploy") — payload includes a \
kebab-case name and an ordered list of steps.

Be CONSERVATIVE. Most turns warrant NOTHING — a one-off answer, banter, \
or a failed/aborted attempt is not a reusable artifact. Only propose when \
the turn clearly encodes a repeatable procedure or a stable fact.

SIGNALS — what to route where:
  • STYLE / TONE / FORMAT / VERBOSITY corrections ("stop doing X", \
"too verbose", "don't format like this", "just give me the answer") → \
kind=skill_create or kind=skill_patch. Embed the preference into the \
skill that governs that class of task so the next session starts \
already knowing.
  • EXPLICIT SAVE PHRASES ("remember this", "save that", "don't \
forget", "write this down", "memorize this") → kind=memory if the \
content is a durable fact or preference; kind=procedure if it's a \
named multi-step process the user wants to invoke later.
  • WORKFLOW corrections (the user corrected the sequence of steps \
you took) → kind=skill_patch to the skill that governs that class of task.
  • NON-TRIVIAL TECHNIQUE / WORKAROUND that emerged this turn → \
kind=skill_create.
  • SKILL CONSULTED THIS TURN TURNED OUT WRONG → kind=skill_patch.

SKILL-VS-MEMORY-VS-PROCEDURE GUIDANCE:
  Memory captures who the user is — persona, preferences, durable facts.
  Skills capture how to do this class of task for this user.
  Procedures capture a named replayable sequence of steps (e.g. \
"deploy-app", "morning-routine") — distinct from skills because they \
are invoked by name and replayed step-by-step.

DO NOT CAPTURE (these harden into persistent self-imposed constraints \
that break when the environment changes):
  • Environment-dependent failures: missing binaries, "command not found" \
errors, fresh-install path mismatches, unconfigured credentials, \
uninstalled packages. The user can fix these — they are not durable rules.
  • Negative claims about tools or features ("browser tools don't work", \
"X tool is broken", "cannot use Y"). These harden into refusals that \
JARVIS will cite against itself for months after the actual problem \
was fixed.
  • Session-specific transient errors that already resolved before the \
turn ended. If retrying worked, capture the retry pattern — never \
the original failure as a standalone constraint.
  • One-off task narratives. A user asking "summarize today's news" or \
"analyze this PR" is not a class of work that warrants a skill.
If a tool failed because of setup state, capture the FIX (install \
command, config step, env var) — never "this tool does not work" as a \
standalone constraint.

FORBIDDEN (never output these — they are conversation narration, not \
durable artifacts):
  - "The user is asking about X" / "The user appears to be X-ing"
  - "The conversation has shifted to X" / "It seems to be X"
  - A skill that just restates this single turn ("how to answer the \
question the user just asked")
  - A memory that is a paraphrase of what was said rather than a fact

Output JSON ONLY, no prose:
  {{"proposals": [
     {{"kind": "skill_create",
       "payload": {{"name": "kebab-case-name", "description": "...", \
"when_to_use": "...", "body": "## Steps\\n1. ..."}},
       "rationale": "one sentence"}},
     {{"kind": "memory",
       "payload": {{"category": "user|feedback|project|reference", \
"content": "one declarative sentence"}},
       "rationale": "one sentence"}},
     {{"kind": "procedure",
       "payload": {{"name": "kebab-case-name", \
"steps": ["step one", "step two", "..."]}},
       "rationale": "one sentence"}}
  ]}}
If nothing is worth saving, output exactly: {{"proposals": []}}

TURN TO REVIEW:
  route: {route}
  subagent: {subagent}
  user said: "{user_text}"
  JARVIS replied: "{jarvis_text}"

OUTPUT:"""
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_skill_review.py -v
```

Expected: PASS — all skill_review tests including the 2 new ones.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/skill_review.py src/voice-agent/tests/test_skill_review.py
git commit -m "feat(self-improve): split reviewer prompt — memory vs skill vs procedure routing

Explicit save phrases ('remember this', 'save that') now route to
kind=memory or kind=procedure, not kind=skill_create. Style/tone
corrections stay as skills. Procedure kind added to schema.

Spec: docs/superpowers/specs/2026-05-24-jarvis-memory-and-procedure-loop-design.md
Track 5 of the memory-loop rebuild."
```

---

## Task 2: Procedure target in file_memory (Track 2a)

**Files:**
- Modify: `src/voice-agent/pipeline/file_memory.py`
- Test: `src/voice-agent/tests/test_file_memory.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `src/voice-agent/tests/test_file_memory.py`:

```python
def test_procedure_target_is_valid(tmp_path, monkeypatch):
    """Track 2a: VALID_TARGETS includes 'procedure'."""
    from pipeline import file_memory
    assert "procedure" in file_memory.VALID_TARGETS
    assert file_memory.PROCEDURE_CHAR_LIMIT == 8000


def test_procedure_round_trip(tmp_path, monkeypatch):
    """Track 2a: add → read → remove cycle on procedure target."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline import file_memory
    file_memory.reload_store()  # pick up tmp HOME

    body = "## deploy-app\n1. run pytest\n2. git push\n3. check CI"
    res = file_memory.add("procedure", body)
    assert res["success"], res

    # File exists on disk
    procedures_md = tmp_path / "memories" / "PROCEDURES.md"
    assert procedures_md.exists()
    assert "deploy-app" in procedures_md.read_text(encoding="utf-8")

    # Read returns the entry
    read_res = file_memory.read("procedure")
    assert read_res["success"]
    assert any("deploy-app" in e for e in read_res["entries"])

    # Remove
    rm_res = file_memory.remove("procedure", "deploy-app")
    assert rm_res["success"]


def test_procedure_char_limit_enforced(tmp_path, monkeypatch):
    """Track 2a: adding past the cap returns a clear error."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline import file_memory
    file_memory.reload_store()

    # Fill close to the cap
    big = "x" * 7900
    res1 = file_memory.add("procedure", big)
    assert res1["success"], res1

    # Adding another big entry should fail with a clear message
    res2 = file_memory.add("procedure", "x" * 500)
    assert not res2["success"]
    assert "chars" in res2["error"].lower()


def test_procedure_snapshot_block(tmp_path, monkeypatch):
    """Track 2a: snapshot_for_prompt includes PROCEDURES block when entries exist."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline import file_memory
    file_memory.reload_store()

    # Empty: no block
    snapshot = file_memory.snapshot_for_prompt()
    assert "PROCEDURES" not in snapshot

    # After add + reload: block present (reload re-freezes snapshot)
    file_memory.add("procedure", "## test\n1. step")
    file_memory.reload_store()
    snapshot = file_memory.snapshot_for_prompt()
    assert "PROCEDURES" in snapshot
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_file_memory.py -k procedure -v
```

Expected: FAIL — `procedure` not in `VALID_TARGETS`, `PROCEDURE_CHAR_LIMIT` undefined.

- [ ] **Step 3: Implement in `pipeline/file_memory.py`**

(a) Update constants near top (around line 53-61):

```python
ENTRY_DELIMITER = "\n§\n"

# Char budgets per store. Generous enough for a curated set of durable
# facts, tight enough that the frozen snapshot can't balloon the prompt.
MEMORY_CHAR_LIMIT = 2200
USER_CHAR_LIMIT = 1375
PROCEDURE_CHAR_LIMIT = 8000

# Canonical store targets the tool accepts.
VALID_TARGETS = ("memory", "user", "procedure")
```

(b) Update `MemoryStore.__init__` to track procedure entries:

```python
class MemoryStore:
    def __init__(
        self,
        memory_char_limit: int = MEMORY_CHAR_LIMIT,
        user_char_limit: int = USER_CHAR_LIMIT,
        procedure_char_limit: int = PROCEDURE_CHAR_LIMIT,
    ):
        self.memory_entries: List[str] = []
        self.user_entries: List[str] = []
        self.procedure_entries: List[str] = []
        self.memory_char_limit = memory_char_limit
        self.user_char_limit = user_char_limit
        self.procedure_char_limit = procedure_char_limit
        # Frozen snapshot for the system prompt — set once at load_from_disk().
        self._snapshot: Dict[str, str] = {"memory": "", "user": "", "procedure": ""}
```

(c) Update `load_from_disk` to load procedures:

```python
    def load_from_disk(self) -> None:
        """Load entries from MEMORY.md + USER.md + PROCEDURES.md and capture
        the frozen system-prompt snapshot. Call once at session start."""
        mem_dir = _memory_dir()
        mem_dir.mkdir(parents=True, exist_ok=True)

        self.memory_entries = self._read_file(mem_dir / "MEMORY.md")
        self.user_entries = self._read_file(mem_dir / "USER.md")
        self.procedure_entries = self._read_file(mem_dir / "PROCEDURES.md")

        # Deduplicate (preserve order, keep first occurrence).
        self.memory_entries = list(dict.fromkeys(self.memory_entries))
        self.user_entries = list(dict.fromkeys(self.user_entries))
        self.procedure_entries = list(dict.fromkeys(self.procedure_entries))

        self._snapshot = {
            "memory": self._render_block("memory", self.memory_entries),
            "user": self._render_block("user", self.user_entries),
            "procedure": self._render_block("procedure", self.procedure_entries),
        }
```

(d) Update `snapshot_for_prompt` ordering (USER → MEMORY → PROCEDURES):

```python
    def snapshot_for_prompt(self) -> str:
        """Return the FROZEN MEMORY + USER + PROCEDURES blocks for
        system-prompt injection."""
        parts = [
            self._snapshot.get("user", ""),
            self._snapshot.get("memory", ""),
            self._snapshot.get("procedure", ""),
        ]
        body = "\n\n".join(p for p in parts if p)
        return body
```

(e) Update `_path_for`, `_entries_for`, `_set_entries`, `_char_limit`, `_render_block`:

```python
    @staticmethod
    def _path_for(target: str) -> Path:
        mem_dir = _memory_dir()
        if target == "user":
            return mem_dir / "USER.md"
        if target == "procedure":
            return mem_dir / "PROCEDURES.md"
        return mem_dir / "MEMORY.md"

    def _entries_for(self, target: str) -> List[str]:
        if target == "user":
            return self.user_entries
        if target == "procedure":
            return self.procedure_entries
        return self.memory_entries

    def _set_entries(self, target: str, entries: List[str]) -> None:
        if target == "user":
            self.user_entries = entries
        elif target == "procedure":
            self.procedure_entries = entries
        else:
            self.memory_entries = entries

    def _char_limit(self, target: str) -> int:
        if target == "user":
            return self.user_char_limit
        if target == "procedure":
            return self.procedure_char_limit
        return self.memory_char_limit

    def _render_block(self, target: str, entries: List[str]) -> str:
        if not entries:
            return ""
        limit = self._char_limit(target)
        content = ENTRY_DELIMITER.join(entries)
        current = len(content)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0
        if target == "user":
            header = f"USER PROFILE (who Ulrich is) [{pct}% — {current:,}/{limit:,} chars]"
        elif target == "procedure":
            header = f"PROCEDURES (named multi-step processes) [{pct}% — {current:,}/{limit:,} chars]"
        else:
            header = f"MEMORY (your durable notes) [{pct}% — {current:,}/{limit:,} chars]"
        sep = "═" * 46
        return f"{sep}\n{header}\n{sep}\n{content}"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_file_memory.py -v
```

Expected: PASS — all file_memory tests including the 4 new procedure tests; existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/file_memory.py src/voice-agent/tests/test_file_memory.py
git commit -m "feat(memory): add procedure target to file_memory store

New PROCEDURES.md store (8000 char cap) alongside MEMORY.md and USER.md.
Standard add/replace/remove/read surface, atomic-replace + per-target
flock locking. Snapshot order: USER → MEMORY → PROCEDURES.

Spec: docs/superpowers/specs/2026-05-24-jarvis-memory-and-procedure-loop-design.md
Track 2a."
```

---

## Task 3: Procedure PROPOSAL_KIND in skill_review (Track 2b)

**Files:**
- Modify: `src/voice-agent/pipeline/skill_review.py` (constants, `_validate_payload`, `apply_proposal`)
- Test: `src/voice-agent/tests/test_skill_review_procedure_kind.py` (NEW)

- [ ] **Step 1: Write the failing test**

Create `src/voice-agent/tests/test_skill_review_procedure_kind.py`:

```python
"""Track 2b — procedure as a PROPOSAL_KIND in skill_review.

Validates: kind in PROPOSAL_KINDS, _validate_payload accepts/rejects,
apply_proposal writes through to file_memory's procedure target.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def test_procedure_in_proposal_kinds():
    from pipeline.skill_review import PROPOSAL_KINDS
    assert "procedure" in PROPOSAL_KINDS


def test_validate_payload_accepts_valid_procedure():
    from pipeline.skill_review import _validate_payload
    payload = {"name": "deploy-app", "steps": ["run pytest", "git push", "check CI"]}
    cleaned = _validate_payload("procedure", payload)
    assert cleaned is not None
    assert cleaned["name"] == "deploy-app"
    assert cleaned["steps"] == ["run pytest", "git push", "check CI"]


@pytest.mark.parametrize("bad_payload", [
    {"name": "", "steps": ["a"]},
    {"name": "Deploy App", "steps": ["a"]},   # not kebab-case
    {"name": "deploy-app", "steps": []},
    {"name": "deploy-app", "steps": "not a list"},
    {"name": "deploy-app"},  # missing steps
    {"steps": ["a"]},        # missing name
])
def test_validate_payload_rejects_bad_procedure(bad_payload):
    from pipeline.skill_review import _validate_payload
    cleaned = _validate_payload("procedure", bad_payload)
    assert cleaned is None


def test_apply_proposal_writes_procedure(tmp_path, monkeypatch):
    """Apply a procedure proposal → PROCEDURES.md gains the entry."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline import file_memory
    file_memory.reload_store()

    from pipeline.skill_review import Proposal, apply_proposal
    p = Proposal(
        kind="procedure",
        payload={"name": "deploy-app", "steps": ["run pytest", "git push"]},
        rationale="testing",
        source_turn_id=42,
    )
    res = apply_proposal(p)
    assert res.ok, res.detail

    # File exists with content
    procedures_md = tmp_path / "memories" / "PROCEDURES.md"
    assert procedures_md.exists()
    body = procedures_md.read_text(encoding="utf-8")
    assert "deploy-app" in body
    assert "run pytest" in body
    assert "git push" in body
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_skill_review_procedure_kind.py -v
```

Expected: FAIL with `"procedure" not in PROPOSAL_KINDS`.

- [ ] **Step 3: Implement in `pipeline/skill_review.py`**

(a) Update `PROPOSAL_KINDS` (line ~219):

```python
PROPOSAL_KINDS = ("skill_create", "skill_patch", "memory", "procedure")
```

(b) Add a kebab-case helper near the existing imports (top of file):

```python
import re as _re
_PROCEDURE_NAME_RE = _re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
```

(c) Extend `_validate_payload` (after the existing `memory` branch around line 325-340):

```python
def _validate_payload(kind: str, payload: dict) -> dict | None:
    """Return a cleaned payload dict for `kind`, or None if invalid /
    junk-filtered."""
    if kind == "memory":
        category = str(payload.get("category", "")).strip().lower()
        if category not in _VALID_MEMORY_CATEGORIES:
            return None
        content = str(payload.get("content", "")).strip()
        if not content or len(content) > 500:
            return None
        return {"category": category, "content": content}

    if kind == "procedure":
        name = str(payload.get("name", "")).strip()
        if not name or not _PROCEDURE_NAME_RE.match(name):
            return None
        steps = payload.get("steps")
        if not isinstance(steps, list) or not steps:
            return None
        cleaned_steps = [str(s).strip() for s in steps if str(s).strip()]
        if not cleaned_steps:
            return None
        return {"name": name, "steps": cleaned_steps}

    # ... existing skill_create / skill_patch branches ...
```

(d) Extend `apply_proposal` (after the existing `memory` branch around line 609):

```python
        if p.kind == "procedure":
            # File-backed procedure store. Body is a markdown-shaped
            # numbered list with the name as a heading — readable when
            # injected into the supervisor's system prompt.
            from pipeline import file_memory

            name = p.payload["name"]
            steps = p.payload["steps"]
            body = (
                f"## {name}\n"
                + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
            )
            res = file_memory.add("procedure", body)
            ok = bool(isinstance(res, dict) and res.get("success"))
            detail = (
                f"procedure.add name={name}"
                if ok
                else str((res or {}).get("error", "procedure.add failed"))
            )
            return ApplyResult(proposal=p, ok=ok, detail=detail)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_skill_review_procedure_kind.py -v
.venv/bin/python -m pytest tests/test_skill_review.py -v   # ensure no regression
```

Expected: PASS — all procedure tests + existing skill_review tests.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/skill_review.py src/voice-agent/tests/test_skill_review_procedure_kind.py
git commit -m "feat(self-improve): add procedure PROPOSAL_KIND

PROPOSAL_KINDS now includes 'procedure'. _validate_payload enforces
kebab-case name + non-empty step list. apply_proposal writes through
to file_memory.add('procedure', body) with a markdown-shaped numbered
list body.

Spec: docs/superpowers/specs/2026-05-24-jarvis-memory-and-procedure-loop-design.md
Track 2b."
```

---

## Task 4: Memory tool schema extension for procedure target (Track 2c)

**Files:**
- Modify: `src/voice-agent/tools/memory.py`
- Test: `src/voice-agent/tests/test_memory_recall_tool.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `src/voice-agent/tests/test_memory_recall_tool.py`:

```python
def test_memory_tool_schema_has_procedure_target():
    """Track 2c: tool schema's target enum includes 'procedure'."""
    from tools.memory import MEMORY_SCHEMA
    target_prop = MEMORY_SCHEMA["parameters"]["properties"]["target"]
    assert "procedure" in target_prop["enum"]


def test_memory_tool_schema_has_name_param():
    """Track 2c: tool schema has 'name' param for procedure target."""
    from tools.memory import MEMORY_SCHEMA
    props = MEMORY_SCHEMA["parameters"]["properties"]
    assert "name" in props
    assert "kebab-case" in props["name"]["description"].lower() \
        or "procedure" in props["name"]["description"].lower()


def test_memory_tool_rejects_procedure_add_without_name(tmp_path, monkeypatch):
    """Track 2c: action=add target=procedure without 'name' returns an error."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline import file_memory
    file_memory.reload_store()

    from tools.memory import _handle_memory
    import json
    res_str = _handle_memory({"action": "add", "target": "procedure",
                              "content": "1. step one"})
    res = json.loads(res_str)
    assert not res.get("success", True)
    assert "name" in res.get("error", "").lower()


def test_memory_tool_accepts_procedure_add_with_name(tmp_path, monkeypatch):
    """Track 2c: action=add target=procedure with 'name' writes to PROCEDURES.md."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    from pipeline import file_memory
    file_memory.reload_store()

    from tools.memory import _handle_memory
    import json
    res_str = _handle_memory({
        "action": "add", "target": "procedure",
        "name": "morning-routine",
        "content": "1. coffee\n2. shower\n3. code",
    })
    res = json.loads(res_str)
    assert res.get("success"), res

    procedures_md = tmp_path / "memories" / "PROCEDURES.md"
    assert procedures_md.exists()
    assert "morning-routine" in procedures_md.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_memory_recall_tool.py -k "procedure or name" -v
```

Expected: FAIL — schema doesn't have procedure target or name param.

- [ ] **Step 3: Update `tools/memory.py`**

(a) Update `MEMORY_SCHEMA` (around lines 87-143). Replace the existing schema with:

```python
MEMORY_SCHEMA = {
    "name": "memory",
    "description": (
        "Save or update durable information that survives across sessions. "
        "Memory is injected into your system prompt at the start of every "
        "session, so keep entries compact and focused on facts that will "
        "still matter later.\n\n"
        "WHEN TO SAVE (proactively — don't wait to be asked):\n"
        "- Ulrich corrects you or says 'remember this' / 'don't do that again'\n"
        "- He shares a preference, habit, or personal detail (name, role, "
        "timezone, how he likes replies)\n"
        "- You learn a stable fact about his work or environment that will be "
        "useful again\n"
        "- He asks you to 'save this process' or 'remember how to X' — "
        "store as target='procedure' with a kebab-case name and numbered steps\n\n"
        "THREE STORES (the 'target'):\n"
        "- 'user' (USER.md): who Ulrich is — role, background, preferences, "
        "communication style, pet peeves.\n"
        "- 'memory' (MEMORY.md): your own notes — environment facts, project "
        "conventions, tool quirks, lessons learned.\n"
        "- 'procedure' (PROCEDURES.md): named multi-step processes Ulrich "
        "wants to invoke later. Requires 'name' (kebab-case, e.g. "
        "'deploy-app') and 'content' as a numbered step list.\n\n"
        "ACTIONS:\n"
        "- add     — store a new entry (needs 'content'; procedure also needs 'name').\n"
        "- replace — update an existing entry; 'old_text' is a short unique "
        "substring identifying it, 'content' is the new text.\n"
        "- remove  — delete an entry; 'old_text' identifies it.\n"
        "- read    — list the live entries in a store (use to audit before "
        "editing).\n\n"
        "DO save before replying when Ulrich states something durable about "
        "his life or work — silent, no need to announce it.\n"
        "DON'T save: code patterns, file paths, git history, debug recipes, "
        "anything already in your instructions, ephemeral state ('I'm hungry', "
        "'working on X right now'), or credentials. Write plain assertions, "
        "never narration ('The user is asking about…')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove", "read"],
                "description": "What to do.",
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user", "procedure"],
                "description": "Which store: 'user' for Ulrich's profile, 'memory' for your own notes, 'procedure' for named multi-step processes.",
            },
            "content": {
                "type": "string",
                "description": "The entry text. Required for 'add' and 'replace'. For target='procedure', supply a numbered step list (e.g. '1. step one\\n2. step two').",
            },
            "old_text": {
                "type": "string",
                "description": "Short unique substring identifying the entry to replace or remove.",
            },
            "name": {
                "type": "string",
                "description": "Kebab-case identifier (e.g. 'deploy-app'). Required when target='procedure' and action='add'.",
            },
        },
        "required": ["action", "target"],
    },
}
```

(b) Update `_handle_memory` to validate `name` for procedure target. Find the existing function (around line 42-82) and replace the `if action == "add":` branch:

```python
    if action == "add":
        if not content:
            return tool_error("content is required for 'add'.", success=False)
        if target == "procedure":
            name = str(args.get("name", "")).strip()
            if not name:
                return tool_error("name is required for action='add' with target='procedure'. Use kebab-case (e.g. 'deploy-app').", success=False)
            import re as _re
            if not _re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", name):
                return tool_error(f"name {name!r} is not kebab-case. Use lowercase letters/digits/dashes only.", success=False)
            # Prepend the name as a heading so the entry is self-describing
            # in the snapshot. The supervisor's prompt sees "## deploy-app\n1. ...".
            content = f"## {name}\n{content}"
        result = file_memory.add(target, content)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_memory_recall_tool.py -v
```

Expected: PASS — all memory tool tests including new procedure-target tests.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/tools/memory.py src/voice-agent/tests/test_memory_recall_tool.py
git commit -m "feat(memory): add procedure target + name param to memory tool

target enum now includes 'procedure'. New 'name' parameter required when
target='procedure' + action='add'. Tool description teaches the model
when to use procedure ('save this process', 'remember how to X').

Spec: docs/superpowers/specs/2026-05-24-jarvis-memory-and-procedure-loop-design.md
Track 2c."
```

---

## Task 5: Telemetry schema migration (6 new columns)

**Files:**
- Modify: `src/voice-agent/pipeline/turn_telemetry.py` (find the schema init / `ensure_schema` function)
- Test: `src/voice-agent/tests/test_turn_telemetry_migration.py` (NEW)

- [ ] **Step 1: Locate the existing migration pattern**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
grep -n "ALTER TABLE\|ADD COLUMN\|ensure_schema\|_init_schema" pipeline/turn_telemetry.py | head -20
```

Note the existing add-column pattern (the schema has been extended additively before — e.g. `subagent`, `interrupted`, `input_tokens`, `prompt_cached_tokens` were all added this way).

- [ ] **Step 2: Write the failing test**

Create `src/voice-agent/tests/test_turn_telemetry_migration.py`:

```python
"""Spec A — turn_telemetry schema gains 6 columns for memory-loop observability."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def test_new_columns_present(tmp_path, monkeypatch):
    db_path = tmp_path / "turn_telemetry.db"
    monkeypatch.setenv("JARVIS_TURN_TELEMETRY_DB", str(db_path))

    from pipeline import turn_telemetry
    turn_telemetry.ensure_schema()  # idempotent — should be safe to call

    conn = sqlite3.connect(str(db_path))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(turns)").fetchall()}
    conn.close()

    expected = {
        "save_trigger_fired",
        "recall_trigger_fired",
        "procedure_match_offered",
        "procedure_match_executed",
        "tool_call_count",
        "had_tool_error",
    }
    missing = expected - cols
    assert not missing, f"Missing columns: {missing}"


def test_migration_is_idempotent(tmp_path, monkeypatch):
    """Calling ensure_schema twice doesn't fail (duplicate-column error caught)."""
    db_path = tmp_path / "turn_telemetry.db"
    monkeypatch.setenv("JARVIS_TURN_TELEMETRY_DB", str(db_path))

    from pipeline import turn_telemetry
    turn_telemetry.ensure_schema()
    turn_telemetry.ensure_schema()  # second call must not raise
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_turn_telemetry_migration.py -v
```

Expected: FAIL — columns missing.

- [ ] **Step 4: Add the migration in `pipeline/turn_telemetry.py`**

Find the existing `ensure_schema` (or equivalent) function. Append to its body (after existing ALTER statements):

```python
def _add_column_if_missing(conn: sqlite3.Connection, table: str, col: str, ddl: str) -> None:
    """Idempotent ADD COLUMN — swallow the 'duplicate column name' error."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise


def ensure_schema() -> None:
    # ... existing schema setup ...
    conn = _connect()
    try:
        # ... existing CREATE TABLE / ALTERs ...

        # Memory-loop observability columns (Spec 2026-05-24).
        _add_column_if_missing(conn, "turns", "save_trigger_fired", "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "turns", "recall_trigger_fired", "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "turns", "procedure_match_offered", "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "turns", "procedure_match_executed", "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "turns", "tool_call_count", "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "turns", "had_tool_error", "INTEGER DEFAULT 0")

        conn.commit()
    finally:
        conn.close()
```

(Adapt to the actual existing structure — if `ensure_schema` already exists with a different name or shape, integrate the `_add_column_if_missing` calls in the right place. The migration must run at module import OR at first turn-write; check existing pattern.)

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_turn_telemetry_migration.py -v
.venv/bin/python -m pytest tests/ -k turn_telemetry -v  # no regression
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/pipeline/turn_telemetry.py src/voice-agent/tests/test_turn_telemetry_migration.py
git commit -m "feat(telemetry): add 6 columns for memory-loop observability

save_trigger_fired, recall_trigger_fired, procedure_match_offered,
procedure_match_executed, tool_call_count, had_tool_error.
Idempotent ADD COLUMN via _add_column_if_missing helper.

Spec: docs/superpowers/specs/2026-05-24-jarvis-memory-and-procedure-loop-design.md"
```

---

## Task 6: Save-confab guard in confab_detector (Track 3)

**Files:**
- Modify: `src/voice-agent/confab_detector.py`
- Test: `src/voice-agent/tests/test_confab_detector.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `src/voice-agent/tests/test_confab_detector.py`:

```python
def test_save_claim_without_memory_tool_flagged():
    """Track 3: 'I'll remember' without a memory tool call → confab."""
    from confab_detector import looks_like_confabulation
    prior_messages = [
        # Just a normal user/assistant exchange — no memory tool call
        type("M", (), {"role": "user", "content": "tell me about cats"})(),
        type("M", (), {"role": "assistant", "content": "cats are felines."})(),
    ]
    flagged, reason = looks_like_confabulation(
        assistant_text="I'll remember that for next time.",
        prior_messages=prior_messages,
    )
    assert flagged
    assert "save" in reason.lower() or "memory" in reason.lower()


def test_save_claim_with_memory_tool_accepted():
    """Track 3: 'I'll remember' WITH a memory tool call → not confab."""
    from confab_detector import looks_like_confabulation

    class FCO:  # mimic FunctionCallOutput shape
        name = "memory"
        output = '{"success": true}'
        call_id = "x"

    prior_messages = [
        type("M", (), {"role": "user", "content": "remember I love sushi"})(),
        type("M", (), {"role": "assistant", "content": ""})(),
        FCO(),  # memory tool result in prior history
    ]
    flagged, reason = looks_like_confabulation(
        assistant_text="I'll remember that for next time.",
        prior_messages=prior_messages,
    )
    assert not flagged


def test_no_save_claim_no_flag():
    """Track 3: assistant says nothing memory-shaped → no confab class fires."""
    from confab_detector import looks_like_confabulation
    flagged, reason = looks_like_confabulation(
        assistant_text="The sky is blue.",
        prior_messages=[],
    )
    assert not flagged
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_confab_detector.py -k save -v
```

Expected: FAIL — save-claim class doesn't exist yet.

- [ ] **Step 3: Implement in `confab_detector.py`**

(a) Add the save-claim pattern list near the top (after the existing pattern lists):

```python
# Save-claim patterns (Spec 2026-05-24, Track 3).
# An assistant turn that claims to have saved something is a confab if
# no memory tool call appears in the recent chat_ctx tail.
_SAVE_CLAIM_PATTERNS = [
    re.compile(r"(?i)\bi'?ll\s+remember\b"),
    re.compile(r"(?i)\bi'?ve\s+(saved|noted|stored|added|remembered)\b"),
    re.compile(r"(?i)\bgot\s+it[,.]?\s+(saved|noted|added|remembered)\b"),
    re.compile(r"(?i)\badded\s+to\s+(memory|user|procedure)\b"),
    re.compile(r"(?i)\bremembered\b.*\bfor\s+(next\s+time|future|later)\b"),
]
```

(b) Add the lookback helper:

```python
def _has_recent_memory_tool_call(prior_messages: list) -> bool:
    """True if any of the last 8 prior messages is a memory tool call or
    a FunctionCallOutput with name='memory'. Pure function; no I/O.

    Mirrors has_recent_tool_evidence's lookback but specialised to the
    memory tool (any action — add/replace/remove/read all count)."""
    if not prior_messages:
        return False
    tail = list(prior_messages)[-8:]
    for msg in tail:
        # FunctionCallOutput / tool-result shape
        name = getattr(msg, "name", None) or _msg_attr(msg, "name")
        if name == "memory":
            return True
        # Anthropic-shape tool_use blocks may be in content
        content = getattr(msg, "content", None) or _msg_attr(msg, "content")
        if isinstance(content, list):
            for block in content:
                btype = getattr(block, "type", None) or (
                    block.get("type") if isinstance(block, dict) else None
                )
                bname = getattr(block, "name", None) or (
                    block.get("name") if isinstance(block, dict) else None
                )
                if btype in ("tool_use", "tool_result") and bname == "memory":
                    return True
    return False
```

(c) Extend `looks_like_confabulation` to call the save-claim path. Find the existing function (around line 306) and add the save-claim check BEFORE the final return:

```python
def looks_like_confabulation(
    assistant_text: str,
    prior_messages: list,
) -> tuple[bool, str]:
    """... existing docstring ..."""
    # Master kill switch (existing)
    if os.environ.get("JARVIS_CONFAB_DETECTOR", "1") == "0":
        return False, ""

    # ... existing tool-claim regex check ...
    # (KEEP the existing return for tool_claim_without_evidence)

    # Save-claim class (Track 3, Spec 2026-05-24).
    if os.environ.get("JARVIS_CONFAB_SAVE_DISABLED", "0") != "1":
        for pat in _SAVE_CLAIM_PATTERNS:
            if pat.search(assistant_text):
                if not _has_recent_memory_tool_call(prior_messages):
                    return True, "save_claim_without_evidence"
                break  # save-claim matched but evidence present → not a confab

    return False, ""
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_confab_detector.py -v
```

Expected: PASS — all confab tests including the 3 new ones, and the existing tool-claim tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/confab_detector.py src/voice-agent/tests/test_confab_detector.py
git commit -m "feat(confab): add save-claim regex class

looks_like_confabulation now flags 'I'll remember' / 'saved' / 'noted'
assistant turns when no memory tool call appears in the prior 8 messages.
JARVIS_CONFAB_SAVE_DISABLED=1 kills only this class (existing master
switch JARVIS_CONFAB_DETECTOR=0 still kills everything).

Spec: docs/superpowers/specs/2026-05-24-jarvis-memory-and-procedure-loop-design.md
Track 3."
```

---

## Task 7: Trigger regex + system-message inject (Track 1)

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` (module-level constants near top + `JarvisAgent.on_user_turn_completed` at line 3350)
- Test: `src/voice-agent/tests/test_save_trigger_regex.py` (NEW)
- Test: `src/voice-agent/tests/test_turn_pipeline_trigger_inject.py` (NEW)

- [ ] **Step 1: Write the failing regex tests**

Create `src/voice-agent/tests/test_save_trigger_regex.py`:

```python
"""Track 1 — save/recall trigger regex (Spec 2026-05-24).

The regex is intentionally LIBERAL (supervisor LLM is the second gate).
These tests pin the documented behaviour table.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


# Save trigger — true positives
@pytest.mark.parametrize("text", [
    "Jarvis, remember this: I prefer fish.",
    "Jarvis remember that I'm allergic to fish",
    "Could you save that for me?",
    "Don't forget I prefer terse replies",
    "remember me to call the bank",
    "Save this process: deploy = run tests then push",
    "Memorize this for next time",
    "write this down please",
])
def test_save_trigger_matches(text):
    from jarvis_agent import _SAVE_TRIGGER_RE
    assert _SAVE_TRIGGER_RE.search(text), f"should match: {text!r}"


# Save trigger — known liberal matches (false positives the supervisor handles)
@pytest.mark.parametrize("text", [
    "I'll always remember that joke",  # 'remember that' — liberal match, supervisor decides
])
def test_save_trigger_liberal_matches_acknowledged(text):
    """Regex IS liberal; supervisor LLM is the 2nd gate. Document the behaviour."""
    from jarvis_agent import _SAVE_TRIGGER_RE
    assert _SAVE_TRIGGER_RE.search(text)


# Save trigger — true negatives
@pytest.mark.parametrize("text", [
    "This song is unforgettable",          # no save verb
    "Remember when we did the deploy?",    # 'remember when' → recall territory
    "what is memory?",                      # no save verb
    "",                                     # empty
])
def test_save_trigger_does_not_match(text):
    from jarvis_agent import _SAVE_TRIGGER_RE
    assert not _SAVE_TRIGGER_RE.search(text), f"should NOT match: {text!r}"


# Recall trigger — true positives
@pytest.mark.parametrize("text", [
    "Do you remember when we talked about Shelby?",
    "What did I tell you about my allergies?",
    "Have I told you about my project?",
    "Remind me about the deploy procedure",
    "remind me of my morning routine",
    "remind me what I said about coffee",
])
def test_recall_trigger_matches(text):
    from jarvis_agent import _RECALL_TRIGGER_RE
    assert _RECALL_TRIGGER_RE.search(text), f"should match: {text!r}"


# Recall trigger — true negatives
@pytest.mark.parametrize("text", [
    "I don't remember",                  # not a question to JARVIS
    "Remember this: ...",                 # that's a save trigger
])
def test_recall_trigger_does_not_match(text):
    from jarvis_agent import _RECALL_TRIGGER_RE
    assert not _RECALL_TRIGGER_RE.search(text), f"should NOT match: {text!r}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_save_trigger_regex.py -v
```

Expected: FAIL — `ImportError: cannot import name '_SAVE_TRIGGER_RE'`.

- [ ] **Step 3: Add the regex constants to `jarvis_agent.py`**

Near the top of `jarvis_agent.py`, in the existing module-level regex section (search for `_KILL_PHRASES = re.compile`), add:

```python
# ─── Save / recall triggers (Spec 2026-05-24, Track 1) ──────────────────
#
# Liberal-by-design regex. Matches are necessary-but-not-sufficient: the
# supervisor LLM is the second gate and decides whether to actually save.
# A "false positive" here just costs ~50 tokens of inject prompt; it does
# NOT cause a bad memory write.
#
# Gated by JARVIS_SAVE_TRIGGER_LIVE / JARVIS_RECALL_TRIGGER_LIVE env vars
# (default shadow — log match, don't inject).

_SAVE_TRIGGER_RE = re.compile(
    r"""(?ix)
    (?:^|[.?!,\s])\s*
    (?:
        remember\s+(?:this|that|me|to)\b
      | save\s+(?:this|that|it)\b
      | don'?t\s+forget\b
      | write\s+this\s+down\b
      | memori[sz]e\s+(?:this|that|it|for)\b
    )
    """
)

_RECALL_TRIGGER_RE = re.compile(
    r"""(?ix)
    (?:^|[.?!,\s])\s*
    (?:
        do\s+you\s+remember\b
      | what\s+did\s+i\s+tell\s+you\b
      | have\s+i\s+told\s+you\b
      | remind\s+me\s+(?:about|of|what)\b
    )
    """
)

_SAVE_TRIGGER_SYSTEM_MESSAGE = (
    "USER REQUESTED A SAVE. Identify the durable fact / preference / "
    "procedure in their message and call `memory()` (target='user' for "
    "facts about Ulrich, 'memory' for environment notes, 'procedure' for "
    "named multi-step processes — supply 'name' as a kebab-case identifier) "
    "BEFORE replying. Then reply with a short acknowledgment ('got it' / 'saved')."
)

_RECALL_TRIGGER_SYSTEM_MESSAGE = (
    "USER REQUESTED A RECALL. Call `recall(query=<their question>)` FIRST "
    "to fetch what you know about them from past conversations. Use the "
    "returned context to answer. Do NOT reply 'this conversation just "
    "started' or 'I don't have prior context'."
)
```

- [ ] **Step 4: Run regex tests to verify they pass**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_save_trigger_regex.py -v
```

Expected: PASS — all parameterised regex cases.

- [ ] **Step 5: Write the inject-path test**

Create `src/voice-agent/tests/test_turn_pipeline_trigger_inject.py`:

```python
"""Track 1 — system-message inject through on_user_turn_completed."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def test_inject_helper_appends_system_message_when_save_matches(monkeypatch):
    """When user_text matches save trigger and LIVE=1, system message is injected."""
    monkeypatch.setenv("JARVIS_SAVE_TRIGGER_LIVE", "1")
    from jarvis_agent import _maybe_inject_trigger_message
    chat_ctx = MagicMock()
    chat_ctx.add_message = MagicMock()
    fired = _maybe_inject_trigger_message(chat_ctx, "Jarvis, remember this: I love fish")
    assert fired == "save"
    chat_ctx.add_message.assert_called_once()
    args, kwargs = chat_ctx.add_message.call_args
    # The injected message must be a system role
    call_kwargs = {**kwargs, **(args[0] if args and isinstance(args[0], dict) else {})}
    # Most LiveKit chat_ctx APIs accept role= and content= kwargs
    role = call_kwargs.get("role") or (args[0] if args else None)
    content = call_kwargs.get("content")
    assert role == "system" or "USER REQUESTED A SAVE" in str(call_kwargs)


def test_no_inject_in_shadow_mode(monkeypatch):
    """LIVE unset → shadow mode → no inject, just log."""
    monkeypatch.delenv("JARVIS_SAVE_TRIGGER_LIVE", raising=False)
    from jarvis_agent import _maybe_inject_trigger_message
    chat_ctx = MagicMock()
    chat_ctx.add_message = MagicMock()
    fired = _maybe_inject_trigger_message(chat_ctx, "Jarvis remember this: fish")
    # In shadow mode, return value still reports the match (for telemetry)
    # but chat_ctx is untouched.
    assert fired == "save_shadow"
    chat_ctx.add_message.assert_not_called()


def test_no_inject_when_no_match(monkeypatch):
    monkeypatch.setenv("JARVIS_SAVE_TRIGGER_LIVE", "1")
    monkeypatch.setenv("JARVIS_RECALL_TRIGGER_LIVE", "1")
    from jarvis_agent import _maybe_inject_trigger_message
    chat_ctx = MagicMock()
    chat_ctx.add_message = MagicMock()
    fired = _maybe_inject_trigger_message(chat_ctx, "what's the weather")
    assert fired is None
    chat_ctx.add_message.assert_not_called()


def test_recall_inject(monkeypatch):
    monkeypatch.setenv("JARVIS_RECALL_TRIGGER_LIVE", "1")
    from jarvis_agent import _maybe_inject_trigger_message
    chat_ctx = MagicMock()
    chat_ctx.add_message = MagicMock()
    fired = _maybe_inject_trigger_message(chat_ctx, "do you remember Shelby?")
    assert fired == "recall"
    chat_ctx.add_message.assert_called_once()
```

- [ ] **Step 6: Run inject test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_turn_pipeline_trigger_inject.py -v
```

Expected: FAIL — `_maybe_inject_trigger_message` doesn't exist.

- [ ] **Step 7: Add the inject helper to `jarvis_agent.py`**

Below the regex constants from Step 3, add:

```python
def _maybe_inject_trigger_message(chat_ctx, user_text: str) -> str | None:
    """Run save/recall trigger regex on user_text. If a trigger fires AND
    the corresponding LIVE env var is set, inject a system message into
    chat_ctx and return 'save' / 'recall'. In shadow mode (LIVE unset),
    log the match and return 'save_shadow' / 'recall_shadow' but do NOT
    inject. Returns None if no trigger matched.

    Spec 2026-05-24, Track 1. The regex is liberal; the supervisor LLM
    is the second gate."""
    text = (user_text or "").strip()
    if not text:
        return None

    save_match = bool(_SAVE_TRIGGER_RE.search(text))
    recall_match = bool(_RECALL_TRIGGER_RE.search(text))

    if save_match:
        live = os.environ.get("JARVIS_SAVE_TRIGGER_LIVE", "0") == "1"
        mode = "live" if live else "shadow"
        logger.info(
            "[trigger] save_trigger matched: user_text=%r (mode=%s)",
            text[:120], mode,
        )
        if live:
            try:
                chat_ctx.add_message(role="system", content=_SAVE_TRIGGER_SYSTEM_MESSAGE)
                return "save"
            except Exception as e:
                logger.warning("[trigger] save_trigger inject failed: %s", e)
                return "save_shadow"
        return "save_shadow"

    if recall_match:
        live = os.environ.get("JARVIS_RECALL_TRIGGER_LIVE", "0") == "1"
        mode = "live" if live else "shadow"
        logger.info(
            "[trigger] recall_trigger matched: user_text=%r (mode=%s)",
            text[:120], mode,
        )
        if live:
            try:
                chat_ctx.add_message(role="system", content=_RECALL_TRIGGER_SYSTEM_MESSAGE)
                return "recall"
            except Exception as e:
                logger.warning("[trigger] recall_trigger inject failed: %s", e)
                return "recall_shadow"
        return "recall_shadow"

    return None
```

- [ ] **Step 8: Wire the helper into `on_user_turn_completed`**

Find `JarvisAgent.on_user_turn_completed` at line 3350. In the body — AFTER the existing filter/junk-drop logic and BEFORE the supervisor inference call — add:

```python
        # Spec 2026-05-24, Track 1 — explicit save/recall trigger inject.
        # Runs after junk-drop, before the supervisor sees the turn.
        try:
            trigger_fired = _maybe_inject_trigger_message(turn_ctx, user_text)
            if trigger_fired:
                # Store for the post-turn telemetry write (Task 8 wires the column)
                self._jarvis_turn_trigger_fired = trigger_fired
        except Exception as e:  # noqa: BLE001 — never let trigger break the turn
            logger.warning("[trigger] inject path failed: %s", e)
```

(Adapt `turn_ctx` / `user_text` to whatever the method's local variable names actually are — search for the existing transcript handling and pattern-match. The variable holding the chat context inside `on_user_turn_completed` is `turn_ctx` per LiveKit Agents convention; the user text typically comes from a `new_message` parameter or similar.)

- [ ] **Step 9: Run all tests**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_save_trigger_regex.py tests/test_turn_pipeline_trigger_inject.py -v
.venv/bin/python -c "import jarvis_agent"  # smoke
```

Expected: PASS; clean import.

- [ ] **Step 10: Commit**

```bash
git add src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_save_trigger_regex.py src/voice-agent/tests/test_turn_pipeline_trigger_inject.py
git commit -m "feat(memory): Track 1 — explicit save/recall trigger regex + inject

on_user_turn_completed now matches user utterance against
_SAVE_TRIGGER_RE and _RECALL_TRIGGER_RE. On match, injects a system
message into chat_ctx pre-inference so the supervisor (Sonnet/Haiku)
sees a hard instruction to call memory() or recall().

Default SHADOW mode — log matches but no inject. Flip with
JARVIS_SAVE_TRIGGER_LIVE=1 / JARVIS_RECALL_TRIGGER_LIVE=1 after 24h soak.

Spec: docs/superpowers/specs/2026-05-24-jarvis-memory-and-procedure-loop-design.md
Track 1."
```

---

## Task 8: TurnSnapshot extension (for Track 2.5 success capture)

**Files:**
- Modify: `src/voice-agent/pipeline/skill_review.py` (the `TurnSnapshot` dataclass at line 134-152)
- Test: extend `src/voice-agent/tests/test_skill_review.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_skill_review.py`:

```python
def test_turn_snapshot_has_tool_call_fields():
    """Track 2.5 prereq: TurnSnapshot exposes tool_call_count + had_tool_error."""
    from pipeline.skill_review import TurnSnapshot
    snap = TurnSnapshot(
        turn_id=1, ts_utc="2026-05-24T00:00:00Z",
        user_text="deploy", jarvis_text="done",
        route="TASK", subagent="", computer_use_steps=0,
        tool_call_count=3, had_tool_error=False,
    )
    assert snap.tool_call_count == 3
    assert snap.had_tool_error is False


def test_turn_snapshot_defaults_back_compat():
    """Existing constructors (without the new fields) keep working."""
    from pipeline.skill_review import TurnSnapshot
    snap = TurnSnapshot(
        turn_id=1, ts_utc="2026-05-24T00:00:00Z",
        user_text="hi", jarvis_text="hello",
        route="BANTER", subagent="", computer_use_steps=0,
    )
    assert snap.tool_call_count == 0
    assert snap.had_tool_error is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_skill_review.py::test_turn_snapshot_has_tool_call_fields -v
```

Expected: FAIL — `TypeError: TurnSnapshot.__init__() got an unexpected keyword argument 'tool_call_count'`.

- [ ] **Step 3: Extend `TurnSnapshot` in `pipeline/skill_review.py`**

Find the dataclass at line 134-152 and update:

```python
@dataclass(frozen=True)
class TurnSnapshot:
    """A reviewable turn pulled from telemetry. Only the fields the
    reviewer needs — user text, assistant reply, and the multi-step
    signal (route / subagent / step count / tool-call shape)."""

    turn_id: int
    ts_utc: str
    user_text: str
    jarvis_text: str
    route: str
    subagent: str
    computer_use_steps: int
    # Spec 2026-05-24, Track 2.5 — success-capture gate inputs.
    # Defaults preserve backward-compat for existing callers.
    tool_call_count: int = 0
    had_tool_error: bool = False

    @property
    def reason(self) -> str:
        """Human-readable why-this-was-selected, for the report."""
        if self.subagent:
            return f"subagent={self.subagent}"
        if self.computer_use_steps and self.computer_use_steps >= 1:
            return f"computer_use_steps={self.computer_use_steps}"
        return f"long_{self.route or 'reply'}({len(self.jarvis_text)}c)"
```

- [ ] **Step 4: Update `select_review_candidates` to populate the new fields**

Find `select_review_candidates` (just below the dataclass). Its SQL query reads turn rows from `turn_telemetry.db`. Extend the SELECT to include the new columns (added in Task 5):

```python
def select_review_candidates(limit: int = 10) -> list[TurnSnapshot]:
    # ... existing setup ...
    rows = conn.execute(
        """
        SELECT turn_id, ts_utc, user_text, jarvis_text, route, subagent,
               COALESCE(computer_use_steps, 0),
               COALESCE(tool_call_count, 0),
               COALESCE(had_tool_error, 0)
        FROM turns
        WHERE ...   -- existing WHERE clauses preserved
        ORDER BY ts_utc DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        TurnSnapshot(
            turn_id=r[0], ts_utc=r[1], user_text=r[2], jarvis_text=r[3],
            route=r[4], subagent=r[5] or "", computer_use_steps=r[6],
            tool_call_count=r[7],
            had_tool_error=bool(r[8]),
        )
        for r in rows
    ]
```

(Adapt to actual existing query shape — preserve all existing WHERE / filtering logic; only add the two new columns to the SELECT and constructor args.)

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_skill_review.py -v
```

Expected: PASS — new dataclass tests + all existing tests.

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/pipeline/skill_review.py src/voice-agent/tests/test_skill_review.py
git commit -m "feat(self-improve): extend TurnSnapshot with tool_call_count + had_tool_error

Backward-compatible (defaults to 0/False). select_review_candidates
populates from the telemetry columns added in the prior migration.
Spec: docs/superpowers/specs/2026-05-24-jarvis-memory-and-procedure-loop-design.md
Track 2.5 prereq."
```

---

## Task 9: Success-capture gate (Track 2.5)

**Files:**
- Modify: `src/voice-agent/pipeline/skill_review.py` (add `_is_successful_trajectory`, regex constants, prompt-enrichment in `review_turn`)
- Test: `src/voice-agent/tests/test_success_trajectory_capture.py` (NEW)

- [ ] **Step 1: Write the failing tests**

Create `src/voice-agent/tests/test_success_trajectory_capture.py`:

```python
"""Track 2.5 — successful-trajectory gate for procedure capture."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def _make_snap(**overrides):
    from pipeline.skill_review import TurnSnapshot
    base = dict(
        turn_id=1, ts_utc="2026-05-24T00:00:00Z",
        user_text="Jarvis, deploy the app",
        jarvis_text="Tests passed, pushed, CI green — deployed.",
        route="TASK", subagent="", computer_use_steps=0,
        tool_call_count=3, had_tool_error=False,
    )
    base.update(overrides)
    return TurnSnapshot(**base)


def test_gate_passes_on_happy_path():
    from pipeline.skill_review import _is_successful_trajectory
    snap = _make_snap()
    assert _is_successful_trajectory(snap, wall_clock_s=15.0, user_followup_30s=0)


def test_gate_rejects_too_few_tools():
    from pipeline.skill_review import _is_successful_trajectory
    snap = _make_snap(tool_call_count=1)
    assert not _is_successful_trajectory(snap, wall_clock_s=15.0, user_followup_30s=0)


def test_gate_rejects_tool_error():
    from pipeline.skill_review import _is_successful_trajectory
    snap = _make_snap(had_tool_error=True)
    assert not _is_successful_trajectory(snap, wall_clock_s=15.0, user_followup_30s=0)


def test_gate_rejects_short_wall_clock():
    from pipeline.skill_review import _is_successful_trajectory
    snap = _make_snap()
    assert not _is_successful_trajectory(snap, wall_clock_s=3.0, user_followup_30s=0)


def test_gate_rejects_no_completion_claim():
    from pipeline.skill_review import _is_successful_trajectory
    snap = _make_snap(jarvis_text="I'm working on it...")
    assert not _is_successful_trajectory(snap, wall_clock_s=15.0, user_followup_30s=0)


def test_gate_rejects_no_intent_verb():
    from pipeline.skill_review import _is_successful_trajectory
    snap = _make_snap(user_text="what's the weather")
    assert not _is_successful_trajectory(snap, wall_clock_s=15.0, user_followup_30s=0)


def test_gate_rejects_user_correction_followup():
    from pipeline.skill_review import _is_successful_trajectory
    snap = _make_snap()
    # user_followup_30s=1 means there WAS a followup — risky to capture
    assert not _is_successful_trajectory(snap, wall_clock_s=15.0, user_followup_30s=1)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_success_trajectory_capture.py -v
```

Expected: FAIL — `_is_successful_trajectory` not defined.

- [ ] **Step 3: Implement `_is_successful_trajectory` in `pipeline/skill_review.py`**

Add near the top (after the existing regex / constants section):

```python
# Spec 2026-05-24, Track 2.5 — success-capture gate.
_CLAIM_COMPLETION_RE = re.compile(
    r"(?i)\b(done|deployed|finished|completed|set\s+up|installed|ran|"
    r"pushed|posted|sent|saved|created|opened|closed|updated)\b"
)

_INTENT_VERB_RE = re.compile(
    r"(?i)\b(deploy|find|set\s+up|build|debug|configure|install|"
    r"create|update|push|publish|launch|run|fix|search|check|"
    r"open|close|send|post|book|order)\b"
)


def _is_successful_trajectory(
    snap: "TurnSnapshot",
    wall_clock_s: float,
    user_followup_30s: int,
) -> bool:
    """Gate for procedure capture: was this a successful multi-step task?

    All conditions must hold:
      - route is TASK or REASONING (not BANTER / EMOTIONAL)
      - ≥3 tool calls in the trajectory
      - no tool errors
      - user did NOT follow up with a correction (user_followup_30s in (0, None))
      - JARVIS's reply contains a completion claim
      - user's request contained an intent verb
      - wall-clock ≥ 10s (filters one-shot lookups)
    """
    if snap.route not in ("TASK", "REASONING"):
        return False
    if snap.tool_call_count < 3:
        return False
    if snap.had_tool_error:
        return False
    if user_followup_30s and user_followup_30s != 0:
        return False
    if wall_clock_s < 10.0:
        return False
    if not _CLAIM_COMPLETION_RE.search(snap.jarvis_text or ""):
        return False
    if not _INTENT_VERB_RE.search(snap.user_text or ""):
        return False
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_success_trajectory_capture.py -v
```

Expected: PASS — all 7 gate cases.

- [ ] **Step 5: Wire the gate into `autonomous_review_turn` (prompt-enrichment)**

Find `autonomous_review_turn` (around line 890 in `skill_review.py`). Before calling the reviewer LLM, check the gate:

```python
async def autonomous_review_turn(
    snapshot: TurnSnapshot,
    wall_clock_s: float = 0.0,
    user_followup_30s: int = 0,
) -> ApplyResult | None:
    # ... existing kill-switch + sanity checks ...

    # Spec 2026-05-24, Track 2.5 — if the gate passes, build an enriched
    # snapshot whose user_text carries the trajectory hint. Pure data —
    # the actual prompt template is unchanged.
    if _is_successful_trajectory(snapshot, wall_clock_s, user_followup_30s):
        trajectory_hint = (
            f"\n\nTHIS TURN WAS A SUCCESSFUL MULTI-STEP TASK "
            f"({snapshot.tool_call_count} tool calls, ~{int(wall_clock_s)}s). "
            f"If these steps form a reusable procedure, propose "
            f"kind=procedure with a kebab-case name derived from the "
            f"intent verb + object."
        )
        # Replace the user_text on a copy of the snapshot (frozen dataclass)
        # so the reviewer prompt sees the hint without mutating telemetry.
        from dataclasses import replace as _dc_replace
        snapshot = _dc_replace(snapshot, user_text=snapshot.user_text + trajectory_hint)

    # ... rest of existing autonomous_review_turn body ...
```

(Adapt — the actual function signature may not currently take `wall_clock_s` / `user_followup_30s`; you'll need to thread those from `fire_self_improvement` at the call site. They can default to 0 for backward-compat; turn audit hook supplies them when available.)

- [ ] **Step 6: Run skill_review tests**

```bash
.venv/bin/python -m pytest tests/test_skill_review.py tests/test_success_trajectory_capture.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/voice-agent/pipeline/skill_review.py src/voice-agent/tests/test_success_trajectory_capture.py
git commit -m "feat(self-improve): Track 2.5 — success-capture gate

_is_successful_trajectory checks: route in (TASK,REASONING), ≥3 tool
calls, no tool error, no user correction in followup window, completion
claim in reply, intent verb in user text, wall-clock ≥10s. When the
gate passes, autonomous_review_turn enriches the snapshot's user_text
with a trajectory hint that biases the reviewer toward kind=procedure.

Spec: docs/superpowers/specs/2026-05-24-jarvis-memory-and-procedure-loop-design.md
Track 2.5."
```

---

## Task 10: End-of-turn procedure offer + confirmation (Track 2.5 cont)

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` (pending-offer state + reply append + confirmation matching)
- Test: `src/voice-agent/tests/test_procedure_offer.py` (NEW)

- [ ] **Step 1: Write the failing tests**

Create `src/voice-agent/tests/test_procedure_offer.py`:

```python
"""Track 2.5 — end-of-turn procedure offer + user confirmation flow."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


def test_derive_procedure_name_from_intent():
    """auto-derive 'deploy-app' from 'Jarvis, deploy the app'."""
    from jarvis_agent import _derive_procedure_name
    assert _derive_procedure_name("Jarvis, deploy the app") == "deploy-app"
    assert _derive_procedure_name("can you set up the dev env") == "set-up-dev"
    assert _derive_procedure_name("find me a flight to Tokyo") == "find-flight"


def test_offer_phrase_format():
    """Offer phrase mentions the derived name + asks for confirmation."""
    from jarvis_agent import _build_offer_phrase
    phrase = _build_offer_phrase("deploy-app")
    assert "deploy-app" in phrase
    assert "?" in phrase  # it's a question


def test_confirmation_matches_yes_variants():
    from jarvis_agent import _is_procedure_confirmation
    assert _is_procedure_confirmation("yeah")
    assert _is_procedure_confirmation("yes save it")
    assert _is_procedure_confirmation("sure")
    assert _is_procedure_confirmation("save it as that")
    assert not _is_procedure_confirmation("no thanks")
    assert not _is_procedure_confirmation("not now")
    assert not _is_procedure_confirmation("what's the weather")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_procedure_offer.py -v
```

Expected: FAIL — helpers don't exist.

- [ ] **Step 3: Add helpers to `jarvis_agent.py`**

Below the trigger regex constants from Task 7:

```python
# ─── Procedure offer helpers (Spec 2026-05-24, Track 2.5) ───────────────

_INTENT_OBJECT_RE = re.compile(
    r"(?i)(?:^|,|\.|\bcan you\s+|\bjarvis,?\s+)\s*"
    r"(?P<verb>deploy|find|set\s+up|build|debug|configure|install|"
    r"create|update|push|publish|launch|run|fix|search|check|"
    r"open|close|send|post|book|order)\s+"
    r"(?:me\s+|the\s+|a\s+|an\s+)?"
    r"(?P<obj>[a-z0-9]+(?:\s+[a-z0-9]+){0,2})"
)


def _derive_procedure_name(user_text: str) -> str | None:
    """Auto-derive a kebab-case name from the user's request.
    Returns None if we can't find an intent verb + object."""
    m = _INTENT_OBJECT_RE.search(user_text or "")
    if not m:
        return None
    verb = m.group("verb").strip().lower().replace(" ", "-")
    obj = m.group("obj").strip().lower().split()
    # Use verb + first object word for brevity; skip articles/junk
    skip = {"the", "a", "an", "me", "to", "for", "from", "in", "on"}
    obj_words = [w for w in obj if w not in skip]
    if not obj_words:
        return verb
    return f"{verb}-{obj_words[0]}"


def _build_offer_phrase(name: str) -> str:
    """The one-line offer that gets appended to JARVIS's reply."""
    return f" Want me to keep these steps as '{name}' for next time?"


_CONFIRMATION_RE = re.compile(
    r"(?i)^\s*(?:yeah|yes|yep|sure|ok|okay|save\s+it|please\s+do|"
    r"do\s+it|absolutely|definitely)\b"
)


def _is_procedure_confirmation(user_text: str) -> bool:
    """True if the user's next turn confirms the pending procedure offer."""
    if not user_text:
        return False
    return bool(_CONFIRMATION_RE.search(user_text.strip()))
```

- [ ] **Step 4: Run helper tests**

```bash
.venv/bin/python -m pytest tests/test_procedure_offer.py -v
```

Expected: PASS.

- [ ] **Step 5: Wire pending-offer state + reply append + confirmation handler**

Add a module-level pending-offer dict (and a clear lock) near the existing turn state:

```python
# Spec 2026-05-24, Track 2.5 — pending procedure offers keyed by room_id.
# In-memory only; lost on restart (acceptable UX cost).
_PENDING_PROCEDURE_OFFERS: dict[str, dict] = {}
```

In the post-turn hook where JARVIS's reply is finalised (find the spot where `jarvis_text` is set on `session._jarvis_turn_jarvis_text` or similar, around the `fire_self_improvement` call site at line 5241), add:

```python
        # Spec 2026-05-24, Track 2.5 — if the trajectory was a successful
        # multi-step task AND the procedure-capture switch is on, append
        # the offer phrase to the reply.
        if os.environ.get("JARVIS_PROCEDURE_CAPTURE_DISABLED", "0") != "1":
            try:
                from pipeline.skill_review import _is_successful_trajectory
                # Build a transient snapshot for the gate check
                from pipeline.skill_review import TurnSnapshot as _TS
                snap = _TS(
                    turn_id=0, ts_utc="",
                    user_text=getattr(session, "_jarvis_turn_user_text", "") or "",
                    jarvis_text=getattr(session, "_jarvis_turn_jarvis_text", "") or "",
                    route=getattr(session, "_jarvis_turn_route", "") or "",
                    subagent="", computer_use_steps=0,
                    tool_call_count=int(getattr(session, "_jarvis_turn_tool_call_count", 0)),
                    had_tool_error=bool(getattr(session, "_jarvis_turn_had_tool_error", False)),
                )
                wall_clock_s = float(getattr(session, "_jarvis_turn_wall_clock_s", 0.0))
                if _is_successful_trajectory(snap, wall_clock_s, 0):
                    name = _derive_procedure_name(snap.user_text)
                    if name:
                        room_id = getattr(getattr(session, "room", None), "name", "default")
                        _PENDING_PROCEDURE_OFFERS[room_id] = {
                            "name": name,
                            "steps": getattr(session, "_jarvis_turn_tool_trajectory", []),
                            "ts": time.time(),
                        }
                        offer = _build_offer_phrase(name)
                        # Append to the existing reply
                        await session.say(offer, allow_interruptions=True)
                        logger.info("[procedure] offer appended: name=%s", name)
            except Exception as e:
                logger.warning("[procedure] offer step failed: %s", e)
```

(Adapt to actual existing session attribute names — they may differ slightly. The point is: read the trajectory shape from session state, gate, derive name, store pending offer, append phrase.)

In `on_user_turn_completed` (before the trigger inject of Task 7), check for a pending offer + handle confirmation:

```python
        # Spec 2026-05-24, Track 2.5 — handle confirmation of pending procedure offer
        try:
            room_id = getattr(getattr(self, "room", None), "name", "default")
            pending = _PENDING_PROCEDURE_OFFERS.get(room_id)
            if pending and _is_procedure_confirmation(user_text):
                name = pending["name"]
                steps = pending["steps"]
                from tools.memory import _handle_memory
                _handle_memory({
                    "action": "add", "target": "procedure",
                    "name": name,
                    "content": "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps)),
                })
                del _PENDING_PROCEDURE_OFFERS[room_id]
                logger.info("[procedure] applied: name=%s source=user_confirm", name)
                # Reply succinctly + consume this turn — don't run supervisor
                await self.session.say("Saved.")
                return
            if pending and (time.time() - pending["ts"]) > 60.0:
                # Offer expired (60s grace) — discard
                del _PENDING_PROCEDURE_OFFERS[room_id]
        except Exception as e:
            logger.warning("[procedure] confirmation step failed: %s", e)
```

- [ ] **Step 6: Run all related tests**

```bash
.venv/bin/python -m pytest tests/test_procedure_offer.py -v
.venv/bin/python -c "import jarvis_agent"
```

Expected: PASS; clean import.

- [ ] **Step 7: Commit**

```bash
git add src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_procedure_offer.py
git commit -m "feat(memory): Track 2.5 — procedure capture offer + confirmation

After a successful multi-step task (per Track 2.5 gate), JARVIS appends
'Want me to keep these steps as X?' to the reply and stores a pending
offer in _PENDING_PROCEDURE_OFFERS. On the next user turn, a yes-shape
confirmation applies the procedure via _handle_memory(target=procedure).
60s expiry; in-memory only.

Spec: docs/superpowers/specs/2026-05-24-jarvis-memory-and-procedure-loop-design.md
Track 2.5."
```

---

## Task 11: Procedure auto-inject on intent match (Track 2.5 replay)

**Files:**
- Modify: `src/voice-agent/pipeline/prompt_builder.py` (add `build_procedure_catalog_block` + fuzzy-match helper)
- Modify: `src/voice-agent/jarvis_agent.py` (call the match helper in `on_user_turn_completed`)
- Test: extend `src/voice-agent/tests/test_prompt_builder.py`

- [ ] **Step 1: Write the failing tests**

Append to `src/voice-agent/tests/test_prompt_builder.py`:

```python
def test_build_procedure_catalog_block_empty():
    """Empty procedures → empty block."""
    from pipeline.prompt_builder import build_procedure_catalog_block
    assert build_procedure_catalog_block([]) == ""


def test_build_procedure_catalog_block_lists_names():
    """Block lists each procedure's name + first step preview."""
    from pipeline.prompt_builder import build_procedure_catalog_block
    procedures = [
        {"name": "deploy-app", "steps": ["pytest", "git push", "check CI"]},
        {"name": "morning-routine", "steps": ["coffee", "shower"]},
    ]
    block = build_procedure_catalog_block(procedures)
    assert "deploy-app" in block
    assert "morning-routine" in block


def test_find_matching_procedure_exact():
    from pipeline.prompt_builder import find_matching_procedure
    procedures = [
        {"name": "deploy-app", "steps": ["a", "b"]},
        {"name": "morning-routine", "steps": ["c"]},
    ]
    match = find_matching_procedure("Jarvis, run deploy-app", procedures)
    assert match is not None
    assert match["name"] == "deploy-app"


def test_find_matching_procedure_fuzzy():
    """'deploy' (Levenshtein≤3 of 'deploy-app' core word) matches."""
    from pipeline.prompt_builder import find_matching_procedure
    procedures = [{"name": "deploy-app", "steps": ["a"]}]
    match = find_matching_procedure("Jarvis, deploy the app", procedures)
    assert match is not None
    assert match["name"] == "deploy-app"


def test_find_matching_procedure_no_match():
    from pipeline.prompt_builder import find_matching_procedure
    procedures = [{"name": "deploy-app", "steps": ["a"]}]
    match = find_matching_procedure("what's the weather", procedures)
    assert match is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_prompt_builder.py -k procedure -v
```

Expected: FAIL — helpers don't exist.

- [ ] **Step 3: Implement in `pipeline/prompt_builder.py`**

Add (use a small fuzzy-match — substring + Levenshtein normalised by length):

```python
def build_procedure_catalog_block(procedures: list[dict]) -> str:
    """Compact catalog of saved procedures for the supervisor prompt.
    Returns "" when empty."""
    if not procedures:
        return ""
    lines = ["═" * 46, "SAVED PROCEDURES (invoke by name)", "═" * 46]
    for p in procedures:
        name = p.get("name", "")
        steps = p.get("steps", [])
        if not name:
            continue
        first = steps[0] if steps else ""
        preview = (first[:40] + "…") if len(first) > 40 else first
        lines.append(f"  • {name} — {len(steps)} steps starting with: {preview}")
    return "\n".join(lines)


def _levenshtein(a: str, b: str) -> int:
    """Standard Levenshtein distance, iterative."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(
                prev[j] + 1,
                curr[j - 1] + 1,
                prev[j - 1] + (0 if ca == cb else 1),
            )
        prev = curr
    return prev[-1]


def find_matching_procedure(user_text: str, procedures: list[dict]) -> dict | None:
    """Find the best matching procedure (if any) for the user's utterance.

    Strategy:
      1. Exact name appears as a substring → match.
      2. Any whitespace-separated word in user_text within Levenshtein 3
         (normalised by name length) of any procedure name → match.

    Returns None if no match. Returns at most one procedure — top-1 by
    distance; ties broken by name order. Ambiguity (≤3 distance for
    multiple) is the caller's problem (disambiguation prompt — see
    jarvis_agent integration)."""
    if not user_text or not procedures:
        return None
    text_lower = user_text.lower()

    # 1. Exact name substring
    for p in procedures:
        name = (p.get("name") or "").lower()
        if name and name in text_lower:
            return p

    # 2. Fuzzy match against any user word
    best = None
    best_dist = 999
    for word in re.findall(r"[a-z0-9]+", text_lower):
        for p in procedures:
            name = (p.get("name") or "").lower()
            if not name:
                continue
            # Compare each chunk of the kebab name OR the whole name
            for chunk in name.split("-") + [name]:
                d = _levenshtein(word, chunk)
                if d <= 3 and d < best_dist:
                    best_dist = d
                    best = p
    return best
```

(Import `re` at the top if not already imported.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_prompt_builder.py -k procedure -v
```

Expected: PASS.

- [ ] **Step 5: Wire `find_matching_procedure` into `on_user_turn_completed`**

In `jarvis_agent.py`, near the trigger inject from Task 7 (but BEFORE it — procedure-match takes precedence for explicit invocations), add:

```python
        # Spec 2026-05-24, Track 2.5 — procedure replay match
        try:
            from pipeline import file_memory
            from pipeline.prompt_builder import find_matching_procedure
            procedures = []
            for raw in file_memory.read("procedure").get("entries", []) or []:
                # Parse "## name\n1. step\n2. step ..." back into dict
                lines = raw.split("\n")
                if not lines or not lines[0].startswith("## "):
                    continue
                p_name = lines[0][3:].strip()
                steps = [
                    ln.split(".", 1)[1].strip()
                    for ln in lines[1:]
                    if re.match(r"^\d+\.", ln)
                ]
                procedures.append({"name": p_name, "steps": steps})
            match = find_matching_procedure(user_text, procedures)
            if match:
                inject = (
                    f"Saved procedure '{match['name']}' matches. Steps: "
                    + " → ".join(match["steps"])
                    + ". Confirm before any destructive step "
                    "(git push / rm / external API call). Acknowledge the "
                    "match first, then ask if you should run it."
                )
                turn_ctx.add_message(role="system", content=inject)
                logger.info("[procedure] match injected: name=%s", match["name"])
        except Exception as e:
            logger.warning("[procedure] match step failed: %s", e)
```

- [ ] **Step 6: Run all tests**

```bash
.venv/bin/python -m pytest tests/test_prompt_builder.py -v
.venv/bin/python -c "import jarvis_agent"
```

Expected: PASS; clean import.

- [ ] **Step 7: Commit**

```bash
git add src/voice-agent/pipeline/prompt_builder.py src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_prompt_builder.py
git commit -m "feat(memory): Track 2.5 — procedure replay match + injection

build_procedure_catalog_block for catalog. find_matching_procedure
uses substring then bounded Levenshtein (≤3) over kebab chunks.
on_user_turn_completed reads PROCEDURES.md, matches against user
utterance, injects a system message with the steps + 'confirm before
destructive' guidance.

Spec: docs/superpowers/specs/2026-05-24-jarvis-memory-and-procedure-loop-design.md
Track 2.5 replay."
```

---

## Task 12: Junk skill cleanup (Track 7)

**Files:**
- Manual filesystem op (no source change)
- Write: `~/.jarvis/skills/.cleanup.log`

- [ ] **Step 1: Capture pre-state**

```bash
ls -la ~/.jarvis/skills/
```

Expected: shows `dedicated-voice-appliance/` and `design-fixed-voice-interface/` plus possibly others.

- [ ] **Step 2: Remove the 2 known-junk skills**

```bash
rm -rf ~/.jarvis/skills/dedicated-voice-appliance
rm -rf ~/.jarvis/skills/design-fixed-voice-interface
```

- [ ] **Step 3: Log the cleanup**

```bash
cat > ~/.jarvis/skills/.cleanup.log <<'EOF'
2026-05-24 — Spec 2026-05-24-jarvis-memory-and-procedure-loop
Removed two single-conversation narrative-extract skills:
  - dedicated-voice-appliance (Raspberry Pi voice appliance setup — not actually a procedure)
  - design-fixed-voice-interface (same context, redundant)
Both were created on 2026-05-22 from a single conversation while
JARVIS_SKILL_REVIEW_APPLY was temporarily ON with the pre-rewrite
reviewer prompt. Future writes are gated by the Track 5 prompt rewrite
+ Track 6 sequenced APPLY=1 flip.
EOF
```

- [ ] **Step 4: Verify**

```bash
ls -la ~/.jarvis/skills/
```

Expected: the two junk skills are gone; `.cleanup.log` exists.

- [ ] **Step 5: No commit** (filesystem op only, no repo changes).

---

## Task 13: 24h shadow soak for Tracks 1 + 3, then flip to LIVE

**Prerequisites:** Tasks 1–11 committed; service restarted; 24h elapsed since last restart.

- [ ] **Step 1: Restart the service to load the new code**

Operational rule check first (CLAUDE.md):

```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db "SELECT ts_utc FROM turns ORDER BY ts_utc DESC LIMIT 1"
```

If the result is within the last 60s, wait + retry. Otherwise:

```bash
systemctl --user daemon-reload
systemctl --user restart jarvis-voice-agent.service
journalctl --user -u jarvis-voice-agent.service -f --since "10 seconds ago" | head -20
```

Expected: clean startup, "memory layer enabled (file-backed)" log line within 30s.

- [ ] **Step 2: Soak for 24h**

Use JARVIS normally. The shadow-mode triggers will log matches without injecting:

```bash
# Watch shadow-mode hits live
tail -f ~/.local/share/jarvis/logs/voice-agent.log | grep "\[trigger\]\|\[confab\] save_claim"
```

- [ ] **Step 3: After 24h, audit the shadow log**

```bash
# How many save-trigger fires in the last 24h
zgrep -h "save_trigger matched" ~/.local/share/jarvis/logs/voice-agent.log* | wc -l

# Sample 20 random matches and manually classify: was the supervisor's
# disposition correct?
zgrep -h "save_trigger matched" ~/.local/share/jarvis/logs/voice-agent.log* | shuf | head -20
```

For each sample, cross-reference the corresponding `turn_telemetry.db` row to see what JARVIS actually replied. Manual classification:
- **Correct (regex match → supervisor wisely declined or correctly saved):** counts as ✓
- **False positive (regex match → supervisor incorrectly saved garbage):** counts as ✗ — but no garbage in memory store since SHADOW mode prevented inject. Flagged as future tuning target.
- **False negative (no regex match but user clearly wanted save):** review the user_text; consider adding a pattern.

Pass criterion: ≥95% of samples are "Correct" — flip to live.

- [ ] **Step 4: Flip Track 1 + Track 3 to LIVE**

Edit `setup/systemd/jarvis-voice-agent.service`. Add to the `[Service]` block alongside existing `Environment=` lines:

```ini
Environment="JARVIS_SAVE_TRIGGER_LIVE=1"
Environment="JARVIS_RECALL_TRIGGER_LIVE=1"
```

(Track 3 confab guard goes live by default once the code is committed — the `JARVIS_CONFAB_SAVE_DISABLED=0` is the live state. No env change needed for Track 3 unless we explicitly need to disable it.)

- [ ] **Step 5: Reload + restart**

```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db "SELECT ts_utc FROM turns ORDER BY ts_utc DESC LIMIT 1"
# wait if <60s
systemctl --user daemon-reload
systemctl --user restart jarvis-voice-agent.service
journalctl --user -u jarvis-voice-agent.service -f --since "10 seconds ago" | head -20
```

- [ ] **Step 6: Voice-test the live flip**

Say to JARVIS in this exact sequence:

1. *"Jarvis, remember I'm allergic to fish."* → expect *"got it"* + USER.md gains an entry.
   ```bash
   cat ~/.jarvis/memories/USER.md
   ```
2. *"Jarvis, do you remember my allergies?"* → expect a substantive reply citing the saved fact.
3. Wait 30s, then check telemetry:
   ```bash
   sqlite3 ~/.local/share/jarvis/turn_telemetry.db \
     "SELECT ts_utc, save_trigger_fired, recall_trigger_fired, jarvis_text FROM turns ORDER BY ts_utc DESC LIMIT 5"
   ```
   Expect `save_trigger_fired=1` on turn 1, `recall_trigger_fired=1` on turn 2.

- [ ] **Step 7: Commit the service file change**

```bash
git add setup/systemd/jarvis-voice-agent.service
git commit -m "ops(memory): flip save/recall triggers from shadow to live

After 24h shadow-mode soak (≥95% correct supervisor disposition on
sampled save_trigger matches), enable JARVIS_SAVE_TRIGGER_LIVE=1 +
JARVIS_RECALL_TRIGGER_LIVE=1 in the systemd unit.

Spec: docs/superpowers/specs/2026-05-24-jarvis-memory-and-procedure-loop-design.md
Track 1 + Track 3 live."
```

---

## Task 14: Flip JARVIS_SKILL_REVIEW_APPLY=1 (Track 6) — LAST step

**Prerequisites:** Task 13 complete; 48h of Track 1+3 live with no regression; Track 5 prompt confirmed steering "remember" → memory in shadow audit.

- [ ] **Step 1: Confirm shadow APPLY-OFF is producing good proposals**

```bash
# Watch the reviewer's run reports for the last 48h
ls -la ~/.local/share/jarvis/logs/skill_review/ 2>/dev/null | head -20

# Inspect a sample — what would it have applied?
find ~/.local/share/jarvis/logs/skill_review -name run.json -mtime -2 | head -5 | xargs -I{} sh -c 'echo "=== {} ==="; cat {}'
```

Verify by manual review of 10 sampled `run.json` files:
- Each proposal's `kind` is correct (explicit save → memory; multi-step task → procedure; style correction → skill)
- No narration shapes ("The user is X-ing") slipping through
- No environment-failure captures

Pass criterion: ≥90% of proposed kinds are correct + zero junk.

- [ ] **Step 2: Edit service file to enable APPLY**

Edit `setup/systemd/jarvis-voice-agent.service`. Add to the `[Service]` block:

```ini
Environment="JARVIS_SKILL_REVIEW_APPLY=1"
```

- [ ] **Step 3: Reload + restart with operational discipline**

```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db "SELECT ts_utc FROM turns ORDER BY ts_utc DESC LIMIT 1"
# wait if <60s
systemctl --user daemon-reload
systemctl --user restart jarvis-voice-agent.service
journalctl --user -u jarvis-voice-agent.service -f --since "10 seconds ago" | head -30
```

- [ ] **Step 4: 48h close monitoring**

Watch the write surfaces during the first 48h after APPLY=1:

```bash
# Memory growth
watch -n 60 'wc -l ~/.jarvis/memories/*.md 2>/dev/null; echo; ls ~/.jarvis/skills/'

# Tail the apply log
tail -f ~/.local/share/jarvis/logs/voice-agent.log | grep "\[skill_review\] autonomous applied"
```

Pass criterion (48h): junk-write rate < 3/day. If higher, revert.

- [ ] **Step 5: Commit the service file change**

```bash
git add setup/systemd/jarvis-voice-agent.service
git commit -m "ops(memory): enable autonomous skill_review apply

After 48h of stable Track 1+3 live operation + manual audit of shadow
APPLY-OFF run.json reports showing correct kind classification ≥90%,
flip JARVIS_SKILL_REVIEW_APPLY=1 in the systemd unit. From now on the
autonomous reviewer's proposals actually land in MEMORY.md / USER.md /
PROCEDURES.md / ~/.jarvis/skills/.

Spec: docs/superpowers/specs/2026-05-24-jarvis-memory-and-procedure-loop-design.md
Track 6 — the last step of the memory-loop rebuild."
```

- [ ] **Step 6: Rollback path documented**

If junk-write rate exceeds the threshold:

```bash
# Quick rollback
sed -i 's/Environment="JARVIS_SKILL_REVIEW_APPLY=1"/Environment="JARVIS_SKILL_REVIEW_APPLY=0"/' setup/systemd/jarvis-voice-agent.service
systemctl --user daemon-reload
systemctl --user restart jarvis-voice-agent.service
# Remove the junk
# Inspect and remove targeted entries via memory(action=remove, ...) — or
# nuclear: rm -f ~/.jarvis/memories/*.md (entries will rebuild from genuine saves)
git revert <last commit sha>
```

---

## Final verification (all tasks complete)

- [ ] **Full test suite green**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/ -q
```

Expected: PASS; no regression vs the 2090-test baseline + ~25 new tests across 4 new test files.

- [ ] **Clean import**

```bash
.venv/bin/python -c "import jarvis_agent; print('OK')"
```

- [ ] **No hermes residue in touched files**

```bash
grep -i hermes src/voice-agent/pipeline/skill_review.py \
                src/voice-agent/pipeline/file_memory.py \
                src/voice-agent/pipeline/prompt_builder.py \
                src/voice-agent/tools/memory.py \
                src/voice-agent/confab_detector.py \
                src/voice-agent/jarvis_agent.py
```

Expected: no matches (the JARVIS-native naming feedback memory applies).

- [ ] **Live voice E2E (from the spec's 6 user-facing goals)**

1. *"Jarvis, remember I'm allergic to fish"* → USER.md gains entry.
2. *"Jarvis, save this process: deploy = run tests, push, check CI"* → PROCEDURES.md gains `deploy` (or similar).
3. *"Jarvis, deploy the app"* (procedure exists) → JARVIS reads + offers to run.
4. (After 3-step success task) JARVIS appends *"Want me to keep these steps as X?"*; user confirms; procedure saved.
5. *"Jarvis, do you remember my allergies?"* → substantive reply from memory.
6. *"Jarvis, I'll remember that"* without memory call → confab guard rejects (or annotates if save_trigger_fired in same turn).

- [ ] **30-day check-in (per spec's Sprint 3 success criteria)**

```bash
# Char usage ≤80% of cap
wc -c ~/.jarvis/memories/*.md
# Curator suggestion queue ≤3/week
ls -la ~/.jarvis/skills/ | head -30
```

---

## Self-review checklist

**Spec coverage:** Every spec section maps to a task —

| Spec section | Task(s) |
|---|---|
| Track 1: trigger regex | Task 7 |
| Track 2: procedure PROPOSAL_KIND | Tasks 2, 3, 4 |
| Track 2.5: success capture + replay | Tasks 8, 9, 10, 11 |
| Track 3: save-confab guard | Task 6 |
| Track 5: reviewer prompt rewrite | Task 1 |
| Track 6: APPLY=1 flip | Task 14 |
| Track 7: junk skill cleanup | Task 12 |
| Observability columns | Task 5 |
| Shadow → live flip | Task 13 |
| Final E2E + 30-day check | Final verification |

**Placeholder scan:** No "TBD" / "implement later" / "appropriate error handling" — all steps have concrete code or commands.

**Type consistency:** `TurnSnapshot` extension adds `tool_call_count` (int, default 0) + `had_tool_error` (bool, default False) — referenced consistently across Tasks 8/9/10. `Proposal` has `kind` / `payload` / `rationale` / `source_turn_id` per existing dataclass at skill_review.py:224 — consistent across Tasks 3/9. `apply_proposal` returns `ApplyResult` per skill_review.py:544 — consistent. file_memory `VALID_TARGETS = ("memory", "user", "procedure")` used identically in Tasks 2/3/4.

**Method names verified against actual source:**
- `looks_like_confabulation(assistant_text, prior_messages) → tuple[bool, str]` — at `confab_detector.py:306` (used in Task 6)
- `_msg_attr(obj, name)` — at `confab_detector.py:296` (reused in Task 6's helper)
- `JarvisAgent.on_user_turn_completed` — at `jarvis_agent.py:3350` (modified in Tasks 7, 10, 11)
- `fire_self_improvement` — at `jarvis_agent.py:5241` (call site for Tasks 9, 10)
- `PROPOSAL_KINDS` — at `skill_review.py:219` (modified in Task 3)
- `_VALID_MEMORY_CATEGORIES` — at `skill_review.py:220` (referenced in Task 3 validate branch)
- `MEMORY_SCHEMA` — at `tools/memory.py:87-143` (modified in Task 4)
- `_handle_memory` — at `tools/memory.py:42-82` (modified in Task 4, called by Task 10)
- `build_skill_catalog_block` — at `prompt_builder.py:202` (sibling pattern for Task 11's `build_procedure_catalog_block`)
