"""Tests for the clean tools batch 1: vuln_check, home_assistant, discord.

Proves each ported tool:
  (a) self-registers in registry.all_entries() after import,
  (b) produces a valid RawFunctionTool via load_all_livekit_tools(),
  (c) behaves correctly in smoke tests (no network, no external services).

No credentials are needed. Tools gated by check_fn (HA, Discord) are
tested at the schema/registration level; their handlers are exercised
with missing-creds error paths only to avoid network calls.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest.mock
from pathlib import Path
from typing import Any

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


# ---------------------------------------------------------------------------
# (a) Self-registration
# ---------------------------------------------------------------------------

class TestSelfRegistration:
    """After importing the tool module, registry.all_entries() must include it."""

    def test_vuln_check_registers(self):
        import tools.vuln_check  # noqa: F401
        assert registry.get_entry("vuln_check") is not None

    def test_ha_list_entities_registers(self):
        import tools.home_assistant  # noqa: F401
        assert registry.get_entry("ha_list_entities") is not None

    def test_ha_get_state_registers(self):
        import tools.home_assistant  # noqa: F401
        assert registry.get_entry("ha_get_state") is not None

    def test_ha_list_services_registers(self):
        import tools.home_assistant  # noqa: F401
        assert registry.get_entry("ha_list_services") is not None

    def test_ha_call_service_registers(self):
        import tools.home_assistant  # noqa: F401
        assert registry.get_entry("ha_call_service") is not None

    def test_discord_registers(self):
        import tools.discord  # noqa: F401
        assert registry.get_entry("discord") is not None

    def test_discord_admin_registers(self):
        import tools.discord  # noqa: F401
        assert registry.get_entry("discord_admin") is not None

    def test_all_in_all_entries(self):
        import tools.vuln_check, tools.home_assistant, tools.discord  # noqa: F401
        names = {e.name for e in registry.all_entries()}
        expected = {
            "vuln_check",
            "ha_list_entities", "ha_get_state", "ha_list_services", "ha_call_service",
            "discord", "discord_admin",
        }
        assert expected.issubset(names), f"Missing from registry: {expected - names}"


# ---------------------------------------------------------------------------
# (b) LiveKit adaptation
# ---------------------------------------------------------------------------

class TestLivekitAdaptation:
    """Adapted tools must be is_raw_function_tool and carry the correct name."""

    @pytest.fixture(scope="class", autouse=True)
    def _ensure_imports(self):
        import tools.vuln_check, tools.home_assistant, tools.discord  # noqa: F401

    def test_all_adapted_are_raw_function_tools(self):
        tools = adapter.load_all_livekit_tools()
        assert all(is_raw_function_tool(t) for t in tools), \
            "All adapted tools must be RawFunctionTool instances"

    def test_vuln_check_in_adapted_tools(self):
        import tools.vuln_check  # noqa: F401
        lk_tools = adapter.load_all_livekit_tools()
        names = {t.info.name for t in lk_tools}
        assert "vuln_check" in names

    def test_ha_tools_NOT_in_adapted_when_no_token(self):
        """HA tools must be suppressed (check_fn=False) when HASS_TOKEN is unset."""
        import tools.home_assistant  # noqa: F401
        # Ensure HASS_TOKEN is absent
        saved = os.environ.pop("HASS_TOKEN", None)
        try:
            lk_tools = adapter.load_all_livekit_tools()
            names = {t.info.name for t in lk_tools}
            assert "ha_list_entities" not in names, "HA tools must be gated when no token"
        finally:
            if saved is not None:
                os.environ["HASS_TOKEN"] = saved

    def test_discord_tools_NOT_in_adapted_when_no_token(self):
        """Discord tools must be suppressed when DISCORD_BOT_TOKEN is unset."""
        import tools.discord  # noqa: F401
        saved = os.environ.pop("DISCORD_BOT_TOKEN", None)
        try:
            lk_tools = adapter.load_all_livekit_tools()
            names = {t.info.name for t in lk_tools}
            assert "discord" not in names, "Discord tools must be gated when no token"
        finally:
            if saved is not None:
                os.environ["DISCORD_BOT_TOKEN"] = saved


# ---------------------------------------------------------------------------
# vuln_check ecosystem casing + fail-closed — live-verify finding 2026-06
# (OSV is case-sensitive: "pypi" → HTTP 400 "Invalid ecosystem"; the old code
# passed the explicit ecosystem straight through and then fail-OPENED the 400
# as "safe", silently not checking. Now normalized + fails closed on 4xx.)
# ---------------------------------------------------------------------------


class TestVulnCheckEcosystem:
    def test_canonical_ecosystem_normalizes_common_slips(self):
        import tools.vuln_check as vc
        assert vc._canonical_ecosystem("pypi") == "PyPI"
        assert vc._canonical_ecosystem("python") == "PyPI"
        assert vc._canonical_ecosystem("PYPI") == "PyPI"
        assert vc._canonical_ecosystem("node") == "npm"
        assert vc._canonical_ecosystem("golang") == "Go"
        assert vc._canonical_ecosystem("cargo") == "crates.io"

    def test_unknown_ecosystem_passes_through(self):
        """A valid-but-unlisted OSV ecosystem must not be mangled."""
        import tools.vuln_check as vc
        assert vc._canonical_ecosystem("Debian:12") == "Debian:12"
        assert vc._canonical_ecosystem("Alpine") == "Alpine"

    def test_explicit_lowercase_ecosystem_is_normalized_before_query(self):
        """A 'pypi' slip must reach _query_osv as 'PyPI', not 400-then-fail-open."""
        import tools.vuln_check as vc
        seen = {}

        def _fake_query(package, ecosystem, version=None):
            seen["ecosystem"] = ecosystem
            return []  # no vulns

        with unittest.mock.patch.object(vc, "_query_osv", _fake_query):
            out = json.loads(vc._handle_vuln_check({"package": "requests", "ecosystem": "pypi"}))
        assert seen["ecosystem"] == "PyPI"
        assert out["safe"] is True

    def test_4xx_fails_closed_not_open(self):
        """An OSV 400 must report safe=False (unverified), not safe=True."""
        import urllib.error
        import tools.vuln_check as vc

        def _raise_400(req, *a, **k):
            raise urllib.error.HTTPError(req.full_url, 400, "Invalid ecosystem", {}, None)

        with unittest.mock.patch.object(vc.urllib.request, "urlopen", _raise_400):
            out = json.loads(vc._handle_vuln_check({"package": "x", "ecosystem": "Bogus"}))
        assert out["safe"] is False
        assert "error" in out and "400" in out["error"]

    def test_5xx_still_fails_open(self):
        """A transient 503 keeps the fail-open behavior (don't block on outage)."""
        import urllib.error
        import tools.vuln_check as vc

        def _raise_503(req, *a, **k):
            raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, None)

        with unittest.mock.patch.object(vc.urllib.request, "urlopen", _raise_503):
            out = json.loads(vc._handle_vuln_check({"package": "requests", "ecosystem": "PyPI"}))
        assert out["safe"] is True
        assert "note" in out


