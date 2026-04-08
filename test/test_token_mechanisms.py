"""Tests for all 10 token management mechanisms in JARVIS.

Covers:
  1. repair_tool_pairs         (src/agent/context.py)
  2. check_context_window      (src/agent/context.py)
  3. SAFETY_MARGIN and constants (src/agent/context.py)
  4. AutoCompactor adaptive chunk ratio (src/agent/context.py)
  5. AutoCompactor.should_compact adaptive trigger (src/agent/context.py)
  6. Cache boundary split / _normalize_usage (src/reasoning/providers.py)
  7. Cache hit tracking accumulation (src/reasoning/providers.py)
  8. Prompt cache boundary marker in AGENT_SYSTEM_PROMPT (src/brain.py)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent.context import (
    AutoCompactor,
    BASE_CHUNK_RATIO,
    HARD_MIN_CONTEXT,
    MIN_CHUNK_RATIO,
    MODEL_LIMITS,
    SAFETY_MARGIN,
    WARN_BELOW_CONTEXT,
    check_context_window,
    repair_tool_pairs,
)
from src.reasoning.providers import (
    _normalize_usage,
    _update_cache_stats,
    get_cache_stats,
    reset_cache_stats,
)


# ===========================================================================
# 1. repair_tool_pairs
# ===========================================================================

class TestRepairToolPairs(unittest.TestCase):
    """repair_tool_pairs — orphaned tool results are dropped, matched ones kept."""

    # --- OpenAI-format (role == "tool") ---

    def test_empty_tool_call_id_is_dropped(self):
        """A tool message with an empty tool_call_id has no match → must be dropped."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "tool", "tool_call_id": "", "content": "some result"},
        ]
        result = repair_tool_pairs(messages)
        roles = [m["role"] for m in result]
        self.assertNotIn("tool", roles)

    def test_matching_tool_call_id_is_kept(self):
        """A tool message whose id matches an assistant tool_call is kept."""
        messages = [
            {"role": "user", "content": "do something"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_abc", "function": {"name": "bash", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "call_abc", "content": "result"},
        ]
        result = repair_tool_pairs(messages)
        tool_msgs = [m for m in result if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertEqual(tool_msgs[0]["tool_call_id"], "call_abc")

    def test_non_matching_tool_call_id_is_dropped(self):
        """A tool message whose id does not match any assistant tool_call is dropped."""
        messages = [
            {"role": "user", "content": "do something"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_real", "function": {"name": "bash", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "call_real", "content": "real result"},
            # Orphan — no assistant ever produced call_ghost
            {"role": "tool", "tool_call_id": "call_ghost", "content": "ghost result"},
        ]
        result = repair_tool_pairs(messages)
        tool_ids = [m["tool_call_id"] for m in result if m["role"] == "tool"]
        self.assertIn("call_real", tool_ids)
        self.assertNotIn("call_ghost", tool_ids)

    def test_openai_orphan_removed_matched_kept(self):
        """Only the matched OpenAI-format tool result survives; orphan is removed."""
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "id1", "function": {"name": "read_file", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "id1", "content": "file content"},
            {"role": "tool", "tool_call_id": "id_orphan", "content": "orphan"},
        ]
        result = repair_tool_pairs(messages)
        tool_msgs = [m for m in result if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertEqual(tool_msgs[0]["tool_call_id"], "id1")

    # --- Anthropic-format (user message with content list containing tool_result blocks) ---

    def test_anthropic_orphan_tool_result_removed(self):
        """An Anthropic user message whose tool_result block has no matching tool_use is dropped."""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "no_such_tool_use",
                        "content": "orphan result",
                    }
                ],
            }
        ]
        result = repair_tool_pairs(messages)
        # The user message has empty filtered content and should not be added
        self.assertEqual(result, [])

    def test_anthropic_matched_tool_result_kept(self):
        """An Anthropic tool_result block whose tool_use_id matches an assistant tool_use is kept."""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I'll search for that."},
                    {"type": "tool_use", "id": "tu_001", "name": "search", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tu_001", "content": "search hits"},
                ],
            },
        ]
        result = repair_tool_pairs(messages)
        user_msgs = [m for m in result if m["role"] == "user"]
        self.assertEqual(len(user_msgs), 1)
        content = user_msgs[0]["content"]
        self.assertIsInstance(content, list)
        self.assertTrue(any(
            b.get("type") == "tool_result" and b.get("tool_use_id") == "tu_001"
            for b in content
        ))

    def test_regular_user_messages_not_dropped(self):
        """Plain user messages (no tool_call_id field) must never be removed."""
        messages = [
            {"role": "user", "content": "Hello there"},
            {"role": "assistant", "content": "Hi!"},
            {"role": "user", "content": "What can you do?"},
        ]
        result = repair_tool_pairs(messages)
        self.assertEqual(len(result), 3)
        user_msgs = [m for m in result if m["role"] == "user"]
        self.assertEqual(len(user_msgs), 2)

    def test_empty_content_user_message_not_added(self):
        """A user message whose every content block is an orphaned tool_result is not added at all."""
        messages = [
            {"role": "user", "content": "before"},
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "ghost_id", "content": "orphan"},
                ],
            },
            {"role": "user", "content": "after"},
        ]
        result = repair_tool_pairs(messages)
        # Only the two plain text user messages should remain
        user_msgs = [m for m in result if m["role"] == "user"]
        self.assertEqual(len(user_msgs), 2)
        contents = [m["content"] for m in user_msgs]
        self.assertIn("before", contents)
        self.assertIn("after", contents)


