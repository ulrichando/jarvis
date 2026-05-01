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
from typing import Any

from livekit.agents import Agent, RunContext, function_tool
from livekit.agents.llm import ChatContext

from .registry import SpecialistSpec

logger = logging.getLogger("jarvis-agent.specialist")


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

    async def on_enter(self) -> None:
        logger.info(f"[specialist:{self._spec.name}] active")

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
        logger.info(
            f"[specialist:{self._spec.name}] task_done → '{summary[:80]}'"
        )
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
        return (
            RegistrySpecialist(
                spec=spec,
                supervisor=supervisor,
                chat_ctx=ctx,
            ),
            spec.ack_phrase,
        )

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
                f"Sorry, sir, I don't have a {role!r} specialist. "
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
