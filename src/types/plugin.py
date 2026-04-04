"""Plugin type definitions and error handling."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Union


@dataclass
class PluginAuthor:
    name: str
    url: Optional[str] = None


@dataclass
class CommandMetadata:
    name: str
    description: Optional[str] = None


@dataclass
class PluginManifest:
    name: str
    description: str
    version: Optional[str] = None
    author: Optional[PluginAuthor] = None


@dataclass
class BundledSkillDefinition:
    name: str
    description: str
    prompt: str


@dataclass
class BuiltinPluginDefinition:
    """Definition for a built-in plugin that ships with the CLI."""
    name: str
    description: str
    version: Optional[str] = None
    skills: Optional[List[BundledSkillDefinition]] = None
    hooks: Optional[Dict[str, Any]] = None
    mcp_servers: Optional[Dict[str, Any]] = None
    is_available: Optional[Callable[[], bool]] = None
    default_enabled: bool = True


@dataclass
class PluginRepository:
    url: str
    branch: str
    last_updated: Optional[str] = None
    commit_sha: Optional[str] = None


@dataclass
class PluginConfig:
    repositories: Dict[str, PluginRepository] = field(default_factory=dict)


@dataclass
class LoadedPlugin:
    name: str
    manifest: PluginManifest
    path: str
    source: str
    repository: str
    enabled: Optional[bool] = None
    is_builtin: bool = False
    sha: Optional[str] = None
    commands_path: Optional[str] = None
    commands_paths: Optional[List[str]] = None
    commands_metadata: Optional[Dict[str, CommandMetadata]] = None
    agents_path: Optional[str] = None
    agents_paths: Optional[List[str]] = None
    skills_path: Optional[str] = None
    skills_paths: Optional[List[str]] = None
    output_styles_path: Optional[str] = None
    output_styles_paths: Optional[List[str]] = None
    hooks_config: Optional[Dict[str, Any]] = None
    mcp_servers: Optional[Dict[str, Any]] = None
    lsp_servers: Optional[Dict[str, Any]] = None
    settings: Optional[Dict[str, Any]] = None


PluginComponent = Literal[
    "commands", "agents", "skills", "hooks", "output-styles"
]


@dataclass
class PluginErrorPathNotFound:
    type: Literal["path-not-found"] = "path-not-found"
    source: str = ""
    plugin: Optional[str] = None
    path: str = ""
    component: str = ""


@dataclass
class PluginErrorGitAuthFailed:
    type: Literal["git-auth-failed"] = "git-auth-failed"
    source: str = ""
    plugin: Optional[str] = None
    git_url: str = ""
    auth_type: Literal["ssh", "https"] = "ssh"


@dataclass
class PluginErrorGitTimeout:
    type: Literal["git-timeout"] = "git-timeout"
    source: str = ""
    plugin: Optional[str] = None
    git_url: str = ""
    operation: Literal["clone", "pull"] = "clone"


@dataclass
class PluginErrorNetworkError:
    type: Literal["network-error"] = "network-error"
    source: str = ""
    plugin: Optional[str] = None
    url: str = ""
    details: Optional[str] = None


@dataclass
class PluginErrorManifestParse:
    type: Literal["manifest-parse-error"] = "manifest-parse-error"
    source: str = ""
    plugin: Optional[str] = None
    manifest_path: str = ""
    parse_error: str = ""


@dataclass
class PluginErrorManifestValidation:
    type: Literal["manifest-validation-error"] = "manifest-validation-error"
    source: str = ""
    plugin: Optional[str] = None
    manifest_path: str = ""
    validation_errors: List[str] = field(default_factory=list)


@dataclass
class PluginErrorNotFound:
    type: Literal["plugin-not-found"] = "plugin-not-found"
    source: str = ""
    plugin_id: str = ""
    marketplace: str = ""


@dataclass
class PluginErrorGeneric:
    type: Literal["generic-error"] = "generic-error"
    source: str = ""
    plugin: Optional[str] = None
    error: str = ""


# Union of all plugin error types
PluginError = Union[
    PluginErrorPathNotFound,
    PluginErrorGitAuthFailed,
    PluginErrorGitTimeout,
    PluginErrorNetworkError,
    PluginErrorManifestParse,
    PluginErrorManifestValidation,
    PluginErrorNotFound,
    PluginErrorGeneric,
]


@dataclass
class PluginLoadResult:
    enabled: List[LoadedPlugin] = field(default_factory=list)
    disabled: List[LoadedPlugin] = field(default_factory=list)
    errors: List[PluginError] = field(default_factory=list)


def get_plugin_error_message(error: PluginError) -> str:
    """Get a display message from any PluginError."""
    if isinstance(error, PluginErrorGeneric):
        return error.error
    elif isinstance(error, PluginErrorPathNotFound):
        return f"Path not found: {error.path} ({error.component})"
    elif isinstance(error, PluginErrorGitAuthFailed):
        return f"Git authentication failed ({error.auth_type}): {error.git_url}"
    elif isinstance(error, PluginErrorGitTimeout):
        return f"Git {error.operation} timeout: {error.git_url}"
    elif isinstance(error, PluginErrorNetworkError):
        details = f" - {error.details}" if error.details else ""
        return f"Network error: {error.url}{details}"
    elif isinstance(error, PluginErrorManifestParse):
        return f"Manifest parse error: {error.parse_error}"
    elif isinstance(error, PluginErrorManifestValidation):
        return f"Manifest validation failed: {', '.join(error.validation_errors)}"
    elif isinstance(error, PluginErrorNotFound):
        return f"Plugin {error.plugin_id} not found in marketplace {error.marketplace}"
    return "Unknown plugin error"
