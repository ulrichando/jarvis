"""Tests for ported modules: memdir, migrations, output_styles, voice, conversation_history, cost_tracker enhancements."""

import json
import os
import struct
import tempfile

import pytest


# =========================================================================
# MemoryDirectory
# =========================================================================


class TestMemoryDirectory:
    def test_store_and_recall(self, tmp_path):
        from src.memory.memdir import MemoryDirectory

        md = MemoryDirectory(str(tmp_path))
        md.store("test_key", "Hello world", category="general", description="a test")
        body = md.recall("test_key")
        assert body == "Hello world"

    def test_recall_nonexistent(self, tmp_path):
        from src.memory.memdir import MemoryDirectory

        md = MemoryDirectory(str(tmp_path))
        assert md.recall("no_such_key") is None

    def test_store_preserves_created_at(self, tmp_path):
        from src.memory.memdir import MemoryDirectory, _parse_frontmatter

        md = MemoryDirectory(str(tmp_path))
        path = md.store("mykey", "v1", category="general")
        raw1 = open(path).read()
        meta1, _ = _parse_frontmatter(raw1)
        created1 = meta1["created_at"]

        # Update the same key
        md.store("mykey", "v2", category="general")
        raw2 = open(path).read()
        meta2, body2 = _parse_frontmatter(raw2)
        assert meta2["created_at"] == created1  # preserved
        assert body2 == "v2"

    def test_search(self, tmp_path):
        from src.memory.memdir import MemoryDirectory

        md = MemoryDirectory(str(tmp_path))
        md.store("alpha", "Python programming language", category="ref")
        md.store("beta", "Rust systems language", category="ref")
        results = md.search("python")
        assert len(results) >= 1
        assert results[0]["name"] == "alpha"

    def test_search_empty_query(self, tmp_path):
        from src.memory.memdir import MemoryDirectory

        md = MemoryDirectory(str(tmp_path))
        md.store("x", "data", category="general")
        assert md.search("") == []

    def test_list_all(self, tmp_path):
        from src.memory.memdir import MemoryDirectory

        md = MemoryDirectory(str(tmp_path))
        md.store("a", "alpha", category="cat1")
        md.store("b", "beta", category="cat2")
        entries = md.list()
        assert len(entries) == 2

    def test_list_by_category(self, tmp_path):
        from src.memory.memdir import MemoryDirectory

        md = MemoryDirectory(str(tmp_path))
        md.store("a", "alpha", category="cat1")
        md.store("b", "beta", category="cat2")
        entries = md.list(category="cat1")
        assert len(entries) == 1
        assert entries[0]["name"] == "a"

    def test_delete(self, tmp_path):
        from src.memory.memdir import MemoryDirectory

        md = MemoryDirectory(str(tmp_path))
        md.store("to_delete", "gone", category="general")
        assert md.recall("to_delete") is not None
        assert md.delete("to_delete") is True
        assert md.recall("to_delete") is None

    def test_delete_nonexistent(self, tmp_path):
        from src.memory.memdir import MemoryDirectory

        md = MemoryDirectory(str(tmp_path))
        assert md.delete("nope") is False

    def test_get_stats(self, tmp_path):
        from src.memory.memdir import MemoryDirectory

        md = MemoryDirectory(str(tmp_path))
        md.store("x", "data", category="cat1")
        md.store("y", "more data", category="cat1")
        md.store("z", "other", category="cat2")
        stats = md.get_stats()
        assert stats["entry_count"] == 3
        assert stats["categories"]["cat1"] == 2
        assert stats["categories"]["cat2"] == 1
        assert stats["total_size"] > 0

    def test_sanitize_key_rejects_traversal(self):
        from src.memory.memdir import _sanitize_key

        with pytest.raises(ValueError, match="traversal"):
            _sanitize_key("../../etc/passwd")

    def test_sanitize_key_rejects_null(self):
        from src.memory.memdir import _sanitize_key

        with pytest.raises(ValueError, match="Null"):
            _sanitize_key("bad\x00key")

    def test_index_updated(self, tmp_path):
        from src.memory.memdir import MemoryDirectory, ENTRYPOINT_NAME

        md = MemoryDirectory(str(tmp_path))
        md.store("indexed", "content", category="general", description="test entry")
        index_path = tmp_path / ENTRYPOINT_NAME
        assert index_path.exists()
        text = index_path.read_text()
        assert "indexed" in text

    def test_load_entrypoint_empty(self, tmp_path):
        from src.memory.memdir import MemoryDirectory

        md = MemoryDirectory(str(tmp_path))
        content = md.load_entrypoint()
        assert "empty" in content.lower()

    def test_tags(self, tmp_path):
        from src.memory.memdir import MemoryDirectory

        md = MemoryDirectory(str(tmp_path))
        md.store("tagged", "content", category="general", tags=["python", "ai"])
        entries = md.list()
        assert "python" in entries[0]["tags"]
        assert "ai" in entries[0]["tags"]


