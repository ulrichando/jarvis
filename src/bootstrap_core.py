"""JARVIS Bootstrap — environment initialization and first-run setup.

Handles config directory creation, environment validation, provider loading,
agent discovery, skill/plugin init, memory init, and config migration.

Brain.__init__ already does runtime init — this module handles the
pre-flight checks and first-run experience that happen BEFORE Brain starts.
"""

import importlib
import json
import logging
import os
import platform
import shutil
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("jarvis.bootstrap")

# ── Defaults ─────────────────────────────────────────────────────────

CONFIG_VERSION = "2"  # Bump when config format changes

DEFAULT_CONFIG_DIR = Path.home() / ".jarvis"
PROJECT_CONFIG_DIR = ".jarvis"

REQUIRED_DIRS = [
    "",               # Root config dir
    "data",
    "logs",
    "plugins",
    "skills",
    "evolved",
    "sessions",
]

OPTIONAL_PACKAGES = {
    "aiohttp": "web server and HTTP tools",
    "rich": "terminal formatting",
    "groq": "Groq API provider",
    "anthropic": "Anthropic API provider",
    "openai": "OpenAI-compatible providers",
    "ollama": "local model backend",
}

API_KEY_VARS = {
    "GROQ_API_KEY": "Groq (free tier available at console.groq.com)",
    "ANTHROPIC_API_KEY": "Anthropic (Claude models)",
    "OPENAI_API_KEY": "OpenAI (GPT models)",
    "XAI_API_KEY": "xAI (Grok models)",
    "TOGETHER_API_KEY": "Together AI",
    "OPENROUTER_API_KEY": "OpenRouter (multi-provider)",
}


# ── Session State (ported from bootstrap/state.ts) ───────────────────


@dataclass
class SessionState:
    """Global session state for JARVIS.

    Tracks runtime metrics, model usage, telemetry counters, and session flags.
    Brain owns an instance; subsystems read/write through it.
    """

    # Identity
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    parent_session_id: str | None = None

    # Paths
    original_cwd: str = field(default_factory=os.getcwd)
    project_root: str = field(default_factory=os.getcwd)
    cwd: str = field(default_factory=os.getcwd)

    # Cost & performance
    total_cost_usd: float = 0.0
    total_api_duration: float = 0.0
    total_tool_duration: float = 0.0
    total_lines_added: int = 0
    total_lines_removed: int = 0
    start_time: float = field(default_factory=lambda: __import__("time").time())
    last_interaction_time: float = field(default_factory=lambda: __import__("time").time())

    # Model
    model_usage: dict[str, dict] = field(default_factory=dict)
    model_override: str | None = None
    initial_model: str = ""

    # Flags
    is_interactive: bool = True
    client_type: str = "cli"         # cli, web, desktop, sdk
    mode: str = "normal"              # normal, agent, plan, berbon
    bypass_permissions: bool = False
    session_trust_accepted: bool = False
    has_exited_plan_mode: bool = False

    # Turn metrics (reset each turn)
    turn_tool_count: int = 0
    turn_hook_count: int = 0
    turn_tool_duration_ms: int = 0
    turn_hook_duration_ms: int = 0

    # Error log (in-memory ring buffer)
    error_log: list[dict] = field(default_factory=list)
    _error_log_max: int = 50

    def log_error(self, error: str) -> None:
        """Append to in-memory error log with timestamp."""
        import time
        entry = {"error": error, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}
        self.error_log.append(entry)
        if len(self.error_log) > self._error_log_max:
            self.error_log = self.error_log[-self._error_log_max:]

    def reset_turn_metrics(self) -> None:
        """Reset per-turn counters at the start of each interaction."""
        self.turn_tool_count = 0
        self.turn_hook_count = 0
        self.turn_tool_duration_ms = 0
        self.turn_hook_duration_ms = 0

    def regenerate_session_id(self, set_parent: bool = False) -> str:
        """Generate a new session ID, optionally preserving lineage."""
        if set_parent:
            self.parent_session_id = self.session_id
        self.session_id = uuid.uuid4().hex
        return self.session_id


# ── Environment Checks ───────────────────────────────────────────────


