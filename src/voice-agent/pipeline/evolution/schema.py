"""v2 learned-rules schema: dataclasses + parser + serializer.

The on-disk format is markdown bullets with HTML-comment metadata
so the existing `pipeline.prompt_builder.load_learned_rules()`
bullet-prefix reader keeps working during the v1 → v2 cutover.
Tiers are markdown section headers (`## ═══ <TIER> ═══`); the
metadata for each rule is in an inline `<!-- key=value … -->`
comment immediately after the `- ` bullet marker.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


__all__ = [
    "Rule",
    "ParsedRules",
    "SchemaError",
    "parse_rules_v2",
    "serialize_rules_v2",
]


VALID_TIERS = {"anchor", "core", "accepted", "staged", "archived"}
TIER_HEADER_RE = re.compile(
    r"^##\s*═{3,}\s*(ANCHOR|CORE|ACCEPTED|STAGED|ARCHIVED)\s*═{3,}\s*$"
)
RULE_LINE_RE = re.compile(
    r"^-\s+<!--\s*(?P<meta>.+?)\s*-->\s*(?P<text>.+?)\s*$"
)
META_TOKEN_RE = re.compile(r"(\w+)=(\{[^}]*\}|\[[^\]]*\]|\"[^\"]*\"|\S+)")
LIST_TOKEN_RE = re.compile(r"^\[(.*)\]$")
EVAL_TOKEN_RE = re.compile(r"^\{(.+)\}$")
FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class SchemaError(ValueError):
    """Raised when parser encounters a structurally invalid document."""


@dataclass
class Rule:
    id: str
    tier: str
    text: str
    created: Optional[str] = None
    reinforced: Optional[str] = None
    retired: Optional[str] = None
    turns: list[str] = field(default_factory=list)
    supersedes: list[str] = field(default_factory=list)
    superseded_by: Optional[str] = None
    proposal: Optional[str] = None
    evidence: str = ""
    reason: Optional[str] = None
    evaluator: dict = field(default_factory=dict)
    shadow_until: Optional[str] = None
    reinforcing_turns: int = 0


@dataclass
class ParsedRules:
    frontmatter: dict
    rules: list[Rule]


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def _parse_list(token: str) -> list[str]:
    m = LIST_TOKEN_RE.match(token)
    if not m:
        return []
    body = m.group(1).strip()
    if not body:
        return []
    return [p.strip() for p in body.split(",") if p.strip()]


def _parse_evaluator(token: str) -> dict:
    m = EVAL_TOKEN_RE.match(token)
    if not m:
        return {}
    out: dict = {}
    for piece in m.group(1).split(","):
        piece = piece.strip()
        if ":" not in piece:
            continue
        k, v = piece.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def _parse_meta(meta: str) -> dict:
    out: dict = {}
    for m in META_TOKEN_RE.finditer(meta):
        key = m.group(1)
        value = m.group(2)
        if value.startswith("["):
            out[key] = _parse_list(value)
        elif value.startswith("{"):
            out[key] = _parse_evaluator(value)
        else:
            out[key] = _strip_quotes(value)
    return out


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONT_RE.match(text)
    if not m:
        return {}, text
    body = m.group(1)
    fm: dict = {}
    for line in body.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip()
        try:
            fm[k.strip()] = int(v)
        except ValueError:
            fm[k.strip()] = v
    return fm, text[m.end():]


def parse_rules_v2(text: str, *, allow_anchor: bool = False) -> ParsedRules:
    frontmatter, body = _parse_frontmatter(text)
    rules: list[Rule] = []
    current_tier: Optional[str] = None
    for line in body.splitlines():
        header_match = TIER_HEADER_RE.match(line)
        if header_match:
            current_tier = header_match.group(1).lower()
            if current_tier == "anchor" and not allow_anchor:
                raise SchemaError(
                    "anchor tier present in non-anchor file — "
                    "anchor rules belong in src/voice-agent/prompts/anchor_rules.md"
                )
            continue
        rule_match = RULE_LINE_RE.match(line)
        if not rule_match or current_tier is None:
            continue
        meta = _parse_meta(rule_match.group("meta"))
        rule_id = meta.get("id")
        if not rule_id:
            continue
        rules.append(Rule(
            id=str(rule_id),
            tier=str(meta.get("tier", current_tier)),
            text=rule_match.group("text").strip(),
            created=meta.get("created"),
            reinforced=meta.get("reinforced"),
            retired=meta.get("retired"),
            turns=meta.get("turns", []) if isinstance(meta.get("turns"), list) else [],
            supersedes=meta.get("supersedes", []) if isinstance(meta.get("supersedes"), list) else [],
            superseded_by=meta.get("superseded_by"),
            proposal=meta.get("proposal"),
            evidence=str(meta.get("evidence", "")),
            reason=meta.get("reason"),
            evaluator=meta.get("evaluator", {}) if isinstance(meta.get("evaluator"), dict) else {},
            shadow_until=meta.get("shadow_until"),
        ))
    return ParsedRules(frontmatter=frontmatter, rules=rules)


def _serialize_rule(r: Rule) -> str:
    parts: list[str] = [f"id={r.id}", f"tier={r.tier}"]
    if r.created:        parts.append(f"created={r.created}")
    if r.reinforced:     parts.append(f"reinforced={r.reinforced}")
    if r.retired:        parts.append(f"retired={r.retired}")
    if r.turns:          parts.append(f"turns=[{','.join(r.turns)}]")
    if r.supersedes:     parts.append(f"supersedes=[{','.join(r.supersedes)}]")
    if r.superseded_by:  parts.append(f"superseded_by={r.superseded_by}")
    if r.proposal:       parts.append(f"proposal={r.proposal}")
    if r.evidence:       parts.append(f'evidence="{r.evidence}"')
    if r.reason:         parts.append(f"reason={r.reason}")
    if r.evaluator:
        body = ",".join(f"{k}:{v}" for k, v in r.evaluator.items())
        parts.append(f"evaluator={{{body}}}")
    if r.shadow_until:   parts.append(f"shadow_until={r.shadow_until}")
    return f"- <!-- {' '.join(parts)} --> {r.text}"


def serialize_rules_v2(parsed: ParsedRules) -> str:
    lines: list[str] = []
    if parsed.frontmatter:
        lines.append("---")
        for k, v in parsed.frontmatter.items():
            lines.append(f"{k}: {v}")
        lines.append("---")
        lines.append("")
    lines.append("# JARVIS Learned Rules")
    lines.append("")
    section_order = ["anchor", "core", "accepted", "staged", "archived"]
    by_tier: dict[str, list[Rule]] = {}
    for rule in parsed.rules:
        by_tier.setdefault(rule.tier, []).append(rule)
    for tier in section_order:
        rules = by_tier.get(tier, [])
        if not rules:
            continue
        lines.append(f"## ═══ {tier.upper()} ═══")
        lines.append("")
        for r in rules:
            lines.append(_serialize_rule(r))
        lines.append("")
    return "\n".join(lines)
