"""Tests for the voice agent's memory tools (tools.memory.py)."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hub"))

import tools.memory as jm


def _run(coro):
    """Use a fresh event loop each call so tests are isolated."""
    return asyncio.new_event_loop().run_until_complete(coro)


# ── Pure helpers ──────────────────────────────────────────────────────


def test_memory_id_is_deterministic_sha256():
    a = jm._memory_id("User runs Pretva.")
    b = jm._memory_id("user runs pretva.")  # different case
    c = jm._memory_id("User runs Pretva.")  # exact match
    assert len(a) == 64  # sha256 hex
    assert a == c
    assert a == b  # normalize before hashing


def test_memory_id_changes_with_content():
    a = jm._memory_id("first thing")
    b = jm._memory_id("second thing")
    assert a != b


def test_normalize_strips_and_lowercases():
    assert jm._normalize("  HELLO World  ") == "hello world"


def test_is_sensitive_detects_common_credential_shapes():
    cases = [
        "OPENAI_API_KEY=sk-abc123",
        "my password is hunter2",
        "Bearer eyJhbGc",
        "ghp_xxxxxxxxxxxxxxxxxxxx",
        "token: ghp_xyz",
        "aws_secret_key = ABCDEF",
    ]
    for text in cases:
        assert jm._is_sensitive(text), f"missed: {text!r}"


def test_is_sensitive_passes_normal_facts():
    cases = [
        "User runs Pretva, a ride-hailing service in Cameroon.",
        "Prefers concise replies.",
        "Lives in Cameroon.",
        "Works on JARVIS, an AI assistant.",
    ]
    for text in cases:
        assert not jm._is_sensitive(text), f"false positive: {text!r}"


# ── remember() tool ──────────────────────────────────────────────────


def test_remember_publishes_upsert_event(monkeypatch):
    captured = []
    monkeypatch.setattr(
        jm, "_publish_event",
        lambda et, payload: captured.append((et, payload)),
    )
    result = _run(jm.remember._func(
        content="User runs Pretva, a ride-hailing service in Cameroon.",
        category="identity",  # legacy name — must normalize to 'user'
    ))
    assert "saved" in result.lower()
    assert len(captured) == 1
    et, payload = captured[0]
    assert et == "memory.value.upserted"
    # 2026-05-06 — memdir taxonomy port: legacy 'identity' normalizes
    # to canonical 'user' on write.
    assert payload["category"] == "user", (
        f"legacy 'identity' alias should normalize to 'user'; "
        f"got {payload['category']!r}"
    )
    assert "Pretva" in payload["content"]
    assert payload["memory_id"] == jm._memory_id(payload["content"])


def test_remember_blocks_sensitive_content(monkeypatch):
    captured = []
    monkeypatch.setattr(
        jm, "_publish_event",
        lambda et, payload: captured.append((et, payload)),
    )
    cases = [
        "OPENAI_API_KEY=sk-abc123",
        "my password is hunter2",
        "ghp_aaaaaaaaaaaaaaaaaaaa",
    ]
    for text in cases:
        result = _run(jm.remember._func(content=text))
        assert (
            "credential" in result.lower()
            or "won't store" in result.lower()
        ), f"not blocked: {text!r} → {result!r}"
    assert captured == [], "sensitive content was published"


def test_remember_rejects_overlong(monkeypatch):
    monkeypatch.setattr(jm, "_publish_event", lambda *a, **kw: None)
    long = "x" * 600
    result = _run(jm.remember._func(content=long))
    assert "too long" in result.lower() or "500" in result


def test_remember_empty_input(monkeypatch):
    monkeypatch.setattr(jm, "_publish_event", lambda *a, **kw: None)
    result = _run(jm.remember._func(content=""))
    assert "empty" in result.lower() or "nothing" in result.lower()


def test_remember_normalizes_invalid_category(monkeypatch):
    """Junk categories fall back to the canonical catchall.
    2026-05-06 memdir port: catchall changed from legacy 'fact' to
    canonical 'reference'."""
    captured = []
    monkeypatch.setattr(
        jm, "_publish_event",
        lambda et, payload: captured.append((et, payload)),
    )
    _run(jm.remember._func(content="Some fact.", category="nonsense"))
    assert captured[0][1]["category"] == "reference"


# ── forget() tool ────────────────────────────────────────────────────


def test_forget_publishes_remove_event(monkeypatch):
    captured = []
    monkeypatch.setattr(
        jm, "_publish_event",
        lambda et, payload: captured.append((et, payload)),
    )
    monkeypatch.setattr(
        jm, "_read_memories_via_sdk",
        lambda **kw: [{"memory_id": "abc", "content": "User runs Pretva"}],
    )
    result = _run(jm.forget._func(query="Pretva"))
    assert "forgotten" in result.lower()
    assert captured[0][0] == "memory.value.removed"
    assert captured[0][1]["memory_id"] == "abc"


def test_forget_no_match_returns_friendly_message(monkeypatch):
    captured = []
    monkeypatch.setattr(
        jm, "_publish_event",
        lambda et, payload: captured.append((et, payload)),
    )
    monkeypatch.setattr(
        jm, "_read_memories_via_sdk",
        lambda **kw: [{"memory_id": "x", "content": "totally unrelated"}],
    )
    result = _run(jm.forget._func(query="nonexistent"))
    assert "no match" in result.lower()
    assert captured == []


def test_forget_empty_query(monkeypatch):
    monkeypatch.setattr(jm, "_publish_event", lambda *a, **kw: None)
    result = _run(jm.forget._func(query=""))
    assert "what should i forget" in result.lower() or "no query" in result.lower()


# ── list_memories() tool ─────────────────────────────────────────────


def test_list_memories_voice_format(monkeypatch):
    monkeypatch.setattr(
        jm, "_read_memories_via_sdk",
        lambda **kw: [
            {"content": "Lives in Cameroon", "category": "identity",
             "use_count": 3},
            {"content": "Prefers terse replies", "category": "preference",
             "use_count": 1},
        ],
    )
    result = _run(jm.list_memories._func())
    assert "Lives in Cameroon" in result
    assert "Prefers terse replies" in result
    assert "[identity]" in result
    assert "[preference]" in result


def test_list_memories_empty(monkeypatch):
    monkeypatch.setattr(jm, "_read_memories_via_sdk", lambda **kw: [])
    result = _run(jm.list_memories._func())
    assert "haven't saved" in result.lower() or "no memories" in result.lower()


# ── format_memories_for_prompt — system-prompt injection ─────────────


def test_format_memories_for_prompt_with_rows(monkeypatch):
    monkeypatch.setattr(
        jm, "_read_memories_via_sdk", lambda **kw: [
            {"memory_id": "m1", "content": "User runs Pretva.",
             "category": "project"},
            {"memory_id": "m2", "content": "Prefers terse replies.",
             "category": "preference"},
        ],
    )
    block = jm.format_memories_for_prompt(top_n=10)
    assert "## What you remember about Ulrich" in block
    assert "User runs Pretva." in block
    assert "[project]" in block
    # use_count must NOT be bumped on prompt injection (2026-05-20 fix —
    # injection is not evidence of usefulness; the bump was a feedback loop).


def test_format_memories_empty_returns_blank(monkeypatch):
    monkeypatch.setattr(jm, "_read_memories_via_sdk", lambda **kw: [])
    monkeypatch.setattr(jm, "_bump_uses_via_sdk", lambda ids: None)
    assert jm.format_memories_for_prompt(top_n=10) == ""


def test_format_memories_top_n_from_env(monkeypatch):
    """JARVIS_MEMORY_TOP_N controls the cap when top_n is None."""
    seen_limit = []
    monkeypatch.setenv("JARVIS_MEMORY_TOP_N", "7")
    monkeypatch.setattr(
        jm, "_read_memories_via_sdk",
        lambda **kw: (seen_limit.append(kw.get("limit")) or []),
    )
    monkeypatch.setattr(jm, "_bump_uses_via_sdk", lambda ids: None)
    jm.format_memories_for_prompt()
    assert seen_limit == [7]


# ── 2026-05-06 memdir taxonomy port (claude-code's memdir/memoryTypes.ts) ─


def test_normalize_category_canonical_passthrough():
    """The 4 canonical names pass through untouched."""
    for name in ("user", "feedback", "project", "reference"):
        assert jm._normalize_category(name) == name


def test_normalize_category_legacy_aliases():
    """Legacy voice category names map to their claude-code equivalents."""
    assert jm._normalize_category("identity") == "user"
    assert jm._normalize_category("preference") == "feedback"
    assert jm._normalize_category("fact") == "reference"
    # 'project' was already shared — passes through.
    assert jm._normalize_category("project") == "project"


def test_normalize_category_unknown_falls_back_to_reference():
    """Junk / typo categories default to the catchall ('reference').
    Matches the legacy default behavior where 'fact' was the catchall."""
    assert jm._normalize_category("nonsense") == "reference"
    assert jm._normalize_category("") == "reference"
    assert jm._normalize_category(None) == "reference"


def test_normalize_category_case_insensitive():
    """Match claude-code's parseMemoryType: tolerate case variation."""
    assert jm._normalize_category("USER") == "user"
    assert jm._normalize_category(" Feedback ") == "feedback"
    assert jm._normalize_category("Identity") == "user"


