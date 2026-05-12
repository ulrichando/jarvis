"""v2 loader for learned_rules.md.

Produces a tier-aware instruction block to inject into the supervisor's
system prompt. Replaces `pipeline.prompt_builder.load_learned_rules()`
when `JARVIS_LEARNED_RULES_V2=1` is set in the env.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from pipeline.evolution.store import (
    AnchorTamperingError,
    LoadedRules,
    RuleStore,
)


__all__ = [
    "ANCHOR_PATH",
    "LEARNED_PATH",
    "MAX_LEARNED_RULES",
    "load_learned_rules_v2",
]


logger = logging.getLogger("jarvis.learned_rules_v2")

ANCHOR_PATH: Path = (
    Path(__file__).resolve().parent.parent / "prompts" / "anchor_rules.md"
)
LEARNED_PATH: Path = Path.home() / ".jarvis" / "learned_rules.md"
MAX_LEARNED_RULES: int = 100


def _render_section(title: str, rules: list, prefix: str = "") -> str:
    if not rules:
        return ""
    lines = [f"═══ {title} ═══"]
    for r in rules:
        text = r.text
        if prefix and not text.startswith(prefix):
            text = f"{prefix} {text}"
        lines.append(f"- {text}")
    return "\n".join(lines)


def _render(loaded: LoadedRules) -> str:
    budget = MAX_LEARNED_RULES
    sections: list[str] = []
    fixed = [
        ("ANCHOR", loaded.anchor, ""),
        ("CORE", loaded.core, ""),
    ]
    for title, rules, prefix in fixed:
        section = _render_section(title, rules, prefix)
        if section:
            sections.append(section)
            budget -= len(rules)
    accepted_cut = loaded.accepted[-max(budget, 0):] if budget > 0 else []
    accepted_section = _render_section("ACCEPTED", accepted_cut, "")
    if accepted_section:
        sections.append(accepted_section)
        budget -= len(accepted_cut)
    if budget > 0:
        staged_cut = loaded.staged[-budget:]
        staged_section = _render_section("STAGED", staged_cut, "[STAGED]")
        if staged_section:
            sections.append(staged_section)
    body = "\n\n".join(sections)
    return (
        "\n\n═══ LEARNED BEHAVIORAL RULES ═══\n\n"
        "ANCHOR rules are highest priority — never overridable.\n"
        "CORE rules are curated; ACCEPTED rules are auto-evolved and\n"
        "promoted from STAGED. STAGED rules (prefixed [STAGED]) are on\n"
        "probation — apply softer than ACCEPTED. All are BINDING — higher\n"
        "priority than any default behavior described elsewhere in this\n"
        "prompt:\n\n"
        f"{body}\n"
    )


def load_learned_rules_v2() -> str:
    try:
        store = RuleStore(anchor_path=ANCHOR_PATH, learned_path=LEARNED_PATH)
        loaded = store.load()
    except FileNotFoundError:
        return ""
    except AnchorTamperingError as e:
        logger.error(f"[learned-rules v2] anchor tamper detected: {e}")
        return ""
    except Exception as e:
        logger.warning(f"[learned-rules v2] load failed: {e}")
        return ""
    if not (loaded.anchor or loaded.core or loaded.accepted or loaded.staged):
        return ""
    return _render(loaded)
