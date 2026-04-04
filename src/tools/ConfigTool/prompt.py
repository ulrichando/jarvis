"""Prompt for the ConfigTool."""
from __future__ import annotations

DESCRIPTION = "Get or set JARVIS configuration settings."


def generate_prompt() -> str:
    """Generate the prompt documentation for the Config tool."""
    return """Get or set JARVIS configuration settings.

  View or change JARVIS settings. Use when the user requests configuration changes, asks about current settings, or when adjusting a setting would benefit them.


## Usage
- **Get current value:** Omit the "value" parameter
- **Set new value:** Include the "value" parameter

## Configurable settings list
The following settings are available for you to change:

### Global Settings (stored in ~/.claude.json)
- theme: "dark", "light", "light-daltonized", "dark-daltonized" - Visual theme for the interface

### Project Settings (stored in settings.json)
- verbose: true/false - Show detailed output
- editorMode: "normal", "vim", "emacs" - Editor key bindings

## Model
- model - Override the default model (sonnet, opus, haiku, best, or full model ID)

## Examples
- Get theme: { "setting": "theme" }
- Set dark theme: { "setting": "theme", "value": "dark" }
- Enable vim mode: { "setting": "editorMode", "value": "vim" }
- Enable verbose: { "setting": "verbose", "value": true }
- Change model: { "setting": "model", "value": "opus" }
- Change permission mode: { "setting": "permissions.defaultMode", "value": "plan" }
"""
