"""Clarify tool — ask the user a question before proceeding.

Voice-agent adaptation of the upstream interactive clarify pattern.
In a CLI you'd render a menu; in a voice session the supervisor just
speaks the question and waits for the user's reply over the normal
turn loop. Accordingly this tool:

  * validates the question and optional choice list,
  * returns a structured JSON with question + choices,
  * lets the voice supervisor speak the question and collect the
    user's verbal reply through the existing turn machinery.

No callback / ACP gateway is needed — the supervisor LLM handles the
dialogue turn. If the user answers, the next user turn is the answer;
the supervisor correlates it.

Upstream simplification: the CLI's arrow-key navigable menu and the
messaging-platform numbered-list path are both removed. JARVIS's
voice turn loop IS the interaction channel.
"""
from __future__ import annotations

import json
from typing import List, Optional

from .registry import registry, tool_error

# Max choices the supervisor can offer. A "none of the above / say
# anything" option is implicitly always available in voice mode since
# the user can speak any answer.
MAX_CHOICES = 4


def _handle_clarify(args: dict) -> str:
    """Handler for the clarify tool.

    Returns a JSON string with ``question`` and (optionally) ``choices``.
    The voice supervisor reads the question aloud; the user's next verbal
    turn is treated as the answer.
    """
    question: str = args.get("question", "")
    choices = args.get("choices")

    if not question or not question.strip():
        return tool_error("clarify: question text is required.")

    question = question.strip()

    # Validate and trim choices.
    if choices is not None:
        if not isinstance(choices, list):
            return tool_error("clarify: choices must be a list of strings.")
        choices = [str(c).strip() for c in choices if str(c).strip()]
        if len(choices) > MAX_CHOICES:
            choices = choices[:MAX_CHOICES]
        if not choices:
            choices = None  # empty list → open-ended

    result: dict = {"question": question}
    if choices:
        result["choices"] = choices
        result["voice_prompt"] = (
            question
            + " You can say: "
            + ", ".join(f'"{c}"' for c in choices)
            + ". Or say anything else."
        )
    else:
        result["voice_prompt"] = question

    return json.dumps(result, ensure_ascii=False)


_SCHEMA = {
    "name": "clarify",
    "description": (
        "Ask the user a question when you need clarification, feedback, or a "
        "decision before proceeding. Supports two modes:\n\n"
        "1. **Multiple choice** — provide up to 4 choices. The user can say one "
        "or give any other verbal answer.\n"
        "2. **Open-ended** — omit choices entirely. The user speaks a free-form "
        "response.\n\n"
        "Use this tool when:\n"
        "- The task is ambiguous and you need the user to choose an approach\n"
        "- You want post-task feedback\n"
        "- A decision has meaningful trade-offs the user should weigh in on\n\n"
        "Do NOT use for simple yes/no confirmation of dangerous commands — those "
        "go through the terminal tool. Prefer making a reasonable default choice "
        "yourself when the decision is low-stakes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to present to the user.",
            },
            "choices": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": MAX_CHOICES,
                "description": (
                    "Up to 4 answer choices. Omit this parameter entirely to ask "
                    "an open-ended question. In voice mode the choices are read "
                    "aloud and the user can speak any option or say something else."
                ),
            },
        },
        "required": ["question"],
    },
}

registry.register(
    name="clarify",
    schema=_SCHEMA,
    handler=_handle_clarify,
    toolset="clarify",
    check_fn=None,   # always available — no external deps
    is_async=False,
    emoji="❓",
)
