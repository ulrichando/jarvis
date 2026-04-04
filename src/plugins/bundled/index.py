"""
Built-in Plugin Initialization

Initializes built-in plugins that ship with the CLI and appear in the
/plugin UI for users to enable/disable.

Not all bundled features should be built-in plugins -- use this for
features that users should be able to explicitly enable/disable. For
features with complex setup or automatic-enabling logic (e.g.
claude-in-chrome), use src/skills/bundled/ instead.

To add a new built-in plugin:
1. Import register_builtin_plugin from ..builtinPlugins
2. Call register_builtin_plugin() with the plugin definition here
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class BundledSkillDefinition:
    name: str
    description: str
    allowed_tools: Optional[list[str]] = None
    argument_hint: Optional[str] = None
    when_to_use: Optional[str] = None
    model: Optional[str] = None
    disable_model_invocation: bool = False
    user_invocable: bool = True
    hooks: Optional[dict[str, Any]] = None
    context: Optional[str] = None
    agent: Optional[dict[str, Any]] = None
    is_enabled: Optional[Callable[[], bool]] = None
    get_prompt_for_command: Optional[Callable[..., str]] = None


def init_builtin_plugins() -> None:
    """
    Initialize built-in plugins. Called during CLI startup.

    No built-in plugins registered yet -- this is the scaffolding for
    migrating bundled skills that should be user-toggleable.
    """
    pass
