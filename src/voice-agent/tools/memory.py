"""Memory layer — durable user-facts that survive chat deletion.

Pattern: ChatGPT/Claude/Gemini "saved memories" — the LLM decides what
is worth keeping via tool calls. Stored in state.db.memories,
propagated through the hub bus (events:memory → broadcasts:memory).

Spec: docs/superpowers/specs/2026-05-03-jarvis-memory-layer-design.md
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import sys
import time
from pathlib import Path

from livekit.agents.llm import function_tool

logger = logging.getLogger("jarvis.memory")

# Make src/hub importable without polluting sys.path globally — same
# pattern jarvis_agent.py uses.
_HUB_DIR = str(Path(__file__).parent.parent / "hub")
if _HUB_DIR not in sys.path:
    sys.path.insert(0, _HUB_DIR)


# ── Sensitive content blocklist — NEVER persist these. ───────────────
# Same regex shape as the unified-settings watcher uses for keys.env.
_SENSITIVE_RE = re.compile(
    r"("
    r"api[\s_-]?key"
    r"|secret"
    r"|password"
    r"|bearer\s+\w+"
    r"|sk-[a-zA-Z0-9]+"
    r"|ghp_\w+"
    r"|aws_(?:access|secret)_key"
    r"|token\s*[:=]"  # "token: xyz" / "token=xyz"
    r")",
    re.I,
)

_MAX_CONTENT_CHARS = 500

# 2026-05-06 — taxonomy port from claude-code's memdir/memoryTypes.ts.
# The four canonical types are now `user` / `feedback` / `project` /
# `reference`. The old voice taxonomy (`identity` / `preference` /
# `project` / `fact`) is accepted as legacy aliases on write — they
# normalize to the new names so the durable store converges on the
# canonical taxonomy without a state.db migration.
_VALID_CATEGORIES = ("user", "feedback", "project", "reference")
_LEGACY_CATEGORY_MAP = {
    "identity":   "user",
    "preference": "feedback",
    "fact":       "reference",
    # `project` is unchanged.
}


def _normalize_category(raw: str | None) -> str:
    """Map legacy category names to their canonical replacement.
    Unknown / missing categories default to `reference` (the catchall
    for external facts) — the closest 1:1 to the legacy `fact` default."""
    if not raw:
        return "reference"
    raw = raw.strip().lower()
    if raw in _VALID_CATEGORIES:
        return raw
    return _LEGACY_CATEGORY_MAP.get(raw, "reference")


# ── Helpers (pure functions, easy to unit-test) ──────────────────────


def _normalize(text: str) -> str:
    return text.strip().lower()


def _memory_id(content: str) -> str:
    """Stable sha256 hex of normalized content. Same fact written twice
    → same id, so the apply path's ON CONFLICT keeps it as one row."""
    return hashlib.sha256(_normalize(content).encode("utf-8")).hexdigest()


def _is_sensitive(text: str) -> bool:
    return bool(_SENSITIVE_RE.search(text))


# ── Hub I/O — kept thin so tests can monkeypatch ────────────────────


async def _publish_event_async(event_type: str, payload: dict) -> None:
    """Publish to events:memory via the hub Python SDK. Lazy-imported
    so this module loads even when the hub is unreachable."""
    from client import HubClient, MEMORY_EVENTS_STREAM
    # Ephemeral client per call — avoids holding a Redis connection
    # across the lifetime of the voice agent and keeps tests simple.
    hub = HubClient.from_url(source="voice")
    sid = os.environ.get("JARVIS_VOICE_SESSION_ID", "voice-default")
    try:
        await hub.publish(
            type=event_type,
            session_id=sid,
            payload=payload,
            stream=MEMORY_EVENTS_STREAM,
        )
    finally:
        try:
            await hub._redis.aclose()
        except Exception:
            pass


