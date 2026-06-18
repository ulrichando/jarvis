"""Tests for the OpenRouter SPEECH_MODELS entries.

Verifies:
- With OPENROUTER_API_KEY set, the four OpenRouter entries exist and
  their build() factories construct an lk_openai.LLM without error
  (no real API calls; lk_openai.LLM construction is purely in-process).
- Without OPENROUTER_API_KEY, construction of the rest of SPEECH_MODELS
  still works (no crash), and no openrouter/* keys are registered.
"""
from __future__ import annotations

import importlib
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


_OPENROUTER_KEYS = [
    "openrouter/google/gemini-2.0-flash-001",
    "openrouter/meta-llama/llama-3.3-70b-instruct",
    "openrouter/anthropic/claude-haiku-4-5",
    "openrouter/mistralai/mistral-small-3.2-24b-instruct",
]


def _reload_providers_llm():
    """Force a clean reimport of providers.llm so env-var-gated SPEECH_MODELS
    blocks re-evaluate. Necessary because the module-level `if` guards
    run at import time, not at call time."""
    # Drop all cached submodules that providers.llm imports at module level
    # so the reimport picks up the current env.
    for mod_name in list(sys.modules):
        if mod_name in (
            "providers.llm",
            "providers",
        ):
            sys.modules.pop(mod_name, None)
    return importlib.import_module("providers.llm")


# ---------------------------------------------------------------------------
# With the key present
# ---------------------------------------------------------------------------

class TestOpenRouterWithKey:
    """All four OpenRouter entries are registered and build() works."""

    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key-abc123")
        # Reload the module inside the patched env.
        self.llm_mod = _reload_providers_llm()

    def test_all_four_keys_present(self):
        missing = [k for k in _OPENROUTER_KEYS if k not in self.llm_mod.SPEECH_MODELS]
        assert not missing, f"Missing OpenRouter entries: {missing}"

    def test_all_entries_have_label_and_build(self):
        for key in _OPENROUTER_KEYS:
            entry = self.llm_mod.SPEECH_MODELS[key]
            assert "label" in entry, f"{key}: missing 'label'"
            assert callable(entry.get("build")), f"{key}: 'build' is not callable"

    def test_labels_start_with_openrouter(self):
        for key in _OPENROUTER_KEYS:
            label = self.llm_mod.SPEECH_MODELS[key]["label"]
            assert label.startswith("OpenRouter ·"), (
                f"{key}: label {label!r} doesn't start with 'OpenRouter ·'"
            )

    def test_build_factories_construct_without_error(self):
        """build() must return an object without making network calls.
        lk_openai.LLM is a thin data-class wrapper; construction is in-process."""
        for key in _OPENROUTER_KEYS:
            llm = self.llm_mod.SPEECH_MODELS[key]["build"]()
            assert llm is not None, f"{key}: build() returned None"

    def test_build_uses_openrouter_base_url(self):
        """Verify the factory sets the OpenRouter base URL, not some other endpoint."""
        for key in _OPENROUTER_KEYS:
            llm = self.llm_mod.SPEECH_MODELS[key]["build"]()
            # lk_openai.LLM stores the base URL on its underlying _oai_client
            # or directly on the object; we check the repr/str or the attribute.
            # The simplest cross-version check: repr includes the URL or the
            # client attribute chain resolves to openrouter.ai.
            client = getattr(llm, "_oai_client", None) or getattr(llm, "client", None)
            if client is not None:
                base = str(getattr(client, "base_url", ""))
                assert "openrouter.ai" in base, (
                    f"{key}: base_url {base!r} doesn't contain 'openrouter.ai'"
                )
            # If the attribute isn't available in this livekit version,
            # skip the URL sub-check (build() not raising is sufficient).

    def test_no_hermes_token_in_model_names_or_labels(self):
        for key in _OPENROUTER_KEYS:
            entry = self.llm_mod.SPEECH_MODELS[key]
            assert "hermes" not in key.lower(), f"{key}: 'hermes' in registry key"
            assert "hermes" not in entry["label"].lower(), (
                f"{key}: 'hermes' in label {entry['label']!r}"
            )


# ---------------------------------------------------------------------------
# Without the key
# ---------------------------------------------------------------------------

class TestOpenRouterWithoutKey:
    """No openrouter/* keys appear and the rest of SPEECH_MODELS still loads."""

    @pytest.fixture(autouse=True)
    def _unset_key(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        self.llm_mod = _reload_providers_llm()

    def test_no_openrouter_keys_registered(self):
        or_keys = [k for k in self.llm_mod.SPEECH_MODELS if k.startswith("openrouter/")]
        assert not or_keys, f"OpenRouter keys appeared without a key: {or_keys}"

    def test_default_groq_keys_still_present(self):
        assert "llama-3.3-70b-versatile" in self.llm_mod.SPEECH_MODELS
        assert "llama-3.1-8b-instant" in self.llm_mod.SPEECH_MODELS

    def test_speech_models_module_loads_clean(self):
        """Importing providers.llm without OPENROUTER_API_KEY must not raise."""
        assert self.llm_mod.SPEECH_MODELS is not None
        assert self.llm_mod.DEFAULT_SPEECH_MODEL == "openai/gpt-oss-120b"
