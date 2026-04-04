"""
First-time setup wizard for JARVIS.

Handles interactive first-run
configuration: API keys, model selection, permission mode, theme, and
initial config-file creation.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _jarvis_home() -> Path:
    return Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


PROVIDER_TEMPLATES: dict[str, dict[str, Any]] = {
    "ollama": {
        "type": "ollama",
        "base_url": "http://localhost:11434",
        "models": ["llama3", "mistral", "codellama"],
        "default_model": "llama3",
    },
    "groq": {
        "type": "groq",
        "base_url": "https://api.groq.com/openai/v1",
        "models": ["llama-3.3-70b-versatile", "mixtral-8x7b-32768"],
        "default_model": "llama-3.3-70b-versatile",
        "env_key": "GROQ_API_KEY",
    },
    "openai": {
        "type": "openai",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "o3-mini"],
        "default_model": "gpt-4o",
        "env_key": "OPENAI_API_KEY",
    },
    "anthropic": {
        "type": "anthropic",
        "base_url": "https://api.anthropic.com",
        "models": [
            "claude-opus-4-6",
            "claude-sonnet-4-6",
        ],
        "default_model": "claude-sonnet-4-6",
        "env_key": "ANTHROPIC_API_KEY",
    },
    "xai": {
        "type": "openai",
        "base_url": "https://api.x.ai/v1",
        "models": ["grok-3", "grok-3-mini"],
        "default_model": "grok-3-mini",
        "env_key": "XAI_API_KEY",
    },
    "together": {
        "type": "openai",
        "base_url": "https://api.together.xyz/v1",
        "models": ["meta-llama/Llama-3.3-70B-Instruct-Turbo"],
        "default_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "env_key": "TOGETHER_API_KEY",
    },
    "openrouter": {
        "type": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "models": ["anthropic/claude-sonnet-4", "google/gemini-2.5-pro"],
        "default_model": "anthropic/claude-sonnet-4",
        "env_key": "OPENROUTER_API_KEY",
    },
}

PERMISSION_MODES = {
    "normal": "Ask before running commands or writing files",
    "auto": "Auto-approve safe operations, ask for dangerous ones",
    "yolo": "Skip all permission prompts (use in trusted environments only)",
}

THEMES = ["dark", "light", "auto"]


# ---------------------------------------------------------------------------
# Interactive setup
# ---------------------------------------------------------------------------

def _prompt(msg: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{msg}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        val = ""
    return val or default


def _prompt_choice(msg: str, choices: list[str], default: str = "") -> str:
    for i, c in enumerate(choices, 1):
        print(f"  {i}. {c}")
    raw = _prompt(msg, default)
    # Accept index or literal value
    try:
        idx = int(raw)
        if 1 <= idx <= len(choices):
            return choices[idx - 1]
    except ValueError:
        pass
    if raw in choices:
        return raw
    return default or choices[0]


def _prompt_api_key(provider_name: str, env_key: str | None) -> str | None:
    """Ask for an API key.  Returns None if the user declines."""
    if env_key:
        existing = os.environ.get(env_key)
        if existing:
            masked = existing[:4] + "..." + existing[-4:] if len(existing) > 8 else "***"
            keep = _prompt(
                f"  {env_key} already set ({masked}). Keep it? [Y/n]", "y"
            )
            if keep.lower() in ("y", "yes", ""):
                return existing

    key = _prompt(f"  Enter API key for {provider_name} (or press Enter to skip)")
    return key if key else None


def run_setup() -> dict[str, Any]:
    """Run the interactive first-time setup wizard.  Returns the config dict."""
    home = _jarvis_home()

    print()
    print("=" * 60)
    print("  Welcome to JARVIS - Autonomous AI Assistant")
    print("=" * 60)
    print()
    print("This wizard will configure JARVIS for first use.")
    print()

    config: dict[str, Any] = {}

    # --- 1. API keys -------------------------------------------------------
    print("--- API Key Configuration ---")
    print("JARVIS supports multiple LLM providers. Configure at least one.\n")

    providers: dict[str, dict[str, Any]] = {}
    provider_names = list(PROVIDER_TEMPLATES.keys())

    for pname in provider_names:
        tmpl = PROVIDER_TEMPLATES[pname]
        add = _prompt(f"Configure {pname}? [y/N]", "n")
        if add.lower() not in ("y", "yes"):
            continue

        entry = dict(tmpl)  # shallow copy
        env_key = entry.pop("env_key", None)
        key = _prompt_api_key(pname, env_key)
        if key:
            entry["api_key"] = key
        elif env_key:
            entry["api_key_env"] = env_key

        providers[pname] = entry
        print()

    # Ollama is always available as local fallback
    if "ollama" not in providers:
        providers["ollama"] = dict(PROVIDER_TEMPLATES["ollama"])

    config["providers"] = providers

    # --- 2. Default model --------------------------------------------------
    print("\n--- Default Model ---")
    all_models: list[str] = []
    for p in providers.values():
        all_models.extend(p.get("models", []))
    if not all_models:
        all_models = ["llama3"]
    default_model = _prompt_choice(
        "Select default model",
        all_models,
        default=all_models[0],
    )
    config["default_model"] = default_model

    # --- 3. Permission mode ------------------------------------------------
    print("\n--- Permission Mode ---")
    for mode, desc in PERMISSION_MODES.items():
        print(f"  {mode:8s} - {desc}")
    perm_mode = _prompt("Select permission mode", "normal")
    if perm_mode not in PERMISSION_MODES:
        perm_mode = "normal"
    config["permission_mode"] = perm_mode

    # --- 4. Theme ----------------------------------------------------------
    print("\n--- Theme ---")
    theme = _prompt_choice("Select theme", THEMES, default="dark")
    config["theme"] = theme

    # --- 5. Write config files ---------------------------------------------
    print("\n--- Writing configuration ---")
    _ensure_dir(home)
    _ensure_dir(home / "plugins")
    _ensure_dir(home / "skills")
    _ensure_dir(home / "logs")

    # providers.json
    providers_path = home / "providers.json"
    providers_path.write_text(json.dumps(config["providers"], indent=2) + "\n")
    print(f"  Wrote {providers_path}")

    # config.json (global)
    global_config = {
        "default_model": config["default_model"],
        "permission_mode": config["permission_mode"],
        "theme": config["theme"],
        "has_completed_onboarding": True,
        "setup_version": 1,
    }
    global_path = home / "config.json"
    global_path.write_text(json.dumps(global_config, indent=2) + "\n")
    print(f"  Wrote {global_path}")

    print("\nSetup complete!  Run 'jarvis' to start.\n")
    return config


# ---------------------------------------------------------------------------
# Setup detection
# ---------------------------------------------------------------------------

def check_setup_needed() -> bool:
    """Return True if first-run setup has not been completed."""
    home = _jarvis_home()
    config_path = home / "config.json"
    if not config_path.exists():
        return True
    try:
        data = json.loads(config_path.read_text())
        return not data.get("has_completed_onboarding", False)
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_setup(config: dict[str, Any]) -> list[str]:
    """Validate a setup config dict.  Returns a list of issue strings (empty = OK)."""
    issues: list[str] = []

    providers = config.get("providers")
    if not providers or not isinstance(providers, dict):
        issues.append("No providers configured")
    else:
        for name, pcfg in providers.items():
            if not isinstance(pcfg, dict):
                issues.append(f"Provider '{name}' has invalid config (expected dict)")
                continue
            if "type" not in pcfg:
                issues.append(f"Provider '{name}' missing 'type' field")
            if name != "ollama":
                has_key = bool(pcfg.get("api_key") or pcfg.get("api_key_env"))
                env_key = pcfg.get("api_key_env")
                if env_key and not os.environ.get(env_key):
                    issues.append(
                        f"Provider '{name}': env var {env_key} not set"
                    )
                if not has_key:
                    issues.append(f"Provider '{name}' has no API key configured")

    default_model = config.get("default_model")
    if not default_model:
        issues.append("No default model selected")

    perm = config.get("permission_mode")
    if perm and perm not in PERMISSION_MODES:
        issues.append(f"Unknown permission mode: {perm}")

    theme = config.get("theme")
    if theme and theme not in THEMES:
        issues.append(f"Unknown theme: {theme}")

    return issues
