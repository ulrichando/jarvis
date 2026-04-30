"""Desktop-action specialist — registered via the SpecialistSpec
pattern. Mirrors the legacy `DesktopActionsAgent` for backwards
compat: same prompt, same tools, same handoff behaviour.

To DISABLE: set `enabled=False` in the register() call below.
To CUSTOMIZE: copy this file as a template and adjust spec fields.
"""
from __future__ import annotations

from .registry import SpecialistSpec, register


# Same prompt as `DESKTOP_INSTRUCTIONS` in jarvis_specialist_agents.py.
# Lifted verbatim so the migration is no-op behaviourally; the canonical
# copy now lives here. The legacy file becomes a re-export shim.
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


def _desktop_tools() -> list:
    """Lazy tool import — runs only when the supervisor actually
    constructs the specialist. Keeps livekit + heavy plugins out of
    the registry-import critical path.

    Mirrors the tool list jarvis_agent.py used to pass into the
    legacy DesktopActionsAgent constructor. See:
        src/voice-agent/jarvis_agent.py::JarvisAgent.transfer_to_desktop
    """
    from jarvis_computer_use import (
        computer_use, computer_stop, click, type_text, scroll, drag,
        key_press, wait, screenshot, live_screen, watch_screen,
        webcam_capture,
    )
    from jarvis_agent import (
        bash, run_jarvis_cli, type_in_terminal, media_control, browser_task,
    )
    return [
        bash, computer_use, computer_stop, click, type_text, scroll,
        drag, key_press, wait, screenshot, live_screen, watch_screen,
        webcam_capture,
        run_jarvis_cli, type_in_terminal, media_control, browser_task,
    ]


_DESKTOP_WHEN = (
    "Use whenever the user wants something done on the Linux desktop: "
    "open an app (Chrome / VS Code / terminal / file manager), launch "
    "N copies, take a screenshot, click somewhere on screen, drag "
    "something, type into a focused window, or any multi-step UI "
    "manipulation."
)


def register_desktop() -> None:
    """Register the desktop specialist. Idempotent — re-registration
    overwrites, so this is safe to call from `__init__.py` on every
    import.

    Phase 4 of the registry migration: desktop is now `enabled=True`
    after the planner specialist proved the registry pattern works
    end-to-end. The legacy `JarvisAgent.transfer_to_desktop` method
    has been retired in the same commit; the registry now owns the
    handoff for both desktop and planner.
    """
    register(SpecialistSpec(
        name="desktop",
        transfer_tool="transfer_to_desktop",
        when_to_use=_DESKTOP_WHEN,
        instructions=DESKTOP_INSTRUCTIONS,
        tool_factory=_desktop_tools,
        ack_phrase="On it, sir.",
        max_history_items=12,
        enabled=True,
    ))
