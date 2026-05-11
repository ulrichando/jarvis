"""Generic specialist Agent — built from a SpecialistSpec.

Used by `build_transfer_tools()` to construct a spec-driven Agent on
the fly when the supervisor's `transfer_to_X` fires. The hand-back
pattern (`task_done(summary)` returning `(supervisor, summary)`) is
the same LiveKit handoff convention DesktopActionsAgent uses today.

Kept separate from `registry.py` because this module imports LiveKit;
tests for the registry data structure can run without a livekit
install.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from livekit.agents import Agent, RunContext, function_tool
from livekit.agents.llm import ChatContext, FunctionCall

from .registry import SpecialistSpec

logger = logging.getLogger("jarvis-agent.specialist")

# Bailout-phrase allowlist for the no-real-tool gate. When a specialist
# is wrongly routed (e.g. supervisor handed it conversational input
# that isn't a desktop/browser action), the spec instructions say
# "call task_done IMMEDIATELY with 'user changed topic'" — but the
# gate would otherwise refuse because no real tool fired this handoff.
#
# This regex matches summaries that DISCLAIM work was done. It must
# stay narrow so a confabulated "Done — new tab is open" can never
# slip through. Each pattern must be an exclusionary phrase ("not",
# "wrong", "needs", "user changed", "cannot") — never a completion
# claim.
#
# Live-captured 2026-05-07 02:11–02:13: 11 REFUSED task_done warnings
# in 2 minutes for "user located laundry basket" / "user appears to be
# describing X" / "user appears to be requesting mute" — supervisor
# was routing conversational input ("Jarvis, mute" / "I love you,
# dear" / "double") to the desktop specialist. The specialist tried
# to bail honestly per its Rule 3, the gate refused, the LLM then
# produced "I'm here to assist with desktop-related tasks" boilerplate
# which got voiced. Allow the bailout to actually happen.
_BAILOUT_SUMMARY_RE = re.compile(
    r"""(?ix)
    \b(?:
        user\s+(?:changed|switched)\s+topic
      | (?:not|isn'?t|is\s+not)\s+(?:a\s+)?(?:desktop|browser|relevant|valid)
      | wrong\s+specialist
      | needs?\s+(?:the\s+)?(?:browser|desktop|planner|supervisor)\s+specialist
      | cannot\s+(?:accomplish|act\s+on|handle)
      | nothing\s+to\s+(?:do|act\s+on)
      | no\s+(?:desktop|browser)\s+(?:action|tool)
      | handing\s+back\s+to\s+(?:the\s+)?supervisor
      | not\s+a\s+request\s+I\s+can\s+act\s+on
      # Environmental gates (added 2026-05-08) — situations where the
      # specialist's underlying service is unreachable. These aren't
      # fabricated outcomes; they're "the door is locked" statements.
      # Tightly anchored so a confab can't slip through with the same
      # words ("connection issue" must be paired with "extension"/"tool"/
      # "browser"/"chrome"/"service" within ~30 chars).
      | (?:extension|tool|service|browser|chrome|firefox)\s+(?:is\s+)?(?:not\s+connected|unavailable|offline|not\s+available)
      | (?:bridge|extension)\s+disconnected
      | google\s+chrome\s+isn'?t\s+available
      # 2026-05-09 — weather specialist's location-failure phrasing
      | couldn'?t\s+determine\s+(?:your\s+|the\s+)?location
    )
    """
)


# Retry ceiling for the no-tool gate. After this many consecutive
# refusals on a single handoff, force-allow task_done with a
# generic-bailout summary so the user isn't trapped in silence
# while the LLM keeps re-rolling the same confab. Captured live
# 2026-05-08 16:33:14–16:33:23: desktop specialist looped 3×
# "Browser opened, sir." (refused each time) → 9s of silence to
# the user. With a retry ceiling, the third REFUSE forces a
# graceful handback so the supervisor can voice an apology.
#
# Read at RUNTIME (not module-import time) so operators editing
# the systemd unit's Environment= line see the change without a
# worker restart, matching the JARVIS_SPECIALIST_TOOL_GATE pattern
# below.
def _no_tool_retry_ceiling() -> int:
    return int(os.environ.get("JARVIS_SPECIALIST_NO_TOOL_RETRY_CEILING", "3"))


class RegistrySpecialist(Agent):
    """Specialist Agent built from a SpecialistSpec.

    On enter: logs the active specialist. On task_done: hands back to
    the supervisor with the spec's summary. Tool list is whatever the
    spec's tool_factory returns at construction time.
    """

    def __init__(
        self,
        *,
        spec: SpecialistSpec,
        supervisor: Agent,
        chat_ctx: ChatContext | None = None,
    ):
        super().__init__(
            instructions=spec.instructions,
            tools=spec.tool_factory(),
            chat_ctx=chat_ctx,
        )
        self._spec = spec
        self._supervisor = supervisor
        # High-water mark of chat_ctx at handoff start. task_done's
        # tool-gate looks at items appended AFTER this index to decide
        # whether the specialist did real work this handoff.
        self._handoff_start_idx: int = 0
        # Counter for consecutive task_done refusals on this handoff.
        # Resets on enter. Ceiling enforced in task_done.
        self._no_tool_refusals: int = 0

    async def on_enter(self) -> None:
        logger.info(f"[specialist:{self._spec.name}] active")
        # Reset the no-tool retry counter for this fresh handoff.
        self._no_tool_refusals = 0
        # Record where this handoff begins. Anything appended to
        # chat_ctx.items past this index is the specialist's own work
        # (its tool calls + outputs). task_done uses this to enforce
        # the "do real work before exiting" rule programmatically.
        try:
            self._handoff_start_idx = len(self.chat_ctx.items)
        except Exception as e:
            logger.warning(
                f"[specialist:{self._spec.name}] couldn't read chat_ctx "
                f"on_enter: {e}; tool-gate will be soft"
            )
            self._handoff_start_idx = 0

    async def on_exit(self) -> None:
        logger.info(f"[specialist:{self._spec.name}] handing back to supervisor")

    @function_tool()
    async def task_done(
        self, context: RunContext, summary: str
    ) -> tuple[Agent, str]:
        """Call this after the work is complete. Hands control back to
        the JARVIS supervisor.

        Args:
            summary: One-line description of what was done.
                     The supervisor will see this and may voice a
                     follow-up to the user.
        """
        # ── No-bailout-first gate ─────────────────────────────────────
        # Specialist instructions explicitly forbid task_done as the
        # first tool call (see browser.py:31-52, "NO BAILOUT-FIRST
        # RULE"). The LLM ignores those instructions periodically —
        # observed 2026-05-02 23:35 (YouTube search hallucinated as
        # task_done with no tool fired) and 2026-05-04 12:58 ("open
        # a new tab" → task_done with no ext_*, confab-detector
        # dropped the false claim, user heard silence and assumed
        # JARVIS was deaf). This is the structural enforcement: count
        # real (non-task_done) tool calls in this handoff window; if
        # zero, refuse the exit and force the LLM to actually act.
        # JARVIS_SPECIALIST_TOOL_GATE=0 disables.
        if os.environ.get("JARVIS_SPECIALIST_TOOL_GATE", "1") == "1":
            try:
                ceiling = _no_tool_retry_ceiling()
                since_handoff = self.chat_ctx.items[self._handoff_start_idx:]
                real_calls = [
                    it for it in since_handoff
                    if isinstance(it, FunctionCall) and it.name != "task_done"
                ]
                if not real_calls:
                    # Bailout-phrase allowlist: when the supervisor wrongly
                    # routed conversational/unclear input here, the spec's
                    # Rule 3 says bail with task_done immediately. Honor
                    # that path — but only for explicit disclaimer phrases
                    # that can't double as a confabulated success claim.
                    if _BAILOUT_SUMMARY_RE.search(summary or ""):
                        logger.info(
                            f"[specialist:{self._spec.name}] task_done bailout "
                            f"(no tool fired, allowed): {summary[:120]!r}"
                        )
                        # Fall through to the normal handoff below.
                    elif self._no_tool_refusals + 1 >= ceiling:
                        # Retry ceiling reached. Force-allow with a
                        # graceful generic-bailout summary so the user
                        # isn't trapped in silence while the LLM keeps
                        # re-rolling the same confab. The supervisor
                        # voices a brief apology when it relays.
                        logger.warning(
                            f"[specialist:{self._spec.name}] task_done "
                            f"FORCE-BAILED after {self._no_tool_refusals + 1} "
                            f"no-tool refusals. Original summary: "
                            f"{(summary or '')[:120]!r}"
                        )
                        summary = (
                            "Cannot accomplish — handing back to supervisor."
                        )
                        # Fall through to the normal handoff below.
                    else:
                        self._no_tool_refusals += 1
                        logger.warning(
                            f"[specialist:{self._spec.name}] task_done REFUSED — "
                            f"no real tool call this handoff "
                            f"(items_since={len(since_handoff)}, "
                            f"refusal #{self._no_tool_refusals}/"
                            f"{ceiling}). "
                            f"Summary attempted: {summary[:120]!r}"
                        )
                        # Stay on the specialist; the corrective string is
                        # returned as the tool result the LLM will read
                        # next. Phrased to be unambiguous: which tool to
                        # try first matters less than NOT returning to
                        # task_done with another guess.
                        return (
                            self,
                            "REFUSED: task_done called without first executing "
                            "any real tool, AND your summary doesn't match an "
                            "allowed bailout phrase. Either: (a) call the right "
                            "tool for the user's request and then task_done with "
                            "a result-based summary, OR (b) if the request truly "
                            "isn't yours to handle, call task_done with one of "
                            "these exact bailout phrases: 'user changed topic', "
                            "'not a desktop task', 'not a browser task', 'wrong "
                            "specialist — needs the X specialist', 'cannot "
                            "accomplish with these tools — handing back to "
                            "supervisor'. Do not retry with a generic summary.",
                        )
            except Exception as e:
                # Soft-fail: never let the gate itself block a real
                # task_done. Better to let one confab through than to
                # wedge every specialist.
                logger.warning(
                    f"[specialist:{self._spec.name}] tool-gate check failed: "
                    f"{type(e).__name__}: {e} — proceeding with task_done"
                )

        logger.info(
            f"[specialist:{self._spec.name}] task_done → '{summary[:80]}'"
        )
        # Clear the tool-busy flag — pair with _mark_tool_start in
        # _transfer above. Tray returns to idle/listening as soon as
        # the supervisor takes the summary.
        try:
            from jarvis_agent import _mark_tool_end
            _mark_tool_end()
        except Exception:
            pass

        # Pre-2026-05-10 this branch wrote a ToolResult to a Redis-backed
        # `blackboard` keyed for the (now-deleted) grounding_gate to read.
        # With supervisor_graph (incl. grounding_gate) removed in f38c358
        # and vision_tap removed in 5065a4b, no production code reads the
        # blackboard — both `Intent` and `ScreenFact` lost their writers
        # too. The whole subsystem is gone; this comment is the only
        # remnant.
        return self._supervisor, summary


def build_transfer_tool(spec: SpecialistSpec):
    """Generate ONE `transfer_to_X` function_tool for `spec`.

    The supervisor reference comes from `self` at call-time — LiveKit
    binds it when the tool is invoked, the same way `JarvisAgent`'s
    method-style `transfer_to_desktop` works. So no closure-bound
    supervisor is needed and there's no chicken-and-egg with agent
    construction.

    Carries chat_ctx forward so the specialist sees the user's request
    without restating it.
    """
    description = (
        f"Hand off to the {spec.name} specialist.\n\n"
        f"{spec.when_to_use}\n\n"
        f"After the specialist completes, control returns here "
        f"automatically with a one-sentence summary.\n\n"
        f"Args:\n"
        f"    request: The user's request, verbatim or paraphrased."
    )

    # NOTE: no `self` parameter. When @function_tool is used as a
    # class method (e.g. `task_done` on RegistrySpecialist), Python's
    # method machinery handles `self` before LiveKit introspects.
    # When it's used on a closure-returned function passed via the
    # supervisor's `tools=[…]` list, LiveKit's Pydantic-schema builder
    # walks the parameter list and tries to look each name up in
    # type_hints — `self` has no annotation → `KeyError: 'self'` and
    # the framework wraps it as APIConnectionError ("failed to
    # generate LLM completion: Connection error") which is misleading.
    #
    # Instead we get the supervisor from `context.session.current_agent`
    # at call-time. The LLM that fired this tool IS the supervisor.
    @function_tool(name=spec.transfer_tool, description=description)
    async def _transfer(
        context: RunContext, request: str
    ) -> tuple[Agent, str]:
        session = context.session
        supervisor = session.current_agent
        try:
            ctx = supervisor.chat_ctx.copy(exclude_instructions=True)
            if spec.max_history_items is not None:
                ctx = ctx.truncate(max_items=spec.max_history_items)
        except Exception:
            ctx = None

        # Stash the specialist name on the session so the assistant
        # `_on_item` telemetry hook in jarvis_agent.py can record it
        # alongside route/emotion.
        try:
            session._jarvis_last_specialist = spec.name
        except Exception:
            pass

        logger.info(
            f"[handoff] → {spec.name} specialist (request: {request[:80]!r})"
        )
        # Mark tool-busy for the FULL duration of the specialist run so
        # the tray icon stays amber until task_done. Without this, the
        # framework's `agent_state_changed` flips back to "idle" between
        # the supervisor's transfer and the specialist's first action,
        # making the tray flicker green mid-task. Lazy import to dodge
        # circular jarvis_agent ↔ specialists registration.
        try:
            from jarvis_agent import _mark_tool_start
            _mark_tool_start(f"specialist:{spec.name}")
        except Exception:
            pass

        # Defensive: catch tool-factory failures (e.g. ImportError from
        # a typo'd specialist module) so the supervisor sees a concrete
        # error string instead of the framework's generic "exception
        # occurred while executing tool". Captured live 2026-05-01:
        # browser_v2's `from jarvis_agent import task_done` ImportError
        # crashed the handoff silently — supervisor's parallel tool call
        # masked it. The supervisor saw nothing actionable.
        try:
            specialist = RegistrySpecialist(
                spec=spec,
                supervisor=supervisor,
                chat_ctx=ctx,
            )
        except Exception as e:
            logger.exception(
                f"[handoff] {spec.name} specialist failed to construct"
            )
            # Stay on supervisor; return an error string the LLM can
            # narrate or recover from.
            return (
                supervisor,
                f"(specialist {spec.name} unavailable: {type(e).__name__}: {e})",
            )
        return (specialist, spec.ack_phrase)

    return _transfer


def build_delegate_tool():
    """Generate the single `delegate(role, task)` function_tool that
    covers ALL registered SubagentSpecs.

    Why one tool instead of one-per-subagent:
      - Token cost in supervisor prompt is constant in N. With 100
        SpecialistSpecs the per-turn input grows by ~30k tokens — at
        Groq's processing rate that's +1500ms TTFW. With one delegate
        tool the cost is ~600 tokens flat, regardless of N.
      - Adding a 101st subagent doesn't change the supervisor's prompt
        — only the role-list inside the description grows.

    The tool accepts `role` as a free-form string for now (LLM picks
    from the role list embedded in the description). Validation
    happens at call-time; an unknown role returns an error string the
    supervisor can voice. Returns `None` if no subagents registered —
    the supervisor then doesn't get the tool at all.
    """
    from .registry import all_subagents, get_subagent

    available = all_subagents()
    if not available:
        return None

    role_list = "\n".join(
        f"  • {s.name} — {s.when_to_use[:140]}"
        for s in available
    )
    description = (
        "Delegate the user's request to a specialist sub-agent. "
        "Pick the role whose description best matches the request.\n\n"
        "Available roles:\n"
        f"{role_list}\n\n"
        "Args:\n"
        "    role: One of the role names listed above (verbatim).\n"
        "    task: Full instruction for the sub-agent — what to do, "
        "what success looks like, any context it needs."
    )

    @function_tool(name="delegate", description=description)
    async def _delegate(
        context: RunContext, role: str, task: str
    ) -> tuple[Agent, str]:
        spec = get_subagent(role)
        if spec is None:
            from .registry import all_subagents as _all_subs
            available_names = sorted(s.name for s in _all_subs())
            logger.warning(
                f"[delegate] unknown role={role!r}; "
                f"available={available_names}"
            )
            # Return a string so the supervisor can voice the error
            # without breaking the conversation. Unknown role is the
            # LLM's mistake; surface it so the user can rephrase.
            return (
                context.session.current_agent,
                f"Sorry — I don't have a {role!r} specialist. "
                f"Available: {', '.join(available_names[:10])}.",
            )

        session = context.session
        supervisor = session.current_agent
        try:
            ctx = supervisor.chat_ctx.copy(exclude_instructions=True)
            if spec.max_history_items is not None:
                ctx = ctx.truncate(max_items=spec.max_history_items)
        except Exception:
            ctx = None

        try:
            session._jarvis_last_specialist = spec.name
        except Exception:
            pass

        logger.info(
            f"[delegate] → {spec.name} (task: {task[:80]!r})"
        )

        # Reuse RegistrySpecialist by adapting SubagentSpec fields onto
        # SpecialistSpec — saves us a parallel Agent class.
        adapter_spec = SpecialistSpec(
            name=spec.name,
            transfer_tool=f"(via delegate)",
            when_to_use=spec.when_to_use,
            instructions=spec.instructions,
            tool_factory=spec.tool_factory,
            ack_phrase=spec.ack_phrase,
            max_history_items=spec.max_history_items,
            enabled=spec.enabled,
        )
        return (
            RegistrySpecialist(
                spec=adapter_spec,
                supervisor=supervisor,
                chat_ctx=ctx,
            ),
            spec.ack_phrase,
        )

    return _delegate


def build_all_transfer_tools() -> list[Any]:
    """All registered specialists' transfer tools + the single delegate
    tool covering subagents. Ready to attach to the supervisor's
    `tools=[…]` list at construction.

    Returns the per-name `transfer_to_X` tools for legacy SpecialistSpecs
    (planner / desktop / browser today) PLUS one `delegate(role, task)`
    tool covering all SubagentSpecs. Both can coexist — the supervisor
    picks `transfer_to_X` for the existing 3 specialists and `delegate`
    for everything new.
    """
    from .registry import all_specs
    tools: list[Any] = [build_transfer_tool(s) for s in all_specs()]
    delegate = build_delegate_tool()
    if delegate is not None:
        tools.append(delegate)
    return tools
