"""Keybinding schema definitions and validation."""

from __future__ import annotations

from typing import Literal

KEYBINDING_CONTEXTS = [
    "Global", "Chat", "Autocomplete", "Confirmation", "Help",
    "Transcript", "HistorySearch", "Task", "ThemePicker", "Settings",
    "Tabs", "Attachments", "Footer", "MessageSelector", "DiffDialog",
    "ModelPicker", "Select", "Plugin",
]

KEYBINDING_CONTEXT_DESCRIPTIONS: dict[str, str] = {
    "Global": "Active everywhere, regardless of focus",
    "Chat": "When the chat input is focused",
    "Autocomplete": "When autocomplete menu is visible",
    "Confirmation": "When a confirmation/permission dialog is shown",
    "Help": "When the help overlay is open",
    "Transcript": "When viewing the transcript",
    "HistorySearch": "When searching command history (ctrl+r)",
    "Task": "When a task/agent is running in the foreground",
    "ThemePicker": "When the theme picker is open",
    "Settings": "When the settings menu is open",
    "Tabs": "When the tab switcher is open",
    "Attachments": "When the attachments menu is open",
    "Footer": "When the footer area is focused",
    "MessageSelector": "When the message selector is active",
    "DiffDialog": "When viewing a diff dialog",
    "ModelPicker": "When the model picker is open",
    "Select": "When a select/dropdown is open",
    "Plugin": "When a plugin UI is active",
}