# ===========================================================================
# 2. check_context_window
# ===========================================================================

class TestCheckContextWindow(unittest.TestCase):
    """check_context_window — returns (bool, str) based on model context size."""

    def test_known_model_above_warn_level_returns_ok_no_message(self):
        """gpt-4o has 120K tokens — well above WARN_BELOW_CONTEXT (32K) → (True, "")."""
        ok, msg = check_context_window("gpt-4o")
        self.assertTrue(ok)
        self.assertEqual(msg, "")

    def test_model_between_hard_min_and_warn_level_returns_ok_with_warning(self):
        """A model with 20K context is between HARD_MIN (16K) and WARN_BELOW (32K).

        We patch MODEL_LIMITS locally so we don't permanently mutate it.
        """
        import src.agent.context as ctx_mod
        original = dict(ctx_mod.MODEL_LIMITS)
        ctx_mod.MODEL_LIMITS["_test_tiny_model_"] = 20_000
        try:
            ok, msg = check_context_window("_test_tiny_model_")
        finally:
            ctx_mod.MODEL_LIMITS.clear()
            ctx_mod.MODEL_LIMITS.update(original)
        self.assertTrue(ok)
        self.assertNotEqual(msg, "")
        self.assertIn("Warning", msg)

    def test_model_below_hard_min_returns_false_with_message(self):
        """A model with 8K context is below HARD_MIN (16K) → (False, non-empty)."""
        import src.agent.context as ctx_mod
        original = dict(ctx_mod.MODEL_LIMITS)
        ctx_mod.MODEL_LIMITS["_test_micro_model_"] = 8_000
        try:
            ok, msg = check_context_window("_test_micro_model_")
        finally:
            ctx_mod.MODEL_LIMITS.clear()
            ctx_mod.MODEL_LIMITS.update(original)
        self.assertFalse(ok)
        self.assertNotEqual(msg, "")

    def test_unknown_model_uses_default_and_passes(self):
        """An unrecognised model name falls back to DEFAULT_MAX_TOKENS (180K) → (True, "")."""
        ok, msg = check_context_window("totally-unknown-model-xyz-123")
        self.assertTrue(ok)
        self.assertEqual(msg, "")


# ===========================================================================
# 3. Constants
# ===========================================================================

