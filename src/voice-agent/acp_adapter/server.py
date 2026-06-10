"""ACP JSON-RPC server: JARVIS as an IDE coding agent.

The IDE invokes the ACP methods this class implements:

  - ``initialize`` / ``authenticate``        — handshake
  - ``new_session`` / ``load_session``       — session lifecycle
  - ``prompt``                                — run the user's request
  - ``cancel``                                — abort the in-flight prompt
  - ``set_session_mode`` / ``set_session_model`` — IDE-driven config

Each prompt enters ``_run_prompt`` which loops the supervisor LLM over
JARVIS's tool registry until the model stops emitting tool calls.
File-mutation tools (``write_file`` / ``patch``) go through ACP's
``request_permission`` before they execute; ``terminal`` goes through it
on every call so the user sees the command first.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import acp
from acp.schema import (
    AgentCapabilities,
    AuthenticateResponse,
    ClientCapabilities,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    AudioContentBlock,
    Implementation,
    InitializeResponse,
    NewSessionResponse,
    LoadSessionResponse,
    PromptCapabilities,
    PromptResponse,
    ResourceContentBlock,
    SessionCapabilities,
    SessionForkCapabilities,
    SessionListCapabilities,
    SessionMode,
    SessionModeState,
    SessionResumeCapabilities,
    SetSessionConfigOptionResponse,
    SetSessionModeResponse,
    SetSessionModelResponse,
    TextContentBlock,
)

from .auth import build_auth_methods, has_provider, NONE_AUTH_METHOD_ID, TERMINAL_SETUP_AUTH_METHOD_ID
from .events import (
    build_plan_update_from_todo_result,
    send_update,
)
from .permissions import make_approval_callback
from .session import (
    MODE_DEFAULT,
    MODE_TO_APPROVAL_POLICY,
    SessionManager,
    SessionState,
    build_acp_tools,
)
from .tools import (
    build_tool_complete,
    build_tool_start,
    make_tool_call_id,
)

logger = logging.getLogger(__name__)


# JARVIS version banner advertised on initialize. Read from git when
# available so an operator can correlate ACP logs with a commit; falls
# back to a static "0.dev" when the tree isn't a git checkout.
try:
    import subprocess

    _JARVIS_VERSION = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=os.path.dirname(__file__),
        stderr=subprocess.DEVNULL,
        timeout=2,
    ).decode().strip() or "0.dev"
except Exception:
    _JARVIS_VERSION = "0.dev"


# One shared executor across sessions — keeps blocking work (filesystem
# tools, terminal commands) off the event loop without spinning up a
# thread per call.
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="jarvis-acp")


# Max iterations of the tool-call loop. The supervisor LLM keeps
# emitting tool calls until it answers in plain text; this caps blow-ups
# from a model that won't stop calling tools.
_MAX_TOOL_LOOP = 16


def _install_provider_sanitizers() -> None:
    """Install the provider-shape sanitizer patches in this process.

    The voice agent installs these at jarvis_agent import time; the ACP
    adapter runs in its own process but builds the same dispatching LLM
    over the same registry tools, so it needs the same request/response
    fixups. Most critically, without ``anthropic_strict_schema`` every
    Anthropic request whose tool schemas contain object nodes lacking
    ``additionalProperties: false`` returns 400 (live failure 2026-05-11)
    — and Anthropic is the dispatcher's primary provider. Each install()
    is idempotent, so calling this once per LLM build is safe.
    """
    import importlib

    for mod_name in (
        "sanitizers.anthropic_strict_schema",
        "sanitizers.strict_schema_relax",
        "sanitizers.tool_name",
        "sanitizers.deepseek_roundtrip",
    ):
        try:
            importlib.import_module(mod_name).install()
        except Exception:
            logger.warning(
                "ACP: could not install %s — the matching provider may "
                "reject tool requests", mod_name, exc_info=True,
            )


def _extract_text(prompt: list) -> str:
    """Pull plain text out of an ACP prompt's content blocks."""
    parts: list[str] = []
    for block in prompt:
        if isinstance(block, TextContentBlock):
            parts.append(block.text)
        elif hasattr(block, "text"):
            parts.append(str(getattr(block, "text", "") or ""))
    return "\n".join(p for p in parts if p)


