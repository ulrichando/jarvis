"""One-shot v1 (dated bullets) → v2 (tiered, metadata-rich) migrator.

Idempotent: re-runs against an already-v2 file produce the same
output. Dead-subsystem refs (ElevenLabs, butler-register) get
archived. Near-duplicates (Levenshtein-ratio ≥ 0.85) collapse to
first occurrence + supersedes pointer.
"""
from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from pathlib import Path

from .schema import ParsedRules, Rule, parse_rules_v2, serialize_rules_v2


__all__ = ["migrate_v1_to_v2"]


_V1_BULLET_RE = re.compile(r"^-\s+\[(\d{4}-\d{2}-\d{2})\]\s+(.+?)\s*$")

_DEAD_SUBSYSTEM_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\belevenlabs\b", re.IGNORECASE), "dead_subsystem"),
    (re.compile(r"\beleven\s+labs\b", re.IGNORECASE), "dead_subsystem"),
    (re.compile(r'\byes\s*,?\s*sir\b', re.IGNORECASE), "dead_subsystem"),
    (re.compile(r'\banswer\s+["\']yes,?\s*sir["\']', re.IGNORECASE), "dead_subsystem"),
]


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _dead_subsystem_reason(text: str) -> str | None:
    for pattern, reason in _DEAD_SUBSYSTEM_PATTERNS:
        if pattern.search(text):
            return reason
    return None


def _parse_v1_bullets(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        m = _V1_BULLET_RE.match(line.strip())
        if not m:
            continue
        out.append((m.group(1), m.group(2).strip()))
    return out


def _next_rule_id(used: set[str]) -> str:
    n = 1
    while f"R-{n:04d}" in used:
        n += 1
    return f"R-{n:04d}"


def migrate_v1_to_v2(
    *,
    v1_path: Path,
    anchor_path: Path,
    out_path: Path,
    similarity_threshold: float = 0.85,
) -> None:
    v1_path = Path(v1_path)
    out_path = Path(out_path)
    anchor_path = Path(anchor_path)

    existing_ids: set[str] = set()
    existing_rules: list[Rule] = []
    if out_path.exists():
        try:
            existing = parse_rules_v2(out_path.read_text(encoding="utf-8"))
            existing_rules = existing.rules
            existing_ids = {r.id for r in existing_rules}
        except (FileNotFoundError, UnicodeDecodeError) as e:
            import logging
            logging.getLogger("jarvis.evolution.migrate").warning(
                f"[migrate] existing v2 file unreadable ({e}); treating as empty"
            )
            existing_rules = []

    raw_text = v1_path.read_text(encoding="utf-8")
    bullets = _parse_v1_bullets(raw_text)

    by_text: dict[str, Rule] = {r.text: r for r in existing_rules}
    new_rules: list[Rule] = []
    archived_dups: list[Rule] = []

    for date_str, text in bullets:
        existing_match = by_text.get(text)
        if existing_match is not None:
            new_rules.append(existing_match)
            continue

        dead = _dead_subsystem_reason(text)
        if dead is not None:
            rid = _next_rule_id(existing_ids | {r.id for r in new_rules})
            new_rules.append(Rule(
                id=rid, tier="archived", text=text,
                created=date_str, retired=date_str, reason=dead,
            ))
            continue

        rid = _next_rule_id(existing_ids | {r.id for r in new_rules})
        new_rules.append(Rule(id=rid, tier="accepted", text=text, created=date_str))

    accepted_only = [r for r in new_rules if r.tier == "accepted"]
    keep: list[Rule] = []
    for candidate in accepted_only:
        twin: Rule | None = None
        for kept in keep:
            if _similarity(candidate.text, kept.text) >= similarity_threshold:
                twin = kept
                break
        if twin is None:
            keep.append(candidate)
            continue
        archived_dups.append(Rule(
            id=candidate.id, tier="archived", text=candidate.text,
            created=candidate.created, retired=candidate.created,
            superseded_by=twin.id, reason="duplicate",
        ))

    final_rules: list[Rule] = []
    for r in new_rules:
        if r.tier == "accepted":
            if any(k.id == r.id for k in keep):
                final_rules.append(r)
            elif any(d.id == r.id for d in archived_dups):
                final_rules.append(next(d for d in archived_dups if d.id == r.id))
        else:
            final_rules.append(r)

    # Preserve any v2-only rules (e.g. self-evolution-added runtime rules)
    # whose text doesn't appear in the v1 input. Without this, re-running the
    # migrator after self-evolution begins erases the runtime store.
    seen_v2_text = {r.text for r in final_rules}
    final_ids = {r.id for r in final_rules}
    for r in existing_rules:
        if r.id not in final_ids and r.text not in seen_v2_text:
            final_rules.append(r)

    anchor_sha = hashlib.sha256(
        anchor_path.read_text(encoding="utf-8").encode("utf-8")
    ).hexdigest()

    parsed = ParsedRules(
        frontmatter={
            "schema_version": 2,
            "anchor_baseline_sha256": anchor_sha,
        },
        rules=final_rules,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(serialize_rules_v2(parsed), encoding="utf-8")
