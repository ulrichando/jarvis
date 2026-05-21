# src/voice-agent/pipeline/skill_review.py
"""Background-review engine — the self-improvement reviewer.

A headless aux-LLM reviews a complex/"hard" turn snapshot pulled from
``~/.local/share/jarvis/turn_telemetry.db`` and may PROPOSE saving or
patching a SKILL, or saving a durable MEMORY. Output is restricted to
exactly those three move types; the engine has no other side effects.

WHY THIS IS A REBUILD (not a port)
----------------------------------
The upstream text-agent reviewer this replaces forks a full text
``AIAgent`` (``from run_agent import AIAgent``) in a daemon thread,
inherits its live provider/model/credentials/prompt cache, and lets the
fork mutate the memory + skill stores directly via tool calls. The
LiveKit voice worker has NO equivalent forked-agent runtime — there is
no ``run_agent.AIAgent`` to fork. So we rebuild on the voice substrate
using the SAME off-band aux-LLM pattern the rest of the voice pipeline
already uses (``pipeline.memory_extractor`` / ``pipeline.memory_consolidator``
/ ``pipeline.curator``): a small model (Groq llama-3.1-8b-instant) called
via httpx, with a pure-function parse seam so unit tests cover parsing
without an LLM. Identical rationale to the curator's
"LLM consolidation review" note.

SUGGESTION-FIRST + SAFE
-----------------------
By DEFAULT the engine only PROPOSES — it logs proposals to
``~/.jarvis/logs/skill_review/`` and writes NOTHING to the skill store or
the memory store. Auto-apply is double-gated behind both the
``run_review(apply=True)`` argument AND the env flag
``JARVIS_SKILL_REVIEW_APPLY=1``. This is deliberate: auto-authoring a
skill/memory from every hard turn is exactly the garbage-spam failure
mode the memory-quality findings flagged (see the meta-paraphrase reject
filter in ``pipeline.memory_extractor``). Proposals are cheap to review;
junk writes are expensive to clean up.

TWO ENTRY POINTS
----------------
1. MANUAL/CLI (propose-first, double-gated):
   ``run_review(limit, apply)`` runs from the ``bin/jarvis-skill-review``
   CLI. By DEFAULT it only proposes; auto-apply needs BOTH the
   ``apply=True`` arg AND ``JARVIS_SKILL_REVIEW_APPLY=1``. This is the
   batch/over-recent-turns surface.

2. LIVE/AUTONOMOUS (auto-apply by default, off the latency path):
   ``autonomous_review_turn(snapshot)`` / ``fire_self_improvement(snapshot)``
   review the JUST-COMPLETED turn and AUTONOMOUSLY APPLY validated
   proposals — the self-improvement loop writes skills/memory on its own.
   This is the substrate adaptation of the upstream "background review
   thread" that auto-writes after a turn. It is fired fire-and-forget from
   ``jarvis_agent.py`` on the turn boundary (alongside the memory
   extractor), NEVER awaited inline, and is fully no-op'd by the kill
   switch ``JARVIS_SELF_IMPROVE_DISABLED=1``. The SAME guard chain runs on
   this path: ``parse_review_output`` (which calls ``validate_name`` +
   the ``_META_PARAPHRASE_RE`` junk filter + memory-category checks) and
   ``apply_proposal`` (which runs ``validate_skill_markdown`` inside
   ``skills_authoring``). Auto-spam is held back by the conservative
   hard-turn gate (only complex turns are reviewed) plus those validators.

Three-step design so unit tests can cover selection + parsing without a
live LLM or network:
  - ``select_review_candidates()``  : pure SQLite read → list[TurnSnapshot]
  - ``review_turn(snapshot, llm_fn)`` : aux-LLM call + parse → list[Proposal]
  - ``apply_proposal(p)``            : guarded write via skills_authoring /
                                       the memory publish path (apply-only)
  - ``run_review(limit, apply)``     : orchestrator (select → review → log →
                                       maybe-apply)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

# Reuse the file-memory meta-paraphrase reject filter so a proposed
# skill/memory that drifts into LLM-narration shape ("The user is
# X-ing", "It seems to be Y") is dropped by the SAME regex that gates
# memory-store writes. Single source of truth for "this is narration,
# not a durable artifact". (Relocated from the retired memory_extractor
# to pipeline.file_memory on 2026-05-21.)
from pipeline.file_memory import _META_PARAPHRASE_RE

logger = logging.getLogger("jarvis.skill_review")


# ── Turn telemetry DB location ───────────────────────────────────────
# Production path. Overridable via JARVIS_TURN_TELEMETRY_DB for tests
# (read at call time so a monkeypatched env takes effect without reload).
def _telemetry_db_path() -> Path:
    override = os.environ.get("JARVIS_TURN_TELEMETRY_DB", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".local" / "share" / "jarvis" / "turn_telemetry.db"


# ── Candidate-selection criterion ────────────────────────────────────
# The `turns` schema (verified 2026-05-21) exposes NO per-turn tool-call
# count column — `notes` is empty across the live DB, and there is no
# `tool_calls`/`n_tools` field. So "complex/hard" is derived from the
# columns that DO carry multi-step signal:
#
#   1. subagent IS NOT NULL/''  — a handoff fired (desktop / browser /
#      computer_use / screen_share). A handoff IS the multi-step
#      workflow marker on this substrate; these are the turns most
#      likely to encode a reusable procedure worth a skill.
#   2. computer_use_steps >= 1  — the vision-plan-act loop actually ran
#      steps (each step is ~1 tool action).
#   3. route IN ('TASK','REASONING') AND length(jarvis_text) >= N — a
#      substantial task/reasoning reply (long multi-step answer) even
#      when no subagent was involved.
#
# Ordered newest-first (ts_utc DESC) so a small `limit` reviews recent
# activity. Banter/emotional/short turns are excluded by construction.
_LONG_REPLY_CHARS_DEFAULT = 400


def _long_reply_chars() -> int:
    try:
        return int(
            os.environ.get(
                "JARVIS_SKILL_REVIEW_LONG_REPLY_CHARS",
                str(_LONG_REPLY_CHARS_DEFAULT),
            )
        )
    except ValueError:
        return _LONG_REPLY_CHARS_DEFAULT


@dataclass(frozen=True)
class TurnSnapshot:
    """A reviewable turn pulled from telemetry. Only the fields the
    reviewer needs — user text, assistant reply, and the multi-step
    signal (route / subagent / step count)."""

    turn_id: int
    ts_utc: str
    user_text: str
    jarvis_text: str
    route: str
    subagent: str
    computer_use_steps: int

    @property
    def reason(self) -> str:
        """Human-readable why-this-was-selected, for the report."""
        if self.subagent:
            return f"subagent={self.subagent}"
        if self.computer_use_steps and self.computer_use_steps >= 1:
            return f"computer_use_steps={self.computer_use_steps}"
        return f"long_{self.route or 'reply'}({len(self.jarvis_text)}c)"


def select_review_candidates(limit: int = 10) -> list[TurnSnapshot]:
    """Query ``turn_telemetry.db`` for complex/hard turns worth reviewing.

    Criterion (documented above): a subagent fired, OR the computer-use
    loop ran >=1 step, OR a TASK/REASONING turn produced a long reply.
    Newest-first; capped at `limit`. Returns [] if the DB is missing or
    empty (safe to run on a fresh install).
    """
    if limit <= 0:
        return []
    db = _telemetry_db_path()
    if not db.exists():
        logger.info("[skill_review] telemetry db missing: %s", db)
        return []

    long_chars = _long_reply_chars()
    sql = """
        SELECT id, ts_utc, user_text, jarvis_text,
               COALESCE(route, '')   AS route,
               COALESCE(subagent,'') AS subagent,
               COALESCE(computer_use_steps, 0) AS cu_steps
        FROM turns
        WHERE (subagent IS NOT NULL AND subagent != '')
           OR (computer_use_steps IS NOT NULL AND computer_use_steps >= 1)
           OR (route IN ('TASK', 'REASONING')
               AND length(jarvis_text) >= ?)
        ORDER BY ts_utc DESC, id DESC
        LIMIT ?
    """
    out: list[TurnSnapshot] = []
    try:
        # read-only connection; never mutate telemetry from the reviewer
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            for r in conn.execute(sql, (long_chars, int(limit))):
                out.append(
                    TurnSnapshot(
                        turn_id=int(r["id"]),
                        ts_utc=str(r["ts_utc"] or ""),
                        user_text=str(r["user_text"] or ""),
                        jarvis_text=str(r["jarvis_text"] or ""),
                        route=str(r["route"] or ""),
                        subagent=str(r["subagent"] or ""),
                        computer_use_steps=int(r["cu_steps"] or 0),
                    )
                )
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.warning(
            "[skill_review] candidate query failed: %s: %s",
            type(e).__name__,
            e,
        )
        return []
    logger.info("[skill_review] selected %d candidate turn(s)", len(out))
    return out


# ── Proposal types (output-restricted to these three) ────────────────
PROPOSAL_KINDS = ("skill_create", "skill_patch", "memory")
_VALID_MEMORY_CATEGORIES = ("user", "feedback", "project", "reference")
_MAX_CONTENT_CHARS = 500


@dataclass
class Proposal:
    """A single proposed self-improvement move. `payload` shape depends
    on `kind`:

      - skill_create: {name, description, when_to_use, body}
      - skill_patch:  {name, old_string, new_string, replace_all?}
      - memory:       {category, content}
    """

    kind: str
    payload: dict
    rationale: str = ""
    source_turn_id: Optional[int] = None

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "payload": dict(self.payload),
            "rationale": self.rationale,
            "source_turn_id": self.source_turn_id,
        }


def _is_junk_text(text: str) -> bool:
    """Reject LLM-narration shapes ("The user is X-ing", "It seems to be
    Y") that must never become a skill or memory. Mirrors the extractor's
    guard — single source of truth via the shared regex."""
    return bool(_META_PARAPHRASE_RE.search(text or ""))


def parse_review_output(raw: str | None, source_turn_id: int | None = None) -> list[Proposal]:
    """Parse the reviewer LLM's JSON into validated Proposal objects.

    Pure function. No DB, no I/O, no network. Returns [] for any input
    that fails validation (treat "" as "nothing to propose this turn").

    Expected shape:
        {"proposals": [
            {"kind": "skill_create",
             "payload": {"name": "...", "description": "...",
                         "when_to_use": "...", "body": "..."},
             "rationale": "..."},
            {"kind": "memory",
             "payload": {"category": "user", "content": "..."},
             "rationale": "..."},
            ...
        ]}
    A bare {"proposals": []} (or "NOTHING" / empty) means no suggestion.

    Validation (per proposal — ALL must pass or the proposal is dropped):
      - kind in PROPOSAL_KINDS
      - payload is a dict with the required keys for that kind, all str
      - no field matches the meta-paraphrase junk filter
      - memory.category in _VALID_MEMORY_CATEGORIES; content <= MAX chars
      - skill payload strings are non-empty
    """
    if not raw or not isinstance(raw, str):
        return []
    text = raw.strip()
    if not text or text.upper() in ("NOTHING", "SKIP", "NONE"):
        return []
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    proposals_raw = obj.get("proposals") if isinstance(obj, dict) else None
    if not isinstance(proposals_raw, list):
        return []

    out: list[Proposal] = []
    for p in proposals_raw:
        if not isinstance(p, dict):
            continue
        kind = p.get("kind")
        payload = p.get("payload")
        rationale = p.get("rationale", "")
        if kind not in PROPOSAL_KINDS:
            continue
        if not isinstance(payload, dict):
            continue
        if not isinstance(rationale, str):
            rationale = ""

        cleaned = _validate_payload(kind, payload)
        if cleaned is None:
            continue
        out.append(
            Proposal(
                kind=kind,
                payload=cleaned,
                rationale=rationale.strip()[:300],
                source_turn_id=source_turn_id,
            )
        )
    return out


def _validate_payload(kind: str, payload: dict) -> dict | None:
    """Return a cleaned payload dict for `kind`, or None if invalid /
    junk. Junk = any user-facing string matching the narration filter."""
    if kind == "memory":
        category = str(payload.get("category", "")).strip().lower()
        content = str(payload.get("content", "")).strip()
        if category not in _VALID_MEMORY_CATEGORIES:
            return None
        if not content or len(content) > _MAX_CONTENT_CHARS:
            return None
        if _is_junk_text(content):
            logger.info("[skill_review] memory junk rejected: %r", content[:80])
            return None
        return {"category": category, "content": content}

    if kind == "skill_create":
        name = str(payload.get("name", "")).strip()
        description = str(payload.get("description", "")).strip()
        when_to_use = str(payload.get("when_to_use", "")).strip()
        body = str(payload.get("body", "")).strip()
        if not name or not description or not body:
            return None
        # Validate against the same naming/markdown rules the writer uses,
        # so an invalid proposal never even reaches the report as "applyable".
        from pipeline.skills_authoring import validate_name

        if validate_name(name) is not None:
            return None
        if _is_junk_text(description) or _is_junk_text(body):
            logger.info("[skill_review] skill junk rejected: name=%r", name)
            return None
        return {
            "name": name,
            "description": description,
            "when_to_use": when_to_use,
            "body": body,
        }

    if kind == "skill_patch":
        name = str(payload.get("name", "")).strip()
        old_string = payload.get("old_string")
        new_string = payload.get("new_string")
        if not name or not isinstance(old_string, str) or not isinstance(new_string, str):
            return None
        if not old_string:
            return None
        if _is_junk_text(new_string):
            logger.info("[skill_review] patch junk rejected: name=%r", name)
            return None
        cleaned = {
            "name": name,
            "old_string": old_string,
            "new_string": new_string,
        }
        if payload.get("replace_all"):
            cleaned["replace_all"] = True
        return cleaned

    return None


# ── Reviewer prompt + aux-LLM call ───────────────────────────────────
# Mirrors the extractor/consolidator call shape EXACTLY (Groq
# llama-3.1-8b-instant via httpx, temp 0, short timeout, graceful
# degrade to "no proposal" on any failure or missing key).
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

Be CONSERVATIVE. Most turns warrant NOTHING — a one-off answer, banter, \
or a failed/aborted attempt is not a reusable artifact. Only propose when \
the turn clearly encodes a repeatable procedure or a stable fact.

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
       "rationale": "one sentence"}}
  ]}}
If nothing is worth saving, output exactly: {{"proposals": []}}

TURN TO REVIEW:
  route: {route}
  subagent: {subagent}
  user said: "{user_text}"
  JARVIS replied: "{jarvis_text}"

OUTPUT:"""


# LLM seam — tests inject a fake; production uses _call_review_llm.
# Type: async (TurnSnapshot) -> raw string.
LLMFn = Callable[["TurnSnapshot"], Awaitable[str]]


async def _call_review_llm(snapshot: TurnSnapshot) -> str:
    """Call llama-3.1-8b-instant via Groq with the review prompt.
    Isolated so tests monkeypatch it without an API key. Degrades to an
    empty-proposal payload on missing key / timeout / non-2xx — identical
    failure handling to memory_extractor / memory_consolidator."""
    import httpx

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.debug("[skill_review] GROQ_API_KEY missing — skipping review")
        return '{"proposals": []}'

    def _clip(s: str, n: int) -> str:
        return (s or "").replace('"', "'").replace("\n", " ")[:n]

    prompt = _REVIEW_PROMPT.format(
        route=snapshot.route or "(none)",
        subagent=snapshot.subagent or "(none)",
        user_text=_clip(snapshot.user_text, 1500),
        jarvis_text=_clip(snapshot.jarvis_text, 1500),
    )

    async with httpx.AsyncClient(timeout=8.0) as client:
        try:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 700,
                    "temperature": 0.0,
                    "stop": ["\nTURN TO REVIEW:", "\n\n\n"],
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(
                "[skill_review] LLM call failed: %s: %s", type(e).__name__, e
            )
            return '{"proposals": []}'


async def review_turn(
    snapshot: TurnSnapshot, llm_fn: LLMFn | None = None
) -> list[Proposal]:
    """Review a single turn snapshot → list[Proposal]. Calls the aux-LLM
    (or the injected `llm_fn` seam), parses + junk-filters the output.
    Never raises — returns [] on any LLM/parse failure."""
    fn = llm_fn or _call_review_llm
    try:
        raw = await fn(snapshot)
    except Exception as e:
        logger.warning(
            "[skill_review] review_turn LLM error on turn %s: %s: %s",
            snapshot.turn_id,
            type(e).__name__,
            e,
        )
        return []
    return parse_review_output(raw, source_turn_id=snapshot.turn_id)


# ── Apply path (apply-only; double-gated) ────────────────────────────
@dataclass
class ApplyResult:
    proposal: Proposal
    ok: bool
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "kind": self.proposal.kind,
            "payload": dict(self.proposal.payload),
            "ok": self.ok,
            "detail": self.detail,
            "source_turn_id": self.proposal.source_turn_id,
        }


