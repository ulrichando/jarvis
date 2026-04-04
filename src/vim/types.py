"""
Vim Mode State Machine Types

This module defines the complete state machine for vim input handling.
The types ARE the documentation -- reading them tells you how the system works.

State Diagram::

                            VimState
    +------------------------------+--------------------------------------+
    |  INSERT                      |  NORMAL                              |
    |  (tracks inserted_text)      |  (CommandState machine)              |
    |                              |                                      |
    |                              |  idle --+-[d/c/y]---> operator       |
    |                              |         +-[1-9]-----> count          |
    |                              |         +-[fFtT]----> find           |
    |                              |         +-[g]-------> g              |
    |                              |         +-[r]-------> replace        |
    |                              |         +-[><]------> indent         |
    |                              |                                      |
    |                              |  operator -+-[motion]---> execute    |
    |                              |            +-[0-9]------> op_count   |
    |                              |            +-[ia]-------> op_textobj |
    |                              |            +-[fFtT]-----> op_find    |
    +------------------------------+--------------------------------------+

Converted from types.ts to Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Union


# ============================================================================
# Core Types
# ============================================================================

Operator = Literal["delete", "change", "yank"]

FindType = Literal["f", "F", "t", "T"]

TextObjScope = Literal["inner", "around"]


# ============================================================================
# State Machine Types
# ============================================================================


@dataclass
class InsertState:
    """INSERT mode: Track text being typed (for dot-repeat)."""
    mode: Literal["INSERT"] = "INSERT"
    inserted_text: str = ""


@dataclass
class IdleCommand:
    type: Literal["idle"] = "idle"


@dataclass
class CountCommand:
    digits: str
    type: Literal["count"] = "count"


@dataclass
class OperatorCommand:
    op: Operator
    count: int
    type: Literal["operator"] = "operator"


@dataclass
class OperatorCountCommand:
    op: Operator
    count: int
    digits: str
    type: Literal["operatorCount"] = "operatorCount"


@dataclass
class OperatorFindCommand:
    op: Operator
    count: int
    find: FindType
    type: Literal["operatorFind"] = "operatorFind"


@dataclass
class OperatorTextObjCommand:
    op: Operator
    count: int
    scope: TextObjScope
    type: Literal["operatorTextObj"] = "operatorTextObj"


@dataclass
class FindCommand:
    find: FindType
    count: int
    type: Literal["find"] = "find"


@dataclass
class GCommand:
    count: int
    type: Literal["g"] = "g"


@dataclass
class OperatorGCommand:
    op: Operator
    count: int
    type: Literal["operatorG"] = "operatorG"


@dataclass
class ReplaceCommand:
    count: int
    type: Literal["replace"] = "replace"


@dataclass
class IndentCommand:
    dir: Literal[">", "<"]
    count: int
    type: Literal["indent"] = "indent"


CommandState = Union[
    IdleCommand,
    CountCommand,
    OperatorCommand,
    OperatorCountCommand,
    OperatorFindCommand,
    OperatorTextObjCommand,
    FindCommand,
    GCommand,
    OperatorGCommand,
    ReplaceCommand,
    IndentCommand,
]


@dataclass
class NormalState:
    """NORMAL mode: Track command being parsed (state machine)."""
    command: CommandState
    mode: Literal["NORMAL"] = "NORMAL"


VimState = Union[InsertState, NormalState]


# ============================================================================
# Persistent State
# ============================================================================


@dataclass
class InsertChange:
    text: str
    type: Literal["insert"] = "insert"


@dataclass
class OperatorChange:
    op: Operator
    motion: str
    count: int
    type: Literal["operator"] = "operator"


@dataclass
class OperatorTextObjChange:
    op: Operator
    obj_type: str
    scope: TextObjScope
    count: int
    type: Literal["operatorTextObj"] = "operatorTextObj"


@dataclass
class OperatorFindChange:
    op: Operator
    find: FindType
    char: str
    count: int
    type: Literal["operatorFind"] = "operatorFind"


@dataclass
class ReplaceChange:
    char: str
    count: int
    type: Literal["replace"] = "replace"


@dataclass
class XChange:
    count: int
    type: Literal["x"] = "x"


@dataclass
class ToggleCaseChange:
    count: int
    type: Literal["toggleCase"] = "toggleCase"


@dataclass
class IndentChange:
    dir: Literal[">", "<"]
    count: int
    type: Literal["indent"] = "indent"


@dataclass
class OpenLineChange:
    direction: Literal["above", "below"]
    type: Literal["openLine"] = "openLine"


@dataclass
class JoinChange:
    count: int
    type: Literal["join"] = "join"


RecordedChange = Union[
    InsertChange,
    OperatorChange,
    OperatorTextObjChange,
    OperatorFindChange,
    ReplaceChange,
    XChange,
    ToggleCaseChange,
    IndentChange,
    OpenLineChange,
    JoinChange,
]


@dataclass
class LastFind:
    type: FindType
    char: str


@dataclass
class PersistentState:
    """
    Persistent state that survives across commands.
    This is the "memory" of vim -- what gets recalled for repeats and pastes.
    """
    last_change: Optional[RecordedChange] = None
    last_find: Optional[LastFind] = None
    register: str = ""
    register_is_linewise: bool = False


# ============================================================================
# Key Groups - Named constants, no magic strings
# ============================================================================

OPERATORS: dict[str, Operator] = {
    "d": "delete",
    "c": "change",
    "y": "yank",
}


def is_operator_key(key: str) -> bool:
    return key in OPERATORS


SIMPLE_MOTIONS: set[str] = {
    "h", "l", "j", "k",       # Basic movement
    "w", "b", "e", "W", "B", "E",  # Word motions
    "0", "^", "$",             # Line positions
}

FIND_KEYS: set[str] = {"f", "F", "t", "T"}

TEXT_OBJ_SCOPES: dict[str, TextObjScope] = {
    "i": "inner",
    "a": "around",
}


def is_text_obj_scope_key(key: str) -> bool:
    return key in TEXT_OBJ_SCOPES


TEXT_OBJ_TYPES: set[str] = {
    "w", "W",          # Word/WORD
    '"', "'", '`',     # Quotes
    "(", ")", "b",     # Parens
    "[", "]",          # Brackets
    "{", "}", "B",     # Braces
    "<", ">",          # Angle brackets
}

MAX_VIM_COUNT: int = 10000


# ============================================================================
# State Factories
# ============================================================================

def create_initial_vim_state() -> VimState:
    return InsertState(inserted_text="")


def create_initial_persistent_state() -> PersistentState:
    return PersistentState()
