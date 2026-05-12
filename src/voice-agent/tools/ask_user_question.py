"""Ask the user a structured multiple-choice question.

Voice-adapted port of claude-code's `AskUserQuestionTool`. The
canonical claude-code variant returns the user's answer directly in
the tool result — its host TUI prompts + captures before handing
control back to the LLM. Voice JARVIS can't do that without
hijacking the LiveKit turn cycle, so this tool is structured as a
formatter + prompt-side discipline:

  1. Supervisor calls `ask_user_question(question, options_json, ...)`.
  2. Tool validates the shape (2-4 options, non-empty + ?-ending
     question, header ≤12 chars) and returns a voice-friendly string
     the supervisor SHOULD speak verbatim.
  3. The supervisor's prompt (`prompts/supervisor.md` — TASK TRACKING
     section's neighbour) teaches it to STOP after voicing — wait
     for the next user turn — then match the reply against the
     option labels by number or substring.

The tool's value over freeform asking:

  - **Forces enumeration** of explicit options instead of vague
    open questions ("did you mean X, Y, or Z?").
  - **Caps choices at 4** so the user can hold them in working
    memory during voice.
  - **Standard phrasing** ("option one X; option two Y. Pick one")
    is easier for the user to answer concisely with "two" or
    "magic link" instead of having to repeat the whole label.
  - **Lands in chat_ctx** as a tool-call shape, so the supervisor's
    next turn has the canonical labels to match against — eliminates
    drift between what was asked and what the labels actually were.

Not implemented (claude-code has these, voice doesn't yet need them):

  - `preview` per option (markdown/HTML mockup — voice can't render).
  - Free-form "Other" answer (the supervisor handles fallback if no
    option matches; no separate Other slot needed).
  - Multi-question batching (1-4 questions per call in claude-code).
    Voice asks one at a time; multi-question batches stack into
    cognitive overload.
"""
from __future__ import annotations

import json
import logging

from livekit.agents.llm import function_tool


__all__ = ["ask_user_question"]


_logger = logging.getLogger("jarvis.tools.ask_user_question")


_MIN_OPTIONS = 2
_MAX_OPTIONS = 4
_MAX_HEADER_CHARS = 12

# Number-words the supervisor's regex-match against the user's reply
# can use on the next turn. Kept here as the canonical set so the
# tool's spoken output and the supervisor's prompt agree.
NUMBER_WORDS = ("one", "two", "three", "four")


def _spoken_options(options: list[str]) -> str:
    """Render the option list as a voice-friendly sentence:
    'option one, JWT; option two, sessions; option three, magic link'.
    """
    parts: list[str] = []
    for i, opt in enumerate(options):
        if i < len(NUMBER_WORDS):
            parts.append(f"option {NUMBER_WORDS[i]}, {opt}")
        else:
            parts.append(f"option {i + 1}, {opt}")
    return "; ".join(parts)


@function_tool
async def ask_user_question(
    question: str,
    options_json: str,
    header: str = "",
    multi_select: bool = False,
) -> str:
    """Ask the user a structured multiple-choice question.

    Use this BEFORE committing to one interpretation of an ambiguous
    request — when the user's intent could mean two or three
    different things, surface the choices instead of guessing.

    Use it ALSO before destructive or irreversible actions when the
    parameters aren't fully constrained ("which tab to close: the
    Gmail one or the Twitter one?").

    Do NOT use it for:
      - Trivial yes/no during continuous conversation (just ask in
        prose).
      - Anything where one option is overwhelmingly correct (just do
        it; the user will correct if wrong).
      - Open-ended creative input ("what should we name this?" — let
        them say anything).

    Args:
        question:     The question, ending in '?'. Voice will speak
                      this verbatim.
        options_json: JSON array of 2-4 option label strings.
                      e.g. `["JWT", "Sessions", "Magic link"]`.
        header:       Optional short label (≤12 chars) for any UI
                      tray that surfaces the question. Empty default.
        multi_select: True if the user can pick multiple options.
                      Default False (single-pick).

    Returns:
        A voice-friendly string the supervisor must speak verbatim,
        then STOP. The supervisor's PROMPT teaches the matching step
        on the next user turn.
    """
    q = (question or "").strip()
    if not q:
        return "Question must be non-empty."
    if not q.endswith("?"):
        return "Question must end with '?'. Rephrase as a question."

    try:
        opts_raw = json.loads(options_json)
    except json.JSONDecodeError as e:
        return f"Bad options_json: {e}. Pass a JSON array of strings."
    if not isinstance(opts_raw, list):
        return "options_json must be a JSON array (top-level)."

    opts: list[str] = []
    for i, item in enumerate(opts_raw):
        if not isinstance(item, str):
            return f"Option {i} is not a string. Each option must be a label string."
        cleaned = item.strip()
        if not cleaned:
            return f"Option {i} is empty. Drop it or fill it."
        opts.append(cleaned)

    if len(opts) < _MIN_OPTIONS:
        return (
            f"Need at least {_MIN_OPTIONS} options for a multiple-choice ask "
            f"(got {len(opts)}). For a binary yes/no, just ask in prose."
        )
    if len(opts) > _MAX_OPTIONS:
        return (
            f"Cap at {_MAX_OPTIONS} options — more than that and the user can't "
            f"hold them in voice memory (got {len(opts)}). Group or rephrase."
        )

    h = (header or "").strip()
    if len(h) > _MAX_HEADER_CHARS:
        return (
            f"Header too long: {len(h)} chars, max {_MAX_HEADER_CHARS}. "
            f"Trim or omit."
        )

    spoken = _spoken_options(opts)
    if multi_select:
        instruction = "Pick one or more — say the numbers or option names."
    else:
        instruction = "Pick one — say the number or the option name."

    _logger.info(
        f"[ask] header={h!r} multi={multi_select} options={opts} q={q[:60]!r}"
    )

    return f"{q} {spoken}. {instruction}"
