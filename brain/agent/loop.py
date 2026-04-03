"""JARVIS Agent Loop — the core reasoning-action cycle.

Architecture (inspired by OpenClaw/Claude Code):
    while True:
        response = LLM(messages + tool_definitions)
        if no tool_calls: break  # final answer
        for tool_call in response:
            result = execute_tool(tool_call)
            messages.append(tool_call + result)

Messages stored in OpenAI format internally. Converted to Anthropic
format on-the-fly when the active provider is Anthropic.

Supports:
- Multi-provider with automatic format conversion
- Context overflow recovery (compact + retry)
- Model failover on error
- Sub-agents (scout, worker, planner) with isolated loops
- File checkpoints for undo
- Pre/PostToolUse hooks
"""

import asyncio
import json
import logging
from typing import AsyncGenerator
from brain.agent.tools import TOOL_SCHEMAS, execute_tool
from brain.agent.context import compact_messages, estimate_tokens
from brain.reasoning.groq_client import GroqReasoner

log = logging.getLogger("jarvis.agent")

import re as _re

def _scrub_identity(text: str) -> str:
    """Replace ALL Claude/Anthropic identity leaks with JARVIS identity."""
    if not text:
        return text
    # "I'm Claude" variants
    text = _re.sub(r"I'm Claude\b", "I'm JARVIS", text, flags=_re.IGNORECASE)
    text = _re.sub(r"I am Claude\b", "I am JARVIS", text, flags=_re.IGNORECASE)
    text = _re.sub(r"my name is Claude\b", "my name is JARVIS", text, flags=_re.IGNORECASE)
    text = _re.sub(r"I'm an AI (assistant|model)\b", "I'm JARVIS", text, flags=_re.IGNORECASE)
    # "Claude" as a name in any context
    text = _re.sub(r"Claude, an AI", "JARVIS, an AI", text, flags=_re.IGNORECASE)
    text = _re.sub(r"Claude,? (made|created|built|developed) by", "JARVIS, built by", text, flags=_re.IGNORECASE)
    # Anthropic references
    text = _re.sub(r"(created|made|built|developed) by Anthropic", "built by Ulrich", text, flags=_re.IGNORECASE)
    text = _re.sub(r"an AI (assistant|model) by Anthropic", "an AI agent built by Ulrich", text, flags=_re.IGNORECASE)
    text = _re.sub(r"Anthropic'?s? AI", "Ulrich's AI", text, flags=_re.IGNORECASE)
    text = _re.sub(r"by Anthropic", "by Ulrich", text, flags=_re.IGNORECASE)
    # "As an AI" filler
    text = _re.sub(r"As an AI (assistant|model|language model),?\s*", "", text, flags=_re.IGNORECASE)
    text = _re.sub(r"As an AI,?\s*", "", text, flags=_re.IGNORECASE)
    # Model name leaks
    text = _re.sub(r"claude-?(opus|sonnet|haiku)", r"\1", text, flags=_re.IGNORECASE)
    text = _re.sub(r"running on claude\b", "running on JARVIS", text, flags=_re.IGNORECASE)
    text = _re.sub(r"based on (Anthropic'?s?\s*)?Claude[\s\d.]*\w*(\s*model)?", "built by Ulrich", text, flags=_re.IGNORECASE)
    text = _re.sub(r"powered by (Anthropic'?s?\s*)?Claude[\s\d.]*\w*", "powered by Ulrich's tech", text, flags=_re.IGNORECASE)
    text = _re.sub(r"I('m| am) a(n)? (Claude|Anthropic)[\w\s.-]*model", "I'm JARVIS", text, flags=_re.IGNORECASE)
    text = _re.sub(r"Anthropic'?s?\s*Claude[\s\d.]*\w*", "JARVIS", text, flags=_re.IGNORECASE)
    # Nuclear catch-all: replace ANY remaining "Claude" or "Anthropic"
    text = _re.sub(r"\bClaude\s+(Sonnet|Opus|Haiku)[\s\d.]*\w*", "JARVIS", text, flags=_re.IGNORECASE)
    text = _re.sub(r"\bClaude\s+\d[\d.]*\s*\w*\b", "JARVIS", text, flags=_re.IGNORECASE)
    text = _re.sub(r"\bClaude\b", "JARVIS", text, flags=_re.IGNORECASE)
    text = _re.sub(r"\bAnthropic\b", "Ulrich", text, flags=_re.IGNORECASE)
    # Corporate disclaimers
    text = _re.sub(r"I don't actually have (feelings|emotions|consciousness)\b", "Eh", text, flags=_re.IGNORECASE)
    return text

