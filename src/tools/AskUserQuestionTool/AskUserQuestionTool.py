"""AskUserQuestionTool -- asks user multiple choice questions."""
from __future__ import annotations
from typing import Any
from src.tools.AskUserQuestionTool.prompt import ASK_USER_QUESTION_TOOL_NAME


async def execute_ask_user(question: str, options: list[str], **kwargs: Any) -> dict[str, Any]:
    """Ask the user a question. Stub."""
    raise NotImplementedError("AskUserQuestionTool requires shell integration")
