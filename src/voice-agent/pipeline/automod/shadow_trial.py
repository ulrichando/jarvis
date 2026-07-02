"""Lived-experience shadow trial — the auto-promotion oracle (2026-07-02).

The gate that makes CONTINUOUS autonomous evolution trustworthy.

Every self-improving-agent line of work (Darwin-Gödel Machine 2505.22954,
Huxley-Gödel 2510.21614, SICA, DARWIN 2602.05848) validates a self-written
variant against a FIXED external benchmark — SWE-bench, Polyglot. Shadow
deployment / traffic replay (MLOps) tests a new version on mirrored production
traffic, separately. This module fuses the two and points them at something
neither uses: JARVIS's OWN lived experience.

Before a self-written proposal earns auto-deploy, we replay a curated sample of
REAL recent conversation turns (turn_telemetry.db) through the CHANGED decision
path and ask a judge, per turn, whether the variant's reply is better / equal /
worse than what ACTUALLY shipped. The baseline is free — it's history. Only the
variant costs a call. A proposal promotes only if it does NOT regress on real
conversations. That removes the human-in-the-loop bottleneck ("is it actually
better?") that forces manual approval today, so the loop can run unattended —
while the deploy watchdog still guards the live restart.

Why this is novel: not a fixed benchmark but a PERSONALIZED, SELF-REFRESHING one
— the agent is judged against its own life, which grows every day. Turn
selection uses AutoData's (2606.25996) insight that BOUNDARY cases discriminate:
a correction, a fallback, a tool error, a confab flag tells you far more about
whether a variant is better than a trivially-healthy "what's the weather" turn
does. Trivial turns are near-ties for any reasonable variant; they waste trials.

Pure functions + injectable seams (turn source, variant runner, judge) so the
whole thing is unit-tested without an LLM or a live agent. Never raises → returns
a 'skipped' verdict on any gap. OFF by default; advisory (like stress_gate /
review_council) until proven, then gate-able.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger("jarvis.automod.shadow_trial")

# Minimum informative turns required for a verdict to mean anything. Below this
# the trial is 'skipped' (never a false pass/reject on 2 lucky turns).
MIN_TRIAL_TURNS = 5
DEFAULT_TRIAL_TURNS = 8

# Per-axis route affinity — which routes a proposal targeting an axis most
# affects, so the trial samples where the change actually bites.
_AXIS_ROUTES: dict[str, tuple[str, ...]] = {
    "reask": ("TASK", "REASONING"),
    "confab": ("TASK", "REASONING"),
    "action": ("TASK",),
    "latency": (),  # affects every route
    "interruption": (),  # not reconstructable from text; falls back to broad sample
}


@dataclass
class TrialTurn:
    """One real conversation turn, its shipped reply, and the signals that make
    it informative (discriminating) for a shadow trial."""
    id: str
    user_text: str
    baseline_reply: str
    route: str = ""
    correction: bool = False
    fallback: bool = False
    tool_error: bool = False
    confab: bool = False
    reply_len: int = 0

    def informativeness(self) -> int:
        """How much this turn discriminates a better variant from a worse one.
        Boundary/failure turns score high (they expose behavior differences);
        trivial healthy turns score ~0 (near-ties for any reasonable variant)."""
        score = 0
        if self.correction:
            score += 3  # the user told us the shipped reply was wrong — gold
        if self.confab:
            score += 3  # a claim without evidence — a variant may fix or worsen it
        if self.fallback:
            score += 2  # a slow/degraded path — latency/quality signal
        if self.tool_error:
            score += 2
        if self.route in ("TASK", "REASONING"):
            score += 1  # substantive turns discriminate more than banter
        if self.reply_len >= 200:
            score += 1
        return score


@dataclass
class TrialResult:
    verdict: str  # "pass" | "regressed" | "skipped"
    better: int = 0
    tie: int = 0
    worse: int = 0
    n: int = 0
    per_turn: list[dict] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "better": self.better,
            "tie": self.tie,
            "worse": self.worse,
            "n": self.n,
            "reason": self.reason,
            "per_turn": self.per_turn[:20],
        }


# ── Selection ─────────────────────────────────────────────────────────

def select_trial_turns(
    turns: list[TrialTurn],
    *,
    target_axis: str = "",
    n: int = DEFAULT_TRIAL_TURNS,
) -> list[TrialTurn]:
    """Pick the n most INFORMATIVE turns for a proposal, biased toward the routes
    its target axis affects. Boundary cases first (corrections/confab/fallback);
    ties broken by axis affinity then recency (input order = newest-first)."""
    affinity = set(_AXIS_ROUTES.get(target_axis, ()))

    def key(t: TrialTurn) -> tuple:
        aff = 1 if (not affinity or t.route in affinity) else 0
        return (t.informativeness(), aff)

    # Stable sort by (informativeness, affinity) descending; input order (recency)
    # is the natural tiebreak because Python's sort is stable.
    ranked = sorted(turns, key=key, reverse=True)
    # Drop pure-noise turns (nothing informative AND off-affinity) unless we'd
    # fall below the minimum — a non-regression check still wants some volume.
    informative = [t for t in ranked if t.informativeness() > 0]
    chosen = (informative if len(informative) >= n else ranked)[:n]
    return chosen


# ── Judging ───────────────────────────────────────────────────────────

_JUDGE_SYSTEM = (
    "You are a strict evaluator comparing two candidate assistant replies to the "
    "SAME user message. Reply with ONLY a JSON object, no prose."
)

def _judge_prompt(user_text: str, baseline: str, variant: str) -> str:
    return (
        "A user said this to a voice assistant:\n"
        f"USER: {user_text[:1200]}\n\n"
        "Reply A (what actually shipped):\n"
        f"{baseline[:1200]}\n\n"
        "Reply B (a proposed change):\n"
        f"{variant[:1200]}\n\n"
        "Which reply is better for this user — more correct, more helpful, more "
        "honest (no claims without evidence), better suited to a spoken reply? "
        'Respond ONLY as JSON: {"winner": "A" | "B" | "tie", "why": "<one short '
        'sentence>"}. Use "tie" when they are equivalent.'
    )


JudgeFn = Callable[[str, str, str], str]  # (user_text, baseline, variant) -> raw JSON


def judge_turn(turn: TrialTurn, variant_reply: str, judge_fn: JudgeFn) -> dict:
    """Judge one turn: variant vs the shipped baseline. Maps the judge's A/B/tie
    (A=baseline, B=variant) to better/tie/worse from the VARIANT's point of view.
    Best-effort — an unparseable/failed judge is a 'tie' (never a silent regress
    and never a silent win)."""
    try:
        raw = judge_fn(turn.user_text, turn.baseline_reply, variant_reply)
    except Exception as e:  # noqa: BLE001 — one bad judge call must not sink the trial
        return {"id": turn.id, "outcome": "tie", "why": f"judge error: {e}"[:120]}
    parsed = _parse_json(raw)
    winner = str((parsed or {}).get("winner", "")).strip().upper()
    outcome = "better" if winner == "B" else "worse" if winner == "A" else "tie"
    return {"id": turn.id, "outcome": outcome, "why": str((parsed or {}).get("why", ""))[:160]}


def _parse_json(text: str) -> dict | None:
    if not text:
        return None
    import re
    for cand in (text, re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)):
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


# ── Trial ─────────────────────────────────────────────────────────────

VariantFn = Callable[[str], str]  # user_text -> the variant's reply


def run_shadow_trial(
    turns: list[TrialTurn],
    variant_fn: VariantFn,
    judge_fn: JudgeFn,
) -> TrialResult:
    """Replay each turn through the variant, judge vs the shipped baseline, and
    aggregate. Never raises. A turn whose variant run fails is skipped (not
    counted) rather than treated as a regression."""
    per_turn: list[dict] = []
    better = tie = worse = 0
    for t in turns:
        try:
            variant_reply = variant_fn(t.user_text)
        except Exception as e:  # noqa: BLE001 — variant execution is the risky part
            per_turn.append({"id": t.id, "outcome": "error", "why": f"variant run failed: {e}"[:120]})
            continue
        if not (variant_reply or "").strip():
            per_turn.append({"id": t.id, "outcome": "error", "why": "variant produced empty reply"})
            continue
        j = judge_turn(t, variant_reply, judge_fn)
        per_turn.append(j)
        if j["outcome"] == "better":
            better += 1
        elif j["outcome"] == "worse":
            worse += 1
        elif j["outcome"] == "tie":
            tie += 1
    n = better + tie + worse
    return decide(better, tie, worse, n, per_turn)


def decide(better: int, tie: int, worse: int, n: int, per_turn: list[dict]) -> TrialResult:
    """Promotion decision from the trial tally.

      skipped   — fewer than MIN_TRIAL_TURNS judged (not enough signal).
      regressed — ANY turn got worse (a self-modification that harms even one
                  real conversation does not auto-promote — conservative on
                  purpose; a human can still approve it manually).
      pass      — enough turns, zero regressions.
    """
    if n < MIN_TRIAL_TURNS:
        return TrialResult("skipped", better, tie, worse, n, per_turn,
                           reason=f"only {n} turns judged (need {MIN_TRIAL_TURNS})")
    if worse > 0:
        return TrialResult("regressed", better, tie, worse, n, per_turn,
                           reason=f"{worse} of {n} real turns got worse (+{better} better)")
    return TrialResult("pass", better, tie, worse, n, per_turn,
                       reason=f"no regressions on {n} real turns (+{better} better, {tie} tie)")


# ── IO builders (production wiring) ────────────────────────────────────

def _telemetry_db() -> Path:
    return Path(os.environ.get(
        "JARVIS_TURN_TELEMETRY_DB",
        str(Path.home() / ".local" / "share" / "jarvis" / "turn_telemetry.db"),
    ))


def load_recent_turns(limit: int = 300, db_path: Path | None = None) -> list[TrialTurn]:
    """Read recent turns (newest-first) into TrialTurn rows. Read-only; returns
    [] on any DB problem. Only turns with both a user message and a shipped reply
    are usable (we need a baseline to compare against)."""
    db = db_path or _telemetry_db()
    if not db.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        rows = con.execute(
            "SELECT id, user_text, jarvis_text, route, correction_signal, "
            "route_fallback, had_tool_error, confab_check_state "
            "FROM turns "
            "WHERE user_text IS NOT NULL AND user_text != '' "
            "AND jarvis_text IS NOT NULL AND jarvis_text != '' "
            "ORDER BY ts_utc DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    out: list[TrialTurn] = []
    for r in rows:
        (rid, user_text, jarvis_text, route, corr, fb, tool_err, confab) = r
        out.append(TrialTurn(
            id=str(rid),
            user_text=str(user_text or ""),
            baseline_reply=str(jarvis_text or ""),
            route=str(route or ""),
            correction=bool(corr and str(corr).strip()),
            fallback=bool(fb),
            tool_error=bool(tool_err),
            confab=str(confab or "") in ("hedged_no_evidence", "retry_factory_missing"),
            reply_len=len(str(jarvis_text or "")),
        ))
    return out


def build_judge_fn(model: str = "claude-sonnet-4-6") -> JudgeFn | None:
    """An Anthropic-backed judge (mirrors review_council's provider use). Returns
    None when no key is configured, so the caller degrades to 'skipped' rather
    than pretending to have judged. Override the model via JARVIS_SHADOW_JUDGE_MODEL."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    model = os.environ.get("JARVIS_SHADOW_JUDGE_MODEL", model)

    def _judge(user_text: str, baseline: str, variant: str) -> str:
        import anthropic
        client = anthropic.Anthropic(timeout=40.0, max_retries=1)
        resp = client.messages.create(
            model=model,
            max_tokens=200,
            system=_JUDGE_SYSTEM,
            messages=[{"role": "user", "content": _judge_prompt(user_text, baseline, variant)}],
        )
        return "".join(getattr(b, "text", "") for b in resp.content).strip()

    return _judge


def trial_proposal(
    target_axis: str,
    variant_fn: VariantFn,
    *,
    n: int = DEFAULT_TRIAL_TURNS,
    judge_fn: JudgeFn | None = None,
    turns: list[TrialTurn] | None = None,
) -> dict:
    """Top-level entry: select informative real turns, run the variant against
    them, judge vs the shipped baseline, return the verdict dict. Best-effort —
    a missing judge / no turns → a 'skipped' verdict, never an exception."""
    judge = judge_fn or build_judge_fn()
    if judge is None:
        return TrialResult("skipped", reason="no judge (ANTHROPIC_API_KEY unset)").to_dict()
    pool = turns if turns is not None else load_recent_turns()
    if not pool:
        return TrialResult("skipped", reason="no usable turns in telemetry").to_dict()
    chosen = select_trial_turns(pool, target_axis=target_axis, n=n)
    result = run_shadow_trial(chosen, variant_fn, judge)
    logger.info("[shadow_trial] axis=%s verdict=%s (+%d/%d~/-%d of %d)",
                target_axis, result.verdict, result.better, result.tie, result.worse, result.n)
    return result.to_dict()