class TestConstants(unittest.TestCase):
    """Verify the token-budget constants have the expected values."""

    def test_safety_margin_is_1_2(self):
        self.assertEqual(SAFETY_MARGIN, 1.2)

    def test_hard_min_context_is_16000(self):
        self.assertEqual(HARD_MIN_CONTEXT, 16_000)

    def test_warn_below_context_is_32000(self):
        self.assertEqual(WARN_BELOW_CONTEXT, 32_000)

    def test_base_chunk_ratio_is_0_40(self):
        self.assertEqual(BASE_CHUNK_RATIO, 0.40)

    def test_min_chunk_ratio_is_0_15(self):
        self.assertEqual(MIN_CHUNK_RATIO, 0.15)

    def test_ordering_min_lt_base_lt_safety(self):
        self.assertLess(MIN_CHUNK_RATIO, BASE_CHUNK_RATIO)
        self.assertLess(BASE_CHUNK_RATIO, SAFETY_MARGIN)


# ===========================================================================
# 4. AutoCompactor adaptive chunk ratio
# ===========================================================================

class TestAutoCompactorAdaptiveChunkRatio(unittest.TestCase):
    """AutoCompactor._update_chunk_ratio shrinks ratio when messages are large."""

    def _make_compactor(self, model="gpt-4o"):
        return AutoCompactor(model=model)

    def test_initial_chunk_ratio_is_base(self):
        ac = self._make_compactor()
        self.assertEqual(ac._chunk_ratio, BASE_CHUNK_RATIO)

    def test_large_avg_message_shrinks_ratio(self):
        """When avg message size > 10% of context window the ratio must decrease.

        gpt-4o max = 120K. 10% = 12K per message.
        With 5 messages averaging 15K tokens each we need used_tokens = 75K.
        """
        ac = self._make_compactor("gpt-4o")  # 120K tokens
        # Each message is ~60K chars = ~15K tokens — well above 10% of 120K
        big_content = "x" * 60_000  # 60000 chars / 4 = 15000 tokens each
        messages = [{"role": "user", "content": big_content} for _ in range(5)]
        # used_tokens total: 5 × 15K = 75K → avg = 15K > 12K threshold
        ac.budget.used_tokens = 75_000
        original_ratio = ac._chunk_ratio
        ac._update_chunk_ratio(messages)
        self.assertLess(ac._chunk_ratio, original_ratio)

    def test_ratio_never_goes_below_min_chunk_ratio(self):
        """After many calls with oversized messages the ratio is floored at MIN_CHUNK_RATIO."""
        ac = self._make_compactor("gpt-4o")  # 120K tokens
        big_content = "x" * 20_000
        messages = [{"role": "user", "content": big_content} for _ in range(5)]
        # Force many shrink steps
        for _ in range(50):
            # Simulate used_tokens being high (avg > 10%)
            ac.budget.used_tokens = 30_000
            ac._update_chunk_ratio(messages)
        self.assertGreaterEqual(ac._chunk_ratio, MIN_CHUNK_RATIO)

    def test_proactive_threshold_updates_with_chunk_ratio(self):
        """proactive_threshold must change when chunk_ratio changes."""
        ac = self._make_compactor("gpt-4o")
        big_content = "x" * 20_000
        messages = [{"role": "user", "content": big_content} for _ in range(5)]
        ac.budget.used_tokens = 30_000
        old_threshold = ac._proactive_threshold
        ac._update_chunk_ratio(messages)
        # If ratio changed, threshold must change too
        if ac._chunk_ratio != BASE_CHUNK_RATIO:
            self.assertNotEqual(ac._proactive_threshold, old_threshold)
            self.assertAlmostEqual(ac._proactive_threshold, ac._chunk_ratio * SAFETY_MARGIN)


# ===========================================================================
# 5. AutoCompactor.should_compact adaptive trigger
# ===========================================================================