def apply_proposal(p: Proposal) -> ApplyResult:
    """Apply a single proposal via the output-restricted write surface:
    skills via ``pipeline.skills_authoring``; memory via the existing
    ``tools.memory`` publish path. Full validation runs inside those
    functions. NO other side effects.

    ONLY called from ``run_review`` when auto-apply is enabled (both the
    `apply=True` arg AND ``JARVIS_SKILL_REVIEW_APPLY=1``). Returns an
    ApplyResult; never raises."""
    try:
        if p.kind == "skill_create":
            from pipeline.skills_authoring import create_user_skill

            res = create_user_skill(
                name=p.payload["name"],
                description=p.payload["description"],
                when_to_use=p.payload.get("when_to_use", ""),
                body=p.payload["body"],
            )
            return ApplyResult(
                proposal=p,
                ok=bool(res.get("ok")),
                detail=str(res.get("path") or res.get("error") or ""),
            )

        if p.kind == "skill_patch":
            from pipeline.skills_authoring import patch_user_skill

            res = patch_user_skill(
                name=p.payload["name"],
                old_string=p.payload["old_string"],
                new_string=p.payload["new_string"],
                replace_all=bool(p.payload.get("replace_all", False)),
            )
            return ApplyResult(
                proposal=p,
                ok=bool(res.get("ok")),
                detail=str(res.get("path") or res.get("error") or ""),
            )

        if p.kind == "memory":
            # File-backed memory (2026-05-21): write directly to the store
            # instead of publishing to the hub `events:memory` stream. Map
            # the proposal category onto a file-memory target — facts ABOUT
            # the user (category 'user') land in USER.md; everything else
            # (feedback / project / reference — JARVIS's own working notes)
            # lands in MEMORY.md.
            from pipeline import file_memory

            content = p.payload["content"]
            target = "user" if p.payload.get("category") == "user" else "memory"
            res = file_memory.add(target, content)
            ok = bool(isinstance(res, dict) and res.get("success"))
            detail = (
                f"memory.add target={target}"
                if ok
                else str((res or {}).get("error", "memory.add failed"))
            )
            return ApplyResult(proposal=p, ok=ok, detail=detail)

        return ApplyResult(proposal=p, ok=False, detail=f"unknown kind {p.kind!r}")
    except Exception as e:
        return ApplyResult(
            proposal=p, ok=False, detail=f"{type(e).__name__}: {e}"
        )