def test_remember_canonical_taxonomy_writes_directly(monkeypatch):
    """A new caller using the canonical 'feedback' / 'user' / 'project' /
    'reference' names writes them straight through — no legacy mapping."""
    captured = []
    monkeypatch.setattr(
        jm, "_publish_event",
        lambda et, payload: captured.append((et, payload)),
    )
    for cat in ("user", "feedback", "project", "reference"):
        captured.clear()
        result = _run(jm.remember._func(
            content=f"Test memory for {cat} category.",
            category=cat,
        ))
        assert "saved" in result.lower()
        assert captured[0][1]["category"] == cat, (
            f"canonical {cat!r} should write as-is; "
            f"got {captured[0][1]['category']!r}"
        )


def test_remember_default_category_is_reference(monkeypatch):
    """Default changed from legacy 'fact' to canonical 'reference'."""
    captured = []
    monkeypatch.setattr(
        jm, "_publish_event",
        lambda et, payload: captured.append((et, payload)),
    )
    _run(jm.remember._func(content="Some plain fact without explicit category."))
    assert captured[0][1]["category"] == "reference"


def test_list_memories_legacy_filter_normalizes(monkeypatch):
    """Calling list_memories(category='preference') filters by 'feedback'
    (the canonical replacement)."""
    seen = []
    monkeypatch.setattr(
        jm, "_read_memories_via_sdk",
        lambda **kw: seen.append(kw.get("category")) or [],
    )
    _run(jm.list_memories._func(category="preference"))
    assert seen == ["feedback"], (
        f"legacy 'preference' filter should normalize to 'feedback'; "
        f"got {seen!r}"
    )


