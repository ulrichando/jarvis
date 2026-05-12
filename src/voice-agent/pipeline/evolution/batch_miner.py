"""Producer B — 12 h telemetry miner.

Scans `~/.local/share/jarvis/turn_telemetry.db` for evolution-relevant
signals (correction phrases, interrupted clusters, route_fallback
patterns, context_pressure spikes), then asks a cheap LLM to propose
1-3 concrete behavioral rules from the categorized evidence. Each
candidate proposal is dropped if its evidence turn count is below
`min_evidence` (default 3).

Replaces the LLM-call surface of `tools/log_analyzer.py`. The
analyzer module continues to exist but its evidence-gathering and
LLM-call functions now delegate here.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


__all__ = ["TELEMETRY_DB_PATH", "mine"]


logger = logging.getLogger("jarvis.evolution.batch_miner")

TELEMETRY_DB_PATH: Path = (
    Path.home() / ".local" / "share" / "jarvis" / "turn_telemetry.db"
)

_CORRECTION_WORDS = [
    "that was wrong", "you keep doing", "don't do that", "never do that",
    "stop doing", "why did you", "that's not what", "didn't ask you to",
    "i didn't say", "you got it wrong", "that's incorrect", "you're wrong",
    "don't open", "don't play", "don't start", "i never asked",
]


def _gather(cutoff_iso: str) -> dict:
    ev: dict = {
        "correction_turns": [],
        "interrupted_turns": [],
        "route_fallback_turns": [],
        "hard_pressure_turns": [],
    }
    if not TELEMETRY_DB_PATH.exists():
        return ev
    try:
        with sqlite3.connect(str(TELEMETRY_DB_PATH), timeout=2.0) as conn:
            rows = conn.execute(
                "SELECT id, ts_utc, user_text, jarvis_text, route, "
                "       interrupted, route_fallback, context_pressure "
                "FROM turns WHERE ts_utc >= ? ORDER BY ts_utc ASC",
                (cutoff_iso,),
            ).fetchall()
    except Exception as e:
        logger.warning(f"[miner] db read failed: {e}")
        return ev

    for row in rows:
        tid, ts, utext, jtext, route, interrupted, rfb, pressure = row
        turn_label = f"t-{tid}"
        text_label = f"{ts} [{route or '?'}] ({turn_label})"
        utext = (utext or "").strip()

        low_u = utext.lower()
        if any(w in low_u for w in _CORRECTION_WORDS):
            ev["correction_turns"].append(f"{text_label} user: {utext[:160]}")
        if interrupted:
            ev["interrupted_turns"].append(f"{text_label} user: {utext[:120]}")
        if rfb:
            ev["route_fallback_turns"].append(f"{text_label} user: {utext[:120]}")
        if pressure == "hard":
            ev["hard_pressure_turns"].append(f"{text_label} user: {utext[:120]}")
    return ev


def _has_signal(ev: dict) -> bool:
    return any(ev.values())


def _propose_with_llm(evidence: dict) -> list[dict]:
    """Submit categorized evidence to Groq; parse JSON proposals.

    Cheap proposer (llama-3.1-8b-instant). The PoLL ensemble judge is
    separate (see evaluator/poll_ensemble.py) and uses different families.
    """
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        logger.warning("[miner] GROQ_API_KEY missing; skipping")
        return []
    def _fmt(items: list[str], n: int = 8) -> str:
        return "\n".join(items[:n]) if items else "(none)"
    text = "\n\n".join(filter(None, [
        f"Correction-phrase user turns ({len(evidence['correction_turns'])}):\n"
        + _fmt(evidence["correction_turns"])
        if evidence["correction_turns"] else "",
        f"Interrupted turns ({len(evidence['interrupted_turns'])}):\n"
        + _fmt(evidence["interrupted_turns"])
        if evidence["interrupted_turns"] else "",
        f"Route-fallback turns ({len(evidence['route_fallback_turns'])}):\n"
        + _fmt(evidence["route_fallback_turns"])
        if evidence["route_fallback_turns"] else "",
        f"Hard context-pressure turns ({len(evidence['hard_pressure_turns'])}):\n"
        + _fmt(evidence["hard_pressure_turns"])
        if evidence["hard_pressure_turns"] else "",
    ]))
    prompt = (
        "You are mining a voice assistant's telemetry to propose specific "
        "behavioral rules that would prevent recurring mistakes.\n\n"
        f"Evidence:\n{text}\n\n"
        "Return a JSON array of up to 3 proposals. Each has:\n"
        "  pattern: one-sentence description of the recurring failure\n"
        "  evidence: 1-3 sentence summary of the concrete signal\n"
        "  rule: a concrete ≤200-char behavioral rule\n"
        "  evidence_turns: list of turn-id strings from the evidence above "
        "(format: 't-<id>')\n"
        "If no clear recurring pattern, return []."
    )
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 800,
    }
    try:
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "jarvis-evolution/1.0 (+batch_miner)",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        raw = data["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        # llama-3.1-8b-instant frequently appends trailing prose after
        # the JSON array ("Hope this helps!", a follow-up object, etc.).
        # json.loads is strict; use raw_decode to consume only the
        # first valid JSON value. Fall back to a bracket-match
        # extractor if raw_decode can't find a value at offset 0.
        try:
            parsed, _end = json.JSONDecoder().raw_decode(raw)
        except json.JSONDecodeError:
            start = raw.find("[")
            if start < 0:
                return []
            try:
                parsed, _end = json.JSONDecoder().raw_decode(raw[start:])
            except json.JSONDecodeError as e:
                logger.warning(f"[miner] JSON extraction failed: {e}")
                return []
        if not isinstance(parsed, list):
            return []
        out: list[dict] = []
        for item in parsed[:3]:
            if not isinstance(item, dict) or not item.get("rule"):
                continue
            turns = item.get("evidence_turns")
            if not isinstance(turns, list):
                turns = []
            out.append({
                "source": "batch_miner",
                "pattern": str(item.get("pattern") or "")[:200],
                "evidence": str(item.get("evidence") or "")[:300],
                "rule": str(item.get("rule") or "")[:200],
                "evidence_turns": [str(t) for t in turns],
                "evidence_quote": str(item.get("evidence") or "")[:300],
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
        return out
    except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
        logger.warning(f"[miner] LLM call failed: {e}")
        return []


def mine(
    *, lookback_days: int = 7, min_evidence: int = 3
) -> list[dict]:
    cutoff_iso = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() - lookback_days * 86400),
    )
    evidence = _gather(cutoff_iso)
    if not _has_signal(evidence):
        logger.info("[miner] no signal in evidence; skipping LLM call")
        return []
    proposals = _propose_with_llm(evidence)
    filtered = [p for p in proposals if len(p.get("evidence_turns") or []) >= min_evidence]
    logger.info(
        f"[miner] {len(proposals)} proposed, {len(filtered)} passed "
        f"min_evidence={min_evidence}"
    )
    return filtered