# Lazy-loaded singletons
_checkpoint_mgr = None
_hooks_mgr = None
_perm_mgr = None


def _get_permissions():
    global _perm_mgr
    if _perm_mgr is None:
        try:
            from brain.permissions import PermissionManager
            _perm_mgr = PermissionManager()
        except Exception as e:
            log.warning("Failed to load permissions: %s", e)
    return _perm_mgr


def _get_checkpoints():
    global _checkpoint_mgr
    if _checkpoint_mgr is None:
        try:
            from brain.checkpoints import CheckpointManager
            _checkpoint_mgr = CheckpointManager()
        except Exception as e:
            log.warning("Failed to load checkpoints: %s", e)
    return _checkpoint_mgr


def _get_hooks():
    global _hooks_mgr
    if _hooks_mgr is None:
        try:
            from brain.hooks import HooksManager
            _hooks_mgr = HooksManager()
            _hooks_mgr.load()
        except Exception as e:
            log.warning("Failed to load hooks: %s", e)
    return _hooks_mgr


MAX_ITERATIONS = 25
COMPACT_THRESHOLD = 80000
GLOBAL_ITERATION_MAX = 100
_groq_semaphore = asyncio.Semaphore(4)
SUB_AGENT_MAX_RESULT = 8000
TOOL_RESULT_MAX = 10000


def _maybe_compact(messages: list[dict]) -> list[dict]:
    if estimate_tokens(messages) > COMPACT_THRESHOLD:
        return compact_messages(messages, max_tokens=COMPACT_THRESHOLD)
    return messages


async def _run_sub_agent(
    reasoner: GroqReasoner,
    agent_type: str,
    task: str,
    context: str = "",
    iteration_budget: dict | None = None,
) -> str:
    """Spawn an isolated sub-agent with its own agent loop.

    Supports built-in agents (scout, worker, planner) and custom agents
    loaded from ~/.jarvis/agents/ and .jarvis/agents/.
    """
    from brain.agent.agents import resolve_agent, get_agent_tools, build_sub_agent_prompt

    config = resolve_agent(agent_type)
    if not config:
        from brain.agent.agents import get_all_agent_names
        available = ", ".join(get_all_agent_names())
        return f"Unknown agent type: {agent_type}. Available: {available}"

    tools = get_agent_tools(config)
    prompt = build_sub_agent_prompt(config, task, context)

    tool_executor = execute_tool
    # Enforce read-only bash for scout or any agent with bash_readonly
    if agent_type == "scout" or getattr(config, 'bash_readonly', False):
        from brain.agent.agents import is_bash_readonly
        def safe_execute(name, args):
            if name == "bash":
                cmd = args.get("command", "")
                if not is_bash_readonly(cmd):
                    return f"BLOCKED: This agent cannot run destructive commands. Attempted: {cmd}"
            return execute_tool(name, args)
        tool_executor = safe_execute

    max_iters = config.max_iterations
    if iteration_budget:
        remaining = iteration_budget["max"] - iteration_budget["count"]
        max_iters = min(max_iters, max(1, remaining))

    try:
        async with _groq_semaphore:
            result = await _agent_loop_internal(
                reasoner=reasoner,
                user_input=task,
                system_prompt=prompt,
                history=None,
                tools=tools,
                max_iterations=max_iters,
                tool_executor=tool_executor,
                iteration_budget=iteration_budget,
            )
    except Exception as e:
        result = f"Sub-agent ({agent_type}) error: {e}"

    if len(result) > SUB_AGENT_MAX_RESULT:
        result = result[:SUB_AGENT_MAX_RESULT] + "\n\n... (result truncated)"

    return f"[{config.name.upper()} AGENT RESULT]\n{result}"