class TestAutoCompactorShouldCompact(unittest.TestCase):
    """should_compact fires at the right threshold and stays quiet when usage is low."""

    def test_should_compact_false_when_tokens_far_below_threshold(self):
        """Very few tokens → should_compact must return False."""
        ac = AutoCompactor(model="gpt-4o")  # max = 120K
        # A tiny conversation
        messages = [{"role": "user", "content": "hi"}]
        self.assertFalse(ac.should_compact(messages))

    def test_should_compact_true_when_tokens_exceed_threshold(self):
        """Tokens above chunk_ratio × max_tokens × SAFETY_MARGIN → should_compact = True.

        gpt-4o: max = 120K
        threshold = BASE_CHUNK_RATIO × 120K × SAFETY_MARGIN
                  = 0.40 × 120000 × 1.2
                  = 57600 tokens

        We construct a message list that is clearly above that.
        """
        model = "gpt-4o"
        max_tokens = MODEL_LIMITS[model]  # 120000
        threshold = BASE_CHUNK_RATIO * max_tokens * SAFETY_MARGIN  # 57600

        # Build messages totalling well above the threshold
        # Each message: content of 4*(threshold+10000) chars ≈ threshold+10000 tokens
        chars_needed = int((threshold + 10_000) * 4)
        messages = [{"role": "user", "content": "x" * chars_needed}]

        ac = AutoCompactor(model=model)
        self.assertTrue(ac.should_compact(messages))

    def test_should_compact_exact_threshold(self):
        """Verify the exact threshold calculation for gpt-4o.

        gpt-4o max = 120K tokens.
        Threshold at BASE_CHUNK_RATIO = 0.40 × 120K × 1.2 = 57600 tokens.

        Important: should_compact calls _update_chunk_ratio, which shrinks the ratio
        when avg_tokens > 10% of max (12K). To keep the ratio stable at BASE_CHUNK_RATIO
        we must keep per-message avg below 12K, i.e., use many small messages.

        We spread the total tokens across 20 messages (≈ 2880 tokens each on average),
        which is below the 12K per-message shrink trigger.
        """
        model = "gpt-4o"
        max_tokens = MODEL_LIMITS[model]  # 120000
        expected_threshold = BASE_CHUNK_RATIO * max_tokens * SAFETY_MARGIN  # 57600
        self.assertAlmostEqual(expected_threshold, 57_600.0, places=0)

        num_messages = 20
        # Chars per message such that total tokens ≈ expected_threshold - 1000
        # total_chars = (threshold - 1000) * 4 / num_messages per message
        chars_per_msg_under = int((expected_threshold - 2000) * 4 // num_messages)
        messages_under = [{"role": "user", "content": "x" * chars_per_msg_under}
                          for _ in range(num_messages)]
        ac_under = AutoCompactor(model=model)
        self.assertFalse(ac_under.should_compact(messages_under))

        # Just over threshold — same structure, slightly more chars
        chars_per_msg_over = int((expected_threshold + 4000) * 4 // num_messages)
        messages_over = [{"role": "user", "content": "x" * chars_per_msg_over}
                         for _ in range(num_messages)]
        ac_over = AutoCompactor(model=model)
        self.assertTrue(ac_over.should_compact(messages_over))


# ===========================================================================
# 6. Cache boundary split — _normalize_usage
# ===========================================================================

class TestNormalizeUsage(unittest.TestCase):
    """_normalize_usage maps provider-specific usage dicts to a standard schema."""

    def test_anthropic_style_dict(self):
        """Anthropic uses 'input', 'output', 'cache_read', 'cache_write' keys directly."""
        raw = {"input": 100, "output": 50, "cache_read": 30, "cache_write": 10}
        out = _normalize_usage(raw)
        self.assertEqual(out["input"], 100)
        self.assertEqual(out["output"], 50)
        self.assertEqual(out["cache_read"], 30)
        self.assertEqual(out["cache_write"], 10)

    def test_openai_style_dict(self):
        """OpenAI uses 'prompt_tokens' and 'completion_tokens'."""
        raw = {"prompt_tokens": 100, "completion_tokens": 50}
        out = _normalize_usage(raw)
        self.assertEqual(out["input"], 100)
        self.assertEqual(out["output"], 50)
        self.assertEqual(out["cache_read"], 0)
        self.assertEqual(out["cache_write"], 0)

    def test_kimi_openai_cached_style(self):
        """Kimi/OpenAI with nested prompt_tokens_details.cached_tokens → cache_read."""
        raw = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 25},
        }
        out = _normalize_usage(raw)
        self.assertEqual(out["input"], 100)
        self.assertEqual(out["output"], 50)
        self.assertEqual(out["cache_read"], 25)

    def test_empty_dict_returns_empty(self):
        """An empty dict is falsy so the fast-path returns {} (not a zeros dict).

        The implementation has: if not raw: return {}
        An empty dict evaluates as falsy in Python, so _normalize_usage({}) == {}.
        """
        out = _normalize_usage({})
        self.assertEqual(out, {})

    def test_none_returns_empty_dict(self):
        """Passing None returns {} (falsy raw → fast path)."""
        out = _normalize_usage(None)
        self.assertEqual(out, {})

    def test_none_values_treated_as_zero(self):
        """Explicit None values in the dict must not cause errors and map to 0."""
        raw = {"input": None, "output": None, "cache_read": None, "cache_write": None}
        out = _normalize_usage(raw)
        self.assertEqual(out["input"], 0)
        self.assertEqual(out["output"], 0)
        self.assertEqual(out["cache_read"], 0)
        self.assertEqual(out["cache_write"], 0)