def _publish_event(event_type: str, payload: dict) -> None:
    """Sync wrapper used by tools (which are async via function_tool
    but the hub SDK is async). The function_tool harness already has
    an event loop; create a task instead of asyncio.run() so we don't
    spin up a second loop."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(_publish_event_async(event_type, payload))
        else:
            loop.run_until_complete(_publish_event_async(event_type, payload))
    except RuntimeError:
        # No event loop — happens in tests. asyncio.run() is fine here.
        asyncio.run(_publish_event_async(event_type, payload))


def _read_memories_via_sdk(
    category: str | None = None, limit: int = 30,
) -> list[dict]:
    """Synchronous read against state.db. No Redis round-trip."""
    from client import HubClient
    return HubClient.read_memories_sync(category=category, limit=limit)


def _bump_uses_via_sdk(memory_ids: list[str]) -> None:
    from client import HubClient
    HubClient.bump_memory_use_sync(memory_ids)


# ── @function_tool entry points the LLM can call ────────────────────


@function_tool
async def remember(content: str, category: str = "reference") -> str:
    """Store a durable fact about Ulrich (or guidance about how to
    behave). Persists across sessions in state.db.

    ## Types of memory (port of claude-code's memdir taxonomy)

    Pick the one that fits — wrong category isn't fatal, but the
    right one means recall surfaces the memory at the right moment.

    - **user** — about Ulrich's role, goals, knowledge, situation.
      Helps you tailor future replies.
        when_to_save: any details about his role, preferences,
                      responsibilities, or knowledge.
        examples:
          "I run Pretva, a ride-hailing service in Cameroon"
          "Background in OHADA / ADR legal practice"
          "Currently focused on JARVIS voice-agent debugging"

    - **feedback** — guidance you've been given about HOW to work.
      Both corrections AND validated approaches.
        when_to_save: user corrects you ("don't", "stop doing X")
                      OR confirms an unusual approach worked
                      ("yes exactly", "perfect"). Quiet
                      confirmations matter — watch for them.
        body_structure (REQUIRED): lead with the rule, then:
            **Why:** {past incident or strong preference}
            **How to apply:** {when/where this guidance kicks in}
        examples:
          "Don't end replies with 'is there anything else'.
           Why: he asked me to stop on 2026-04-28 after I did it 21
           times in 25 turns. How to apply: every reply — close
           when the answer's done, no hedge-question."

          "For voice-agent debugging, lead with the diagnosis, not
           the category. Why: he validated this 2026-05-05 when I
           said 'circular import' instead of 'several reasons'.
           How to apply: code questions, system questions."

    - **project** — ongoing work, goals, initiatives, bugs,
      incidents the user is in the middle of.
        when_to_save: who's doing what, why, by when. State changes
                      fast — keep current. ALWAYS convert relative
                      dates to absolute: "Thursday" → "2026-05-08".
        body_structure (REQUIRED): lead with the fact/decision, then:
            **Why:** {motivation — constraint, deadline, ask}
            **How to apply:** {how this should shape suggestions}
        examples:
          "Barge-in fix ships behind JARVIS_APM_AEC 2026-05-20.
           Why: 'drop mic while speaking' killed interruption; the
           reverse-stream AEC restores it. How to apply: when the
           user reports echo or barge-in issues, check the AEC
           cascade layers before touching the supervisor."

    - **reference** — pointers to external systems where info lives.
        when_to_save: when you learn an external resource and its
                      purpose (Linear project, Slack channel,
                      Grafana dashboard, etc.).
        examples:
          "Pretva driver issues tracked at /etc/pretva/issues.md
           — check before answering operational questions."

    ## What NOT to save (lifted from claude-code's ban list)

    - Code patterns, conventions, architecture, file paths, or
      project structure — these can be derived by reading the
      current project state.
    - Git history, recent changes, who-changed-what — git log /
      git blame are authoritative.
    - Debugging fix recipes — the fix is in the code; the commit
      message has the context.
    - Anything already documented in CLAUDE.md or the supervisor
      prompt.
    - Ephemeral task details: in-progress work, temporary state,
      "right now I'm hungry", "today I'm working on X".
    - Credentials / secrets — blocked automatically by the
      sensitive-content filter.

    These exclusions apply even when the user explicitly asks you
    to save. If they ask "save my recent PR list" / "remember
    today's activity log", ask what was *surprising* or
    *non-obvious* about it — that's the part worth keeping.

    ## Backward compatibility

    Legacy category names (identity / preference / fact) still work
    on write — they normalize to (user / feedback / reference)
    automatically. No migration needed.

    Args:
        content: The fact in one short sentence (≤500 chars). For
                 feedback/project, INCLUDE the structured Why: /
                 How to apply: lines.
        category: One of 'user' / 'feedback' / 'project' /
                  'reference'. Default 'reference'.
    """
    text = (content or "").strip()
    if not text:
        return "(empty memory — nothing to save)"
    if _is_sensitive(text):
        logger.info("[memory] blocked sensitive content")
        return "That looks like a credential — I won't store it."
    if len(text) > _MAX_CONTENT_CHARS:
        return (
            f"Memory too long — keep it under "
            f"{_MAX_CONTENT_CHARS} characters."
        )
    # Normalize legacy aliases to the canonical taxonomy.
    category = _normalize_category(category)

    mid = _memory_id(text)
    _publish_event("memory.value.upserted", {
        "memory_id": mid,
        "content": text,
        "category": category,
        "source_session_id": os.environ.get("JARVIS_VOICE_SESSION_ID"),
    })
    return "Saved."


@function_tool
async def forget(query: str) -> str:
    """Remove a memory matching a query. Use when the user says
    'forget that I…' / 'remove the memory about X'.

    Args:
        query: Keyword(s) describing the memory to remove.
    """
    if not query or not query.strip():
        return "(no query — what should I forget?)"

    candidates = _read_memories_via_sdk(limit=50)
    q = query.strip().lower()
    match = next(
        (m for m in candidates if q in m["content"].lower()),
        None,
    )
    if not match:
        return f"No match for {query!r}."

    _publish_event("memory.value.removed", {"memory_id": match["memory_id"]})
    snippet = match["content"]
    if len(snippet) > 80:
        snippet = snippet[:77] + "…"
    return f"Forgotten: {snippet}"


@function_tool
async def list_memories(category: str | None = None) -> str:
    """List your saved memories. Use when the user asks 'what do you
    remember about me' or wants to audit what you know.

    Args:
        category: Optional filter — 'user' / 'feedback' / 'project'
                  / 'reference'. Legacy aliases ('identity' /
                  'preference' / 'fact') auto-normalize.
    """
    if category:
        category = _normalize_category(category)
    rows = _read_memories_via_sdk(category=category, limit=30)
    if not rows:
        return "I haven't saved any memories yet."
    bullets = "\n  - ".join(
        f"[{r['category']}] {r['content']}" for r in rows
    )
    return f"What I remember:\n  - {bullets}"


# ── System-prompt injection (called per-turn from jarvis_agent) ─────


# 2026-05-06 — port of claude-code's memdir/memoryAge.ts. Models are
# poor at date arithmetic (a raw ISO timestamp doesn't trigger
# staleness reasoning the way "47 days ago" does), so age strings are
# rendered as human-readable phrases. The drift caveat we already
# shipped in the supervisor prompt told the LLM to question stale
# memories — these helpers give it the AGE info it needs to do that.

# >= this many days → render a system-reminder line warning the LLM
# the memory may be stale. 30 days matches claude-code's default
# `MEMORY_FRESHNESS_DAYS` floor for code-citation drift.
_STALE_DAYS = 30


def _memory_age_days(updated_ts_unix: int | float | None) -> int | None:
    """Days elapsed since updated_ts. Floor-rounded — 0 for today,
    1 for yesterday, 2+ for older. Negative inputs (clock skew, future
    mtime) clamp to 0. Returns None if no timestamp."""
    if updated_ts_unix is None:
        return None
    try:
        delta_s = max(0.0, time.time() - float(updated_ts_unix))
        return int(delta_s // 86_400)
    except (TypeError, ValueError):
        return None


def _memory_age(updated_ts_unix: int | float | None) -> str:
    """Human-readable age. 'today' / 'yesterday' / 'N days ago' / ''."""
    d = _memory_age_days(updated_ts_unix)
    if d is None:
        return ""
    if d == 0:
        return "today"
    if d == 1:
        return "yesterday"
    return f"{d} days ago"


def _memory_freshness_text(updated_ts_unix: int | float | None) -> str:
    """Per-memory staleness caveat for memories ≥ _STALE_DAYS old.
    Empty string for fresh memories so they don't add prompt noise.

    Voice port of claude-code's memoryFreshnessText() — voice TTS
    can't render <system-reminder> tags so this returns the bare
    text; format_memories_for_prompt() prefixes the block once
    rather than per-bullet."""
    d = _memory_age_days(updated_ts_unix)
    if d is None or d < _STALE_DAYS:
        return ""
    return (
        f"This memory is {d} days old. Memories are point-in-time "
        f"observations, not live state — claims about people, "
        f"projects, or systems may be outdated. Verify against "
        f"current state before relying on it."
    )


def format_memories_for_prompt(top_n: int | None = None) -> str:
    """Render top-N memories as a system-prompt block. Empty string
    when nothing is saved (so the system prompt stays clean for new
    users).

    Each bullet now includes the memory's age so the LLM can apply
    the drift caveat (in the supervisor prompt's MEMORY section)
    proportionally — fresh memories trusted, stale ones verified
    before relying on. A trailing system-reminder warns explicitly
    when ANY memory in the block is ≥30 days old.

    Side effect: bumps use_count + last_used_ts for each memory
    included so heavily-referenced memories rise.
    """
    if top_n is None:
        # Unified default to 8 (matches pipeline/config.py:MEMORY_TOP_N)
        # — was 30 here, which silently flooded every supervisor prompt
        # with 30 (largely-polluted) memories. Per global review §P0-8.
        top_n = int(os.environ.get("JARVIS_MEMORY_TOP_N", "8"))
    rows = _read_memories_via_sdk(limit=top_n)
    if not rows:
        return ""

    # Build bullets with age annotations.
    bullets_out: list[str] = []
    has_stale = False
    for r in rows:
        age = _memory_age(r.get("updated_ts"))
        age_part = f" · {age}" if age else ""
        bullets_out.append(f"  - [{r['category']}{age_part}] {r['content']}")
        if (_memory_age_days(r.get("updated_ts")) or 0) >= _STALE_DAYS:
            has_stale = True

    bullets = "\n".join(bullets_out)
    block_parts = [
        "## What you remember about Ulrich",
        "(Curated facts. Use them naturally; don't recite them. Age "
        "shown next to each — verify older memories before relying.)",
        bullets,
    ]
    # Single staleness reminder when any memory is over the threshold —
    # one line is enough; per-memory reminders would balloon the
    # block. The supervisor prompt's MEMORY section's drift caveat
    # tells the LLM what to DO with this signal.
    if has_stale:
        block_parts.append(
            f"<system-reminder>One or more memories above are "
            f"≥{_STALE_DAYS} days old. Per the MEMORY section's "
            f"drift caveat: verify against current state before "
            f"acting on stale claims.</system-reminder>"
        )
    block = "\n".join(block_parts)

    try:
        _bump_uses_via_sdk([r["memory_id"] for r in rows])
    except Exception as e:
        logger.warning("[memory] bump failed: %s", e)
    return block


def _word_overlap_ratio(a: str, b: str) -> float:
    """Jaccard similarity on word sets, lowercased + stopwords stripped.
    Returns 0..1. Used by audit_memories to flag near-duplicate pairs.

    Stopwords are the trivial English connectives — without filtering
    them, "I prefer terse" and "I prefer images" would score 67% just
    on the shared "i" + "prefer" tokens. With the filter the overlap
    drops to 0% (no shared content words)."""
    _STOPWORDS = frozenset({
        "the", "a", "an", "is", "are", "was", "were", "to", "of", "and",
        "or", "but", "in", "on", "at", "for", "with", "as", "by", "from",
        "i", "you", "he", "she", "it", "we", "they", "me", "him", "her",
        "us", "them", "my", "your", "his", "its", "our", "their",
        "be", "do", "have", "has", "had", "this", "that", "these", "those",
        "not", "no", "yes",
    })
    a_words = {w.lower() for w in re.findall(r"\w+", a) if w.lower() not in _STOPWORDS}
    b_words = {w.lower() for w in re.findall(r"\w+", b) if w.lower() not in _STOPWORDS}
    if not a_words or not b_words:
        return 0.0
    return len(a_words & b_words) / len(a_words | b_words)


# Threshold for flagging near-duplicates. 50% of (non-stopword) word
# overlap is high enough to catch genuine redundancy without false-
# positiving on memories that happen to share a domain noun
# (e.g. two distinct "Pretva" facts).
_NEAR_DUPE_THRESHOLD = 0.5


@function_tool
async def audit_memories() -> str:
    """Audit the durable-fact memory store and produce a report.

    Use when the user says any of:
      "audit my memories" / "review what you remember" /
      "clean up your memory" / "what do you have on me" /
      "review your memory" / "show me what you remember".

    The report covers:
      - total count + count per category (user/feedback/project/reference)
      - stale entries (≥30 days old — the same staleness threshold
        format_memories_for_prompt uses for the system-reminder)
      - near-duplicate pairs (≥50% word overlap, stopwords stripped)

    The supervisor's job is to voice the GIST briefly:
      "23 memories — 2 stale, 1 near-duplicate pair. Want me
       to walk through them?"
    Then call forget(query) on individual entries the user wants
    pruned. The full report is what audit_memories() returns; the
    supervisor decides how much detail to voice.
    """
    rows = _read_memories_via_sdk(limit=200)
    if not rows:
        return "No memories saved yet."

    # Group by category.
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(r.get("category", "?"), []).append(r)

    # Stale check (uses the same threshold as the system-reminder
    # that the supervisor prompt's drift caveat references).
    stale: list[tuple[dict, int]] = []
    for r in rows:
        days = _memory_age_days(r.get("updated_ts"))
        if days is not None and days >= _STALE_DAYS:
            stale.append((r, days))

    # Near-duplicate detection — pairwise on the full set. O(n²) is
    # fine for the expected scale (tens to low hundreds of memories).
    near_dupes: list[tuple[dict, dict, float]] = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            overlap = _word_overlap_ratio(rows[i]["content"], rows[j]["content"])
            if overlap >= _NEAR_DUPE_THRESHOLD:
                near_dupes.append((rows[i], rows[j], overlap))

    # Build the report.
    lines = [
        "## Memory audit",
        f"Total: {len(rows)} memories.",
        "By category: " + ", ".join(
            f"{c}={len(rs)}" for c, rs in sorted(by_cat.items())
        ) + ".",
    ]

    if stale:
        lines.append(f"\nStale (≥{_STALE_DAYS} days, may be outdated):")
        for r, days in sorted(stale, key=lambda x: -x[1]):
            content = r["content"]
            if len(content) > 80:
                content = content[:77] + "…"
            lines.append(f"  - [{r.get('category', '?')} · {days}d] {content}")
    else:
        lines.append("\nStale: none.")

    if near_dupes:
        lines.append(
            f"\nNear-duplicate pairs (≥{int(_NEAR_DUPE_THRESHOLD*100)}% "
            f"word overlap, may be redundant):"
        )
        for a, b, overlap in sorted(near_dupes, key=lambda x: -x[2]):
            def _short(s: str) -> str:
                return s[:60] + "…" if len(s) > 60 else s
            lines.append(
                f"  - {int(overlap*100)}% overlap:"
            )
            lines.append(f"      [{a.get('category', '?')}] {_short(a['content'])}")
            lines.append(f"      [{b.get('category', '?')}] {_short(b['content'])}")
    else:
        lines.append("\nNear-duplicates: none.")

    return "\n".join(lines)


def is_available() -> bool:
    """True if the hub state.db is readable. Otherwise tools won't be
    registered and the system-prompt block stays empty."""
    try:
        from client import HubClient
        HubClient.read_memories_sync(limit=1)
        return True
    except Exception as e:
        logger.warning("[memory] hub unavailable: %s", e)
        return False
