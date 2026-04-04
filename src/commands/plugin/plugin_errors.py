"""Plugin error types and handling."""

from __future__ import annotations


class PluginError(Exception):
    """Base class for plugin errors."""
    pass


class PluginNotFoundError(PluginError):
    """Plugin was not found."""
    def __init__(self, plugin_name: str) -> None:
        self.plugin_name = plugin_name
        super().__init__(f"Plugin '{plugin_name}' not found.")


class PluginInstallError(PluginError):
    """Error during plugin installation."""
    def __init__(self, plugin_name: str, reason: str) -> None:
        self.plugin_name = plugin_name
        self.reason = reason
        super().__init__(f"Failed to install '{plugin_name}': {reason}")


class PluginTrustError(PluginError):
    """Plugin trust verification failed."""
    def __init__(self, plugin_name: str) -> None:
        self.plugin_name = plugin_name
        super().__init__(f"Plugin '{plugin_name}' is not trusted.")
