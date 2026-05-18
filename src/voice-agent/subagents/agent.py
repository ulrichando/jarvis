"""Generic subagent Agent — built from a HandoffSubagent.

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

from .registry import HandoffSubagent

logger = logging.getLogger("jarvis.subagent")

# Bailout-phrase allowlist for the no-real-tool gate. When a subagent
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
# dear" / "double") to the desktop subagent. The subagent tried
# to bail honestly per its Rule 3, the gate refused, the LLM then
# produced "I'm here to assist with desktop-related tasks" boilerplate
# which got voiced. Allow the bailout to actually happen.
_BAILOUT_SUMMARY_RE = re.compile(
    r"""(?ix)
    \b(?:
        user\s+(?:changed|switched)\s+topic
      | (?:not|isn'?t|is\s+not)\s+(?:a\s+)?(?:desktop|browser|relevant|valid)
      | wrong\s+subagent
      | needs?\s+(?:the\s+)?(?:browser|desktop|planner|supervisor)\s+subagent
      | cannot\s+(?:accomplish|act\s+on|handle)
      | nothing\s+to\s+(?:do|act\s+on)
      | no\s+(?:desktop|browser)\s+(?:action|tool)
      | handing\s+back\s+to\s+(?:the\s+)?supervisor
      | not\s+a\s+request\s+I\s+can\s+act\s+on
      # Environmental gates (added 2026-05-08) — situations where the
      # subagent's underlying service is unreachable. These aren't
      # fabricated outcomes; they're "the door is locked" statements.
      # Tightly anchored so a confab can't slip through with the same
      # words ("connection issue" must be paired with "extension"/"tool"/
      # "browser"/"chrome"/"service" within ~30 chars).
      | (?:extension|tool|service|browser|chrome|firefox)\s+(?:is\s+)?(?:not\s+connected|unavailable|offline|not\s+available)
      | (?:bridge|extension)\s+disconnected
      | google\s+chrome\s+isn'?t\s+available
      # 2026-05-09 — weather subagent's location-failure phrasing
      | couldn'?t\s+determine\s+(?:your\s+|the\s+)?location
      # 2026-05-11 — screen-share subagent self-bails when the
      # supervisor routed here but no video stream is reachable
      # (user wasn't actually sharing, or the track hasn't started
      # publishing yet). Supervisor can then fall back to screenshot().
      | screen[-\s]share\s+(?:not\s+active|isn'?t\s+active|off)
      | no\s+video\s+frames(?:\s+received)?
    )
    """
)


# Retry ceiling for the no-tool gate. After this many consecutive
# refusals on a single handoff, force-allow task_done with a
# generic-bailout summary so the user isn't trapped in silence
# while the LLM keeps re-rolling the same confab. Captured live
# 2026-05-08 16:33:14–16:33:23: desktop subagent looped 3×
# "Browser opened, sir." (refused each time) → 9s of silence to
# the user. With a retry ceiling, the third REFUSE forces a
# graceful handback so the supervisor can voice an apology.
#
# Read at RUNTIME (not module-import time) so operators editing the
# systemd unit's Environment= line see the change without a worker
# restart. The env var was JARVIS_SPECIALIST_NO_TOOL_RETRY_CEILING
# before the 2026-05-11 terminology sweep — any old unit file must be
# updated to the new name.
def _no_tool_retry_ceiling() -> int:
    return int(os.environ.get("JARVIS_SUBAGENT_NO_TOOL_RETRY_CEILING", "3"))


class RegistrySubagent(Agent):
    """Subagent Agent built from a HandoffSubagent.

    On enter: logs the active subagent. On task_done: hands back to
    the supervisor with the spec's summary. Tool list is whatever the
    spec's tool_factory returns at construction time.
    """

    def __init__(
        self,
        *,
        spec: HandoffSubagent,
        supervisor: Agent,
        chat_ctx: ChatContext | None = None,
        worktree_path: Optional[str] = None,
    ):
        # Per-subagent LLM override. Most specs leave llm_factory=None
        # and inherit the supervisor's LLM. The screen_share subagent
        # uses this to swap in a Gemini Live RealtimeModel so it gets
        # sub-second responses with continuous vision context — the
        # supervisor stays on Claude Haiku + Orpheus when this
        # subagent isn't active.

        # Base instructions: either the spec's dynamic `instructions_factory`
        # (when present — used by browser to disclose CDP-vs-extension
        # backend per the 2026-05-18 capability-disclosure audit) or the
        # static `instructions` field. Factory errors fall back to the
        # static field rather than crashing the handoff.
        instructions = spec.instructions
        if spec.instructions_factory is not None:
            try:
                dyn = spec.instructions_factory()
                if dyn:
                    instructions = dyn
            except Exception as e:
                logger.warning(
                    f"[subagent:{spec.name}] instructions_factory raised "
                    f"{type(e).__name__}: {e}; using static instructions"
                )

        # If the spec requested filesystem isolation, the transfer
        # tool has already created a worktree and is passing the path
        # here. Inject the absolute path into the subagent's
        # instructions so the LLM operates inside it. The subagent's
        # bash() / read() / edit() / write() tools have no per-spec
        # cwd switching, so the path-in-prompt is the enforcement
        # mechanism — instruct the subagent to use absolute paths or
        # `cd <worktree> && cmd` patterns.
        if worktree_path:
            instructions = instructions + (
                f"\n\n═══ ISOLATION WORKTREE ═══\n\n"
                f"You are running INSIDE an isolated git worktree at:\n"
                f"  {worktree_path}\n\n"
                f"Any file operations (read / edit / write / bash) must\n"
                f"target absolute paths inside that directory, OR use\n"
                f"`cd {worktree_path} && ...` patterns. DO NOT touch\n"
                f"files outside the worktree — the user's main checkout\n"
                f"must stay clean. On `task_done`, the worktree auto-\n"
                f"cleans IF you left no uncommitted changes. If you\n"
                f"committed work, the branch survives and the user can\n"
                f"PR it; if you have uncommitted changes the worktree\n"
                f"is preserved with a warning logged."
            )

        kwargs: dict = {
            "instructions": instructions,
            "tools": spec.tool_factory(),
            "chat_ctx": chat_ctx,
        }
        if spec.llm_factory is not None:
            try:
                kwargs["llm"] = spec.llm_factory()
            except Exception as e:
                logger.warning(
                    f"[subagent:{spec.name}] llm_factory raised "
                    f"{type(e).__name__}: {e} — falling back to "
                    f"supervisor's LLM"
                )
        super().__init__(**kwargs)
        self._spec = spec
        self._supervisor = supervisor
        self._worktree_path: Optional[str] = worktree_path
        # High-water mark of chat_ctx at handoff start. task_done's
        # tool-gate looks at items appended AFTER this index to decide
        # whether the subagent did real work this handoff.
        self._handoff_start_idx: int = 0
        # Counter for consecutive task_done refusals on this handoff.
        # Resets on enter. Ceiling enforced in task_done.
        self._no_tool_refusals: int = 0

    async def on_enter(self) -> None:
        logger.info(f"[subagent:{self._spec.name}] active")
        # Reset the no-tool retry counter for this fresh handoff.
        self._no_tool_refusals = 0
        # Record where this handoff begins. Anything appended to
        # chat_ctx.items past this index is the subagent's own work
        # (its tool calls + outputs). task_done uses this to enforce
        # the "do real work before exiting" rule programmatically.
        try:
            self._handoff_start_idx = len(self.chat_ctx.items)
        except Exception as e:
            logger.warning(
                f"[subagent:{self._spec.name}] couldn't read chat_ctx "
                f"on_enter: {e}; tool-gate will be soft"
            )
            self._handoff_start_idx = 0

    async def on_exit(self) -> None:
        logger.info(f"[subagent:{self._spec.name}] handing back to supervisor")

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
        # Subagent instructions explicitly forbid task_done as the
        # first tool call (see browser.py:31-52, "NO BAILOUT-FIRST
        # RULE"). The LLM ignores those instructions periodically —
        # observed 2026-05-02 23:35 (YouTube search hallucinated as
        # task_done with no tool fired) and 2026-05-04 12:58 ("open
        # a new tab" → task_done with no ext_*, confab-detector
        # dropped the false claim, user heard silence and assumed
        # JARVIS was deaf). This is the structural enforcement: count
        # real (non-task_done) tool calls in this handoff window; if
        # zero, refuse the exit and force the LLM to actually act.
        # JARVIS_SUBAGENT_TOOL_GATE=0 disables.
        #
        # Per-subagent opt-out: spec.tools_required=False skips this
        # gate entirely. Added 2026-05-11 evening for the screen_share
        # subagent, whose RealtimeModel produces work via audio +
        # transcription streaming — no function tools to count. The
        # gate's purpose is to catch confabulating LLMs that bail
        # before acting; irrelevant when there are no tools to act with.
        spec_requires_tools = getattr(self._spec, "tools_required", True)
        gate_enabled = os.environ.get("JARVIS_SUBAGENT_TOOL_GATE", "1") == "1"
        if gate_enabled and spec_requires_tools:
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
                            f"[subagent:{self._spec.name}] task_done bailout "
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
                            f"[subagent:{self._spec.name}] task_done "
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
                            f"[subagent:{self._spec.name}] task_done REFUSED — "
                            f"no real tool call this handoff "
                            f"(items_since={len(since_handoff)}, "
                            f"refusal #{self._no_tool_refusals}/"
                            f"{ceiling}). "
                            f"Summary attempted: {summary[:120]!r}"
                        )
                        # Stay on the subagent; the corrective string is
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
                            "subagent — needs the X subagent', 'cannot "
                            "accomplish with these tools — handing back to "
                            "supervisor'. Do not retry with a generic summary.",
                        )
            except Exception as e:
                # Soft-fail: never let the gate itself block a real
                # task_done. Better to let one confab through than to
                # wedge every subagent.
                logger.warning(
                    f"[subagent:{self._spec.name}] tool-gate check failed: "
                    f"{type(e).__name__}: {e} — proceeding with task_done"
                )

        logger.info(
            f"[subagent:{self._spec.name}] task_done → '{summary[:80]}'"
        )
        # Clear the tool-busy flag — pair with _mark_tool_start in
        # _transfer above. Tray returns to idle/listening as soon as
        # the supervisor takes the summary.
        try:
            from jarvis_agent import _mark_tool_end
            _mark_tool_end()
        except Exception:
            pass

        # Mask bailout-shape summaries before handing back. Phrases
        # like "not a screen-share task" / "user changed topic" /
        # "handing back to supervisor" are framework-internal signals
        # — they're how the subagent tells the supervisor "this didn't
        # work, you take over." They were never meant to reach the
        # user's ears. Live failure 2026-05-11 16:42 UTC: user heard
        # "not a screen-share task" voiced verbatim because the
        # supervisor LLM echoed the tool_result string back as its
        # next utterance. Replacing the summary with a neutral
        # internal-only cue removes the temptation.
        if _BAILOUT_SUMMARY_RE.search(summary or ""):
            summary = (
                "(subagent could not handle this; pick up the user's "
                "original request and answer it naturally — do NOT "
                "reference the failed handoff or the subagent by name)"
            )

        # Isolation worktree cleanup — fires only if the spec opted
        # into `isolation="worktree"` AND the create succeeded at
        # handoff time. Best-effort: clean worktree removes cleanly;
        # dirty worktree is left for the user to review. Never blocks
        # the hand-back to supervisor.
        if self._worktree_path:
            try:
                from ._isolation import cleanup_isolation_worktree
                outcome = await cleanup_isolation_worktree(self._worktree_path)
                logger.info(
                    f"[subagent:{self._spec.name}] isolation cleanup: {outcome}"
                )
            except Exception as e:
                logger.warning(
                    f"[subagent:{self._spec.name}] isolation cleanup raised "
                    f"{type(e).__name__}: {e}"
                )

        return self._supervisor, summary


def build_transfer_tool(spec: HandoffSubagent):
    """Generate ONE `transfer_to_X` function_tool for `spec`.

    The supervisor reference comes from `self` at call-time — LiveKit
    binds it when the tool is invoked, the same way `JarvisAgent`'s
    method-style `transfer_to_desktop` works. So no closure-bound
    supervisor is needed and there's no chicken-and-egg with agent
    construction.

    Carries chat_ctx forward so the subagent sees the user's request
    without restating it.
    """
    description = (
        f"Hand off to the {spec.name} subagent.\n\n"
        f"{spec.when_to_use}\n\n"
        f"After the subagent completes, control returns here "
        f"automatically with a one-sentence summary.\n\n"
        f"Args:\n"
        f"    request: The user's request, VERBATIM. Pass exactly what\n"
        f"        the user said (or the closest STT transcription). DO\n"
        f"        NOT paraphrase, summarize, or add inferred destinations.\n"
        f"        Live failure 2026-05-13: user said 'open YouTube',\n"
        f"        supervisor passed request='Open Gmail' (inferred from\n"
        f"        stale chat_ctx), subagent obediently opened Gmail.\n"
        f"        Pass the user's actual phrasing; the subagent decides\n"
        f"        the right action from there."
    )

    # NOTE: no `self` parameter. When @function_tool is used as a
    # class method (e.g. `task_done` on RegistrySubagent), Python's
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

        # Pre-transfer hook: idempotent prerequisite setup before the
        # subagent is constructed. Spec authors use this to enforce
        # tool sequencing that the supervisor LLM keeps forgetting in
        # prose-only prompts (e.g. screen_share's hook ensures the
        # screen-share track is on so the Live subagent never lands
        # with no video frames). Returning a non-None string aborts
        # the transfer; the supervisor sees it as the tool_result and
        # can voice a graceful failure. Exceptions are caught here
        # rather than propagated — a buggy hook must not crash the
        # handoff machinery.
        if spec.pre_transfer is not None:
            try:
                abort = await spec.pre_transfer(context, request, supervisor)
            except Exception as e:
                logger.exception(
                    f"[handoff] {spec.name} pre_transfer raised; aborting"
                )
                return (
                    supervisor,
                    f"(prerequisite for {spec.name} failed: "
                    f"{type(e).__name__}: {e})",
                )
            if abort is not None:
                logger.info(
                    f"[handoff] {spec.name} pre_transfer aborted "
                    f"(reason: {abort!r})"
                )
                return (supervisor, abort)

        try:
            ctx = supervisor.chat_ctx.copy(exclude_instructions=True)
            if spec.max_history_items is not None:
                ctx = ctx.truncate(max_items=spec.max_history_items)
        except Exception:
            ctx = None

        # Stash the subagent name on the session so the assistant
        # `_on_item` telemetry hook in jarvis_agent.py can record it
        # alongside route/emotion.
        try:
            session._jarvis_last_subagent = spec.name
        except Exception:
            pass

        logger.info(
            f"[handoff] → {spec.name} subagent (request: {request[:80]!r})"
        )
        # Mark tool-busy for the FULL duration of the subagent run so
        # the tray icon stays amber until task_done. Without this, the
        # framework's `agent_state_changed` flips back to "idle" between
        # the supervisor's transfer and the subagent's first action,
        # making the tray flicker green mid-task. Lazy import to dodge
        # circular jarvis_agent ↔ subagents registration.
        try:
            from jarvis_agent import _mark_tool_start
            _mark_tool_start(f"subagent:{spec.name}")
        except Exception:
            pass

        # Per-handoff filesystem isolation. If the spec opts in with
        # `isolation="worktree"`, spawn a fresh `<repo>/.worktrees/
        # <name>-<short_id>/` checkout NOW (before the subagent
        # constructs) and pass the absolute path to RegistrySubagent.
        # The subagent's instructions get a TRAILING block telling the
        # LLM to operate inside that directory. On task_done, cleanup
        # fires automatically (clean → removed; dirty → kept + logged).
        # Failure to create the worktree falls back to running WITHOUT
        # isolation — better degraded than a hard handoff abort.
        worktree_path: Optional[str] = None
        if getattr(spec, "isolation", None) == "worktree":
            try:
                from ._isolation import create_isolation_worktree
                wt = await create_isolation_worktree(spec.name)
                if wt is not None:
                    worktree_path = str(wt)
            except Exception as e:
                logger.warning(
                    f"[handoff] {spec.name} isolation worktree creation "
                    f"raised {type(e).__name__}: {e} — running without "
                    f"isolation"
                )

        # Custom Agent class on the spec — bypass RegistrySubagent and
        # construct the user-supplied class directly. Used by computer_use
        # whose on_enter runs an Anthropic Computer Use loop.
        if getattr(spec, "agent_class", None) is not None:
            try:
                subagent = spec.agent_class(
                    spec=spec,
                    supervisor=supervisor,
                    chat_ctx=ctx,
                )
            except Exception as e:
                logger.exception(
                    f"[handoff] {spec.name} custom agent_class construct failed"
                )
                return (
                    supervisor,
                    f"(subagent {spec.name} unavailable: {type(e).__name__}: {e})",
                )
            return (subagent, spec.ack_phrase)

        # Defensive: catch tool-factory failures (e.g. ImportError from
        # a typo'd subagent module) so the supervisor sees a concrete
        # error string instead of the framework's generic "exception
        # occurred while executing tool". Captured live 2026-05-01:
        # browser_v2's `from jarvis_agent import task_done` ImportError
        # crashed the handoff silently — supervisor's parallel tool call
        # masked it. The supervisor saw nothing actionable.
        try:
            subagent = RegistrySubagent(
                spec=spec,
                supervisor=supervisor,
                chat_ctx=ctx,
                worktree_path=worktree_path,
            )
        except Exception as e:
            logger.exception(
                f"[handoff] {spec.name} subagent failed to construct"
            )
            # Stay on supervisor; return an error string the LLM can
            # narrate or recover from.
            return (
                supervisor,
                f"(subagent {spec.name} unavailable: {type(e).__name__}: {e})",
            )
        return (subagent, spec.ack_phrase)

    return _transfer


def build_delegate_tool():
    """Generate the single `delegate(role, task)` function_tool that
    covers ALL registered DelegatedSubagents.

    Why one tool instead of one-per-subagent:
      - Token cost in supervisor prompt is constant in N. With 100
        HandoffSubagents the per-turn input grows by ~30k tokens — at
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
        "Delegate the user's request to a subagent sub-agent. "
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
                f"Sorry — I don't have a {role!r} subagent. "
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
            session._jarvis_last_subagent = spec.name
        except Exception:
            pass

        logger.info(
            f"[delegate] → {spec.name} (task: {task[:80]!r})"
        )

        # Reuse RegistrySubagent by adapting DelegatedSubagent fields onto
        # HandoffSubagent — saves us a parallel Agent class.
        adapter_spec = HandoffSubagent(
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
            RegistrySubagent(
                spec=adapter_spec,
                supervisor=supervisor,
                chat_ctx=ctx,
            ),
            spec.ack_phrase,
        )

    return _delegate


def build_all_transfer_tools() -> list[Any]:
    """All registered subagents' transfer tools + the single delegate
    tool covering subagents. Ready to attach to the supervisor's
    `tools=[…]` list at construction.

    Returns the per-name `transfer_to_X` tools for legacy HandoffSubagents
    (planner / desktop / browser today) PLUS one `delegate(role, task)`
    tool covering all DelegatedSubagents. Both can coexist — the supervisor
    picks `transfer_to_X` for the existing 3 subagents and `delegate`
    for everything new.
    """
    from .registry import all_specs
    tools: list[Any] = [build_transfer_tool(s) for s in all_specs()]
    delegate = build_delegate_tool()
    if delegate is not None:
        tools.append(delegate)
    return tools
