"""Tests for the bulk-integrated batch of upstream tools.

Covers the modules landed in the bulk-integration pass:

  * ``x_search``      — xAI X (Twitter) search; gated by XAI_API_KEY.
  * ``feishu_doc``    — feishu_doc_read; gated by the lark_oapi SDK.
  * ``feishu_drive``  — feishu_drive_list_comments / list_comment_replies /
                        reply_comment / add_comment; gated by lark_oapi.

Each tool proves:
  (a) self-registers in registry.all_entries() after import,
  (b) produces a valid RawFunctionTool when its check_fn passes,
  (c) is gated inert (absent from the adapted surface) when creds/SDK absent,
  (d) carries the right requires_env / schema shape,
  (e) handler error paths return clean JSON without network calls.

Gate-inert tools (feishu) only get registration + gating checks — no live API
calls. x_search's handler is exercised only via its missing-credential path.

All tests run against the local voice-agent .venv and need no credentials.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import re
from pathlib import Path

import pytest

# Ensure the voice-agent root is importable.
_VA_ROOT = Path(__file__).resolve().parent.parent
if str(_VA_ROOT) not in sys.path:
    sys.path.insert(0, str(_VA_ROOT))

from livekit.agents.llm import is_raw_function_tool  # noqa: E402
from tools import _adapter as adapter  # noqa: E402
from tools.registry import registry  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _invoke(tool, args: dict) -> str:
    return _run(tool(raw_arguments=args))


_LARK_PRESENT = importlib.util.find_spec("lark_oapi") is not None

_ALL_NEW_TOOLS = [
    "x_search",
    "feishu_doc_read",
    "feishu_drive_list_comments",
    "feishu_drive_list_comment_replies",
    "feishu_drive_reply_comment",
    "feishu_drive_add_comment",
]


# ---------------------------------------------------------------------------
# (a) Self-registration
# ---------------------------------------------------------------------------

class TestSelfRegistration:
    """After importing the tool modules, registry.all_entries() must include them."""

    @pytest.fixture(scope="class", autouse=True)
    def _ensure_imports(self):
        import tools.x_search, tools.feishu_doc, tools.feishu_drive  # noqa: F401

    def test_x_search_registers(self):
        assert registry.get_entry("x_search") is not None

    def test_feishu_doc_read_registers(self):
        assert registry.get_entry("feishu_doc_read") is not None

    @pytest.mark.parametrize("name", [
        "feishu_drive_list_comments",
        "feishu_drive_list_comment_replies",
        "feishu_drive_reply_comment",
        "feishu_drive_add_comment",
    ])
    def test_feishu_drive_registers(self, name):
        assert registry.get_entry(name) is not None

    def test_all_in_all_entries(self):
        names = {e.name for e in registry.all_entries()}
        assert set(_ALL_NEW_TOOLS).issubset(names), \
            f"Missing from registry: {set(_ALL_NEW_TOOLS) - names}"


# ---------------------------------------------------------------------------
# (b/c) LiveKit adaptation + gating
# ---------------------------------------------------------------------------

class TestAdaptationAndGating:
    """Adapted tools must be valid RawFunctionTools; gated tools must vanish."""

    @pytest.fixture(scope="class", autouse=True)
    def _ensure_imports(self):
        import tools.x_search, tools.feishu_doc, tools.feishu_drive  # noqa: F401

    def test_all_adapted_are_raw_function_tools(self):
        tools = adapter.load_all_livekit_tools()
        assert all(is_raw_function_tool(t) for t in tools), \
            "All adapted tools must be RawFunctionTool instances"

    def test_x_search_gated_when_no_key(self):
        """x_search must be suppressed (check_fn False) when XAI_API_KEY unset."""
        saved = os.environ.pop("XAI_API_KEY", None)
        try:
            registry  # touch
            from tools.registry import invalidate_check_fn_cache
            invalidate_check_fn_cache()
            names = {t.info.name for t in adapter.load_all_livekit_tools()}
            assert "x_search" not in names, "x_search must be gated when no XAI_API_KEY"
        finally:
            if saved is not None:
                os.environ["XAI_API_KEY"] = saved

    def test_x_search_present_when_key_set(self):
        """With XAI_API_KEY set, x_search must adapt to a valid RawFunctionTool."""
        from tools.registry import invalidate_check_fn_cache
        os.environ["XAI_API_KEY"] = "test-key-not-real"
        try:
            invalidate_check_fn_cache()
            matched = [t for t in adapter.load_all_livekit_tools() if t.info.name == "x_search"]
            assert matched, "x_search must appear when XAI_API_KEY is set"
            assert is_raw_function_tool(matched[0])
        finally:
            os.environ.pop("XAI_API_KEY", None)
            invalidate_check_fn_cache()

    @pytest.mark.skipif(_LARK_PRESENT, reason="lark_oapi installed — feishu tools are live, not gated")
    def test_feishu_tools_gated_without_sdk(self):
        """When lark_oapi is absent, all feishu tools must be gated out."""
        names = {t.info.name for t in adapter.load_all_livekit_tools()}
        for n in ("feishu_doc_read", "feishu_drive_list_comments",
                  "feishu_drive_reply_comment", "feishu_drive_add_comment"):
            assert n not in names, f"{n} must be gated when lark_oapi is absent"


# ---------------------------------------------------------------------------
# (d) Metadata / schema shape
# ---------------------------------------------------------------------------

class TestMetadata:
    @pytest.fixture(scope="class", autouse=True)
    def _ensure_imports(self):
        import tools.x_search, tools.feishu_doc, tools.feishu_drive  # noqa: F401

    def test_x_search_requires_env(self):
        entry = registry.get_entry("x_search")
        assert "XAI_API_KEY" in entry.requires_env

    def test_x_search_schema_required_query(self):
        entry = registry.get_entry("x_search")
        params = entry.schema["parameters"]
        assert params["required"] == ["query"]
        assert "query" in params["properties"]

    @pytest.mark.parametrize("name,required", [
        ("feishu_doc_read", ["doc_token"]),
        ("feishu_drive_list_comments", ["file_token"]),
        ("feishu_drive_list_comment_replies", ["file_token", "comment_id"]),
        ("feishu_drive_reply_comment", ["file_token", "comment_id", "content"]),
        ("feishu_drive_add_comment", ["file_token", "content"]),
    ])
    def test_feishu_schema_required(self, name, required):
        entry = registry.get_entry(name)
        assert entry.schema["parameters"]["required"] == required


# ---------------------------------------------------------------------------
# (e) Handler error-path smoke tests — no network
# ---------------------------------------------------------------------------

class TestXSearchBehavior:
    @pytest.fixture(autouse=True)
    def _import_and_clear_key(self):
        import tools.x_search  # noqa: F401
        saved = os.environ.pop("XAI_API_KEY", None)
        yield
        if saved is not None:
            os.environ["XAI_API_KEY"] = saved

    def test_empty_query_returns_error(self):
        import tools.x_search as m
        result = json.loads(m._handle_x_search({"query": "   "}))
        assert "error" in result

    def test_missing_credentials_returns_clean_error(self):
        """No XAI_API_KEY → clean tool error, never a network call / 401."""
        import tools.x_search as m
        result = json.loads(m._handle_x_search({"query": "anything"}))
        assert "error" in result
        assert "XAI_API_KEY" in result["error"]

    def test_check_fn_false_without_key(self):
        entry = registry.get_entry("x_search")
        assert entry.check_fn() is False

    def test_check_fn_true_with_key(self):
        os.environ["XAI_API_KEY"] = "test-key-not-real"
        entry = registry.get_entry("x_search")
        assert entry.check_fn() is True

    def test_conflicting_handles_rejected(self):
        """allowed + excluded handles together must be rejected (with a key set)."""
        os.environ["XAI_API_KEY"] = "test-key-not-real"
        import tools.x_search as m
        result = json.loads(m._handle_x_search({
            "query": "x",
            "allowed_x_handles": ["a"],
            "excluded_x_handles": ["b"],
        }))
        assert "error" in result
        assert "cannot be used together" in result["error"]


class TestFeishuBehavior:
    """feishu handlers must error cleanly when no client is injected."""

    @pytest.fixture(autouse=True)
    def _imports(self):
        import tools.feishu_doc, tools.feishu_drive  # noqa: F401

    def test_doc_read_no_token(self):
        import tools.feishu_doc as m
        result = json.loads(m._handle_feishu_doc_read({"doc_token": ""}))
        assert "error" in result

    def test_doc_read_no_client(self):
        import tools.feishu_doc as m
        # doc_token present but no client injected → "client not available".
        result = json.loads(m._handle_feishu_doc_read({"doc_token": "tok123"}))
        assert "error" in result
        assert "client" in result["error"].lower()

    def test_drive_list_comments_no_client(self):
        import tools.feishu_drive as m
        result = json.loads(m._handle_list_comments({"file_token": "f1"}))
        assert "error" in result
        assert "client" in result["error"].lower()

    def test_drive_reply_missing_args(self):
        import tools.feishu_drive as m
        # Inject a dummy client so we get past the client gate to arg validation.
        m.set_client(object())
        try:
            result = json.loads(m._handle_reply_comment({"file_token": "f1"}))
            assert "error" in result
        finally:
            m.set_client(None)

    def test_drive_add_comment_missing_args(self):
        import tools.feishu_drive as m
        m.set_client(object())
        try:
            result = json.loads(m._handle_add_comment({"file_token": "f1"}))
            assert "error" in result
        finally:
            m.set_client(None)

    def test_set_get_client_roundtrip(self):
        import tools.feishu_drive as m
        sentinel = object()
        m.set_client(sentinel)
        try:
            assert m.get_client() is sentinel
        finally:
            m.set_client(None)
        assert m.get_client() is None


# ---------------------------------------------------------------------------
# Hermes token leak guard
# ---------------------------------------------------------------------------

class TestNoHermesTokens:
    """Confirm no 'hermes' strings appear in any newly integrated tool file."""

    _NEW_FILES = [
        _VA_ROOT / "tools" / "x_search.py",
        _VA_ROOT / "tools" / "feishu_doc.py",
        _VA_ROOT / "tools" / "feishu_drive.py",
    ]

    @pytest.mark.parametrize("path", _NEW_FILES)
    def test_no_hermes_token_in_file(self, path: Path):
        text = path.read_text(encoding="utf-8").lower()
        matches = re.findall(r"\bhermes\b", text)
        assert not matches, (
            f"{path.name} contains 'hermes' tokens: {matches}. "
            "All JARVIS tools must use JARVIS-native names."
        )
