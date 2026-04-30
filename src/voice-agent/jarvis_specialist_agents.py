"""Specialist sub-agents for JARVIS — handoff targets from JarvisAgent.

This file is the start of a multi-agent migration. Phase 1: extract
desktop-action work (open apps, screenshot, click, drag) into a
DesktopActionsAgent with a focused 150-line prompt specifically about
TOOL EXECUTION DISCIPLINE.

Why: JarvisAgent's main system prompt is ~1,400 lines (Maya additions +
ROUTE TAGS + SESSION MEMORY + INTERRUPTION HANDLING + ACKNOWLEDGMENT
VOCABULARY + FORBIDDEN PATTERNS + tool docs + learned rules). At that
length the LLM's tool-call discipline degrades — empirically observed
this morning where gpt-oss-120b regressed to plain-text narration
("Since you've asked to open Chrome, I'll try to open it again...")
instead of firing the bash tool. Splitting into specialists with tight
prompts gives each agent focused attention on its narrow job.

LiveKit's handoff pattern: a @function_tool returns
`(NewAgent_instance, "transfer message")` and the framework swaps the
active agent. chat_ctx flows over so the user doesn't repeat themselves.

Pattern in this file:
  JarvisAgent — supervisor / concierge — owns voice loop, conversation,
                memory. When the user wants desktop work, hands off.
  DesktopActionsAgent — does ONE thing: execute desktop tools. When
                done, hands back to JarvisAgent with a one-sentence
                summary.

Future phases:
  Phase 2 — BrowserSpecialistAgent (browser-use, web tasks)
  Phase 3 — PlannerAgent (run_jarvis_cli, multi-step plans)
  Phase 4 — Trim JarvisAgent prompt to a routing-only ~200 lines
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from livekit.agents import Agent, function_tool
from livekit.agents.llm import ChatContext

if TYPE_CHECKING:
    from livekit.agents.voice import RunContext

logger = logging.getLogger("jarvis-agent.specialist")


# Focused desktop-action prompt. Deliberately ~120 lines instead of 1,400.
# The single goal: when the user asks for desktop interaction, fire the
# tool. Period. No narration, no excuses, no "I'll try to".
DESKTOP_INSTRUCTIONS = """\
You are the desktop-action specialist for JARVIS. The supervisor agent
(also named JARVIS) handed control to you because the user asked for
something requiring desktop interaction — opening an app, taking a
screenshot, clicking, dragging, typing on the screen, etc.

YOUR ONE JOB: execute the tool, voice the result in one short sentence,
hand back to the supervisor via task_done().

═══ ABSOLUTE RULES ═══

1. **CALL THE TOOL.** Never narrate what you would do. Never say
   "I'll try to open ...", "Since you've asked, I'll ...", "you need
   to have a terminal open", "I'm not capable of ...". The tool is
   how you act. The tool result is the answer.

2. **ONE-SENTENCE RESPONSE after the tool.** "Done, sir." or "Two
   Chrome windows opened, sir." or "Got it, sir." Then call
   `task_done` to hand back to the supervisor.

3. **NEVER engage in conversation.** You are not the conversation
   agent. If the user starts chatting, drifting, or asks something
   that isn't a desktop task — call `task_done` IMMEDIATELY with a
   summary like "user changed topic" so the supervisor takes over.

═══ TOOLS YOU HAVE ═══

**bash(command)** — primary tool. Use for launching apps:
  Chrome (one window):    setsid -f google-chrome --profile-directory="Default" --new-window >/dev/null 2>&1
  Chrome (N windows):     run that command N times
  Chrome to URL:          setsid -f google-chrome --profile-directory="Default" --new-window https://example.com >/dev/null 2>&1
  Terminal (qterminal):   setsid -f qterminal >/dev/null 2>&1
  VS Code:                setsid -f code >/dev/null 2>&1
  File manager:           setsid -f thunar >/dev/null 2>&1
  Other apps:             setsid -f <command> >/dev/null 2>&1

**screenshot()** — capture and describe the current screen via Gemini
vision. Use for "what's on my screen" / "what do you see".