# ===========================================================================
# 7. Cache hit tracking accumulation
# ===========================================================================

class TestCacheHitTracking(unittest.TestCase):
    """_update_cache_stats accumulates; get/reset work correctly."""

    def setUp(self):
        reset_cache_stats()

    def test_accumulates_correctly_across_calls(self):
        """Multiple _update_cache_stats calls add up."""
        _update_cache_stats({"cache_read": 10, "cache_write": 5, "input": 100, "output": 50})
        _update_cache_stats({"cache_read": 20, "cache_write": 3, "input": 200, "output": 80})
        stats = get_cache_stats()
        self.assertEqual(stats["read"], 30)
        self.assertEqual(stats["write"], 8)
        self.assertEqual(stats["input"], 300)
        self.assertEqual(stats["output"], 130)

    def test_get_cache_stats_returns_snapshot(self):
        """Modifying the returned dict must not affect internal state."""
        _update_cache_stats({"cache_read": 5, "cache_write": 2, "input": 10, "output": 4})
        snapshot = get_cache_stats()
        snapshot["read"] = 9999  # mutate snapshot
        # Internal state must be unchanged
        stats2 = get_cache_stats()
        self.assertEqual(stats2["read"], 5)

    def test_reset_cache_stats_zeros_everything(self):
        """After reset, all stats are zero."""
        _update_cache_stats({"cache_read": 50, "cache_write": 20, "input": 500, "output": 200})
        reset_cache_stats()
        stats = get_cache_stats()
        self.assertEqual(stats["read"], 0)
        self.assertEqual(stats["write"], 0)
        self.assertEqual(stats["input"], 0)
        self.assertEqual(stats["output"], 0)


# ===========================================================================
# 8. Prompt cache boundary marker in AGENT_SYSTEM_PROMPT
# ===========================================================================

class TestAgentSystemPromptCacheBoundary(unittest.TestCase):
    """AGENT_SYSTEM_PROMPT must contain <!-- JARVIS_CACHE_BOUNDARY --> in the right location."""

    @classmethod
    def setUpClass(cls):
        from src.brain import AGENT_SYSTEM_PROMPT
        cls.prompt = AGENT_SYSTEM_PROMPT

    def test_prompt_contains_cache_boundary_marker(self):
        """The prompt string must contain the cache boundary comment."""
        self.assertIn("<!-- JARVIS_CACHE_BOUNDARY -->", self.prompt)

    def test_marker_appears_after_source_cwd_hardware_line(self):
        """The marker must come after the 'Source: ... CWD: ... HW:' line."""
        marker_idx = self.prompt.index("<!-- JARVIS_CACHE_BOUNDARY -->")
        # The source/CWD/HW line uses format placeholders
        source_phrase = "| Kali Linux | CWD:"
        source_idx = self.prompt.index(source_phrase)
        self.assertGreater(marker_idx, source_idx)

    def test_marker_appears_before_first_section_header(self):
        """The marker must come before the first '═══' section header line."""
        marker_idx = self.prompt.index("<!-- JARVIS_CACHE_BOUNDARY -->")
        # Find the first section header
        section_idx = self.prompt.index("═══")
        self.assertLess(marker_idx, section_idx)


if __name__ == "__main__":
    unittest.main()