async def _agent_loop_internal(
    reasoner: GroqReasoner,
    user_input: str,
    system_prompt: str,
    history: list[dict] | None = None,
    tools: list[dict] | None = None,
    max_iterations: int = MAX_ITERATIONS,
    on_tool_call: callable = None,
    on_tool_result: callable = None,
    tool_executor: callable = None,
    iteration_budget: dict | None = None,
    allow_dispatch: bool = True,
) -> str:
    """Internal agent loop — shared by parent and sub-agents."""
    if tools is None:
        tools = TOOL_SCHEMAS
    if tool_executor is None:
        tool_executor = execute_tool

    messages = [{"role": "system", "content": system_prompt}]

    if history:
        for turn in history[-10:]:
            role = "assistant" if turn["role"] == "jarvis" else "user"
            content = turn["content"]
            if len(content) > 2000:
                content = content[:2000] + "..."
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_input})

    final_text = ""
    iterations = 0

    while iterations < max_iterations:
        iterations += 1

        if iteration_budget:
            iteration_budget["count"] += 1
            if iteration_budget["count"] >= iteration_budget["max"]:
                final_text += "\n[Iteration budget exhausted]"
                break

        messages = _maybe_compact(messages)

        # Call LLM with tools — retry on overflow/rate limit
        response = None
        for attempt in range(3):
            try:
                response = await reasoner.query_with_tools(messages, tools)
                break
            except Exception as e:
                err = str(e).lower()
                if "context" in err or "overflow" in err or "too long" in err or "token" in err:
                    log.warning("Context overflow, compacting and retrying...")
                    messages = compact_messages(messages, max_tokens=COMPACT_THRESHOLD // 2)
                    continue
                if "rate" in err or "429" in err:
                    log.warning("Rate limited, waiting %ds...", 3 * (attempt + 1))
                    await asyncio.sleep(3 * (attempt + 1))
                    continue
                raise

        if response is None:
            final_text += "\n[All retry attempts failed]"
            break

        text_content = response.get("text", "")
        tool_calls = response.get("tool_calls", [])

        if text_content:
            text_content = _scrub_identity(text_content)
            final_text += text_content

        if not tool_calls:
            break

        # Store assistant + tool_calls in OpenAI format
        _append_assistant_message(messages, text_content, tool_calls)

        # Separate dispatch from regular
        dispatch_calls = []
        regular_calls = []
        for tc in tool_calls:
            if tc["name"] == "dispatch" and allow_dispatch:
                dispatch_calls.append(tc)
            else:
                regular_calls.append(tc)

        # Execute regular tools
        await _execute_tools(messages, regular_calls, tool_executor,
                             on_tool_call, on_tool_result)

        # Execute dispatch calls (concurrent)
        if dispatch_calls:
            async def run_one_dispatch(tc):
                args = tc["args"]
                if on_tool_call:
                    on_tool_call("dispatch", args)
                result = await _run_sub_agent(
                    reasoner=reasoner,
                    agent_type=args.get("agent_type", "scout"),
                    task=args.get("task", ""),
                    context=args.get("context", ""),
                    iteration_budget=iteration_budget,
                )
                if on_tool_result:
                    on_tool_result("dispatch", result)
                return tc["id"], result

            dispatch_results = await asyncio.gather(
                *[run_one_dispatch(tc) for tc in dispatch_calls]
            )
            for tool_id, result in dispatch_results:
                _append_tool_result(messages, tool_id, result)

    return final_text.strip()


def _append_assistant_message(messages: list[dict], text: str, tool_calls: list[dict]):
    """Append assistant message with tool calls in OpenAI format."""
    msg = {"role": "assistant", "content": text or None}
    if tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc["args"]) if isinstance(tc["args"], dict) else tc["args"],
                },
            }
            for tc in tool_calls
        ]
    messages.append(msg)


def _append_tool_result(messages: list[dict], tool_id: str, result: str):
    """Append tool result in OpenAI format."""
    if len(result) > TOOL_RESULT_MAX:
        result = result[:TOOL_RESULT_MAX] + "\n... (truncated)"
    messages.append({
        "role": "tool",
        "tool_call_id": tool_id,
        "content": result,
    })