# =========================================================================
# MigrationManager
# =========================================================================


class TestMigrations:
    def test_register_and_pending(self, tmp_path):
        from src.migrations_brain import MigrationManager

        mgr = MigrationManager(str(tmp_path))
        mgr.register("1.0", "first", lambda: None)
        mgr.register("1.1", "second", lambda: None)
        assert len(mgr.pending()) == 2

    def test_apply_all(self, tmp_path):
        from src.migrations_brain import MigrationManager

        results = []
        mgr = MigrationManager(str(tmp_path))
        mgr.register("1.0", "first", lambda: results.append("1.0"))
        mgr.register("1.1", "second", lambda: results.append("1.1"))
        applied = mgr.apply_all()
        assert applied == ["1.0", "1.1"]
        assert results == ["1.0", "1.1"]
        assert mgr.current_version() == "1.1"
        assert len(mgr.pending()) == 0

    def test_idempotent(self, tmp_path):
        from src.migrations_brain import MigrationManager

        counter = [0]
        mgr = MigrationManager(str(tmp_path))
        mgr.register("1.0", "first", lambda: counter.__setitem__(0, counter[0] + 1))
        mgr.apply_all()
        mgr.apply_all()
        assert counter[0] == 1

    def test_persistence(self, tmp_path):
        from src.migrations_brain import MigrationManager

        mgr1 = MigrationManager(str(tmp_path))
        mgr1.register("1.0", "first", lambda: None)
        mgr1.apply_all()

        mgr2 = MigrationManager(str(tmp_path))
        mgr2.register("1.0", "first", lambda: None)
        mgr2.register("1.1", "second", lambda: None)
        assert mgr2.current_version() == "1.0"
        assert len(mgr2.pending()) == 1

    def test_rollback(self, tmp_path):
        from src.migrations_brain import MigrationManager

        state = {"val": 0}
        mgr = MigrationManager(str(tmp_path))
        mgr.register(
            "1.0", "first",
            lambda: state.update(val=1),
            lambda: state.update(val=0),
        )
        mgr.register(
            "1.1", "second",
            lambda: state.update(val=2),
            lambda: state.update(val=1),
        )
        mgr.apply_all()
        assert state["val"] == 2
        mgr.rollback("1.0")
        assert state["val"] == 1
        assert mgr.current_version() == "1.0"

    def test_duplicate_version_rejected(self, tmp_path):
        from src.migrations_brain import MigrationManager

        mgr = MigrationManager(str(tmp_path))
        mgr.register("1.0", "first", lambda: None)
        with pytest.raises(ValueError, match="already registered"):
            mgr.register("1.0", "duplicate", lambda: None)

    def test_default_manager_has_builtins(self, tmp_path):
        from src.migrations_brain import get_migration_manager

        mgr = get_migration_manager(str(tmp_path))
        versions = [m.version for m in mgr.pending()]
        assert "1.0" in versions
        assert "2.0" in versions

    def test_current_version_default(self, tmp_path):
        from src.migrations_brain import MigrationManager

        mgr = MigrationManager(str(tmp_path))
        assert mgr.current_version() == "0.0"


# =========================================================================
# OutputStyles
# =========================================================================


