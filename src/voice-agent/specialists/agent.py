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


def build_transfer_tool(supervisor: Agent, spec: SpecialistSpec):
    """Generate ONE `transfer_to_X` function_tool for `spec`.

    The closure binds (supervisor, spec) so the LLM can call it with
    just `request: str`. Carries chat_ctx forward so the specialist
    sees the user's request without restating it.
    """
    @function_tool(name=spec.transfer_tool)
    async def _transfer(
        self, context: RunContext, request: str
    ) -> tuple[Agent, str]:
        """Hand off to the {spec.name} specialist.

        {when_to_use}

        After the specialist completes, control returns here automatically
        with a one-sentence summary.

        Args:
            request: The user's request, verbatim or paraphrased.
                     The specialist also reads chat context, so this is
                     a hint, not the only signal.
        """
        try:
            ctx = self.chat_ctx.copy(exclude_instructions=True)
            if spec.max_history_items is not None:
                ctx = ctx.truncate(max_items=spec.max_history_items)
        except Exception:
            ctx = None
        logger.info(
            f"[handoff] → {spec.name} specialist (request: {request[:80]!r})"
        )
        return (
            RegistrySpecialist(
                spec=spec,
                supervisor=self,
                chat_ctx=ctx,
            ),
            spec.ack_phrase,
        )

    # Patch the docstring with the spec's when_to_use so the LLM gets
    # accurate routing guidance per-specialist. The decorator already
    # captured the static docstring at decoration time, but replacing
    # __doc__ post-hoc still flows into livekit's tool description
    # extraction (verified against the framework's `function_tool`).
    _transfer.__doc__ = (
        f"Hand off to the {spec.name} specialist.\n\n"
        f"{spec.when_to_use}\n\n"
        f"After the specialist completes, control returns here "
        f"automatically with a one-sentence summary.\n\n"
        f"Args:\n"
        f"    request: The user's request, verbatim or paraphrased."
    )
    return _transfer


def build_all_transfer_tools(supervisor: Agent) -> list[Any]:
    """All registered specialists' transfer tools, ready to attach to
    the supervisor's `tools=[…]` list at construction."""
    from .registry import all_specs
    return [build_transfer_tool(supervisor, s) for s in all_specs()]