def check_environment() -> list[str]:
    """Check the runtime environment and return a list of issues.

    Checks:
    - Python version (3.10+)
    - Required directories exist
    - API keys configured
    - Optional packages available

    Returns:
        List of human-readable issue strings. Empty = all good.
    """
    issues: list[str] = []

    # Python version
    if sys.version_info < (3, 10):
        issues.append(
            f"Python 3.10+ required, running {sys.version_info.major}.{sys.version_info.minor}"
        )

    # Config directory
    config_dir = Path(os.environ.get("JARVIS_HOME", DEFAULT_CONFIG_DIR))
    if not config_dir.exists():
        issues.append(f"Config directory missing: {config_dir} (will be created on first run)")

    # API keys
    has_any_key = False
    for var, desc in API_KEY_VARS.items():
        val = os.environ.get(var, "")
        if val:
            has_any_key = True
    if not has_any_key:
        # Check for Ollama as fallback
        ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
        try:
            import urllib.request
            req = urllib.request.Request(f"{ollama_url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=2):
                has_any_key = True  # Ollama is running
        except Exception:
            pass
        if not has_any_key:
            issues.append(
                "No API keys found and Ollama not reachable. "
                "Set at least one of: " + ", ".join(API_KEY_VARS.keys()) +
                " or start Ollama."
            )

    # Optional packages
    missing_packages = []
    for pkg, desc in OPTIONAL_PACKAGES.items():
        try:
            importlib.import_module(pkg)
        except ImportError:
            missing_packages.append(f"{pkg} ({desc})")
    if missing_packages:
        issues.append(f"Optional packages not installed: {', '.join(missing_packages)}")

    return issues


# ── Directory Setup ──────────────────────────────────────────────────


def _ensure_config_dirs(config_dir: Path) -> list[str]:
    """Create config directories if they don't exist. Returns list of created dirs."""
    created = []
    for subdir in REQUIRED_DIRS:
        d = config_dir / subdir if subdir else config_dir
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(str(d))
            log.info("Created directory: %s", d)
    return created


def _ensure_project_config() -> list[str]:
    """Create .jarvis/ in the current project if not present."""
    proj_dir = Path.cwd() / PROJECT_CONFIG_DIR
    created = []
    if not proj_dir.exists():
        proj_dir.mkdir(parents=True, exist_ok=True)
        created.append(str(proj_dir))
        # Create default settings.json
        settings_file = proj_dir / "settings.json"
        if not settings_file.exists():
            settings_file.write_text(json.dumps({
                "version": CONFIG_VERSION,
                "project_name": Path.cwd().name,
            }, indent=2))
            created.append(str(settings_file))
    return created


# ── Provider Discovery ───────────────────────────────────────────────


def _load_providers(config_dir: Path) -> dict:
    """Load provider configuration from providers.json."""
    providers_file = config_dir / "providers.json"
    if not providers_file.exists():
        return {"providers": [], "active": None}
    try:
        data = json.loads(providers_file.read_text())
        log.info("Loaded %d providers from %s", len(data.get("providers", [])), providers_file)
        return data
    except Exception as e:
        log.warning("Failed to load providers.json: %s", e)
        return {"providers": [], "active": None, "error": str(e)}


# ── Skill & Plugin Discovery ────────────────────────────────────────


def _discover_skills(config_dir: Path) -> list[str]:
    """Find skill files in the skills directory."""
    skills_dir = config_dir / "skills"
    if not skills_dir.exists():
        return []
    skills = []
    for f in skills_dir.iterdir():
        if f.suffix in (".md", ".yaml", ".yml") and not f.name.startswith("."):
            skills.append(f.name)
    return skills


def _discover_plugins(config_dir: Path) -> list[str]:
    """Find plugin files in the plugins directory."""
    plugins_dir = config_dir / "plugins"
    if not plugins_dir.exists():
        return []
    plugins = []
    for f in plugins_dir.iterdir():
        if f.suffix == ".py" and not f.name.startswith("_"):
            plugins.append(f.name)
    return plugins


# ── Bootstrap Entry Point ────────────────────────────────────────────


def bootstrap_jarvis(config_dir: str | None = None) -> dict:
    """Initialize the JARVIS environment.

    This is the pre-flight check that runs before Brain.__init__.
    It ensures directories exist, validates the environment, loads
    provider config, discovers skills/plugins, and returns a status report.

    Args:
        config_dir: Override for the config directory path.
                    Defaults to JARVIS_HOME env var or ~/.jarvis/.

    Returns:
        Status dict with keys:
        - config_dir: str, resolved config directory path
        - dirs_created: list[str], directories that were created
        - issues: list[str], environment issues found
        - providers: dict, loaded provider configuration
        - skills: list[str], discovered skill filenames
        - plugins: list[str], discovered plugin filenames
        - version: str, config format version
        - first_run: bool, True if this is a fresh install
    """
    cd = Path(config_dir) if config_dir else Path(
        os.environ.get("JARVIS_HOME", DEFAULT_CONFIG_DIR)
    )

    first_run = not cd.exists()

    # Create directories
    dirs_created = _ensure_config_dirs(cd)
    dirs_created.extend(_ensure_project_config())

    # Environment checks
    issues = check_environment()

    # Load providers
    providers = _load_providers(cd)

    # Discover skills and plugins
    skills = _discover_skills(cd)
    plugins = _discover_plugins(cd)

    # Check config version for migration
    version_file = cd / "version"
    current_version = "0"
    if version_file.exists():
        current_version = version_file.read_text().strip()
    if current_version != CONFIG_VERSION:
        migration_result = migrate_config(current_version, CONFIG_VERSION)
        if migration_result:
            issues.append(f"Config migrated: {migration_result}")
        version_file.write_text(CONFIG_VERSION)

    status = {
        "config_dir": str(cd),
        "dirs_created": dirs_created,
        "issues": issues,
        "providers": providers,
        "skills": skills,
        "plugins": plugins,
        "version": CONFIG_VERSION,
        "first_run": first_run,
    }

    if first_run:
        log.info("First run detected. Config dir: %s", cd)
    else:
        log.info("Bootstrap complete: %d skills, %d plugins, %d issues",
                 len(skills), len(plugins), len(issues))

    return status


# ── First Run Setup ──────────────────────────────────────────────────


def first_run_setup(config_dir: str | None = None) -> None:
    """Interactive first-run configuration.

    Called when bootstrap_jarvis detects a fresh install. Guides the user
    through initial setup: provider selection, API key entry, and basic
    configuration.

    Args:
        config_dir: Override config directory path.
    """
    cd = Path(config_dir) if config_dir else Path(
        os.environ.get("JARVIS_HOME", DEFAULT_CONFIG_DIR)
    )

    print("\n=== JARVIS First Run Setup ===\n")
    print(f"Config directory: {cd}")
    print(f"Python: {sys.version}")
    print(f"Platform: {platform.system()} {platform.machine()}\n")

    # Check for API keys
    found_keys = []
    for var, desc in API_KEY_VARS.items():
        if os.environ.get(var):
            found_keys.append(f"  {var} -> {desc}")

    if found_keys:
        print("API keys detected:")
        for k in found_keys:
            print(k)
    else:
        print("No API keys found in environment.")
        print("You can set them in ~/.jarvis/.env or export them in your shell.")
        print(f"Supported: {', '.join(API_KEY_VARS.keys())}")

    # Check Ollama
    ollama_available = False
    try:
        import urllib.request
        ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
        req = urllib.request.Request(f"{ollama_url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
            models = [m["name"] for m in data.get("models", [])]
            if models:
                print(f"\nOllama running with models: {', '.join(models[:5])}")
                ollama_available = True
            else:
                print("\nOllama running but no models pulled.")
    except Exception:
        print("\nOllama not reachable (optional, for local model fallback).")

    # Create default .env if none exists
    env_file = cd / ".env"
    if not env_file.exists():
        env_file.write_text(
            "# JARVIS API Keys\n"
            "# Uncomment and fill in the keys you want to use:\n\n"
            + "\n".join(f"# {var}=your-key-here" for var in API_KEY_VARS)
            + "\n"
        )
        print(f"\nCreated {env_file} — edit to add your API keys.")

    # Create default providers.json
    providers_file = cd / "providers.json"
    if not providers_file.exists():
        default_providers: dict[str, Any] = {"providers": [], "active": None}
        if ollama_available:
            default_providers["providers"].append({
                "name": "ollama",
                "type": "ollama",
                "url": os.environ.get("OLLAMA_URL", "http://localhost:11434"),
                "model": os.environ.get("JARVIS_LOCAL_MODEL", "qwen2.5:7b"),
            })
            default_providers["active"] = "ollama"
        providers_file.write_text(json.dumps(default_providers, indent=2))
        print(f"Created {providers_file}")

    print("\nSetup complete. Run 'jarvis' to start.\n")


# ── Config Migration ────────────────────────────────────────────────


def migrate_config(old_version: str, new_version: str) -> str:
    """Handle config format changes between versions.

    Args:
        old_version: Current config version string.
        new_version: Target config version string.

    Returns:
        Description of what was migrated, or empty string if nothing needed.
    """
    migrations_applied = []

    config_dir = Path(os.environ.get("JARVIS_HOME", DEFAULT_CONFIG_DIR))

    # v0 -> v1: providers.json format change
    if old_version < "1" and new_version >= "1":
        providers_file = config_dir / "providers.json"
        if providers_file.exists():
            try:
                data = json.loads(providers_file.read_text())
                # v0 had flat list, v1 has { providers: [...], active: ... }
                if isinstance(data, list):
                    new_data = {"providers": data, "active": data[0]["name"] if data else None}
                    providers_file.write_text(json.dumps(new_data, indent=2))
                    migrations_applied.append("providers.json: flat list -> structured format")
            except Exception as e:
                log.warning("Migration v0->v1 failed for providers.json: %s", e)

    # v1 -> v2: add sessions directory, normalize paths
    if old_version < "2" and new_version >= "2":
        sessions_dir = config_dir / "sessions"
        if not sessions_dir.exists():
            sessions_dir.mkdir(parents=True, exist_ok=True)
            migrations_applied.append("created sessions/ directory")

        # Migrate hooks.yaml format if needed
        hooks_file = config_dir / "hooks.yaml"
        if hooks_file.exists():
            try:
                import yaml
                hooks_data = yaml.safe_load(hooks_file.read_text())
                if isinstance(hooks_data, list):
                    # v1 had flat list, v2 wraps in { hooks: [...] }
                    new_hooks = {"hooks": hooks_data}
                    hooks_file.write_text(yaml.dump(new_hooks, default_flow_style=False))
                    migrations_applied.append("hooks.yaml: flat list -> structured format")
            except ImportError:
                pass  # yaml not available, skip
            except Exception as e:
                log.warning("Migration v1->v2 failed for hooks.yaml: %s", e)

    if migrations_applied:
        result = f"v{old_version} -> v{new_version}: " + "; ".join(migrations_applied)
        log.info("Config migration: %s", result)
        return result

    return ""