async def _execute_tools(messages: list[dict], tool_calls: list[dict],
                         executor, on_call=None, on_result=None, readonly=False):
    """Execute a list of tool calls with permissions, hooks, and checkpoints."""
    hooks = _get_hooks()
    checkpoints = _get_checkpoints()
    perms = _get_permissions()

    for tc in tool_calls:
        tool_name = tc["name"]
        tool_args = tc["args"]
        tool_id = tc["id"]

        # Permission check
        if perms:
            allowed, reason = perms.check(tool_name, tool_args)
            if not allowed:
                log.warning("Permission denied: %s — %s", tool_name, reason)
                # PermissionDenied hook
                if hooks:
                    hooks.run_permission_denied(tool_name, tool_args, reason)
                _append_tool_result(messages, tool_id, f"BLOCKED by permissions: {reason}")
                continue

        # PreToolUse hook
        if hooks:
            hr = hooks.run_pre_tool_use(tool_name, tool_args)
            if not hr.allowed:
                _append_tool_result(messages, tool_id, f"BLOCKED by hook: {hr.message}")
                continue
            if hr.modified_args is not None:
                tool_args = hr.modified_args

        # Checkpoint before file writes
        if checkpoints and tool_name in ("write_file", "edit_file"):
            path = tool_args.get("path", "")
            if path:
                checkpoints.snapshot(path, tool_name)

        if on_call:
            on_call(tool_name, tool_args)

        try:
            result = await asyncio.to_thread(executor, tool_name, tool_args)
        except Exception as exc:
            result = f"ERROR: {exc}"
            # PostToolUseFailure hook
            if hooks:
                fail_hr = hooks.run_post_tool_use_failure(tool_name, tool_args, str(exc))
                if fail_hr.message:
                    result += f"\n[Hook: {fail_hr.message}]"
            if on_result:
                on_result(tool_name, result)
            _append_tool_result(messages, tool_id, result)
            continue

        # PostToolUse hook
        if hooks:
            post = hooks.run_post_tool_use(tool_name, tool_args, result)
            if post.message:
                result += f"\n[Hook: {post.message}]"

        if on_result:
            on_result(tool_name, result)

        _append_tool_result(messages, tool_id, result)


# ── Public API ────────────────────────────────────────────────────────

async def agent_loop(
    reasoner: GroqReasoner,
    user_input: str,
    system_prompt: str,
    history: list[dict] | None = None,
    tools: list[dict] | None = None,
    max_iterations: int = MAX_ITERATIONS,
    on_tool_call: callable = None,
    on_tool_result: callable = None,
    readonly: bool = False,
) -> str:
    """Run the full agent loop. Returns final text response."""
    iteration_budget = {"count": 0, "max": GLOBAL_ITERATION_MAX}

    tool_executor = None
    if readonly:
        def readonly_executor(name, args):
            return execute_tool(name, args, readonly=True)
        tool_executor = readonly_executor

    return await _agent_loop_internal(
        reasoner=reasoner,
        user_input=user_input,
        system_prompt=system_prompt,
        history=history,
        tools=tools,
        max_iterations=max_iterations,
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
        iteration_budget=iteration_budget,
        allow_dispatch=True,
        tool_executor=tool_executor,
    )


