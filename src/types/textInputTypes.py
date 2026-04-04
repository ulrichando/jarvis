"""Text input type definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Union


@dataclass
class InlineGhostText:
    """Inline ghost text for mid-input command autocomplete."""
    text: str
    full_command: str
    insert_position: int


@dataclass
class BaseTextInputProps:
    """Base props for text input components."""
    value: str = ""
    columns: int = 80
    cursor_offset: int = 0
    placeholder: Optional[str] = None
    multiline: bool = True
    focus: bool = True
    mask: Optional[str] = None
    show_cursor: bool = True
    highlight_pasted_text: bool = False
    dim_color: bool = False
    max_visible_lines: Optional[int] = None
    argument_hint: Optional[str] = None
    disable_cursor_movement_for_up_down_keys: bool = False
    disable_escape_double_press: bool = False
    inline_ghost_text: Optional[InlineGhostText] = None

    # Callbacks (set to None by default; assigned at runtime)
    on_change: Optional[Callable[[str], None]] = None
    on_submit: Optional[Callable[[str], None]] = None
    on_exit: Optional[Callable[[], None]] = None
    on_exit_message: Optional[Callable[[bool, Optional[str]], None]] = None
    on_history_up: Optional[Callable[[], None]] = None
    on_history_down: Optional[Callable[[], None]] = None
    on_history_reset: Optional[Callable[[], None]] = None
    on_clear_input: Optional[Callable[[], None]] = None
    on_image_paste: Optional[Callable[..., None]] = None
    on_paste: Optional[Callable[[str], None]] = None
    on_is_pasting_change: Optional[Callable[[bool], None]] = None
    on_change_cursor_offset: Optional[Callable[[int], None]] = None
    on_undo: Optional[Callable[[], None]] = None


VimMode = Literal["INSERT", "NORMAL"]


@dataclass
class VimTextInputProps(BaseTextInputProps):
    """Extended props for VimTextInput."""
    initial_mode: VimMode = "INSERT"
    on_mode_change: Optional[Callable[[VimMode], None]] = None


@dataclass
class BaseInputState:
    """Common properties for input hook results."""
    rendered_value: str = ""
    offset: int = 0
    cursor_line: int = 0
    cursor_column: int = 0
    viewport_char_offset: int = 0
    viewport_char_end: int = 0
    is_pasting: bool = False

    on_input: Optional[Callable[[str, Any], None]] = None
    set_offset: Optional[Callable[[int], None]] = None


@dataclass
class TextInputState(BaseInputState):
    """State for text input."""
    pass


@dataclass
class VimInputState(BaseInputState):
    """State for vim input with mode."""
    mode: VimMode = "INSERT"
    set_mode: Optional[Callable[[VimMode], None]] = None


PromptInputMode = Literal["bash", "prompt", "orphaned-permission", "task-notification"]

EditablePromptInputMode = Literal["bash", "prompt", "orphaned-permission"]

QueuePriority = Literal["now", "next", "later"]


@dataclass
class PastedContent:
    """Pasted content (text or image)."""
    id: int
    type: Literal["text", "image"]
    content: str
    media_type: Optional[str] = None
    filename: Optional[str] = None


@dataclass
class QueuedCommand:
    """Queued command type."""
    value: Union[str, list]
    mode: PromptInputMode = "prompt"
    priority: Optional[QueuePriority] = None
    uuid: Optional[str] = None
    orphaned_permission: Optional[Any] = None
    pasted_contents: Optional[Dict[int, PastedContent]] = None
    pre_expansion_value: Optional[str] = None
    skip_slash_commands: bool = False
    bridge_origin: bool = False
    is_meta: bool = False
    origin: Optional[str] = None
    workload: Optional[str] = None
    agent_id: Optional[str] = None


def is_valid_image_paste(c: PastedContent) -> bool:
    """Type guard for image PastedContent with non-empty data."""
    return c.type == "image" and len(c.content) > 0


def get_image_paste_ids(
    pasted_contents: Optional[Dict[int, PastedContent]],
) -> Optional[List[int]]:
    """Extract image paste IDs from pasted contents."""
    if not pasted_contents:
        return None
    ids = [c.id for c in pasted_contents.values() if is_valid_image_paste(c)]
    return ids if ids else None


@dataclass
class OrphanedPermission:
    permission_result: Any  # PermissionResult
    assistant_message: Any  # AssistantMessage