# ── 2026-05-06 memdir port #4a: memory age + freshness ──────────────


def test_memory_age_days_today():
    """Just-written memory: 0 days."""
    import time as _t
    assert jm._memory_age_days(_t.time()) == 0


def test_memory_age_days_yesterday():
    """24 hours ago: 1 day."""
    import time as _t
    assert jm._memory_age_days(_t.time() - 86_400) == 1


def test_memory_age_days_handles_clock_skew():
    """Future mtime (clock skew or NTP correction): clamp to 0."""
    import time as _t
    assert jm._memory_age_days(_t.time() + 3600) == 0


def test_memory_age_days_none_returns_none():
    """No timestamp: None (so callers can omit the age label)."""
    assert jm._memory_age_days(None) is None


def test_memory_age_strings():
    """today / yesterday / N days ago — claude-code phrasing because
    LLMs reason about staleness from natural-language ages, not ISO
    timestamps."""
    import time as _t
    now = _t.time()
    assert jm._memory_age(now) == "today"
    assert jm._memory_age(now - 86_400) == "yesterday"
    assert jm._memory_age(now - 47 * 86_400) == "47 days ago"
    assert jm._memory_age(None) == ""


def test_memory_freshness_text_silent_for_fresh():
    """No noise for memories under the staleness threshold."""
    import time as _t
    now = _t.time()
    assert jm._memory_freshness_text(now) == ""
    assert jm._memory_freshness_text(now - 86_400) == ""
    assert jm._memory_freshness_text(now - 29 * 86_400) == ""