# ---------------------------------------------------------------------------
# (c) Behavior smoke tests — no network calls
# ---------------------------------------------------------------------------

class TestVulnCheckBehavior:
    """vuln_check handler smoke tests — OSV API mocked."""

    @pytest.fixture(autouse=True)
    def _import_module(self):
        import tools.vuln_check  # noqa: F401

    def _get_handler(self):
        entry = registry.get_entry("vuln_check")
        assert entry is not None, "vuln_check must be registered"
        return entry.handler

    def test_missing_args_returns_error(self):
        h = self._get_handler()
        result = json.loads(h({}))
        assert "error" in result

    def test_command_mode_infers_npm(self):
        """npx @foo/bar → ecosystem npm."""
        h = self._get_handler()
        # Mock the OSV query to avoid network
        with unittest.mock.patch("tools.vuln_check._query_osv", return_value=[]) as mock_q:
            result_raw = h({"command": "npx", "args_list": ["@modelcontextprotocol/server-everything"]})
            result = json.loads(result_raw)
        assert result.get("safe") is True
        assert result.get("ecosystem") == "npm"
        assert result.get("package") == "@modelcontextprotocol/server-everything"

    def test_command_mode_infers_pypi(self):
        """pip install requests==2.28 → ecosystem PyPI."""
        h = self._get_handler()
        with unittest.mock.patch("tools.vuln_check._query_osv", return_value=[]) as mock_q:
            result_raw = h({"command": "pip", "args_list": ["requests==2.28.1"]})
            result = json.loads(result_raw)
        assert result.get("ecosystem") == "PyPI"
        assert result.get("package") == "requests"
        assert result.get("version") == "2.28.1"

    def test_direct_mode(self):
        h = self._get_handler()
        with unittest.mock.patch("tools.vuln_check._query_osv", return_value=[]) as mock_q:
            result_raw = h({"package": "requests", "ecosystem": "PyPI", "version": "2.28.0"})
            result = json.loads(result_raw)
        assert result.get("safe") is True
        assert result.get("vuln_count") == 0

    def test_malware_flagged_as_unsafe(self):
        """A MAL-* advisory must set safe=False."""
        h = self._get_handler()
        fake_vulns = [{"id": "MAL-0001", "summary": "Malicious payload"}]
        with unittest.mock.patch("tools.vuln_check._query_osv", return_value=fake_vulns):
            result_raw = h({"package": "evil-pkg", "ecosystem": "npm", "malware_only": True})
            result = json.loads(result_raw)
        assert result.get("safe") is False
        assert result.get("malware_count") == 1

    def test_network_error_is_fail_open(self):
        """Network errors must return safe=True (fail-open)."""
        h = self._get_handler()
        with unittest.mock.patch("tools.vuln_check._query_osv", side_effect=OSError("timeout")):
            result_raw = h({"package": "requests", "ecosystem": "PyPI"})
            result = json.loads(result_raw)
        assert result.get("safe") is True
        assert "note" in result

    def test_unknown_command_returns_error(self):
        h = self._get_handler()
        result = json.loads(h({"command": "cargo", "args_list": ["serde"]}))
        assert "error" in result