def _run_coro(coro: Awaitable[None]) -> None:
    """Run an async publish from the (sync) apply path. The CLI runs
    outside an event loop, so asyncio.run is correct here. If somehow
    called inside a loop, schedule a task instead of nesting run()."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)  # type: ignore[arg-type]
        return
    loop.create_task(coro)  # pragma: no cover - CLI is sync


# ── Report logging ───────────────────────────────────────────────────
def _reports_root() -> Path:
    """Per-run reports under the JARVIS home logs dir. Mirrors the
    curator's ``logs/curator/`` convention. Isolated in tests via
    JARVIS_HOME (see tools.runtime.get_jarvis_home)."""
    from tools.runtime import get_jarvis_home

    root = get_jarvis_home() / "logs" / "skill_review"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.debug("[skill_review] reports dir create failed: %s", e)
    return root


def _write_run_report(
    started_at: datetime,
    candidates: list[TurnSnapshot],
    proposals: list[Proposal],
    applied: list[ApplyResult],
    apply_enabled: bool,
) -> Optional[Path]:
    """Write run.json + a readable SUMMARY.md under
    ``logs/skill_review/{YYYYMMDD-HHMMSS}/``. Returns the dir, or None on
    write failure (never raises — a report failure must not lose the run)."""
    root = _reports_root()
    stamp = started_at.strftime("%Y%m%d-%H%M%S")
    run_dir = root / stamp
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = root / f"{stamp}-{suffix}"
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except Exception as e:
        logger.debug("[skill_review] run dir create failed: %s", e)
        return None

    by_kind: dict[str, int] = {}
    for p in proposals:
        by_kind[p.kind] = by_kind.get(p.kind, 0) + 1

    payload = {
        "started_at": started_at.isoformat(),
        "apply_enabled": apply_enabled,
        "counts": {
            "candidates": len(candidates),
            "proposals": len(proposals),
            "applied_ok": sum(1 for a in applied if a.ok),
            "applied_failed": sum(1 for a in applied if not a.ok),
            "by_kind": by_kind,
        },
        "candidates": [
            {
                "turn_id": c.turn_id,
                "ts_utc": c.ts_utc,
                "reason": c.reason,
                "route": c.route,
                "subagent": c.subagent,
            }
            for c in candidates
        ],
        "proposals": [p.as_dict() for p in proposals],
        "applied": [a.as_dict() for a in applied],
    }

    try:
        (run_dir / "run.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        logger.debug("[skill_review] run.json write failed: %s", e)

    try:
        (run_dir / "SUMMARY.md").write_text(
            _render_summary_md(payload), encoding="utf-8"
        )
    except Exception as e:
        logger.debug("[skill_review] SUMMARY.md write failed: %s", e)

    return run_dir


def _render_summary_md(payload: dict) -> str:
    c = payload["counts"]
    lines = [
        "# Skill-review run",
        "",
        f"- started: {payload['started_at']}",
        f"- apply_enabled: {payload['apply_enabled']}",
        f"- candidates reviewed: {c['candidates']}",
        f"- proposals: {c['proposals']} "
        f"(applied_ok={c['applied_ok']}, applied_failed={c['applied_failed']})",
    ]
    if c["by_kind"]:
        kinds = ", ".join(f"{k}={n}" for k, n in sorted(c["by_kind"].items()))
        lines.append(f"- by kind: {kinds}")
    lines.append("")

    if not payload["proposals"]:
        lines.append("_No proposals this run._")
        return "\n".join(lines) + "\n"

    lines.append("## Proposals")
    lines.append("")
    applied_by_idx = {i: a for i, a in enumerate(payload["applied"])}
    for i, p in enumerate(payload["proposals"]):
        pl = p["payload"]
        if p["kind"] == "memory":
            head = f"memory[{pl.get('category')}]: {pl.get('content')}"
        elif p["kind"] == "skill_create":
            head = f"skill_create {pl.get('name')!r}: {pl.get('description')}"
        elif p["kind"] == "skill_patch":
            head = f"skill_patch {pl.get('name')!r}"
        else:
            head = p["kind"]
        status = ""
        a = applied_by_idx.get(i)
        if a is not None:
            status = f"  → {'APPLIED' if a['ok'] else 'FAILED'}: {a['detail']}"
        src = p.get("source_turn_id")
        src_s = f" (turn {src})" if src is not None else ""
        lines.append(f"- **{p['kind']}**{src_s}: {head}{status}")
        if p.get("rationale"):
            lines.append(f"  - why: {p['rationale']}")
    return "\n".join(lines) + "\n"


# ── Orchestrator ─────────────────────────────────────────────────────
def _apply_enabled(apply: bool) -> bool:
    """Auto-apply requires BOTH the explicit apply=True arg AND the env
    flag JARVIS_SKILL_REVIEW_APPLY=1. Either alone → propose-only."""
    return bool(apply) and os.environ.get("JARVIS_SKILL_REVIEW_APPLY") == "1"


@dataclass
class ReviewRun:
    run_dir: Optional[str]
    candidates: int
    proposals: list[Proposal] = field(default_factory=list)
    applied: list[ApplyResult] = field(default_factory=list)
    apply_enabled: bool = False


async def run_review(limit: int = 10, apply: bool = False, llm_fn: LLMFn | None = None) -> ReviewRun:
    """Orchestrate one review pass: select → review → log → maybe-apply.

    DEFAULT is propose-only — proposals are LOGGED to
    ``~/.jarvis/logs/skill_review/`` and NOTHING is written to the skill
    store or the memory store. Writes happen ONLY when
    ``_apply_enabled(apply)`` is true (apply=True AND
    JARVIS_SKILL_REVIEW_APPLY=1).

    `llm_fn` is the test seam (defaults to the live Groq aux-LLM)."""
    started_at = datetime.now(timezone.utc)
    apply_on = _apply_enabled(apply)

    candidates = select_review_candidates(limit=limit)
    proposals: list[Proposal] = []
    for snap in candidates:
        proposals.extend(await review_turn(snap, llm_fn=llm_fn))

    applied: list[ApplyResult] = []
    if apply_on and proposals:
        for p in proposals:
            applied.append(apply_proposal(p))
            logger.info(
                "[skill_review] applied %s ok=%s detail=%s",
                p.kind,
                applied[-1].ok,
                applied[-1].detail,
            )
    elif proposals:
        logger.info(
            "[skill_review] propose-only — %d proposal(s) logged, NOT applied "
            "(apply arg=%s, env JARVIS_SKILL_REVIEW_APPLY=%r)",
            len(proposals),
            apply,
            os.environ.get("JARVIS_SKILL_REVIEW_APPLY"),
        )

    run_dir = _write_run_report(
        started_at=started_at,
        candidates=candidates,
        proposals=proposals,
        applied=applied,
        apply_enabled=apply_on,
    )
    logger.info(
        "[skill_review] run complete: candidates=%d proposals=%d applied=%d "
        "apply_enabled=%s report=%s",
        len(candidates),
        len(proposals),
        len(applied),
        apply_on,
        run_dir,
    )
    return ReviewRun(
        run_dir=str(run_dir) if run_dir else None,
        candidates=len(candidates),
        proposals=proposals,
        applied=applied,
        apply_enabled=apply_on,
    )


def run_review_sync(limit: int = 10, apply: bool = False) -> ReviewRun:
    """Sync wrapper for the CLI (which runs outside an event loop)."""
    return asyncio.run(run_review(limit=limit, apply=apply))


# ── Autonomous live trigger (fired off the turn boundary) ─────────────
# The self-improvement loop's live path. Mirrors the upstream "background
# review thread that auto-writes after a turn" on JARVIS's async voice
# substrate: instead of a daemon thread forking an agent, the voice worker
# fires a fire-and-forget asyncio task on the turn boundary (see
# jarvis_agent.py, alongside the memory extractor's create_task). On the
# autonomous path, validated proposals APPLY BY DEFAULT — no
# JARVIS_SKILL_REVIEW_APPLY needed. Held back only by (a) the conservative
# hard-turn gate below and (b) the full validator/junk-filter chain that
# parse_review_output + apply_proposal already enforce.


def self_improve_disabled() -> bool:
    """Single master kill-switch for the autonomous loop. When
    ``JARVIS_SELF_IMPROVE_DISABLED=1`` BOTH the review and the curator
    fire sites are no-ops. Read at call time so a runtime env edit takes
    effect without a process restart."""
    return os.environ.get("JARVIS_SELF_IMPROVE_DISABLED", "0") == "1"


def is_hard_turn(snapshot: TurnSnapshot) -> bool:
    """Live-turn equivalent of ``select_review_candidates``' WHERE clause:
    a turn is "hard" (worth a review) iff a subagent fired, OR the
    computer-use loop ran >=1 step, OR a TASK/REASONING turn produced a
    long reply (>= ``JARVIS_SKILL_REVIEW_LONG_REPLY_CHARS``). Pure; no I/O.

    Keeping this in lock-step with the SQL criterion means the live
    autonomous path reviews exactly the same class of turns the batch/CLI
    path would have picked — banter, short replies, and emotional turns are
    excluded by construction (the auto-spam guard)."""
    if snapshot.subagent:
        return True
    if snapshot.computer_use_steps and snapshot.computer_use_steps >= 1:
        return True
    if snapshot.route in ("TASK", "REASONING") and len(
        snapshot.jarvis_text or ""
    ) >= _long_reply_chars():
        return True
    return False


async def autonomous_review_turn(
    snapshot: TurnSnapshot, llm_fn: LLMFn | None = None
) -> list[ApplyResult]:
    """Review ONE just-completed turn and AUTONOMOUSLY APPLY validated
    proposals (the self-improvement loop's auto-write).

    Differs from ``run_review`` in two ways: (1) it reviews a single live
    snapshot rather than re-querying telemetry, and (2) it APPLIES BY
    DEFAULT — there is no ``JARVIS_SKILL_REVIEW_APPLY`` gate on this path;
    apply is suppressed ONLY by the master kill switch.

    Guard chain (unchanged, all preserved):
      - ``self_improve_disabled()`` → immediate no-op (no LLM, no apply).
      - ``review_turn`` → ``parse_review_output`` runs ``validate_name`` +
        the ``_META_PARAPHRASE_RE`` junk filter + memory-category/length
        checks; invalid/narration proposals never become Proposal objects.
      - ``apply_proposal`` → ``create_user_skill`` / ``patch_user_skill``
        run ``validate_skill_markdown`` inside ``skills_authoring``.

    NEVER raises — any failure returns ``[]`` so the turn handler that
    fired this can't break. Returns the list of ``ApplyResult``."""
    if self_improve_disabled():
        logger.debug(
            "[skill_review] autonomous review skipped — "
            "JARVIS_SELF_IMPROVE_DISABLED=1"
        )
        return []
    try:
        proposals = await review_turn(snapshot, llm_fn=llm_fn)
    except Exception as e:  # defense-in-depth — review_turn already guards
        logger.warning(
            "[skill_review] autonomous review_turn error on turn %s: %s: %s",
            snapshot.turn_id,
            type(e).__name__,
            e,
        )
        return []
    if not proposals:
        return []

    results: list[ApplyResult] = []
    for p in proposals:
        try:
            res = apply_proposal(p)
        except Exception as e:  # apply_proposal already guards, belt+braces
            res = ApplyResult(proposal=p, ok=False, detail=f"{type(e).__name__}: {e}")
        results.append(res)
        logger.info(
            "[skill_review] autonomous applied %s ok=%s detail=%s (turn %s)",
            p.kind,
            res.ok,
            res.detail,
            snapshot.turn_id,
        )
    return results


async def _run_curator_off_loop() -> None:
    """Run the interval-gated curator without ever blocking the event loop.
    ``maybe_run_curation`` is sync + does file I/O, so it runs in a thread
    executor. Self-gates by interval (``should_run_now``) — calling it every
    turn boundary is fine; it only acts when due. Never raises."""
    try:
        from pipeline.curator import maybe_run_curation

        await asyncio.to_thread(maybe_run_curation)
    except Exception as e:
        logger.debug(
            "[skill_review] curator fire failed: %s: %s",
            type(e).__name__,
            e,
        )


def fire_self_improvement(snapshot: TurnSnapshot) -> list["asyncio.Task"]:
    """Turn-boundary fire-and-forget for the autonomous loop. Schedules the
    background tasks — the per-turn skill review (hard turns only) and the
    interval-gated curator — and returns IMMEDIATELY. NEVER awaited by the
    caller; NEVER blocks the voice latency path; NEVER raises.

    Wired into ``jarvis_agent.py`` right alongside the memory extractor's
    ``create_task`` on the turn boundary. The whole body is guarded so a
    scheduling failure (e.g. no running loop) is swallowed.

    - Review: only fired when ``is_hard_turn(snapshot)`` — banter/short/
      emotional turns add no review load.
    - Curator: ``maybe_run_curation()`` self-gates by interval, so it's safe
      to invoke every turn boundary; it only acts when due. It is
      turn-content-agnostic, so it fires regardless of whether THIS turn was
      hard.
    - Kill switch: ``JARVIS_SELF_IMPROVE_DISABLED=1`` suppresses BOTH.

    Returns the scheduled tasks (the live caller ignores them; tests await
    them deterministically). Returns ``[]`` when nothing was scheduled."""
    tasks: list["asyncio.Task"] = []
    try:
        if self_improve_disabled():
            return tasks

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Defensive: the live caller is always inside the agent's event
            # loop, but never crash a (hypothetical) sync caller.
            logger.debug("[skill_review] fire_self_improvement: no running loop")
            return tasks

        # 1. Per-turn autonomous review — hard turns only, off the latency
        #    path. autonomous_review_turn() is itself fully try/except'd.
        if is_hard_turn(snapshot):
            tasks.append(loop.create_task(autonomous_review_turn(snapshot)))

        # 2. Interval-gated curator — content-agnostic; self-gates so calling
        #    it every turn boundary is fine. Runs its sync I/O in a thread.
        tasks.append(loop.create_task(_run_curator_off_loop()))
    except Exception as e:  # absolute backstop — must never break a turn
        logger.warning(
            "[skill_review] fire_self_improvement failed: %s: %s",
            type(e).__name__,
            e,
        )
    return tasks