class TestOutputStyles:
    def test_builtin_styles_exist(self):
        from src.output_styles_brain import BUILTIN_STYLES

        assert "default" in BUILTIN_STYLES
        assert "minimal" in BUILTIN_STYLES
        assert "developer" in BUILTIN_STYLES
        assert "concise" in BUILTIN_STYLES

    def test_default_style_properties(self):
        from src.output_styles_brain import get_style

        s = get_style("default")
        assert s.markdown_enabled is True
        assert s.tool_display == "expanded"
        assert s.thinking_visible is False

    def test_developer_style_has_thinking(self):
        from src.output_styles_brain import get_style

        s = get_style("developer")
        assert s.thinking_visible is True
        assert s.timestamps is True

    def test_apply_style_strips_markdown(self):
        from src.output_styles_brain import apply_style, get_style

        text = "# Title\n**bold** and *italic*"
        result = apply_style(text, get_style("minimal"))
        assert "**" not in result
        assert "#" not in result

    def test_apply_style_truncates(self):
        from src.output_styles_brain import apply_style, get_style

        long_text = "word " * 1000
        result = apply_style(long_text, get_style("concise"))
        assert len(result) < len(long_text)
        assert "truncated" in result

    def test_get_style_fallback(self):
        from src.output_styles_brain import get_style

        s = get_style("nonexistent_style_xyz")
        assert s.name == "default"

    def test_list_styles(self):
        from src.output_styles_brain import list_styles

        names = list_styles()
        assert "default" in names
        assert "developer" in names

    def test_invalid_tool_display(self):
        from src.output_styles_brain import OutputStyle

        with pytest.raises(ValueError, match="tool_display"):
            OutputStyle(name="bad", tool_display="invalid")

    def test_format_tool_call(self):
        from src.output_styles_brain import format_tool_call, get_style

        # "minimal" style uses compact tool_display, so args are shown
        compact_minimal = format_tool_call("read_file", {"path": "/foo"}, get_style("minimal"))
        assert "read_file" in compact_minimal
        assert "path=" in compact_minimal

        # Test with a raw minimal tool_display style
        from src.output_styles_brain import OutputStyle
        raw_minimal = OutputStyle(name="raw", tool_display="minimal")
        minimal_result = format_tool_call("read_file", {"path": "/foo"}, raw_minimal)
        assert minimal_result == "[read_file]"

        compact = format_tool_call("read_file", {"path": "/foo"}, get_style("concise"))
        assert "read_file" in compact
        assert "path=" in compact

        expanded = format_tool_call("read_file", {"path": "/foo"}, get_style("default"))
        assert "Tool: read_file" in expanded

    def test_apply_empty_string(self):
        from src.output_styles_brain import apply_style, get_style

        assert apply_style("", get_style("default")) == ""


# =========================================================================
# Voice
# =========================================================================


class TestVoice:
    def test_voice_config_defaults(self):
        from src.voice_brain import VoiceConfig

        cfg = VoiceConfig()
        assert cfg.sample_rate == 16000
        assert cfg.language == "en"
        assert cfg.tts_voice == "en-US-GuyNeural"

    def test_vad_silence(self):
        from src.voice_brain import detect_speech_activity

        silence = b"\x00\x00" * 1000
        assert detect_speech_activity(silence) is False

    def test_vad_loud(self):
        from src.voice_brain import detect_speech_activity

        samples = struct.pack("<" + "h" * 1000, *([16384] * 1000))
        assert detect_speech_activity(samples, threshold=0.01) is True

    def test_vad_empty(self):
        from src.voice_brain import detect_speech_activity

        assert detect_speech_activity(b"") is False

    def test_vad_threshold(self):
        from src.voice_brain import detect_speech_activity

        samples = struct.pack("<" + "h" * 1000, *([100] * 1000))
        assert detect_speech_activity(samples, threshold=0.001) is True
        assert detect_speech_activity(samples, threshold=0.1) is False

    def test_voice_input_availability_check(self):
        from src.voice_brain import VoiceInput

        vi = VoiceInput()
        result = vi.is_available()
        assert isinstance(result, bool)


# =========================================================================
# ConversationHistory
# =========================================================================