# ACP-specific addendum stitched onto the supervisor system prompt.
# Short on purpose — the bulk of the persona/operations lives in
# prompts/soul.md and prompts/supervisor.md.
_ACP_PROMPT_ADDENDUM = """

═══ ACP CONTEXT ═══

You are running in an IDE chat pane (Zed, Cursor, VS Code, etc.) via the
Agent Client Protocol. Output is text, not voice — be concise but not
truncated. File edits flow through the IDE's edit-approval UI; terminal
commands prompt before they run. Use plan-mode (todo tool) for any task
that needs more than three tool calls.
""".strip()


def _build_system_prompt() -> str:
    """Assemble JARVIS's supervisor system prompt + the ACP addendum.

    Reuses the same SOUL + JARVIS_INSTRUCTIONS that the voice supervisor
    uses; the only delta is the short ACP context block tacked on the
    end so the LLM knows it's writing for an IDE.
    """
    from pathlib import Path

    prompts_dir = Path(__file__).resolve().parent.parent / "prompts"
    try:
        from pipeline.prompt_builder import load_soul

        soul = load_soul()
    except Exception:
        soul = (prompts_dir / "soul.md").read_text(encoding="utf-8") \
            if (prompts_dir / "soul.md").exists() else ""
    try:
        instructions = (prompts_dir / "supervisor.md").read_text(encoding="utf-8")
    except Exception:
        instructions = ""
    parts = [p for p in (soul, instructions, _ACP_PROMPT_ADDENDUM) if p.strip()]
    return "\n\n".join(parts)


