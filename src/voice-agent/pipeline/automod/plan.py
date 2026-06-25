"""Pre-build PLAN stage for the self-evolution loop (2026-06-25) — the SDLC
'design' step that was missing.

Before a build agent writes any code, two independent PLAN agents (different
model families, reusing review_council's provider dispatch) draft a short plan
for an intent — approach + the files they expect to touch + risks. A judge fuses
them (picks the sounder approach + rules on feasibility), and the result is GATED
before the expensive build runs: a plan that would touch a blocklisted path,
leave src/voice-agent/, or that the judge calls infeasible is REJECTED early. A
passing plan is handed to the build agent as guidance.

This is "think (+ get reviewed) before you build." Best-effort: any LLM failure
degrades to 'proceed with no plan' (the pre-2026-06-25 behavior) — only an
explicit blocklist / scope / infeasible verdict skips the build. The finalize
diff-gate remains the real enforcement on the ACTUAL diff; this is a cheap early
filter + build guidance. Gated by JARVIS_AUTOMOD_PLAN_STAGE (default on).
"""
from __future__ import annotations

import json
import logging
import os

from pipeline.automod._state import is_blocked_path
from pipeline.automod.introspection import _parse_json_object
from pipeline.automod.review_council import _call_model

logger = logging.getLogger("jarvis.automod.plan")

ALLOWED_PREFIX = "src/voice-agent/"

# Two different families draft; a judge fuses. Override via env.
_PLAN_A = os.environ.get("JARVIS_PLAN_MODEL_A", "anthropic:claude-sonnet-4-6")
_PLAN_B = os.environ.get("JARVIS_PLAN_MODEL_B", "deepseek:deepseek-chat")
_PLAN_JUDGE = os.environ.get("JARVIS_PLAN_MODEL_JUDGE", "anthropic:claude-sonnet-4-6")

_DRAFT_PROMPT = (
    "You are planning a small, focused change to the JARVIS voice agent (Python, "
    "under src/voice-agent/). Given the INTENT, draft a SHORT plan: the approach "
    "in 1-3 sentences, the EXACT files you expect to touch (paths under "
    "src/voice-agent/), and the main risks. Do NOT write code. Respond with ONLY "
    "this JSON:\n"
    '{"approach": "<1-3 sentences>", "files": ["src/voice-agent/..."], '
    '"risks": ["<short>", ...]}'
)

_JUDGE_PROMPT = (
    "Two engineers drafted plans for the same INTENT on the JARVIS voice agent. "
    "Pick the sounder plan, decide whether the intent is FEASIBLE as a SMALL, "
    "SAFE change (<=5 files, all under src/voice-agent/), and list the final set "
    "of files the change should touch. Respond with ONLY this JSON:\n"
    '{"chosen": 1 | 2, "feasible": true | false, "reason": "<one sentence>", '
    '"approach": "<the chosen approach, refined>", '
    '"files": ["src/voice-agent/..."], "risks": ["<short>", ...]}'
)


def _spec(raw: str) -> tuple[str, str]:
    provider, sep, model = raw.partition(":")
    return (provider, model) if sep else ("anthropic", provider)


def _call_json(model_spec: str, prompt: str) -> dict | None:
    provider, model = _spec(model_spec)
    try:
        text = _call_model(provider, model, prompt)
    except Exception as e:  # noqa: BLE001 — best-effort
        logger.warning("[plan] %s call failed: %s", model_spec, e)
        return None
    return _parse_json_object(text)


def _draft(model_spec: str, intent: str) -> dict | None:
    p = _call_json(model_spec, f"{_DRAFT_PROMPT}\n\nINTENT:\n{intent[:2000]}")
    if not p:
        return None
    return {
        "approach": str(p.get("approach", "")).strip(),
        "files": [str(f).strip() for f in (p.get("files") or [])][:12],
        "risks": [str(r) for r in (p.get("risks") or [])][:6],
    }


def _gate(files: list[str]) -> tuple[bool, str]:
    """(ok, reason). Reject if any planned file leaves src/voice-agent/ or is
    blocklisted. Advisory (the build's real diff is re-checked by finalize)."""
    for f in files:
        f = (f or "").strip()
        if not f:
            continue
        if not f.startswith(ALLOWED_PREFIX):
            return False, f"plan leaves src/voice-agent/: {f}"
        if is_blocked_path(f):
            return False, f"plan touches a blocklisted path: {f}"
    return True, ""


def _proceed(reason: str, plan: dict | None, models: list[str]) -> dict:
    return {"verdict": "proceed", "reason": reason, "plan": plan, "models": models}


def _reject(reason: str, plan: dict | None, models: list[str]) -> dict:
    return {"verdict": "reject", "reason": reason, "plan": plan, "models": models}


def make_plan(intent: str) -> dict:
    """2-agent plan fusion + early gate. Returns:
      {"verdict": "proceed"|"reject", "reason": str, "plan": {...}|None, "models": [...]}

    Best-effort: if the LLMs are down it returns 'proceed' (no plan) so the loop
    is never blocked by the plan stage; only an explicit blocklist / scope /
    infeasible verdict rejects. Never raises."""
    if not (intent or "").strip():
        return _proceed("no intent text", None, [])

    a = _draft(_PLAN_A, intent)
    b = _draft(_PLAN_B, intent)
    drafts = [d for d in (a, b) if d]
    if not drafts:
        return _proceed("plan agents unavailable", None, [_PLAN_A, _PLAN_B])

    # Conservative early gate: if EITHER draft plans a blocklisted / out-of-scope
    # file, reject now (high-confidence bad) — saves the build.
    union_files = sorted({f for d in drafts for f in d["files"] if f})
    ok, reason = _gate(union_files)
    if not ok:
        return _reject(reason, drafts[0], [_PLAN_A, _PLAN_B])

    # Fuse: a judge picks the sounder plan + rules feasibility.
    j = None
    if a and b:
        j = _call_json(
            _PLAN_JUDGE,
            f"{_JUDGE_PROMPT}\n\nINTENT:\n{intent[:2000]}\n\n"
            f"PLAN 1:\n{json.dumps(a, ensure_ascii=False)}\n\n"
            f"PLAN 2:\n{json.dumps(b, ensure_ascii=False)}",
        )
    if j is None:
        # Judge down or only one draft → proceed with a draft as guidance.
        return _proceed("judge unavailable; using draft", drafts[0], [_PLAN_A, _PLAN_B])

    final_files = [str(f).strip() for f in (j.get("files") or union_files)][:12]
    final_plan = {
        "approach": str(j.get("approach", "")).strip(),
        "files": final_files,
        "risks": [str(r) for r in (j.get("risks") or [])][:6],
    }
    models = [_PLAN_A, _PLAN_B, _PLAN_JUDGE]
    ok, reason = _gate(final_files)
    if not ok:
        return _reject(reason, final_plan, models)
    if j.get("feasible") is False:
        return _reject(f"judged infeasible as a small safe change: {j.get('reason', '')}".strip(),
                       final_plan, models)
    return _proceed(str(j.get("reason", "plan looks sound")), final_plan, models)


def format_for_prompt(plan: dict | None) -> str:
    """Render a passed plan as a short block to prepend to the build prompt."""
    if not plan:
        return ""
    lines = [f"REVIEWED PLAN — approach: {plan.get('approach', '')}"]
    if plan.get("files"):
        lines.append("REVIEWED PLAN — files: " + ", ".join(plan["files"]))
    if plan.get("risks"):
        lines.append("REVIEWED PLAN — risks: " + "; ".join(plan["risks"]))
    return "\n".join(lines)