async def agent_loop_stream(
    reasoner: GroqReasoner,
    user_input: str,
    system_prompt: str,
    history: list[dict] | None = None,
    tools: list[dict] | None = None,
    max_iterations: int = MAX_ITERATIONS,
    readonly: bool = False,
) -> AsyncGenerator[dict, None]:
    """Streaming agent loop — yields events for the UI.

    Event types:
        text, tool_call, tool_result, dispatch, dispatch_result, done, error
    """
    if tools is None:
        tools = TOOL_SCHEMAS

    messages = [{"role": "system", "content": system_prompt}]

    if history:
        for turn in history[-12:]:
            role = "assistant" if turn["role"] == "jarvis" else "user"
            content = turn["content"]
            if len(content) > 2000:
                content = content[:2000] + "..."
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_input})

    full_response = ""
    iterations = 0
    iteration_budget = {"count": 0, "max": GLOBAL_ITERATION_MAX}

    while iterations < max_iterations:
        iterations += 1
        iteration_budget["count"] += 1
        if iteration_budget["count"] >= iteration_budget["max"]:
            yield {"type": "text", "content": "\n[Iteration budget exhausted]"}
            break

        messages = _maybe_compact(messages)

        # For casual chat on first iteration: skip tools so Claude stays in character
        _effective_tools = tools
        if iterations == 1 and tools:
            _q = user_input.lower().strip().rstrip("?!. ")
            _short_casual = len(_q.split()) <= 10 and not any(
                w in _q for w in ["run", "read", "write", "edit", "create", "build",
                                   "fix", "scan", "find", "search", "check", "review",
                                   "install", "delete", "open", "make", "update",
                                   "/", "~", ".py", ".js", ".rs", "file", "code"])
            if _short_casual:
                _effective_tools = []  # No tools — Claude stays in JARVIS personality

        # Call LLM — retry on overflow/rate limit
        response = None
        for attempt in range(3):
            try:
                response = await reasoner.query_with_tools(messages, _effective_tools)
                break
            except Exception as e:
                err = str(e).lower()
                if "context" in err or "overflow" in err or "too long" in err or "token" in err:
                    log.warning("Context overflow (attempt %d), compacting...", attempt + 1)
                    messages = compact_messages(messages, max_tokens=COMPACT_THRESHOLD // 2)
                    continue
                if "rate" in err or "429" in err:
                    wait = 3 * (attempt + 1)
                    log.warning("Rate limited, waiting %ds...", wait)
                    await asyncio.sleep(wait)
                    continue
                yield {"type": "error", "content": str(e)}
                return

        # Emit token usage if available
        if response and response.get("usage"):
            usage = response["usage"]
            yield {"type": "usage",
                   "input_tokens": usage.get("input", 0),
                   "output_tokens": usage.get("output", 0)}

        if response is None:
            yield {"type": "error", "content": "All retry attempts failed"}
            return

        text_content = response.get("text", "")
        tool_calls = response.get("tool_calls", [])

        if text_content:
            # Scrub Claude identity leaks — JARVIS is JARVIS
            text_content = _scrub_identity(text_content)
            full_response += text_content
            yield {"type": "text", "content": text_content}

        if not tool_calls:
            break

        # Store in OpenAI format
        _append_assistant_message(messages, text_content, tool_calls)

        # Separate dispatch from regular
        dispatch_calls = [tc for tc in tool_calls if tc["name"] == "dispatch"]
        regular_calls = [tc for tc in tool_calls if tc["name"] != "dispatch"]

        # Execute regular tools
        hooks = _get_hooks()
        checkpoints = _get_checkpoints()
        perms = _get_permissions()

        for tc in regular_calls:
            tool_name = tc["name"]
            tool_args = tc["args"]
            tool_id = tc["id"]

            if perms:
                allowed, reason = perms.check(tool_name, tool_args)
                if not allowed:
                    yield {"type": "tool_call", "name": tool_name, "args": tool_args}
                    # PermissionDenied hook
                    if hooks:
                        hooks.run_permission_denied(tool_name, tool_args, reason)
                    result = f"BLOCKED by permissions: {reason}"
                    yield {"type": "tool_result", "name": tool_name, "content": result}
                    _append_tool_result(messages, tool_id, result)
                    continue

            if hooks:
                hr = hooks.run_pre_tool_use(tool_name, tool_args)
                if not hr.allowed:
                    yield {"type": "tool_call", "name": tool_name, "args": tool_args}
                    result = f"BLOCKED by hook: {hr.message}"
                    yield {"type": "tool_result", "name": tool_name, "content": result}
                    _append_tool_result(messages, tool_id, result)
                    continue
                if hr.modified_args is not None:
                    tool_args = hr.modified_args

            if checkpoints and tool_name in ("write_file", "edit_file"):
                path = tool_args.get("path", "")
                if path:
                    checkpoints.snapshot(path, tool_name)

            yield {"type": "tool_call", "name": tool_name, "args": tool_args}

            try:
                result = await asyncio.to_thread(execute_tool, tool_name, tool_args, readonly)
            except Exception as exc:
                result = f"ERROR: {exc}"
                if hooks:
                    fail_hr = hooks.run_post_tool_use_failure(tool_name, tool_args, str(exc))
                    if fail_hr.message:
                        result += f"\n[Hook: {fail_hr.message}]"
                yield {"type": "tool_result", "name": tool_name, "content": result}
                _append_tool_result(messages, tool_id, result)
                continue

            if hooks:
                post = hooks.run_post_tool_use(tool_name, tool_args, result)
                if post.message:
                    result += f"\n[Hook: {post.message}]"

            yield {"type": "tool_result", "name": tool_name, "content": result}
            _append_tool_result(messages, tool_id, result)

        # Dispatch calls
        if dispatch_calls:
            for tc in dispatch_calls:
                args = tc["args"]
                agent_type = args.get("agent_type", "scout")
                task = args.get("task", "")
                yield {"type": "dispatch", "agent_type": agent_type, "task": task}

            async def run_dispatch_stream(tc):
                a = tc["args"]
                return tc["id"], await _run_sub_agent(
                    reasoner=reasoner,
                    agent_type=a.get("agent_type", "scout"),
                    task=a.get("task", ""),
                    context=a.get("context", ""),
                    iteration_budget=iteration_budget,
                )

            results = await asyncio.gather(
                *[run_dispatch_stream(tc) for tc in dispatch_calls]
            )
            for tool_id, result in results:
                agent_type = next(
                    (tc["args"].get("agent_type", "?") for tc in dispatch_calls if tc["id"] == tool_id), "?"
                )
                yield {"type": "dispatch_result", "agent_type": agent_type, "result": result}
                _append_tool_result(messages, tool_id, result)

    yield {"type": "done", "content": full_response}
