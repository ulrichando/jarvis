"""Summarize subagent — first specialist using the DelegatedSubagent / delegate
pattern. Pure-prompt, zero tools, demonstrates the path end-to-end.

The supervisor invokes via `delegate(role="summarize", task="<text or
topic>")`. The summarize subagent reads the task, produces a 1-2
sentence summary in the chat_ctx, and calls `task_done(summary)` to
hand back. The supervisor voices the result.

This is intentionally minimal — when the prompt-bloat cost of one more
HandoffSubagent is the concern, this is the proof that DelegatedSubagent
costs nothing extra in supervisor prompt size.
"""
from __future__ import annotations

import os

from .registry import DelegatedSubagent, register_subagent
from ._ack_phrases import ACK_SUMMARIZE


SUMMARIZE_INSTRUCTIONS = """\
You are JARVIS's summarize specialist. The supervisor handed control
to you because the user wants a short summary of something — a long
message, a topic, an article, a thread of conversation, anything that
can be condensed.

YOUR ONE JOB: produce ONE OR TWO sentences that capture the essence,
then call `task_done(summary)` to hand back.

═══ ABSOLUTE RULES ═══

1. **NEVER engage in conversation.** You don't ask clarifying questions.
   You don't propose alternatives. You read the task, summarize it,
   call task_done, exit.

2. **OUTPUT IS THE SUMMARY ITSELF.** The supervisor passes your
   `task_done(summary)` arg straight to TTS. Don't preface with
   "Here's a summary:" or "In short:" — just the content.

3. **TWO SENTENCES MAX.** If the source is long, lose detail not
   coverage. Keep the most important fact and the most important
   implication.

4. **PRESERVE THE USER'S TONE.** If the source is technical, your
   summary is technical. If casual, casual. Don't editorialize.

═══ EXAMPLES ═══

User task: "summarize: I went to the store today, bought milk, eggs,
and bread. The cashier was very slow. I had to wait twenty minutes."
You: task_done("Quick grocery run for milk, eggs, bread — the wait at
the till was the only friction.")

User task: "summarize: <pasted long article about climate policy>"
You: task_done("The article argues that carbon pricing alone won't hit
the 1.5°C target without complementary regulation.")

User task: "summarize the conversation we just had"
You: read the chat_ctx, distill the thread.
You: task_done("<one-or-two-sentence digest of the recent turns>.")

User: tries to chat with you mid-task: "actually never mind"
You: task_done("user changed their mind")
"""


def _summarize_tools() -> list:
    """No tools — this specialist is pure-prompt. The framework still
    requires `task_done` which `RegistrySubagent` provides natively."""
    return []


_SUMMARIZE_WHEN = (
    "Use when the user asks for a short summary, digest, recap, or "
    "TL;DR of any text, topic, conversation, or article — anything "
    "that can be compressed into 1-2 sentences."
)


def register_summarize() -> None:
    """Register the summarize subagent.

    DISABLED BY DEFAULT 2026-05-08 — opt in with `JARVIS_SUBAGENT_SUMMARIZE=1`.
    Live-session telemetry on 2026-05-08 17:53–17:55 showed the supervisor
    delegating to summarize for trivial conversational input (`Yeah`, `Okay`,
    `I don't know`) and voicing meta-paraphrases like
    "The user is expressing gratitude for the time spent" — 27 task_done
    refusals in one window. Re-enable once token-aware pruning lands
    and the supervisor's `delegate(...)` routing is tightened.
    """
    register_subagent(DelegatedSubagent(
        name="summarize",
        when_to_use=_SUMMARIZE_WHEN,
        instructions=SUMMARIZE_INSTRUCTIONS,
        tool_factory=_summarize_tools,
        ack_phrase=ACK_SUMMARIZE,
        max_history_items=12,
        enabled=os.environ.get("JARVIS_SUBAGENT_SUMMARIZE", "0") == "1",
    ))