class JarvisACPAgent(acp.Agent):
    """ACP ``Agent`` that drives JARVIS's supervisor + tools per prompt."""

    # Edit-approval policy id used in set_config_option (matches what
    # ACP clients like Zed put on the wire).
    _EDIT_APPROVAL_POLICY_CONFIG_ID = "edit_approval_policy"

    def __init__(
        self,
        session_manager: SessionManager | None = None,
        *,
        llm_builder: Any = None,
        tools_builder: Any = None,
    ) -> None:
        """Build the ACP agent shell.

        ``llm_builder`` / ``tools_builder`` exist as test seams: tests
        inject lightweight stand-ins instead of standing up the full
        Anthropic+Groq dispatcher and the 30+ registered tools.
        """
        super().__init__()
        self.session_manager = session_manager or SessionManager()
        self._conn: Optional[acp.Client] = None
        self._llm_builder = llm_builder
        self._tools_builder = tools_builder

    # ---- Connection lifecycle ----------------------------------------------

    def on_connect(self, conn: acp.Client) -> None:
        """Stash the client connection so ``session/update`` calls can fire."""
        self._conn = conn
        logger.info("ACP client connected")

    # ---- ACP lifecycle methods --------------------------------------------

    async def initialize(
        self,
        protocol_version: int | None = None,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        client_name = client_info.name if client_info else "unknown"
        logger.info(
            "Initialize from %s (protocol v%s)", client_name, protocol_version
        )
        return InitializeResponse(
            protocol_version=acp.PROTOCOL_VERSION,
            agent_info=Implementation(name="jarvis-agent", version=_JARVIS_VERSION),
            agent_capabilities=AgentCapabilities(
                load_session=True,
                prompt_capabilities=PromptCapabilities(image=True),
                session_capabilities=SessionCapabilities(
                    fork=SessionForkCapabilities(),
                    list=SessionListCapabilities(),
                    resume=SessionResumeCapabilities(),
                ),
            ),
            auth_methods=build_auth_methods(),
        )

    async def authenticate(self, method_id: str, **kwargs: Any) -> AuthenticateResponse | None:
        """Accept the auth methods we advertised in ``initialize``.

        JARVIS is local-only: ``none`` is the no-op path used by Zed when
        provider keys are already configured. The terminal-setup id is a
        hint surface; we don't implement it as an interactive flow yet,
        but we accept the call so the IDE doesn't error out.
        """
        if not isinstance(method_id, str):
            return None
        normalized = method_id.strip().lower()
        if normalized == NONE_AUTH_METHOD_ID:
            return AuthenticateResponse() if has_provider() else None
        if normalized == TERMINAL_SETUP_AUTH_METHOD_ID:
            return AuthenticateResponse() if has_provider() else None
        return None

    # ---- Session lifecycle -------------------------------------------------

    def _session_modes(self, state: SessionState) -> SessionModeState:
        current = state.mode if state.mode in MODE_TO_APPROVAL_POLICY else MODE_DEFAULT
        return SessionModeState(
            current_mode_id=current,
            available_modes=[
                SessionMode(
                    id=MODE_DEFAULT,
                    name="Default",
                    description="Ask before file edits and terminal commands.",
                ),
                SessionMode(
                    id="accept_edits",
                    name="Accept Edits",
                    description="Auto-allow workspace edits; still asks for sensitive paths.",
                ),
                SessionMode(
                    id="dont_ask",
                    name="Don't Ask",
                    description="Auto-allow file edits for this session except sensitive paths.",
                ),
            ],
        )

    def _approval_policy_for_state(self, state: SessionState) -> tuple[str, str | None]:
        policy = MODE_TO_APPROVAL_POLICY.get(state.mode, "ask")
        return policy, state.cwd

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        state = self.session_manager.create_session(cwd=cwd)
        return NewSessionResponse(
            session_id=state.session_id,
            modes=self._session_modes(state),
        )

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse | None:
        state = self.session_manager.update_cwd(session_id, cwd)
        if state is None:
            logger.warning("load_session: %s not found", session_id)
            return None
        # Replay the prior conversation so the IDE shows the transcript.
        if self._conn is not None:
            for msg in state.history:
                role = msg.get("role")
                text = msg.get("content")
                if not isinstance(text, str) or not text.strip():
                    continue
                if role == "user":
                    await send_update(
                        self._conn, session_id, acp.update_user_message_text(text)
                    )
                elif role == "assistant":
                    await send_update(
                        self._conn, session_id, acp.update_agent_message_text(text)
                    )
        return LoadSessionResponse(modes=self._session_modes(state))

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        state = self.session_manager.get_session(session_id)
        if state is None:
            return
        if state.cancel_event is not None:
            state.cancel_event.set()
        with state.runtime_lock:
            if state.is_running and state.current_prompt_text:
                state.interrupted_prompt_text = state.current_prompt_text
        logger.info("Cancelled ACP session %s", session_id)

    async def set_session_mode(
        self, mode_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModeResponse | None:
        state = self.session_manager.get_session(session_id)
        if state is None:
            return None
        normalized = str(mode_id or "").strip()
        if normalized not in MODE_TO_APPROVAL_POLICY:
            normalized = MODE_DEFAULT
        state.mode = normalized
        self.session_manager.save_session(session_id)
        return SetSessionModeResponse()

    async def set_session_model(
        self, model_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModelResponse | None:
        state = self.session_manager.get_session(session_id)
        if state is None:
            return None
        state.model = str(model_id or "").strip()
        # Drop cached LLM/tools so the next prompt rebuilds with the new
        # model id (read by ``_build_or_get_supervisor_llm``).
        state._tools = None
        self.session_manager.save_session(session_id)
        return SetSessionModelResponse()

    async def set_config_option(
        self, config_id: str, session_id: str, value: str, **kwargs: Any
    ) -> SetSessionConfigOptionResponse | None:
        state = self.session_manager.get_session(session_id)
        if state is None:
            return None
        if str(config_id) == self._EDIT_APPROVAL_POLICY_CONFIG_ID:
            for mode, pol in MODE_TO_APPROVAL_POLICY.items():
                if pol == str(value):
                    state.mode = mode
                    break
        self.session_manager.save_session(session_id)
        return SetSessionConfigOptionResponse(config_options=[])

    # ---- Prompt: the actual work -------------------------------------------

    async def prompt(
        self,
        prompt: list,
        session_id: str,
        **kwargs: Any,
    ) -> PromptResponse:
        """Run a user prompt through JARVIS's supervisor + tool loop."""
        state = self.session_manager.get_session(session_id)
        if state is None:
            logger.error("prompt: session %s not found", session_id)
            return PromptResponse(stop_reason="refusal")

        user_text = _extract_text(prompt).strip()
        if not user_text:
            return PromptResponse(stop_reason="end_turn")

        queued_depth = 0
        with state.runtime_lock:
            if state.is_running:
                state.queued_prompts.append(user_text)
                queued_depth = len(state.queued_prompts)
            else:
                state.is_running = True
                state.current_prompt_text = user_text

        if queued_depth:
            # Notify OUTSIDE the lock. runtime_lock is a threading.Lock;
            # awaiting while holding it would block any concurrent
            # acquirer on this loop thread (cancel, a third prompt) —
            # and since the blocked acquirer freezes the loop, the
            # holder could never resume to release: full deadlock.
            if self._conn:
                await send_update(
                    self._conn,
                    session_id,
                    acp.update_agent_message_text(
                        f"Queued for the next turn ({queued_depth} queued)."
                    ),
                )
            return PromptResponse(stop_reason="end_turn")

        if state.cancel_event is not None:
            state.cancel_event.clear()

        try:
            stop_reason = await self._run_prompt(state, user_text)
        finally:
            with state.runtime_lock:
                state.is_running = False
                state.current_prompt_text = ""
            self.session_manager.save_session(session_id)

        # Drain any prompts the user piled up while this one ran.
        while True:
            with state.runtime_lock:
                if not state.queued_prompts:
                    break
                next_prompt = state.queued_prompts.pop(0)
            if self._conn:
                await send_update(
                    self._conn, session_id, acp.update_user_message_text(next_prompt)
                )
            await self.prompt(
                prompt=[TextContentBlock(type="text", text=next_prompt)],
                session_id=session_id,
            )

        return PromptResponse(stop_reason=stop_reason)

    # ---- Core supervisor loop ----------------------------------------------

    async def _run_prompt(self, state: SessionState, user_text: str) -> str:
        """Drive the supervisor LLM + tool loop until the model is done."""
        conn = self._conn
        loop = asyncio.get_running_loop()

        # Append the user turn to history so a later session/load can
        # replay it.
        state.history.append({"role": "user", "content": user_text})

        # Build or hydrate the per-session tool list.
        if state._tools is None:
            try:
                if self._tools_builder is not None:
                    state._tools = self._tools_builder()
                else:
                    state._tools = build_acp_tools()
            except Exception:
                logger.warning("ACP session %s: tool build failed", state.session_id, exc_info=True)
                state._tools = []
        tools = state._tools

        # Build the supervisor LLM (Anthropic/Groq dispatcher) unless
        # the test wired its own.
        try:
            llm = self._build_or_get_supervisor_llm(state)
        except Exception as exc:
            logger.error("ACP session %s: LLM build failed: %s", state.session_id, exc, exc_info=True)
            if conn:
                await send_update(
                    conn, state.session_id,
                    acp.update_agent_message_text(
                        f"JARVIS failed to start the supervisor LLM: {exc}"
                    ),
                )
            return "refusal"

        # Bind the per-turn edit-approval requester so file mutate tools
        # round-trip through the IDE.
        from .edit_approval import (
            make_acp_edit_approval_requester,
            set_edit_approval_requester,
            reset_edit_approval_requester,
        )

        edit_approval_token = None
        if conn is not None:
            try:
                requester = make_acp_edit_approval_requester(
                    conn.request_permission, loop, state.session_id,
                    auto_approve_getter=lambda: self._approval_policy_for_state(state),
                )
                edit_approval_token = set_edit_approval_requester(requester)
            except Exception:
                logger.debug("Could not install ACP edit-approval requester", exc_info=True)

        # Bind the terminal approval callback (so the terminal tool can
        # gate dangerous commands through the IDE).
        terminal_approval_cb = None
        if conn is not None:
            terminal_approval_cb = make_approval_callback(
                conn.request_permission, loop, state.session_id
            )

        previous_approval = None
        try:
            if terminal_approval_cb is not None:
                try:
                    from tools import terminal_tool

                    previous_approval = getattr(terminal_tool, "_approval_callback", None)
                    if hasattr(terminal_tool, "set_approval_callback"):
                        terminal_tool.set_approval_callback(terminal_approval_cb)
                except Exception:
                    logger.debug("Could not install ACP terminal approval callback", exc_info=True)

            stop_reason = await self._dispatch_supervisor_loop(
                state, user_text, llm, tools, conn, loop,
            )
        finally:
            if edit_approval_token is not None:
                try:
                    reset_edit_approval_requester(edit_approval_token)
                except Exception:
                    pass
            if terminal_approval_cb is not None:
                try:
                    from tools import terminal_tool

                    if hasattr(terminal_tool, "set_approval_callback"):
                        terminal_tool.set_approval_callback(previous_approval)
                except Exception:
                    pass

        return stop_reason

    def _build_or_get_supervisor_llm(self, state: SessionState):
        """Build the dispatching LLM for this session."""
        if self._llm_builder is not None:
            return self._llm_builder()
        # Real providers need the provider-shape patches; test seams don't.
        _install_provider_sanitizers()
        from providers.llm import build_dispatching_llm

        return build_dispatching_llm()

    async def _dispatch_supervisor_loop(
        self,
        state: SessionState,
        user_text: str,
        llm: Any,
        tools: list,
        conn: acp.Client | None,
        loop: asyncio.AbstractEventLoop,
    ) -> str:
        """Run the supervisor LLM + tool loop, streaming updates to the IDE."""
        from livekit.agents.llm import ChatContext, ChatMessage, FunctionCall, FunctionCallOutput

        system_prompt = state._system_prompt or _build_system_prompt()
        state._system_prompt = system_prompt

        # Build the chat_ctx from persisted history (rehydrated from disk)
        # plus the system prompt. Skip messages we can't translate so a
        # malformed entry doesn't kill the turn.
        ctx_items: list = [
            ChatMessage(role="system", content=[system_prompt]),
        ]
        seen_call_ids: set[str] = set()
        for msg in state.history:
            role = msg.get("role")
            content = msg.get("content")
            if role in ("user", "assistant") and isinstance(content, str) and content:
                ctx_items.append(ChatMessage(role=role, content=[content]))
            elif role == "function_call":
                call_id = str(msg.get("call_id") or "")
                if call_id:
                    ctx_items.append(FunctionCall(
                        call_id=call_id,
                        name=str(msg.get("name") or ""),
                        arguments=str(msg.get("arguments") or "{}"),
                    ))
                    seen_call_ids.add(call_id)
            elif role == "tool":
                call_id = str(msg.get("tool_call_id") or msg.get("call_id") or "")
                output = msg.get("content")
                name = msg.get("tool_name") or msg.get("name") or ""
                # Only rebuild outputs whose call row precedes them — an
                # orphan FunctionCallOutput gets dropped by the provider
                # formatter anyway (one warning per item per turn).
                # Sessions persisted before function_call rows existed
                # have only orphans; skipping them here silences that.
                if call_id in seen_call_ids and isinstance(output, str):
                    ctx_items.append(FunctionCallOutput(
                        call_id=call_id, name=name, output=output, is_error=False,
                    ))

        chat_ctx = ChatContext(items=ctx_items)
        stop_reason = "end_turn"

        for iteration in range(_MAX_TOOL_LOOP):
            if state.cancel_event is not None and state.cancel_event.is_set():
                stop_reason = "cancelled"
                break

            stream = llm.chat(chat_ctx=chat_ctx, tools=tools)
            full_text = ""
            tool_calls: list[dict[str, Any]] = []

            try:
                async with stream:
                    async for chunk in stream:
                        delta = getattr(chunk, "delta", None)
                        if delta is None:
                            continue
                        if delta.content:
                            full_text += delta.content
                            if conn is not None:
                                await send_update(
                                    conn, state.session_id,
                                    acp.update_agent_message_text(delta.content),
                                )
                        for tc in (delta.tool_calls or []):
                            # Normalise tool call shape — LiveKit emits
                            # FunctionToolCall objects; we keep id/name/args.
                            tc_id = getattr(tc, "call_id", None) or getattr(tc, "id", None) or make_tool_call_id()
                            tc_name = getattr(tc, "name", "") or ""
                            tc_args_raw = getattr(tc, "arguments", "") or "{}"
                            if isinstance(tc_args_raw, str):
                                try:
                                    tc_args = json.loads(tc_args_raw)
                                except Exception:
                                    tc_args = {"raw": tc_args_raw}
                            else:
                                tc_args = tc_args_raw if isinstance(tc_args_raw, dict) else {}
                            tool_calls.append({
                                "id": str(tc_id),
                                "name": str(tc_name),
                                "args": tc_args,
                            })
            except asyncio.CancelledError:
                stop_reason = "cancelled"
                break
            except Exception as exc:
                logger.error("Supervisor LLM stream failed: %s", exc, exc_info=True)
                if conn is not None:
                    await send_update(
                        conn, state.session_id,
                        acp.update_agent_message_text(f"\nLLM stream failed: {exc}"),
                    )
                stop_reason = "refusal"
                break

            # Persist the assistant message.
            if full_text:
                state.history.append({"role": "assistant", "content": full_text})
                ctx_items.append(ChatMessage(role="assistant", content=[full_text]))

            if not tool_calls:
                stop_reason = "end_turn"
                break

            # Add the tool calls to chat_ctx and dispatch each. Persist
            # them to history too — the next prompt rebuilds chat_ctx
            # from history, and a tool-result row without its matching
            # call row is dropped as an orphan by the provider
            # formatter, silently erasing all prior tool evidence.
            for tc in tool_calls:
                arguments_json = json.dumps(tc["args"], ensure_ascii=False)
                state.history.append({
                    "role": "function_call",
                    "call_id": tc["id"],
                    "name": tc["name"],
                    "arguments": arguments_json,
                })
                ctx_items.append(FunctionCall(
                    call_id=tc["id"],
                    name=tc["name"],
                    arguments=arguments_json,
                ))

            for tc in tool_calls:
                if conn is not None:
                    await send_update(
                        conn, state.session_id,
                        build_tool_start(tc["id"], tc["name"], tc["args"]),
                    )

                # Execute the tool. The handler emits its own JSON-shape
                # result string; we surface that back as ToolCallProgress.
                tool_result = await self._dispatch_tool_call(
                    state, tc["name"], tc["args"], tools, loop,
                )

                # Track and persist the result.
                state.history.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "tool_name": tc["name"],
                    "content": tool_result,
                })
                ctx_items.append(FunctionCallOutput(
                    call_id=tc["id"],
                    name=tc["name"],
                    output=tool_result,
                    is_error=False,
                ))

                if conn is not None:
                    await send_update(
                        conn, state.session_id,
                        build_tool_complete(tc["id"], tc["name"], tool_result, tc["args"]),
                    )
                    # Native plan update mirroring for the todo tool.
                    if tc["name"] == "todo":
                        plan = build_plan_update_from_todo_result(tool_result)
                        if plan is not None:
                            await send_update(conn, state.session_id, plan)

            # Loop again — the model gets to see the tool results in
            # the next round.
            chat_ctx = ChatContext(items=ctx_items)

        else:
            # Loop guard fell through naturally — the supervisor kept
            # asking for more tool calls than we'd run in one turn.
            logger.warning(
                "ACP session %s: hit max tool loop (%d iterations)",
                state.session_id, _MAX_TOOL_LOOP,
            )
            stop_reason = "max_tokens"

        return stop_reason

    async def _dispatch_tool_call(
        self,
        state: SessionState,
        tool_name: str,
        args: dict[str, Any],
        tools: list,
        loop: asyncio.AbstractEventLoop,
    ) -> str:
        """Run a single tool call, returning its JSON-string result.

        Edit-approval for ``write_file`` / ``patch`` fires HERE on the
        event loop (NOT inside the tool handler's executor thread)
        because the approval coroutine needs to round-trip through the
        same loop the supervisor is awaiting on; running it from inside
        the executor would deadlock the loop on its own
        ``run_in_executor`` future.
        """
        # Event-loop-side edit approval. When the IDE denies, we never
        # invoke the actual tool — the supervisor gets the denial back
        # as the tool result and acknowledges it on the next round.
        approval_result = await self._maybe_approve_edit(state, tool_name, args)
        if approval_result is not None:
            return approval_result

        # Find the tool in the LiveKit-shaped list. The framework wraps
        # each handler as ``RawFunctionTool`` whose ``.info.name`` is
        # the registered tool name; the callable behind it is the async
        # ``_run(raw_arguments)`` wrapper from tools/_adapter.py.
        for tool in tools:
            info = getattr(tool, "info", None)
            name = getattr(info, "name", None) if info is not None else None
            if name == tool_name:
                handler = getattr(tool, "_callable", None) or tool
                # contextvars are per-task so the edit-approval requester
                # the supervisor loop set in this task is visible inside
                # the awaited handler.
                ctx = contextvars.copy_context()
                try:
                    if asyncio.iscoroutinefunction(handler):
                        result = await handler(raw_arguments=args)
                    else:
                        result = await loop.run_in_executor(
                            _EXECUTOR, ctx.run, lambda: handler(raw_arguments=args),
                        )
                except Exception as exc:
                    logger.warning("Tool %s raised: %s", tool_name, exc)
                    return f"Error: {tool_name} failed: {exc}"
                if isinstance(result, str):
                    return result
                if result is None:
                    return ""
                return str(result)

        return f"Error: {tool_name} failed: tool not registered in ACP surface"

    async def _maybe_approve_edit(
        self,
        state: SessionState,
        tool_name: str,
        args: dict[str, Any],
    ) -> Optional[str]:
        """Run the ACP edit-approval round-trip on the event loop.

        Returns a JSON error string when the IDE denies (caller surfaces
        it as the tool result); ``None`` when no approval was needed or
        the user approved. Policy + permission routing use the session
        that owns the tool call — never another concurrently-running
        session's.
        """
        from .edit_approval import build_edit_proposal, should_auto_approve_edit
        from .permissions import is_permissive_mode

        if is_permissive_mode():
            return None

        try:
            proposal = build_edit_proposal(tool_name, args)
        except Exception as exc:
            logger.warning("Could not build edit proposal for %s: %s", tool_name, exc)
            return json.dumps(
                {"error": f"Edit approval denied: could not prepare diff ({exc})"},
                ensure_ascii=False,
            )

        if proposal is None or self._conn is None:
            return None

        # Auto-approve under this session's workspace/session policy.
        policy, cwd = self._approval_policy_for_state(state)
        try:
            if should_auto_approve_edit(proposal, policy, cwd):
                return None
        except Exception:
            logger.debug("Auto-approve check failed", exc_info=True)

        from .edit_approval import build_acp_edit_tool_call
        from acp.schema import PermissionOption

        options = [
            PermissionOption(option_id="allow_once", kind="allow_once", name="Allow edit"),
            PermissionOption(option_id="deny", kind="reject_once", name="Deny"),
        ]
        tool_call = build_acp_edit_tool_call(proposal)
        try:
            response = await self._conn.request_permission(
                session_id=state.session_id,
                tool_call=tool_call,
                options=options,
            )
        except Exception as exc:
            logger.warning("Edit approval request failed: %s", exc)
            return json.dumps(
                {"error": "Edit approval denied (request failed)"},
                ensure_ascii=False,
            )
        outcome = getattr(response, "outcome", None) if response else None
        if (
            getattr(outcome, "outcome", None) == "selected"
            and getattr(outcome, "option_id", None) == "allow_once"
        ):
            return None
        return json.dumps(
            {"error": "Edit approval denied by ACP client; file was not modified."},
            ensure_ascii=False,
        )