def test_memory_freshness_text_warns_for_stale():
    """≥30 days: returns a caveat the LLM can read."""
    import time as _t
    text = jm._memory_freshness_text(_t.time() - 60 * 86_400)
    assert "60 days old" in text
    assert "point-in-time" in text
    assert "verify" in text.lower()


def test_format_memories_includes_age_in_bullets(monkeypatch):
    """Each rendered bullet shows the age next to the category tag.
    Lifted from claude-code's pattern: the LLM applies the drift
    caveat proportionally — fresh trusted, old verified — but only
    if the age info is actually on the screen."""
    import time as _t
    now = _t.time()
    monkeypatch.setattr(
        jm, "_read_memories_via_sdk", lambda **kw: [
            {"memory_id": "m1", "content": "Recent fact.",
             "category": "user", "updated_ts": now - 86_400},
            {"memory_id": "m2", "content": "Old fact.",
             "category": "reference", "updated_ts": now - 47 * 86_400},
        ],
    )
    monkeypatch.setattr(jm, "_bump_uses_via_sdk", lambda ids: None)

    block = jm.format_memories_for_prompt(top_n=10)
    assert "[user · yesterday]" in block, (
        f"yesterday's memory should render with age; got:\n{block}"
    )
    assert "[reference · 47 days ago]" in block, (
        f"47-day-old memory should render with age; got:\n{block}"
    )


def test_format_memories_emits_stale_reminder_when_threshold_crossed(monkeypatch):
    """Block carries a single <system-reminder> line if any memory
    is at/over the staleness threshold."""
    import time as _t
    now = _t.time()
    monkeypatch.setattr(
        jm, "_read_memories_via_sdk", lambda **kw: [
            {"memory_id": "m1", "content": "Old fact.",
             "category": "user", "updated_ts": now - 60 * 86_400},
        ],
    )
    monkeypatch.setattr(jm, "_bump_uses_via_sdk", lambda ids: None)
    block = jm.format_memories_for_prompt(top_n=10)
    assert "<system-reminder>" in block
    assert "30 days old" in block or "≥30" in block
    assert "drift caveat" in block.lower()


def test_format_memories_no_stale_reminder_when_all_fresh(monkeypatch):
    """All memories fresh: NO <system-reminder> noise."""
    import time as _t
    now = _t.time()
    monkeypatch.setattr(
        jm, "_read_memories_via_sdk", lambda **kw: [
            {"memory_id": "m1", "content": "Fresh fact.",
             "category": "user", "updated_ts": now - 5 * 86_400},
            {"memory_id": "m2", "content": "Also fresh.",
             "category": "feedback", "updated_ts": now - 14 * 86_400},
        ],
    )
    monkeypatch.setattr(jm, "_bump_uses_via_sdk", lambda ids: None)
    block = jm.format_memories_for_prompt(top_n=10)
    assert "<system-reminder>" not in block, (
        f"all-fresh block must not carry the stale reminder:\n{block}"
    )


def test_audit_memories_empty_store(monkeypatch):
    """Empty store → tells the user there's nothing yet."""
    monkeypatch.setattr(jm, "_read_memories_via_sdk", lambda **kw: [])
    result = _run(jm.audit_memories._func())
    assert "no memories" in result.lower()


def test_audit_memories_lists_total_and_categories(monkeypatch):
    """Audit reports total + per-category breakdown."""
    import time as _t
    now = _t.time()
    monkeypatch.setattr(
        jm, "_read_memories_via_sdk", lambda **kw: [
            {"memory_id": "a", "content": "Runs Pretva.",
             "category": "user", "updated_ts": now - 5 * 86400},
            {"memory_id": "b", "content": "Prefers terse replies.",
             "category": "feedback", "updated_ts": now - 5 * 86400},
            {"memory_id": "c", "content": "Likes Claude-grade engagement.",
             "category": "feedback", "updated_ts": now - 5 * 86400},
        ],
    )
    result = _run(jm.audit_memories._func())
    assert "Total: 3 memories" in result
    assert "user=1" in result
    assert "feedback=2" in result
    # Fresh store → no stale + no dupes.
    assert "Stale: none" in result
    assert "Near-duplicates: none" in result


