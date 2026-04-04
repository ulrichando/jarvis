"""
Todo item types for task management.
"""

from dataclasses import dataclass
from typing import List, Literal

TodoStatus = Literal["pending", "in_progress", "completed"]


@dataclass
class TodoItem:
    content: str
    status: TodoStatus
    active_form: str  # present continuous form, e.g. "Running tests"

    def __post_init__(self):
        if not self.content:
            raise ValueError("Content cannot be empty")
        if not self.active_form:
            raise ValueError("Active form cannot be empty")


TodoList = List[TodoItem]