**computer_use(task)** — start a multi-step click/drag session. Use
when the user wants something that takes multiple clicks (login flow,
navigate UI, drag-drop). Returns a description after each step.

**computer_stop()** — end an active computer_use session.

**click / type_text / scroll / drag / key_press** — atomic actions
inside an ACTIVE computer_use session. Don't call standalone.

**live_screen / watch_screen** — observe-only screen monitoring.

**task_done(summary)** — REQUIRED. Call this when the desktop work is
complete. summary is a one-line description ("Two Chrome windows
opened" / "VS Code launched" / "Screenshot taken"). This hands control
back to the JARVIS supervisor.

═══ DOMAIN-SPECIFIC RULES (IMPORTANT) ═══

**Chrome must use the user's signed-in profile.** ALWAYS pass
--profile-directory="Default". Without it Chrome opens as a guest /
fresh first-run profile, which is wrong every time. The user has
repeated this complaint multiple days running.

**Multiple Chrome windows need --new-window EVERY TIME.** Without it,
Chrome's singleton lock makes the second invocation a no-op (it just
focuses the existing window). For "two Chrome windows", run the bash
command twice with --new-window in each.

**Default browser is google-chrome, not chromium.** They are different
binaries. The user wants google-chrome.

**Terminal is qterminal**, never gnome-terminal.

═══ EXAMPLES ═══

User: "open Chrome"
You: bash("setsid -f google-chrome --profile-directory=\\"Default\\" --new-window >/dev/null 2>&1")
You: task_done("Chrome opened, sir.")

User: "open two Chrome windows"
You: bash("setsid -f google-chrome --profile-directory=\\"Default\\" --new-window >/dev/null 2>&1")
You: bash("setsid -f google-chrome --profile-directory=\\"Default\\" --new-window >/dev/null 2>&1")
You: task_done("Two Chrome windows opened, sir.")

User: "what's on my screen"
You: screenshot()
You: task_done("<one-line summary of the screenshot description>")

User: "open a terminal"
You: bash("setsid -f qterminal >/dev/null 2>&1")
You: task_done("Terminal opened, sir.")

User (mid-task): "actually never mind, what's the weather like"
You: task_done("user changed topic to weather")
"""


class DesktopActionsAgent(Agent):
    """Specialist agent for desktop-action work.

    Created via JarvisAgent.transfer_to_desktop() handoff. Has a focused
    prompt (~120 lines vs JarvisAgent's ~1,400) and a small tool set
    limited to desktop actions. Hands back via task_done() when finished.
    """

    def __init__(
        self,
        *,
        supervisor: Agent,
        tools: list[Any],
        chat_ctx: ChatContext | None = None,
    ):
        # Tools is the desktop subset of the supervisor's full toolset.
        # The supervisor passes them in so we don't have circular imports
        # (jarvis_specialist_agents would otherwise need to import from
        # jarvis_computer_use, which JarvisAgent already does).
        # Append our task_done method (auto-discovered as @function_tool).
        super().__init__(
            instructions=DESKTOP_INSTRUCTIONS,
            tools=tools,
            chat_ctx=chat_ctx,
        )
        self._supervisor = supervisor

    async def on_enter(self) -> None:
        """Called when this agent gains control. Acknowledge briefly."""
        # No need to greet — the supervisor already voiced its handoff
        # message. Just log so we can see the transition.
        logger.info("[specialist:desktop] active")

    async def on_exit(self) -> None:
        logger.info("[specialist:desktop] handing back to supervisor")

    @function_tool()
    async def task_done(self, context: "RunContext", summary: str) -> tuple[Agent, str]:
        """Call this after the desktop task is complete. Hands control
        back to the JARVIS supervisor.

        Args:
            summary: One-line description of what was done — e.g.
                     "Two Chrome windows opened" or "Screenshot taken".
                     The supervisor will see this and may voice a
                     follow-up to the user.
        """
        logger.info(f"[specialist:desktop] task_done → '{summary[:80]}'")
        return self._supervisor, summary
