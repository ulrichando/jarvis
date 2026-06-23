"""Autonomy graduation (sub-project D, 2026-06-23).

The capstone of the evolution program: a staged autonomy ladder with evidence
gates, plus the staged auto-deploy mechanism — built SAFE-BY-DEFAULT.

Stages: human_review (default) → canary → autonomous.

  human_review  every deploy needs a human (current live behavior).
  canary        auto-deploy only LOW-RISK proposals (small, prompt/doc-only)
                when eligibility is met; higher-risk still waits for a human.
  autonomous    auto-deploy any proposal that passed the gates.

`maybe_auto_deploy()` is TRIPLE-GATED OFF: it does nothing unless BOTH
`JARVIS_EVOLUTION_AUTONOMY_STAGE` is set past human_review AND
`JARVIS_EVOLUTION_AUTODEPLOY=1`. With defaults it always returns
{"action": "hold"} and never calls deploy — so wiring it in changes no live
behavior. The watchdog (auto-rollback) still guards any deploy it does make.

`evaluate()` scores the graduation criteria from existing evidence (artifacts,
ledger, audit log) so the human can SEE readiness before flipping the flags.
The same criteria are mirrored read-only in the web /evolution UI.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from pipeline.automod import artifact
from pipeline.automod._state import _automod_home, evolution_log_path

logger = logging.getLogger("jarvis.automod.graduation")

STAGES = ("human_review", "canary", "autonomous")

# Evidence thresholds for graduating to the next stage.
GREEN_MIN_SAMPLE = 5          # need at least this many finalized proposals
GREEN_RATIO = 0.8            # ... of which >= 80% passed (pending/merged, not failed)
ROLLBACK_WINDOW_DAYS = 30     # no watchdog rollbacks in this window
CORRECT_APPROVALS = 3         # >= this many merged with no later rollback
FITNESS_FLOOR = 0.7           # latest composite >= this (and not trending down)


def current_stage() -> str:
    s = os.environ.get("JARVIS_EVOLUTION_AUTONOMY_STAGE", "human_review")
    return s if s in STAGES else "human_review"


def _autodeploy_enabled() -> bool:
    return os.environ.get("JARVIS_EVOLUTION_AUTODEPLOY", "0") == "1"


def _load_artifacts() -> list[dict]:
    out: list[dict] = []
    for p in sorted(_automod_home().glob("automod-*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def _rollback_count(window_days: int) -> int:
    cutoff = time.time() - window_days * 86400
    n = 0
    p = evolution_log_path()
    if not p.exists():
        return 0
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = rec.get("kind") or rec.get("event") or ""
        if kind in ("automod_reverted", "evolution_rolled_back"):
            ts = rec.get("ts")
            try:
                t = time.mktime(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S")) if ts else cutoff
            except (ValueError, TypeError):
                t = cutoff
            if t >= cutoff:
                n += 1
    return n


def _latest_fitness() -> tuple[float | None, str | None]:
    try:
        from evolution.ledger import read_readings, DEFAULT_LEDGER_DB
        rows = read_readings(limit=5, db_path=DEFAULT_LEDGER_DB)
    except Exception:  # noqa: BLE001
        return None, None
    if not rows:
        return None, None
    latest = float(rows[0].get("composite", 0.0))
    trend = None
    if len(rows) >= 2:
        d = latest - float(rows[1].get("composite", 0.0))
        trend = "flat" if abs(d) < 1e-6 else ("up" if d > 0 else "down")
    return latest, trend


def evaluate() -> dict:
    """Score the graduation criteria from existing evidence. Read-only."""
    arts = _load_artifacts()
    finalized = [a for a in arts if a.get("status") in ("pending", "merged", "failed")]
    passed = [a for a in finalized if a.get("status") in ("pending", "merged")]
    merged = [a for a in arts if a.get("status") == "merged"]
    reverted = [a for a in arts if a.get("rollback_sha") or a.get("status") == "reverted"]
    rollbacks = _rollback_count(ROLLBACK_WINDOW_DAYS)
    latest_fit, trend = _latest_fitness()

    green_ratio = (len(passed) / len(finalized)) if finalized else 0.0
    blocklist_hits = sum(
        1 for a in arts
        if "blocklist" in str(a.get("rejection_reason", "")).lower()
        or "diff_validation_failed" in str(a.get("rejection_reason", "")).lower()
    )

    criteria = [
        {
            "id": "green_history",
            "label": "Sustained green proposal history",
            "met": len(finalized) >= GREEN_MIN_SAMPLE and green_ratio >= GREEN_RATIO,
            "detail": f"{len(passed)}/{len(finalized)} passed (need ≥{GREEN_MIN_SAMPLE} at ≥{int(GREEN_RATIO*100)}%)",
        },
        {
            "id": "no_rollbacks",
            "label": "No watchdog rollbacks in window",
            "met": rollbacks == 0,
            "detail": f"{rollbacks} rollback(s) in last {ROLLBACK_WINDOW_DAYS}d",
        },
        {
            "id": "no_blocklist",
            "label": "No safety/blocklist violations",
            "met": blocklist_hits == 0,
            "detail": f"{blocklist_hits} blocklist/diff-validation rejection(s)",
        },
        {
            "id": "fitness",
            "label": "Measurable fitness, not regressing",
            "met": latest_fit is not None and latest_fit >= FITNESS_FLOOR and trend != "down",
            "detail": (f"latest {latest_fit:.2f} (trend {trend})" if latest_fit is not None
                       else "no fitness readings yet"),
        },
        {
            "id": "correct_approvals",
            "label": "Consistently correct approvals",
            "met": len(merged) >= CORRECT_APPROVALS and len(reverted) == 0,
            "detail": f"{len(merged)} merged, {len(reverted)} reverted (need ≥{CORRECT_APPROVALS}, 0 reverted)",
        },
    ]
    met = sum(1 for c in criteria if c["met"])
    return {
        "stage": current_stage(),
        "autodeploy_enabled": _autodeploy_enabled(),
        "criteria": criteria,
        "met_count": met,
        "total": len(criteria),
        "eligible_for_next": met == len(criteria),
    }


def proposal_risk(art: dict) -> str:
    """low = small, prompt/doc-only diff (safe for canary auto-deploy); else high."""
    files = art.get("files_changed") or []
    if not files:
        return "high"
    prompt_or_doc = all(f.endswith(".md") or "/prompts/" in f for f in files)
    diff = art.get("diff") or ""
    small = diff.count("\n") < 80
    return "low" if (prompt_or_doc and small) else "high"


def maybe_auto_deploy(automod_id: str) -> dict:
    """TRIPLE-GATED auto-deploy decision. Default = hold (no live change).

    Acts only when stage != human_review AND JARVIS_EVOLUTION_AUTODEPLOY=1 AND
    (canary ⇒ low-risk) AND eligibility met. Any deploy it makes still goes
    through deploy()+watchdog (auto-rollback). Never raises.
    """
    stage = current_stage()
    if stage == "human_review" or not _autodeploy_enabled():
        return {"action": "hold", "reason": f"stage={stage} autodeploy={_autodeploy_enabled()}"}
    try:
        art = artifact.load(automod_id)
    except Exception as e:  # noqa: BLE001
        return {"action": "hold", "reason": f"artifact load failed: {e}"}
    if art.get("status") != "pending":
        return {"action": "hold", "reason": f"status={art.get('status')}"}

    elig = evaluate()
    if not elig["eligible_for_next"]:
        return {"action": "hold", "reason": f"not eligible ({elig['met_count']}/{elig['total']})"}

    risk = proposal_risk(art)
    if stage == "canary" and risk != "low":
        return {"action": "hold", "reason": "canary: high-risk proposal needs a human"}

    logger.warning("[graduation] AUTO-DEPLOYING %s (stage=%s risk=%s)", automod_id, stage, risk)
    try:
        from pipeline.automod.deploy import deploy as _deploy
        ok, info = _deploy(automod_id)
    except Exception as e:  # noqa: BLE001
        return {"action": "deploy_error", "reason": str(e)}
    artifact.audit("automod_auto_deployed" if ok else "automod_auto_deploy_failed",
                   id=automod_id, stage=stage, risk=risk, info=str(info)[:200])
    return {"action": "auto_deploy" if ok else "deploy_failed", "reason": str(info)}
