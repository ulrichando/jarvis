"""Rule store: read/write learned_rules.md with anchor sha-check.

Single point of truth for any code that wants to mutate the rule
file. Refuses anchor-tier writes structurally — there is no API to
write to the anchor file from runtime code. The anchor file is
git-tracked and human-edited only.
"""
from __future__ import annotations

import hashlib
import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .schema import (
    ParsedRules,
    Rule,
    SchemaError,
    parse_rules_v2,
    serialize_rules_v2,
)


__all__ = [
    "AnchorTamperingError",
    "AnchorWriteRefused",
    "LoadedRules",
    "RuleStore",
]


logger = logging.getLogger("jarvis.evolution.store")


_DEFAULT_ANCHOR_PATH = (
    Path(__file__).resolve().parents[2] / "prompts" / "anchor_rules.md"
)
_DEFAULT_LEARNED_PATH = Path.home() / ".jarvis" / "learned_rules.md"


class AnchorTamperingError(RuntimeError):
    """Anchor file sha doesn't match the baseline recorded in learned_rules.md."""


class AnchorWriteRefused(PermissionError):
    """A runtime caller tried to write to the anchor tier or file."""


@dataclass
class LoadedRules:
    anchor: list[Rule] = field(default_factory=list)
    core: list[Rule] = field(default_factory=list)
    accepted: list[Rule] = field(default_factory=list)
    staged: list[Rule] = field(default_factory=list)
    archived: list[Rule] = field(default_factory=list)

    @property
    def all_rules(self) -> list[Rule]:
        return self.anchor + self.core + self.accepted + self.staged + self.archived

    def with_replacement(self, rule_id: str, replacement: Rule) -> "LoadedRules":
        out = LoadedRules(
            anchor=list(self.anchor),
            core=[r for r in self.core if r.id != rule_id],
            accepted=[r for r in self.accepted if r.id != rule_id],
            staged=[r for r in self.staged if r.id != rule_id],
            archived=[r for r in self.archived if r.id != rule_id],
        )
        getattr(out, replacement.tier).append(replacement)
        return out


class RuleStore:
    def __init__(
        self,
        *,
        anchor_path: Path = _DEFAULT_ANCHOR_PATH,
        learned_path: Path = _DEFAULT_LEARNED_PATH,
    ) -> None:
        self.anchor_path = Path(anchor_path)
        self.learned_path = Path(learned_path)
        self._loaded: Optional[LoadedRules] = None
        self._anchor_sha: Optional[str] = None

    @staticmethod
    def _sha256_of(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _read_anchor(self) -> tuple[ParsedRules, str]:
        text = self.anchor_path.read_text(encoding="utf-8")
        sha = self._sha256_of(text)
        return parse_rules_v2(text, allow_anchor=True), sha

    def _read_learned(self) -> ParsedRules:
        if not self.learned_path.exists():
            return ParsedRules(frontmatter={"schema_version": 2}, rules=[])
        return parse_rules_v2(
            self.learned_path.read_text(encoding="utf-8"),
            allow_anchor=False,
        )

    def load(self) -> LoadedRules:
        anchor_parsed, anchor_sha = self._read_anchor()
        learned = self._read_learned()

        baseline = learned.frontmatter.get("anchor_baseline_sha256")
        if baseline and baseline != anchor_sha:
            raise AnchorTamperingError(
                f"anchor sha mismatch: file={anchor_sha[:12]} "
                f"baseline={str(baseline)[:12]} — refusing to load"
            )
        if not baseline:
            logger.info(
                "[store] no anchor baseline recorded; first run, "
                f"writing baseline={anchor_sha[:12]}"
            )
            learned.frontmatter["anchor_baseline_sha256"] = anchor_sha

        out = LoadedRules()
        for rule in anchor_parsed.rules:
            if rule.tier == "anchor":
                out.anchor.append(rule)
        for rule in learned.rules:
            if rule.tier == "anchor":
                raise SchemaError(
                    "anchor-tier rule in learned_rules.md — these belong "
                    "in the git-tracked anchor file"
                )
            bucket = getattr(out, rule.tier, None)
            if bucket is None:
                raise SchemaError(
                    f"unknown tier {rule.tier!r} for rule {rule.id} — "
                    f"valid tiers: anchor / core / accepted / staged / archived"
                )
            bucket.append(rule)

        self._loaded = out
        self._anchor_sha = anchor_sha
        self._learned_frontmatter = learned.frontmatter
        return out

    def _ensure_loaded(self) -> LoadedRules:
        if self._loaded is None:
            self.load()
        assert self._loaded is not None
        return self._loaded

    def _write_learned(self, loaded: LoadedRules) -> None:
        non_anchor: list[Rule] = (
            loaded.core + loaded.accepted + loaded.staged + loaded.archived
        )
        parsed = ParsedRules(
            frontmatter={
                "schema_version": 2,
                "anchor_baseline_sha256": self._anchor_sha or "",
            },
            rules=non_anchor,
        )
        text = serialize_rules_v2(parsed)
        self.learned_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.learned_path.with_suffix(self.learned_path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.learned_path)

    def save_rule(self, rule: Rule) -> None:
        if rule.tier == "anchor":
            raise AnchorWriteRefused(
                f"refused to write rule {rule.id} with tier=anchor; "
                "anchor edits go through the git-tracked anchor file"
            )
        loaded = self._ensure_loaded()
        bucket = getattr(loaded, rule.tier, None)
        if bucket is None:
            raise SchemaError(f"unknown tier: {rule.tier!r}")
        bucket[:] = [r for r in bucket if r.id != rule.id]
        bucket.append(rule)
        self._write_learned(loaded)

    def update_tier(self, rule_id: str, *, new_tier: str) -> None:
        if new_tier == "anchor":
            raise AnchorWriteRefused(
                f"refused to promote rule {rule_id} to anchor tier"
            )
        loaded = self._ensure_loaded()
        target: Optional[Rule] = None
        for bucket_name in ("core", "accepted", "staged", "archived"):
            bucket = getattr(loaded, bucket_name)
            for r in bucket:
                if r.id == rule_id:
                    target = r
                    break
            if target is not None:
                bucket[:] = [r for r in bucket if r.id != rule_id]
                break
        if target is None:
            raise KeyError(f"rule {rule_id!r} not found")
        target.tier = new_tier
        getattr(loaded, new_tier).append(target)
        self._write_learned(loaded)