def test_audit_memories_flags_stale_entries(monkeypatch):
    """Memories ≥30 days old appear under the Stale section with their age."""
    import time as _t
    now = _t.time()
    monkeypatch.setattr(
        jm, "_read_memories_via_sdk", lambda **kw: [
            {"memory_id": "fresh", "content": "Recent fact.",
             "category": "user", "updated_ts": now - 5 * 86400},
            {"memory_id": "old1", "content": "Two-month-old fact.",
             "category": "reference", "updated_ts": now - 60 * 86400},
            {"memory_id": "old2", "content": "Six-month-old fact.",
             "category": "user", "updated_ts": now - 180 * 86400},
        ],
    )
    result = _run(jm.audit_memories._func())
    assert "Stale (≥30 days" in result
    assert "60d" in result
    assert "180d" in result
    # Stale section sorted oldest first → 180d before 60d.
    assert result.index("180d") < result.index("60d")
    # Fresh memory NOT in stale section.
    assert "Recent fact" not in result.split("Stale")[1].split("Near-duplicates")[0]


def test_audit_memories_flags_near_duplicates(monkeypatch):
    """Two memories that share most non-stopword content tokens get
    flagged as a near-duplicate pair."""
    import time as _t
    now = _t.time()
    monkeypatch.setattr(
        jm, "_read_memories_via_sdk", lambda **kw: [
            {"memory_id": "a",
             "content": "User runs Pretva ride-hailing service in Cameroon.",
             "category": "user", "updated_ts": now - 5 * 86400},
            {"memory_id": "b",
             "content": "Pretva is a ride-hailing service operating in Cameroon.",
             "category": "user", "updated_ts": now - 3 * 86400},
            # Distinct memory: should NOT pair with the above.
            {"memory_id": "c",
             "content": "Prefers terse replies, no trailing summaries.",
             "category": "feedback", "updated_ts": now - 1 * 86400},
        ],
    )
    result = _run(jm.audit_memories._func())
    assert "Near-duplicate pairs" in result
    # The first pair should be flagged.
    assert "Pretva" in result.split("Near-duplicate")[1]
    # The unrelated 'terse replies' memory must NOT show as a duplicate.
    near_dup_section = result.split("Near-duplicate pairs")[1]
    assert "Prefers terse replies" not in near_dup_section


def test_audit_memories_no_false_positives_on_different_topics(monkeypatch):
    """Two memories in the same category but on different topics
    (no shared content words after stopword strip) must NOT pair."""
    import time as _t
    now = _t.time()
    monkeypatch.setattr(
        jm, "_read_memories_via_sdk", lambda **kw: [
            {"memory_id": "a", "content": "Wife's birthday is March 12.",
             "category": "user", "updated_ts": now - 1 * 86400},
            {"memory_id": "b", "content": "Allergic to penicillin.",
             "category": "user", "updated_ts": now - 1 * 86400},
        ],
    )
    result = _run(jm.audit_memories._func())
    assert "Near-duplicates: none" in result


def test_word_overlap_ratio():
    """Direct unit test of the Jaccard helper."""
    # Identical content → 1.0
    assert jm._word_overlap_ratio("hello world", "hello world") == 1.0
    # Disjoint content (after stopword strip) → 0.0
    assert jm._word_overlap_ratio(
        "I run Pretva in Cameroon",
        "She owns a bakery in Paris",
    ) < 0.3  # at most 'in' shared, but 'in' is a stopword
    # Empty input → 0.0
    assert jm._word_overlap_ratio("", "anything") == 0.0
    # Stopwords-only → 0.0 (filtered out)
    assert jm._word_overlap_ratio("the a in", "of and to") == 0.0


def test_format_memories_handles_missing_updated_ts(monkeypatch):
    """Legacy rows without updated_ts shouldn't crash render — the
    bullet just omits the age suffix."""
    monkeypatch.setattr(
        jm, "_read_memories_via_sdk", lambda **kw: [
            {"memory_id": "m1", "content": "Legacy memory.",
             "category": "reference"},  # no updated_ts
        ],
    )
    monkeypatch.setattr(jm, "_bump_uses_via_sdk", lambda ids: None)
    block = jm.format_memories_for_prompt(top_n=10)
    # Bullet renders, no age suffix.
    assert "[reference] Legacy memory." in block
    assert "·" not in block.split("[reference]")[1].split("\n")[0]