class TestHomeAssistantBehavior:
    """ha_* handler smoke tests — no real HA needed."""

    @pytest.fixture(autouse=True)
    def _import_and_clear_token(self):
        import tools.home_assistant  # noqa: F401
        saved = os.environ.pop("HASS_TOKEN", None)
        yield
        if saved is not None:
            os.environ["HASS_TOKEN"] = saved

    def test_check_fn_false_when_no_token(self):
        entry = registry.get_entry("ha_list_entities")
        assert entry is not None
        assert entry.check_fn is not None
        assert entry.check_fn() is False

    def test_check_fn_true_with_token(self):
        os.environ["HASS_TOKEN"] = "test-token-xyz"
        entry = registry.get_entry("ha_list_entities")
        assert entry.check_fn() is True

    def test_ha_get_state_invalid_entity_id(self):
        """Handler should reject malformed entity_id without making a network call."""
        import tools.home_assistant as ha_mod
        result = json.loads(ha_mod._handle_get_state({"entity_id": "INVALID/FORMAT"}))
        assert "error" in result

    def test_ha_call_service_blocked_domain(self):
        """shell_command domain must be blocked."""
        import tools.home_assistant as ha_mod
        result = json.loads(ha_mod._handle_call_service(
            {"domain": "shell_command", "service": "run", "entity_id": "light.x"}
        ))
        assert "error" in result
        assert "blocked" in result["error"].lower()

    def test_ha_call_service_missing_domain(self):
        import tools.home_assistant as ha_mod
        result = json.loads(ha_mod._handle_call_service({"service": "turn_on"}))
        assert "error" in result

    def test_ha_call_service_invalid_json_data(self):
        import tools.home_assistant as ha_mod
        result = json.loads(ha_mod._handle_call_service(
            {"domain": "light", "service": "turn_on", "data": "not-json{"}
        ))
        assert "error" in result

    def test_requires_env_set(self):
        entry = registry.get_entry("ha_list_entities")
        assert "HASS_TOKEN" in entry.requires_env


