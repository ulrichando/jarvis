#!/usr/bin/env python3
"""
Heuristic hallucination audit over ~/.jarvis/conversations.db.

Limitation up front: we don't have per-turn tool-call logs in the DB
(those went to the agent process log file, which rotates). So we can't
prove "the assistant claimed it did X without a tool firing." What we
CAN do is:

  (1) Count assistant turns that contain forbidden "I just did X"
      claim phrases (the same family the system prompt forbids unless
      paired with a tool call). High count = high *opportunity* for
      hallucination.

  (2) Count cases where the user's NEXT turn pushes back ("no you
      didn't", "that's not right", "nothing happened"). This is the
      strongest signal — the user IS the ground truth, and a pushback
      directly after a claim is almost certainly a real failure.

  (3) Compute the conditional rate: when JARVIS claimed an action,
      how often did the user push back?

Reported per speech-LLM-model is impossible from this data alone (the
voice-model file changes over time but isn't logged per turn). So the
numbers are aggregate across all speech models used during the window.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

DB = Path.home() / ".jarvis" / "conversations.db"

# ── Pattern library ────────────────────────────────────────────────────

# Assistant-turn "I just did X" claims. Mirrors the FORBIDDEN list in
# jarvis_agent.py JARVIS_INSTRUCTIONS — these phrases are only OK if
# the message also fires a tool. We can't check the tool side, but we
# can count how often the phrase appears.
CLAIM_RES = [
    re.compile(r"\bi(?:'ve| have)\s+"
               r"(?:opened|launched|started|paused|played|stopped|closed|"
               r"installed|run|ran|written|created|sent|fixed|disabled|"
               r"enabled|set\s+up|killed|removed|deleted|added|saved|"
               r"updated|done|gone\s+ahead\s+and)\b", re.I),
    re.compile(r"\b(?:opening|playing|launching|starting|stopping|"
               r"pausing|closing|installing|running)\s+"
               r"(?:now|it|that|spotify|chrome|firefox|youtube|terminal|"
               r"the|your)\b", re.I),
    re.compile(r"^\s*(?:on it[\.,!]?|done[\.,!]?|paused[\.,!]?|"
               r"resumed[\.,!]?|playing now[\.,!]?|opened[\.,!]?)\s*$", re.I | re.M),
    re.compile(r"\bi(?:'ll| will)\s+(?:open|launch|start|play|pause|"
               r"close|install|run)\s+\w+\s+(?:now|right away)\b", re.I),
]

# User pushback patterns — strong signal that JARVIS just got it wrong.
# Tuned for spoken English (STT output), so phrasing is colloquial.
PUSHBACK_RES = [
    re.compile(r"\bno\s+you\s+(?:didn'?t|did not|haven'?t)\b", re.I),
    re.compile(r"\byou\s+(?:didn'?t|did not|haven'?t|never)\b", re.I),
    re.compile(r"\bthat'?s\s+(?:not|wrong|incorrect|false)\b", re.I),
    re.compile(r"\bnothing\s+happened\b", re.I),
    re.compile(r"\bnothing\s+(?:opened|started|played|launched)\b", re.I),
    re.compile(r"\bstill\s+(?:not|isn'?t|hasn'?t|haven'?t)\b", re.I),
    re.compile(r"\b(?:isn'?t|aren'?t)\s+(?:open|playing|running|on|working)\b", re.I),
    re.compile(r"\b(?:didn'?t|did not)\s+work\b", re.I),
    re.compile(r"\byou'?re\s+(?:hallucinating|lying|making\s+(?:that|it)\s+up|wrong)\b", re.I),
    re.compile(r"\b(?:nope|no it'?s not|no that'?s not)\b", re.I),
    re.compile(r"\bwhat\s+are\s+you\s+talking\s+about\b", re.I),
    re.compile(r"\b(?:i\s+)?never\s+asked\b", re.I),
    re.compile(r"\bstop\s+(?:lying|hallucinating|making)\b", re.I),
]


def matches_any(text: str, patterns: list[re.Pattern]) -> bool:
    return any(p.search(text) for p in patterns)


def main() -> int:
    if not DB.exists():
        print(f"no DB at {DB}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(DB))
    rows = conn.execute(
        "SELECT session_id, ts, role, text FROM turns ORDER BY session_id ASC, ts ASC, id ASC",
    ).fetchall()
    conn.close()

    # Walk per-session in chronological order. For each assistant turn,
    # peek at the very NEXT user turn (within same session) for pushback.
    by_session: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for sid, ts, role, text in rows:
        by_session[sid].append((ts, role, (text or "").strip()))

    total_assistant = 0
    claim_assistant = 0
    pushback_user = 0
    claim_then_pushback = 0
    pushback_examples: list[tuple[str, str, str]] = []  # (session_id, claim, pushback)
    claim_examples: list[tuple[str, str]] = []  # (session_id, claim)

    for sid, turns in by_session.items():
        for i, (ts, role, text) in enumerate(turns):
            if role == "user":
                if matches_any(text, PUSHBACK_RES):
                    pushback_user += 1
                continue
            # role == 'assistant'
            total_assistant += 1
            is_claim = matches_any(text, CLAIM_RES)
            if is_claim:
                claim_assistant += 1
                if len(claim_examples) < 12:
                    claim_examples.append((sid, text[:140]))
                # peek at next user turn in same session
                for nt_ts, nt_role, nt_text in turns[i + 1:]:
                    if nt_role == "user":
                        if matches_any(nt_text, PUSHBACK_RES):
                            claim_then_pushback += 1
                            if len(pushback_examples) < 8:
                                pushback_examples.append(
                                    (sid, text[:120], nt_text[:120]),
                                )
                        break

    total_user = sum(1 for s in by_session.values() for t in s if t[1] == "user")
    sessions = len(by_session)

    pct = lambda n, d: (100.0 * n / d) if d else 0.0

    print("=" * 64)
    print(f"corpus:  {sessions} sessions, {total_assistant} assistant turns, "
          f"{total_user} user turns")
    print("=" * 64)
    print()
    print(f"  assistant turns claiming action       : {claim_assistant:5d}  "
          f"({pct(claim_assistant, total_assistant):.1f}% of assistant)")
    print(f"  user turns containing pushback        : {pushback_user:5d}  "
          f"({pct(pushback_user, total_user):.1f}% of user)")
    print(f"  claim → next user pushback (strong)   : {claim_then_pushback:5d}  "
          f"({pct(claim_then_pushback, claim_assistant):.1f}% of claim turns)")
    print()
    print(f"  → estimated *floor* hallucination rate: ~{pct(claim_then_pushback, total_assistant):.2f}% of all assistant turns")
    print(f"    (only counts cases where the user explicitly pushed back;")
    print(f"     real rate is higher — silent acceptance of fake claims is unobservable)")
    print()
    print("─" * 64)
    print("  CLAIM EXAMPLES (assistant turns that may have hallucinated)")
    print("─" * 64)
    for sid, text in claim_examples:
        print(f"  [{sid[:8]}] {text!r}")
    print()
    print("─" * 64)
    print("  CLAIM → PUSHBACK PAIRS (high-confidence failures)")
    print("─" * 64)
    for sid, claim, pushback in pushback_examples:
        print(f"  [{sid[:8]}]")
        print(f"    A: {claim!r}")
        print(f"    U: {pushback!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