class TestConversationHistory:
    def test_add_and_get_recent(self, tmp_path):
        from src.conversation_history import ConversationHistory

        hist = ConversationHistory(str(tmp_path))
        hist.add_turn("user", "Hello")
        hist.add_turn("assistant", "Hi there!")
        recent = hist.get_recent(10)
        assert len(recent) == 2

    def test_search(self, tmp_path):
        from src.conversation_history import ConversationHistory

        hist = ConversationHistory(str(tmp_path))
        hist.add_turn("user", "Tell me about Python")
        hist.add_turn("assistant", "Python is great")
        hist.add_turn("user", "What about Rust?")
        results = hist.search("Python")
        assert len(results) == 2

    def test_search_case_insensitive(self, tmp_path):
        from src.conversation_history import ConversationHistory

        hist = ConversationHistory(str(tmp_path))
        hist.add_turn("user", "JARVIS help")
        results = hist.search("jarvis")
        assert len(results) == 1

    def test_get_session(self, tmp_path):
        from src.conversation_history import ConversationHistory

        hist = ConversationHistory(str(tmp_path))
        hist.add_turn("user", "msg1")
        hist.add_turn("assistant", "msg2")
        session = hist.get_session(hist.session_id)
        assert len(session) == 2
        assert session[0]["turn_index"] < session[1]["turn_index"]

    def test_list_sessions(self, tmp_path):
        from src.conversation_history import ConversationHistory

        hist = ConversationHistory(str(tmp_path))
        hist.add_turn("user", "Hello")
        sessions = hist.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["turn_count"] == 1

    def test_export_json(self, tmp_path):
        from src.conversation_history import ConversationHistory

        hist = ConversationHistory(str(tmp_path))
        hist.add_turn("user", "test message")
        exported = hist.export("json")
        data = json.loads(exported)
        assert len(data) == 1
        assert data[0]["content"] == "test message"

    def test_export_markdown(self, tmp_path):
        from src.conversation_history import ConversationHistory

        hist = ConversationHistory(str(tmp_path))
        hist.add_turn("user", "Hello")
        hist.add_turn("assistant", "World")
        md = hist.export("markdown")
        assert "User" in md
        assert "JARVIS" in md
        assert "Hello" in md

    def test_clear_session(self, tmp_path):
        from src.conversation_history import ConversationHistory

        hist = ConversationHistory(str(tmp_path))
        sid = hist.session_id
        hist.add_turn("user", "to be cleared")
        hist.clear_session(sid)
        assert hist.get_session(sid) == []

    def test_metadata(self, tmp_path):
        from src.conversation_history import ConversationHistory

        hist = ConversationHistory(str(tmp_path))
        hist.add_turn("user", "test", metadata={"model": "opus", "tokens": 42})
        recent = hist.get_recent(1)
        assert recent[0]["metadata"]["model"] == "opus"


# =========================================================================
# CostTracker enhancements
# =========================================================================


class TestCostTrackerEnhancements:
    def test_lines_changed(self):
        from src.agent.cost_tracker import CostTracker

        t = CostTracker("test")
        t.add_lines_changed(added=10, removed=3)
        assert t.get_lines_changed() == (10, 3)
        t.add_lines_changed(added=5)
        assert t.get_lines_changed() == (15, 3)

    def test_format_total_cost(self):
        from src.agent.cost_tracker import CostTracker

        t = CostTracker("test")
        t.record_usage("claude-opus-4", input_tokens=1000, output_tokens=500)
        t.add_lines_changed(added=10, removed=3)
        summary = t.format_total_cost()
        assert "lines added" in summary
        assert "lines removed" in summary
        assert "$" in summary

    def test_get_total_duration(self):
        from src.agent.cost_tracker import CostTracker

        t = CostTracker("test")
        dur = t.get_total_duration()
        assert dur >= 0

    def test_reset_clears_lines(self):
        from src.agent.cost_tracker import CostTracker

        t = CostTracker("test")
        t.add_lines_changed(added=10, removed=3)
        t.reset()
        assert t.get_lines_changed() == (0, 0)

    def test_save_load_with_lines(self, tmp_path):
        from src.agent.cost_tracker import CostTracker

        t = CostTracker("test-save")
        t.record_usage("claude-opus-4", input_tokens=100, output_tokens=50)
        t.add_lines_changed(added=5, removed=2)
        save_path = str(tmp_path / "cost.json")
        t.save(save_path)

        t2 = CostTracker("test-save")
        t2.load(save_path)
        assert t2.get_lines_changed() == (5, 2)

    def test_format_duration(self):
        from src.agent.cost_tracker import CostTracker

        assert "s" in CostTracker._format_duration(30.5)
        assert "m" in CostTracker._format_duration(125)
        assert "h" in CostTracker._format_duration(3700)