class TestDiscordBehavior:
    """discord handler smoke tests — no real Discord connection."""

    @pytest.fixture(autouse=True)
    def _import_and_clear_token(self):
        import tools.discord  # noqa: F401
        saved = os.environ.pop("DISCORD_BOT_TOKEN", None)
        yield
        if saved is not None:
            os.environ["DISCORD_BOT_TOKEN"] = saved

    def test_check_fn_false_when_no_token(self):
        entry = registry.get_entry("discord")
        assert entry is not None
        assert entry.check_fn is not None
        assert entry.check_fn() is False

    def test_check_fn_true_with_token(self):
        os.environ["DISCORD_BOT_TOKEN"] = "Bot.fake.token"
        entry = registry.get_entry("discord")
        assert entry.check_fn() is True

    def test_missing_action_returns_error(self):
        import tools.discord as discord_mod
        result = json.loads(discord_mod._handle_discord_core({}))
        assert "error" in result

    def test_unknown_action_returns_error(self):
        import tools.discord as discord_mod
        result = json.loads(discord_mod._handle_discord_core({"action": "explode"}))
        assert "error" in result

    def test_admin_action_unknown_returns_error(self):
        import tools.discord as discord_mod
        result = json.loads(discord_mod._handle_discord_admin({"action": "destroy_server"}))
        assert "error" in result

    def test_missing_token_returns_error(self):
        import tools.discord as discord_mod
        # DISCORD_BOT_TOKEN already cleared by fixture
        result = json.loads(discord_mod._handle_discord_core({"action": "fetch_messages", "channel_id": "123"}))
        assert "error" in result

    def test_requires_env_set(self):
        entry = registry.get_entry("discord")
        assert "DISCORD_BOT_TOKEN" in entry.requires_env

    def test_discord_admin_requires_env_set(self):
        entry = registry.get_entry("discord_admin")
        assert "DISCORD_BOT_TOKEN" in entry.requires_env

    def test_core_actions_are_core_set(self):
        """fetch_messages, search_members, create_thread must be in core; not in admin."""
        import tools.discord as discord_mod
        core = discord_mod._CORE_ACTION_NAMES
        admin = discord_mod._ADMIN_ACTION_NAMES
        assert "fetch_messages" in core
        assert "search_members" in core
        assert "create_thread" in core
        # admin should not contain core actions
        assert not (core & admin), "Core and admin action sets must not overlap"


# ---------------------------------------------------------------------------
# Hermes token leak guard
# ---------------------------------------------------------------------------

class TestNoHermesTokens:
    """Confirm no 'hermes' strings appear in any ported tool file."""

    _NEW_FILES = [
        _VA_ROOT / "tools" / "vuln_check.py",
        _VA_ROOT / "tools" / "home_assistant.py",
        _VA_ROOT / "tools" / "discord.py",
    ]

    @pytest.mark.parametrize("path", _NEW_FILES)
    def test_no_hermes_token_in_file(self, path: Path):
        text = path.read_text(encoding="utf-8").lower()
        # Look for standalone word 'hermes' that isn't part of a different word.
        import re
        matches = re.findall(r'\bhermes\b', text)
        assert not matches, (
            f"{path.name} contains 'hermes' tokens: {matches}. "
            "All JARVIS tools must use JARVIS-native names."
        )
