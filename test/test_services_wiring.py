"""Tests for wired services: token estimation, compact prompt, tips, session memory."""

import asyncio
import json
import os
import tempfile

import pytest


# =========================================================================
# Token Estimation (src.services.tokenEstimation)
# =========================================================================


class TestTokenEstimation:
    def test_rough_estimation(self):
        from src.services.tokenEstimation import rough_token_count_estimation

        # 4 bytes per token by default
        assert rough_token_count_estimation("hello world!") == 3  # 12 chars / 4

    def test_file_type_json_uses_denser_ratio(self):
        from src.services.tokenEstimation import (
            rough_token_count_estimation_for_file_type,
            bytes_per_token_for_file_type,
        )

        assert bytes_per_token_for_file_type("json") == 2
        assert bytes_per_token_for_file_type("py") == 4

        content = '{"key": "value"}'  # 16 chars
        assert rough_token_count_estimation_for_file_type(content, "json") == 8
        assert rough_token_count_estimation_for_file_type(content, "py") == 4

    def test_content_blocks(self):
        from src.services.tokenEstimation import rough_token_count_estimation_for_content

        assert rough_token_count_estimation_for_content("hello") == 1  # 5 / 4 rounded
        assert rough_token_count_estimation_for_content(None) == 0
        assert rough_token_count_estimation_for_content([]) == 0

        blocks = [
            {"type": "text", "text": "hello world"},
            {"type": "image"},
        ]
        result = rough_token_count_estimation_for_content(blocks)
        assert result > 2000  # image block alone is 2000

    def test_tool_use_block(self):
        from src.services.tokenEstimation import rough_token_count_estimation_for_content

        blocks = [
            {"type": "tool_use", "name": "bash", "input": {"command": "ls"}},
        ]
        result = rough_token_count_estimation_for_content(blocks)
        assert result > 0


# =========================================================================
# Enhanced estimate_tokens in context.py
# =========================================================================


class TestEnhancedEstimateTokens:
    def test_basic_messages(self):
        from src.agent.context import estimate_tokens

        msgs = [
            {"role": "user", "content": "hello world"},  # 11 chars -> 2 tokens
            {"role": "assistant", "content": "hi there"},  # 8 chars -> 2 tokens
        ]
        result = estimate_tokens(msgs)
        assert result >= 2  # at least some tokens

    def test_tool_calls_use_json_ratio(self):
        from src.agent.context import estimate_tokens

        msgs = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {
                            "name": "bash",
                            "arguments": '{"command": "ls -la /home"}',
                        },
                    }
                ],
            },
        ]
        result = estimate_tokens(msgs)
        # JSON args use 2 bytes/token + 20 overhead
        assert result > 20

    def test_structured_content_blocks(self):
        from src.agent.context import estimate_tokens

        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this image"},
                    {"type": "image"},
                ],
            },
        ]
        result = estimate_tokens(msgs)
        assert result >= 2000  # image block contributes ~2000


# =========================================================================
# Compact Prompt (src.services.compact.prompt)
# =========================================================================


class TestCompactPrompt:
    def test_build_compact_prompt_full(self):
        from src.services.compact.prompt import build_compact_prompt

        prompt = build_compact_prompt("full")
        assert "CRITICAL" in prompt  # NO_TOOLS_PREAMBLE
        assert "<summary>" in prompt
        assert "User goals" in prompt

    def test_build_compact_prompt_partial(self):
        from src.services.compact.prompt import build_compact_prompt

        prompt = build_compact_prompt("partial")
        assert "recent messages" in prompt

    def test_format_compact_summary(self):
        from src.services.compact.compact import format_compact_summary

        raw = "<analysis>thinking...</analysis><summary>The user asked to fix a bug in main.py</summary>"
        result = format_compact_summary(raw)
        assert "fix a bug" in result
        assert "analysis" not in result.lower()

    def test_format_compact_summary_no_tags(self):
        from src.services.compact.compact import format_compact_summary

        raw = "Just a plain summary without tags"
        result = format_compact_summary(raw)
        assert result == raw.strip()


