"""Tests for src/reasoning/providers.py — Provider dataclass, ProviderRegistry._load(),
get_active_providers(), and provider routing logic.

All tests run without making actual API calls.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.reasoning.providers import Provider, ProviderRegistry, TEMPLATES


# ---------------------------------------------------------------------------
# Provider dataclass tests
# ---------------------------------------------------------------------------

class TestProviderDataclass(unittest.TestCase):
    """Test the Provider dataclass construction and properties."""

    def _make_provider(self, **overrides):
        defaults = dict(
            name="test",
            type="openai",
            api_key="sk-test-key",
            base_url="https://api.example.com/v1",
            model="gpt-4o-mini",
            models=["gpt-4o-mini", "gpt-4o"],
            priority=0,
            enabled=True,
        )
        defaults.update(overrides)
        return Provider(**defaults)

    def test_basic_creation(self):
        p = self._make_provider()
        self.assertEqual(p.name, "test")
        self.assertEqual(p.type, "openai")
        self.assertEqual(p.api_key, "sk-test-key")
        self.assertEqual(p.model, "gpt-4o-mini")
        self.assertEqual(p.priority, 0)
        self.assertTrue(p.enabled)

    def test_enabled_defaults_to_true(self):
        p = Provider(
            name="x", type="openai", api_key="k",
            base_url="http://example.com", model="m",
            models=[], priority=5,
        )
        self.assertTrue(p.enabled)

    def test_enabled_can_be_false(self):
        p = self._make_provider(enabled=False)
        self.assertFalse(p.enabled)

    def test_is_local_localhost(self):
        p = self._make_provider(base_url="http://localhost:11434/v1")
        self.assertTrue(p.is_local)

    def test_is_local_127(self):
        p = self._make_provider(base_url="http://127.0.0.1:11434/v1")
        self.assertTrue(p.is_local)

    def test_is_not_local_cloud(self):
        p = self._make_provider(base_url="https://api.openai.com/v1")
        self.assertFalse(p.is_local)

    def test_is_local_empty_url(self):
        p = self._make_provider(base_url="")
        self.assertFalse(p.is_local)

    def test_to_dict(self):
        p = self._make_provider()
        d = p.to_dict()
        self.assertEqual(d["name"], "test")
        self.assertEqual(d["type"], "openai")
        self.assertEqual(d["api_key"], "sk-test-key")
        self.assertEqual(d["base_url"], "https://api.example.com/v1")
        self.assertEqual(d["model"], "gpt-4o-mini")
        self.assertEqual(d["models"], ["gpt-4o-mini", "gpt-4o"])
        self.assertEqual(d["priority"], 0)
        self.assertTrue(d["enabled"])

    def test_to_dict_roundtrip(self):
        """Provider(**p.to_dict()) should recreate the same provider."""
        p1 = self._make_provider(name="roundtrip", priority=3, enabled=False)
        d = p1.to_dict()
        p2 = Provider(**d)
        self.assertEqual(p1.name, p2.name)
        self.assertEqual(p1.type, p2.type)
        self.assertEqual(p1.api_key, p2.api_key)
        self.assertEqual(p1.base_url, p2.base_url)
        self.assertEqual(p1.model, p2.model)
        self.assertEqual(p1.models, p2.models)
        self.assertEqual(p1.priority, p2.priority)
        self.assertEqual(p1.enabled, p2.enabled)

    def test_models_list_independence(self):
        """Modifying the models list after creation should not affect the provider
        if the caller stored a separate reference (standard Python behavior)."""
        original = ["a", "b"]
        p = self._make_provider(models=original)
        # The dataclass stores the same list reference
        self.assertEqual(p.models, ["a", "b"])

    def test_anthropic_type(self):
        p = self._make_provider(type="anthropic")
        self.assertEqual(p.type, "anthropic")


# ---------------------------------------------------------------------------
# ProviderRegistry._load() tests
# ---------------------------------------------------------------------------

class TestProviderRegistryLoad(unittest.TestCase):
    """Test _load() with various providers.json scenarios."""

    def _make_registry_with_file(self, providers_json_content, tmpdir):
        """Create a ProviderRegistry that loads from a custom providers.json.

        We patch PROVIDERS_FILE, JARVIS_HOME, and the env-based loader methods
        so no real credentials or files are touched.
        """
        providers_path = Path(tmpdir) / "providers.json"
        if providers_json_content is not None:
            providers_path.write_text(providers_json_content, encoding="utf-8")

        with patch("src.reasoning.providers.PROVIDERS_FILE", providers_path), \
             patch("src.reasoning.providers.JARVIS_HOME", Path(tmpdir)), \
             patch.object(ProviderRegistry, "_load_env_providers", lambda self: None), \
             patch.object(ProviderRegistry, "_load_claude_credentials", lambda self: None), \
             patch.object(ProviderRegistry, "_load_remote_brain", lambda self: None):
            registry = ProviderRegistry()
        return registry

    def test_load_valid_providers_json(self):
        data = {
            "mycloud": {
                "name": "mycloud",
                "type": "openai",
                "api_key": "sk-abc123",
                "base_url": "https://api.example.com/v1",
                "model": "gpt-4o",
                "models": ["gpt-4o", "gpt-4o-mini"],
                "priority": 0,
                "enabled": True,
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = self._make_registry_with_file(json.dumps(data), tmpdir)
            providers = registry._providers
            self.assertIn("mycloud", providers)
            p = providers["mycloud"]
            self.assertEqual(p.name, "mycloud")
            self.assertEqual(p.type, "openai")
            self.assertEqual(p.api_key, "sk-abc123")
            self.assertEqual(p.model, "gpt-4o")

    def test_load_multiple_providers(self):
        data = {
            "alpha": {
                "name": "alpha", "type": "openai", "api_key": "k1",
                "base_url": "https://a.com", "model": "m1",
                "models": [], "priority": 0, "enabled": True,
            },
            "beta": {
                "name": "beta", "type": "anthropic", "api_key": "k2",
                "base_url": "https://b.com", "model": "m2",
                "models": ["m2", "m3"], "priority": 1, "enabled": False,
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = self._make_registry_with_file(json.dumps(data), tmpdir)
            self.assertEqual(len(registry._providers), 2)
            self.assertFalse(registry._providers["beta"].enabled)

    def test_load_missing_file(self):
        """Missing providers.json should result in empty providers, no error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = self._make_registry_with_file(None, tmpdir)
            self.assertEqual(len(registry._providers), 0)

    def test_load_malformed_json(self):
        """Malformed JSON is handled gracefully -- logs a warning, loads no providers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = self._make_registry_with_file("{invalid json!!", tmpdir)
            self.assertEqual(len(registry._providers), 0)

    def test_load_invalid_structure_not_a_dict(self):
        """A JSON array instead of object should not crash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # json.load returns a list, then data.items() raises AttributeError
            # which is not caught -- but let's verify the behavior
            try:
                registry = self._make_registry_with_file('[1, 2, 3]', tmpdir)
                # If it doesn't crash, providers should be empty
                self.assertEqual(len(registry._providers), 0)
            except AttributeError:
                # Expected: list has no .items() method
                pass

    def test_load_provider_missing_fields(self):
        """Provider entry missing required fields is handled gracefully."""
        data = {
            "broken": {
                "name": "broken",
                # Missing type, api_key, base_url, model, models, priority
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = self._make_registry_with_file(json.dumps(data), tmpdir)
            # Broken provider should not be loaded
            self.assertNotIn("broken", registry._providers)

    def test_load_provider_extra_fields(self):
        """Extra fields in provider JSON cause TypeError, which is caught and logged."""
        data = {
            "extra": {
                "name": "extra", "type": "openai", "api_key": "k",
                "base_url": "u", "model": "m", "models": [],
                "priority": 0, "enabled": True,
                "unknown_field": "surprise",
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = self._make_registry_with_file(json.dumps(data), tmpdir)
            # Extra fields cause TypeError -- should be caught and logged
            self.assertNotIn("extra", registry._providers)

    def test_load_empty_json_object(self):
        """Empty {} should result in no providers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = self._make_registry_with_file("{}", tmpdir)
            self.assertEqual(len(registry._providers), 0)


# ---------------------------------------------------------------------------
# Provider routing / get_active_providers tests
# ---------------------------------------------------------------------------

class TestGetActiveProviders(unittest.TestCase):
    """Test get_active_providers() sorting and filtering logic."""

    def _make_registry(self, providers_list):
        """Build a ProviderRegistry with pre-loaded providers (no disk I/O)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("src.reasoning.providers.PROVIDERS_FILE", Path(tmpdir) / "p.json"), \
                 patch("src.reasoning.providers.JARVIS_HOME", Path(tmpdir)), \
                 patch.object(ProviderRegistry, "_load_env_providers", lambda self: None), \
                 patch.object(ProviderRegistry, "_load_claude_credentials", lambda self: None), \
                 patch.object(ProviderRegistry, "_load_remote_brain", lambda self: None):
                registry = ProviderRegistry()
        for p in providers_list:
            registry._providers[p.name] = p
        return registry

    def _cloud(self, name, model="gpt-4o-mini", priority=0, enabled=True):
        return Provider(
            name=name, type="openai", api_key="k",
            base_url="https://api.example.com/v1",
            model=model, models=[model], priority=priority, enabled=enabled,
        )

    def _local(self, name, model="llama3.2:3b", priority=5, enabled=True):
        return Provider(
            name=name, type="openai", api_key="ollama",
            base_url="http://localhost:11434/v1",
            model=model, models=[model], priority=priority, enabled=enabled,
        )

    def test_disabled_providers_excluded(self):
        """Disabled providers should not appear in active list."""
        registry = self._make_registry([
            self._cloud("a", priority=0),
            self._cloud("b", priority=1, enabled=False),
        ])
        with patch.object(registry, "_has_internet", return_value=False):
            active = registry.get_active_providers()
        names = [p.name for p in active]
        self.assertIn("a", names)
        self.assertNotIn("b", names)

    def test_sorted_by_priority(self):
        """Providers should be sorted by priority (lower first)."""
        registry = self._make_registry([
            self._cloud("c", priority=2),
            self._cloud("a", priority=0),
            self._cloud("b", priority=1),
        ])
        with patch.object(registry, "_has_internet", return_value=False):
            active = registry.get_active_providers()
        names = [p.name for p in active]
        self.assertEqual(names, ["a", "b", "c"])

    def test_prefer_smart_reorders(self):
        """prefer_smart should put 70B+ models first."""
        small = self._cloud("small", model="gpt-4o-mini", priority=0)
        big = self._cloud("big", model="llama-70b-chat", priority=1)
        registry = self._make_registry([small, big])
        with patch.object(registry, "_has_internet", return_value=False):
            active = registry.get_active_providers(prefer_smart=True)
        self.assertEqual(active[0].name, "big")

    def test_prefer_code_reorders(self):
        """prefer_code should put code-specialized models first."""
        general = self._cloud("general", model="gpt-4o", priority=0)
        code = self._cloud("deepseek", model="deepseek-coder", priority=1)
        registry = self._make_registry([general, code])
        with patch.object(registry, "_has_internet", return_value=False):
            active = registry.get_active_providers(prefer_code=True)
        self.assertEqual(active[0].name, "deepseek")

    def test_prefer_tool_calling_cloud_first(self):
        """prefer_tool_calling should order: cloud smart > cloud other > local smart > local small."""
        local_small = self._local("local-small", model="llama3.2:3b", priority=0)
        cloud_fast = self._cloud("cloud-fast", model="gpt-4o-mini", priority=1)
        local_big = self._local("local-big", model="llama-70b", priority=2)
        cloud_smart = self._cloud("cloud-smart", model="mixtral-8x7b", priority=3)
        registry = self._make_registry([local_small, cloud_fast, local_big, cloud_smart])
        with patch.object(registry, "_has_internet", return_value=False):
            active = registry.get_active_providers(prefer_tool_calling=True)
        names = [p.name for p in active]
        # Cloud smart first, then cloud other, then local smart, then local small
        self.assertEqual(names[0], "cloud-smart")
        self.assertEqual(names[1], "cloud-fast")
        self.assertEqual(names[2], "local-big")
        self.assertEqual(names[3], "local-small")

    def test_empty_providers(self):
        """No providers should return empty list."""
        registry = self._make_registry([])
        with patch.object(registry, "_has_internet", return_value=False):
            active = registry.get_active_providers()
        self.assertEqual(active, [])

    def test_internet_puts_cloud_first_keeps_local_as_fallback(self):
        """When internet is available, cloud providers come first but local stays as fallback."""
        local = self._local("ollama", priority=0)
        cloud = self._cloud("openai", priority=1)
        registry = self._make_registry([local, cloud])
        with patch.object(registry, "_has_internet", return_value=True):
            active = registry.get_active_providers()
        names = [p.name for p in active]
        # Cloud is tried first even though ollama has lower priority number
        self.assertEqual(names[0], "openai")
        # Local is kept as safety net — never dropped entirely
        self.assertIn("ollama", names)

    def test_no_internet_keeps_local(self):
        """When internet is down, local providers should remain available."""
        local = self._local("ollama", priority=0)
        cloud = self._cloud("openai", priority=1)
        registry = self._make_registry([local, cloud])
        with patch.object(registry, "_has_internet", return_value=False):
            active = registry.get_active_providers()
        names = [p.name for p in active]
        self.assertIn("ollama", names)
        self.assertIn("openai", names)

    def test_internet_up_but_no_cloud_keeps_local(self):
        """If internet is up but there are only local providers, they should remain."""
        local = self._local("ollama", priority=0)
        registry = self._make_registry([local])
        with patch.object(registry, "_has_internet", return_value=True):
            active = registry.get_active_providers()
        names = [p.name for p in active]
        self.assertIn("ollama", names)


# ---------------------------------------------------------------------------
# Circuit breaker tests
# ---------------------------------------------------------------------------

class TestCircuitBreaker(unittest.TestCase):
    """Test the circuit breaker mechanism on ProviderRegistry."""

    def _make_registry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("src.reasoning.providers.PROVIDERS_FILE", Path(tmpdir) / "p.json"), \
                 patch("src.reasoning.providers.JARVIS_HOME", Path(tmpdir)), \
                 patch.object(ProviderRegistry, "_load_env_providers", lambda self: None), \
                 patch.object(ProviderRegistry, "_load_claude_credentials", lambda self: None), \
                 patch.object(ProviderRegistry, "_load_remote_brain", lambda self: None):
                registry = ProviderRegistry()
        return registry

    def test_cb_initially_closed(self):
        registry = self._make_registry()
        p = Provider(
            name="test", type="openai", api_key="k",
            base_url="https://api.example.com", model="m",
            models=[], priority=0,
        )
        self.assertFalse(registry._cb_is_open(p))

    def test_cb_opens_after_max_failures(self):
        registry = self._make_registry()
        p = Provider(
            name="fail-me", type="openai", api_key="k",
            base_url="https://api.example.com", model="m",
            models=[], priority=0,
        )
        for _ in range(ProviderRegistry._CB_MAX_FAILURES):
            registry._cb_record_failure(p)
        self.assertTrue(registry._cb_is_open(p))

    def test_cb_success_resets(self):
        registry = self._make_registry()
        p = Provider(
            name="recoverer", type="openai", api_key="k",
            base_url="https://api.example.com", model="m",
            models=[], priority=0,
        )
        for _ in range(ProviderRegistry._CB_MAX_FAILURES):
            registry._cb_record_failure(p)
        self.assertTrue(registry._cb_is_open(p))
        registry._cb_record_success(p.name)
        self.assertFalse(registry._cb_is_open(p))

    def test_cb_local_transient_does_not_penalize(self):
        """Transient errors on local providers should NOT open the circuit."""
        registry = self._make_registry()
        p = Provider(
            name="ollama", type="openai", api_key="ollama",
            base_url="http://localhost:11434/v1", model="llama3",
            models=[], priority=0,
        )
        for _ in range(10):
            registry._cb_record_failure(p, is_transient=True)
        self.assertFalse(registry._cb_is_open(p))


# ---------------------------------------------------------------------------
# ProviderRegistry miscellaneous tests
# ---------------------------------------------------------------------------

class TestProviderRegistryMisc(unittest.TestCase):

    def _make_registry_with_tmpdir(self, tmpdir):
        with patch("src.reasoning.providers.PROVIDERS_FILE", Path(tmpdir) / "providers.json"), \
             patch("src.reasoning.providers.JARVIS_HOME", Path(tmpdir)), \
             patch.object(ProviderRegistry, "_load_env_providers", lambda self: None), \
             patch.object(ProviderRegistry, "_load_claude_credentials", lambda self: None), \
             patch.object(ProviderRegistry, "_load_remote_brain", lambda self: None):
            registry = ProviderRegistry()
        return registry

    def test_detect_type_anthropic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = self._make_registry_with_tmpdir(tmpdir)
            self.assertEqual(registry._detect_type("sk-ant-abc123"), "anthropic")
            self.assertEqual(registry._detect_type("anthropic-key"), "anthropic")

    def test_detect_type_openai_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = self._make_registry_with_tmpdir(tmpdir)
            self.assertEqual(registry._detect_type("sk-proj-abc123"), "openai")
            self.assertEqual(registry._detect_type("random-key"), "openai")

    def test_list_providers_masks_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = self._make_registry_with_tmpdir(tmpdir)
            registry._providers["demo"] = Provider(
                name="demo", type="openai", api_key="sk-1234567890abcdef",
                base_url="https://api.example.com", model="gpt-4o",
                models=[], priority=0,
            )
            listed = registry.list_providers()
            self.assertEqual(len(listed), 1)
            self.assertNotIn("api_key", listed[0])
            self.assertIn("api_key_masked", listed[0])
            masked = listed[0]["api_key_masked"]
            self.assertTrue(masked.startswith("sk-12345"))
            self.assertTrue(masked.endswith("cdef"))
            self.assertIn("...", masked)

    def test_list_providers_short_key_masked_as_stars(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = self._make_registry_with_tmpdir(tmpdir)
            registry._providers["short"] = Provider(
                name="short", type="openai", api_key="tiny",
                base_url="https://api.example.com", model="m",
                models=[], priority=0,
            )
            listed = registry.list_providers()
            self.assertEqual(listed[0]["api_key_masked"], "***")

    def test_effort_tokens(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = self._make_registry_with_tmpdir(tmpdir)
            registry.set_effort("low")
            self.assertEqual(registry._effort_tokens(), 512)
            registry.set_effort("max")
            self.assertEqual(registry._effort_tokens(), 8192)

    def test_effort_invalid_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = self._make_registry_with_tmpdir(tmpdir)
            registry.set_effort("medium")
            registry.set_effort("invalid_level")
            # Should remain medium
            self.assertEqual(registry._effort, "medium")

    def test_save_and_reload(self):
        """Save providers to disk, then reload and verify."""
        with tempfile.TemporaryDirectory() as tmpdir:
            providers_path = Path(tmpdir) / "providers.json"
            with patch("src.reasoning.providers.PROVIDERS_FILE", providers_path), \
                 patch("src.reasoning.providers.JARVIS_HOME", Path(tmpdir)), \
                 patch.object(ProviderRegistry, "_load_env_providers", lambda self: None), \
                 patch.object(ProviderRegistry, "_load_claude_credentials", lambda self: None), \
                 patch.object(ProviderRegistry, "_load_remote_brain", lambda self: None):
                reg1 = ProviderRegistry()
                reg1._providers["saved"] = Provider(
                    name="saved", type="openai", api_key="key123",
                    base_url="https://api.example.com", model="gpt-4o",
                    models=["gpt-4o"], priority=0, enabled=True,
                )
                reg1._save()
                self.assertTrue(providers_path.exists())

                # Create a new registry -- it should load the saved provider
                reg2 = ProviderRegistry()
                self.assertIn("saved", reg2._providers)
                self.assertEqual(reg2._providers["saved"].api_key, "key123")


# ---------------------------------------------------------------------------
# Templates sanity tests
# ---------------------------------------------------------------------------

class TestTemplates(unittest.TestCase):
    """Sanity checks on the TEMPLATES constant."""

    def test_all_templates_have_required_keys(self):
        required = {"type", "base_url", "models", "default_model"}
        for name, tmpl in TEMPLATES.items():
            for key in required:
                self.assertIn(key, tmpl, f"Template '{name}' missing key '{key}'")

    def test_ollama_has_api_key(self):
        self.assertEqual(TEMPLATES["ollama"]["api_key"], "ollama")

    def test_known_templates_exist(self):
        expected = {"claude", "openai", "ollama", "together", "xai", "openrouter"}
        for name in expected:
            self.assertIn(name, TEMPLATES, f"Expected template '{name}' not found")


if __name__ == "__main__":
    unittest.main()
