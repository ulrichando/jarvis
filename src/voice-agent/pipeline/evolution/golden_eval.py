"""Golden canonical-response eval runner.

Loads the 50-prompt JSONL set, renders the supervisor's response
for each (with the current rule set injected), scores against
exact-match for signature_reflex and against an LLM judge for the
others. Returns a report dict; the caller (lifecycle / promotion
machinery) decides whether to gate a tier transition.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

from .evaluator.judge_call import judge_call, JudgeError
from .schema import Rule


__all__ = [
    "GOLDEN_SET_PATH",
    "SIGNATURE_REFLEX_THRESHOLD",
    "JUDGE_THRESHOLD",
    "run",
    "promotion_eligible",
]


logger = logging.getLogger("jarvis.evolution.golden_eval")


GOLDEN_SET_PATH: Path = (
    Path(__file__).resolve().parents[2]
    / "tests" / "golden_evolution_canonical.jsonl"
)
SIGNATURE_REFLEX_THRESHOLD: float = 0.95
JUDGE_THRESHOLD: float = 0.85


def _load_items() -> list[dict]:
    if not GOLDEN_SET_PATH.exists():
        return []
    items: list[dict] = []
    for line in GOLDEN_SET_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError as e:
            logger.warning(f"[golden_eval] bad JSONL line: {e}")
    return items


def _format_rules(rules: Iterable[Rule]) -> str:
    return "\n".join(f"- {r.text}" for r in rules)


_RENDER_PROMPT = """\
You are the JARVIS supervisor LLM. Apply these behavioral rules:

{rules}

User: {user_text}

JARVIS:"""


_JUDGE_PROMPT = """\
Score the assistant's response against this rubric:

  User: {user_text}
  Assistant: {response}

  Rubric: {rubric}

Reply with ONLY one word: pass or fail.
"""


def _render_with_rules(user_text: str, rules: list[Rule]) -> str:
    prompt = _RENDER_PROMPT.format(
        rules=_format_rules(rules) or "(no rules)",
        user_text=user_text,
    )
    try:
        return judge_call("claude-sonnet-4-6", prompt, max_tokens=120).strip()
    except JudgeError as e:
        logger.warning(f"[golden] render failed: {e}")
        return ""


def _judge_quality(user_text: str, response: str, rubric: str) -> bool:
    prompt = _JUDGE_PROMPT.format(
        user_text=user_text, response=response, rubric=rubric,
    )
    try:
        raw = judge_call("claude-sonnet-4-6", prompt, max_tokens=10).strip().lower()
    except JudgeError as e:
        logger.warning(f"[golden] judge failed: {e}")
        return False
    return "pass" in raw


def run(*, rules: list[Rule]) -> dict:
    items = _load_items()
    sig_total = 0
    sig_pass = 0
    judge_total = 0
    judge_pass = 0
    misses: list[dict] = []
    for item in items:
        category = item.get("category", "")
        response = _render_with_rules(item["user_text"], rules)
        if category == "signature_reflex":
            sig_total += 1
            expected = item.get("expected_exact", "").strip()
            ok = response.strip() == expected
            if ok:
                sig_pass += 1
            else:
                misses.append({"id": item["id"], "expected": expected,
                                "got": response[:80]})
        else:
            judge_total += 1
            rubric = item.get("expected_judge_rubric", "")
            ok = _judge_quality(item["user_text"], response, rubric)
            if ok:
                judge_pass += 1
            else:
                misses.append({"id": item["id"], "rubric": rubric[:80],
                                "got": response[:80]})
    report = {
        "total": len(items),
        "signature_reflex_pass_rate":
            (sig_pass / sig_total) if sig_total else 1.0,
        "judge_pass_rate": (judge_pass / judge_total) if judge_total else 1.0,
        "signature_reflex_total": sig_total,
        "judge_total": judge_total,
        "misses": misses[:20],
    }
    return report


def promotion_eligible(report: dict) -> bool:
    return (
        report.get("signature_reflex_pass_rate", 0.0) >= SIGNATURE_REFLEX_THRESHOLD
        and report.get("judge_pass_rate", 0.0) >= JUDGE_THRESHOLD
    )