# =========================================================================
# Tips System (src.services.tips)
# =========================================================================


class TestTips:
    def test_tip_registry(self):
        from src.services.tips.tipRegistry import _BUILTIN_TIPS

        assert len(_BUILTIN_TIPS) > 0
        for tip in _BUILTIN_TIPS:
            assert tip.id
            assert callable(tip.content)
            assert tip.cooldown_sessions > 0

    @pytest.mark.asyncio
    async def test_get_relevant_tips(self):
        from src.services.tips.tipRegistry import get_relevant_tips

        tips = await get_relevant_tips()
        # All tips should be relevant on first run (no history)
        assert len(tips) > 0

    @pytest.mark.asyncio
    async def test_tip_scheduler(self):
        from src.services.tips.tipScheduler import get_tip_to_show_on_spinner

        tip = await get_tip_to_show_on_spinner()
        # Should return a tip since none have been shown
        assert tip is not None
        assert tip.id

    def test_tip_content_callable(self):
        from src.services.tips.tipRegistry import _BUILTIN_TIPS

        # All tip content callables should return strings
        for tip in _BUILTIN_TIPS:
            result = tip.content()
            assert isinstance(result, str)
            assert len(result) > 0


# =========================================================================
# Session Memory Utils (src.services.SessionMemory)
# =========================================================================


class TestSessionMemoryUtils:
    def test_config_defaults(self):
        from src.services.SessionMemory.sessionMemoryUtils import (
            SessionMemoryConfig,
            get_session_memory_config,
        )

        config = get_session_memory_config()
        assert config.minimum_message_tokens_to_init == 10000
        assert config.tool_calls_between_updates == 3

    def test_threshold_checks(self):
        from src.services.SessionMemory.sessionMemoryUtils import (
            has_met_initialization_threshold,
            has_met_update_threshold,
            reset_session_memory_state,
        )

        reset_session_memory_state()
        assert not has_met_initialization_threshold(5000)
        assert has_met_initialization_threshold(15000)
        assert has_met_update_threshold(10000)

    def test_extraction_state(self):
        from src.services.SessionMemory.sessionMemoryUtils import (
            mark_extraction_started,
            mark_extraction_completed,
            reset_session_memory_state,
        )

        reset_session_memory_state()
        mark_extraction_started()
        mark_extraction_completed()
        # Should not raise

    def test_session_memory_manager_init(self):
        from src.services.SessionMemory.sessionMemory import init_session_memory

        mgr = init_session_memory()
        assert mgr is not None
        assert mgr._tool_calls_since_last_update == 0

    @pytest.mark.asyncio
    async def test_get_session_memory_content(self):
        from src.services.SessionMemory.sessionMemoryUtils import get_session_memory_content

        # Should return None or string, not raise
        content = await get_session_memory_content()
        assert content is None or isinstance(content, str)


# =========================================================================
# Integration: context.py uses service imports
# =========================================================================


class TestContextServiceIntegration:
    def test_context_imports_token_estimation(self):
        """Verify that context.py can import from tokenEstimation service."""
        from src.agent.context import estimate_tokens
        # Should not raise
        assert callable(estimate_tokens)

    def test_compaction_prompt_used(self):
        """Verify that build_compaction_prompt uses the service prompt."""
        from src.agent.context import build_compaction_prompt, MessageGroup

        groups = [
            MessageGroup(
                messages=[{"role": "user", "content": "Fix the bug in main.py"}],
                group_type="user_turn",
            ),
            MessageGroup(
                messages=[{"role": "assistant", "content": "Done"}],
                group_type="user_turn",
                is_recent=True,
            ),
        ]
        prompt = build_compaction_prompt(groups, preserve_recent=1)
        # Should contain the service's prompt template markers
        assert "CRITICAL" in prompt or "summary" in prompt.lower()
