"""JARVIS Agent Loop — the core reasoning-action cycle.

Architecture:
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
from src.agent.tools import TOOL_SCHEMAS, get_active_tools, execute_tool
from src.agent.context import (
    compact_messages, estimate_tokens, AutoCompactor,
    repair_tool_pairs, check_context_window,
)
from src.agent.tool_registry import (
    get_concurrency_safe_tools, get_result_size_limit, persist_large_result,
)
from src.agent.cost_tracker import get_tracker as get_cost_tracker
from src.reasoning.groq_client import GroqReasoner
from src.ecc.tool_fixer import ToolFixer
from src.ecc.goal_verifier import GoalVerifier

log = logging.getLogger("jarvis.agent")

# Concurrency-safe tools (from registry)
_CONCURRENCY_SAFE = get_concurrency_safe_tools()

import hashlib as _hashlib
import re as _re


def _tool_call_sig(tool_name: str, tool_args: dict) -> str:
    """Stable hash of a (tool_name, args) pair for semantic-retry detection."""
    key = f"{tool_name}:{sorted(tool_args.items()) if isinstance(tool_args, dict) else tool_args}"
    return _hashlib.md5(key.encode()).hexdigest()


def _is_tool_failure(result: str) -> bool:
    """Return True if a tool result string indicates a non-zero / error outcome."""
    if not result:
        return False
    if _re.match(r"exit_code=0\b", result):
        return False
    if _re.match(r"exit_code=[1-9]", result):
        return True
    return result.startswith(("ERROR:", "BLOCKED:", "Command failed", "Syntax error",
                               "[Tool calling failed", "[All retry"))


def _validate_tool_calls(tool_calls: list) -> list:
    """Filter and normalize tool calls, dropping any missing required keys."""
    valid = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            log.warning("Skipping non-dict tool call: %s", type(tc))
            continue
        if "name" not in tc:
            log.warning("Skipping tool call missing 'name': %s", tc)
            continue
        tc.setdefault("args", {})
        tc.setdefault("id", f"tc_{id(tc)}")
        valid.append(tc)
    return valid


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
            from src.permissions import PermissionManager
            _perm_mgr = PermissionManager()
        except Exception as e:
            log.warning("Failed to load permissions: %s", e)
    return _perm_mgr


def _get_checkpoints():
    global _checkpoint_mgr
    if _checkpoint_mgr is None:
        try:
            from src.checkpoints import CheckpointManager
            _checkpoint_mgr = CheckpointManager()
        except Exception as e:
            log.warning("Failed to load checkpoints: %s", e)
    return _checkpoint_mgr


def _get_hooks():
    global _hooks_mgr
    if _hooks_mgr is None:
        try:
            from src.hooks import HooksManager
            _hooks_mgr = HooksManager()
            _hooks_mgr.load()
        except Exception as e:
            log.warning("Failed to load hooks: %s", e)
    return _hooks_mgr


MAX_ITERATIONS = 999
COMPACT_THRESHOLD = 80000
GLOBAL_ITERATION_MAX = 999
_groq_semaphore = asyncio.Semaphore(4)
SUB_AGENT_MAX_RESULT = 20000
TOOL_RESULT_MAX = 20000

# Session directory for persisted tool results
_session_dir: str = ""


def _get_session_dir() -> str:
    global _session_dir
    if not _session_dir:
        import tempfile
        _session_dir = tempfile.mkdtemp(prefix="jarvis-session-")
    return _session_dir


def _maybe_compact(messages: list[dict], compactor: AutoCompactor | None = None) -> list[dict]:
    """Compact messages using AutoCompactor if available, else legacy threshold."""
    if compactor:
        messages, did_compact = compactor.maybe_compact(messages)
        if did_compact:
            hooks = _get_hooks()
            if hooks:
                budget = compactor.get_budget()
                hooks.run_context_compacted(budget.used_tokens + 1000, budget.used_tokens)
        return messages
    if estimate_tokens(messages) > COMPACT_THRESHOLD:
        return compact_messages(messages, max_tokens=COMPACT_THRESHOLD)
    return messages


async def _maybe_compact_async(messages: list[dict], compactor: AutoCompactor | None = None) -> list[dict]:
    """Async compaction — uses LLM-based smart_compact when compactor has a summarizer."""
    if compactor:
        if compactor.should_compact(messages) and compactor._summarizer is not None:
            messages = await compactor.auto_compact_async(messages)
            hooks = _get_hooks()
            if hooks:
                budget = compactor.get_budget()
                hooks.run_context_compacted(budget.used_tokens + 1000, budget.used_tokens)
            return messages
        # Fall back to sync maybe_compact for sub-threshold or no summarizer
        return _maybe_compact(messages, compactor)
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
    from src.agent.agents import resolve_agent, get_agent_tools, build_sub_agent_prompt

    config = resolve_agent(agent_type)
    if not config:
        from src.agent.agents import get_all_agent_names
        available = ", ".join(get_all_agent_names())
        return f"Unknown agent type: {agent_type}. Available: {available}"

    tools = get_agent_tools(config)
    prompt = build_sub_agent_prompt(config, task, context)

    tool_executor = execute_tool
    # Enforce read-only bash for scout or any agent with bash_readonly
    if agent_type == "scout" or getattr(config, 'bash_readonly', False):
        from src.agent.agents import is_bash_readonly
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
    consolidate_fn: callable = None,
) -> str:
    """Internal agent loop — shared by parent and sub-agents."""
    if tools is None:
        tools = get_active_tools()
    if tool_executor is None:
        tool_executor = execute_tool

    # Initialize AutoCompactor with model-aware context limits
    model_name = getattr(reasoner, 'model', '') or getattr(reasoner, 'active_model_name', '') or ''
    compactor = AutoCompactor(model=model_name, consolidate_fn=consolidate_fn)

    # Context-window guard — hard minimum 16K, warn below 32K
    _ctx_ok, _ctx_warn = check_context_window(model_name)
    if not _ctx_ok:
        return f"[JARVIS] Cannot run agent: {_ctx_warn}"
    if _ctx_warn:
        log.warning(_ctx_warn)

    # Adaptive history: budget by character count, not turn count.
    # Voice conversations have many short turns — turn limits miss older context.
    from src.agent.context import MODEL_LIMITS, DEFAULT_MAX_TOKENS
    ctx_limit = MODEL_LIMITS.get(model_name, DEFAULT_MAX_TOKENS)
    max_content_len = 2000 if ctx_limit <= 32000 else 4000 if ctx_limit <= 200000 else 8000
    # Reserve ~25% of context for history (~3 chars per token)
    max_history_chars = (ctx_limit // 4) * 3

    messages = [{"role": "system", "content": system_prompt}]

    if history:
        selected = []
        char_budget = max_history_chars
        for turn in reversed(history):
            content = turn["content"]
            if len(content) > max_content_len:
                content = content[:max_content_len] + "..."
            cost = len(content) + 20
            if char_budget - cost < 0 and selected:
                break
            char_budget -= cost
            role = "assistant" if turn["role"] == "jarvis" else "user"
            selected.append({"role": role, "content": content})
        for msg in reversed(selected):
            messages.append(msg)

    messages.append({"role": "user", "content": user_input})

    # ── Bootstrap budget tracking ──────────────────────────────────────
    # Warn when the initial injection (system + history + user turn) already
    # consumes ≥85% of the context window before any agent loop iterations.
    # Truncate (prune oldest history) if at 100%.
    from src.agent.context import estimate_tokens, SAFETY_MARGIN
    _boot_tokens = estimate_tokens(messages)
    _boot_pct    = _boot_tokens / ctx_limit * 100 if ctx_limit else 0
    if _boot_pct >= 100:
        # Hard prune: drop oldest non-system/non-user-input messages until < 85%
        _target = int(ctx_limit * 0.5)
        _sys_msgs = [m for m in messages if m.get("role") == "system"]
        _hist_msgs = [m for m in messages[len(_sys_msgs):-1]]  # exclude last user msg
        _last_user = messages[-1:]
        while estimate_tokens(_sys_msgs + _hist_msgs + _last_user) > _target and len(_hist_msgs) > 2:
            _hist_msgs.pop(0)
        messages = _sys_msgs + _hist_msgs + _last_user
        log.warning("Bootstrap budget exceeded (%.0f%%) — pruned %d history messages",
                    _boot_pct, len(messages) - len(_sys_msgs) - 1)
    elif _boot_pct >= 85:
        log.warning("Bootstrap budget at %.0f%% (%d/%d tokens) before first LLM call",
                    _boot_pct, _boot_tokens, ctx_limit)

    final_text = ""
    iterations = 0
    _failed_call_sigs_ns: set[str] = set()  # semantic-retry tracking (non-streaming)
    _write_ops: list[str] = []  # track write/edit/destructive bash operations for verifier
    _ecc_fixer = ToolFixer()   # ECC-L2: per-turn tool parameter mutation

    while iterations < max_iterations:
        iterations += 1

        if iteration_budget:
            iteration_budget["count"] += 1
            if iteration_budget["count"] >= iteration_budget["max"]:
                final_text += "\n[Iteration budget exhausted]"
                break

        messages = _maybe_compact(messages, compactor=compactor)

        # Repair orphaned tool_use/tool_result pairs before every LLM call.
        # Orphaned tool_results cause a 400 on Anthropic and silent errors on
        # OpenAI — this must run even when no compaction happened.
        messages = repair_tool_pairs(messages)

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
        tool_calls = _validate_tool_calls(response.get("tool_calls", []))

        if text_content:
            text_content = _scrub_identity(text_content)
            final_text += text_content

        if not tool_calls:
            # Auto-continue if the response suggests more work ahead
            _forward_signals = (
                "next", "now i'll", "now i will", "i'll now", "i will now",
                "then i'll", "then i will", "let me", "i need to", "i'll create",
                "i'll write", "i'll build", "i'll implement", "i'll add",
                "moving on", "continuing", "proceeding", "step ", "phase ",
                "first,", "second,", "third,", "finally,",
            )
            _tc_lower = text_content.lower()
            _should_continue = (
                tools
                and any(sig in _tc_lower for sig in _forward_signals)
                and iterations < max_iterations
            )
            if _should_continue:
                _append_assistant_message(messages, text_content, [])
                messages.append({"role": "user", "content": "Continue."})
                continue
            break

        # Store assistant + tool_calls in OpenAI format
        _append_assistant_message(messages, text_content, tool_calls)

        # Separate sentinel tools from regular ones
        dispatch_calls = []
        regular_calls = []
        sentinel_calls = []
        _SENTINEL_TOOLS = {
            "EnterPlanMode", "ExitPlanMode", "EnterWorktree", "ExitWorktree",
            "SendMessage", "TeamCreate", "TeamDelete", "Skill", "LSP",
            "ScheduleCron", "RemoteTrigger", "ask_user",
        }
        for tc in tool_calls:
            if tc["name"] == "dispatch" and allow_dispatch:
                dispatch_calls.append(tc)
            elif tc["name"] in _SENTINEL_TOOLS:
                sentinel_calls.append(tc)
            else:
                regular_calls.append(tc)

        # Handle sentinel tools — these change loop/brain state
        for tc in sentinel_calls:
            name, args, tid = tc["name"], tc["args"], tc["id"]
            if on_tool_call:
                on_tool_call(name, args)
            result = await _handle_sentinel_tool(name, args)
            if on_tool_result:
                on_tool_result(name, result)
            _append_tool_result(messages, tid, result, tool_name=name)

        # Track write operations for verifier threshold
        _WRITE_TOOLS = {"write_file", "edit_file"}
        _WRITE_BASH_PATTERNS = (" > ", " >> ", " >| ", "tee ", "mkdir ", "rm ", "mv ", "cp ")
        for _tc in regular_calls:
            _tc_name = _tc["name"]
            if _tc_name in _WRITE_TOOLS:
                _write_ops.append(f"{_tc_name}: {_tc['args'].get('path', _tc['args'].get('file_path', '?'))}")
            elif _tc_name == "bash":
                _cmd = _tc["args"].get("command", "")
                if any(p in _cmd for p in _WRITE_BASH_PATTERNS):
                    _write_ops.append(f"bash: {_cmd[:80]}")

        # Execute regular tools (respect plan mode)
        _readonly = _loop_state.get("plan_mode", False)
        await _execute_tools(messages, regular_calls, tool_executor,
                             on_tool_call, on_tool_result, readonly=_readonly,
                             failed_sigs=_failed_call_sigs_ns,
                             ecc_fixer=_ecc_fixer)

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
                _append_tool_result(messages, tool_id, result, tool_name="dispatch")

    # Auto-spawn verifier for non-trivial work (≥3 write/edit/destructive ops)
    _VERIFIER_THRESHOLD = 3
    if allow_dispatch and len(_write_ops) >= _VERIFIER_THRESHOLD:
        modified_summary = "\n".join(_write_ops[:20])  # cap at 20 for context
        verify_task = (
            f"Verify the work just completed.\n\n"
            f"Original task: {user_input[:500]}\n\n"
            f"Operations performed:\n{modified_summary}"
        )
        log.info("Non-trivial work detected (%d ops) — spawning verifier", len(_write_ops))
        try:
            verdict = await _run_sub_agent(
                reasoner=reasoner,
                agent_type="verifier",
                task=verify_task,
                iteration_budget=iteration_budget,
            )
            final_text += f"\n\n{verdict}"
        except Exception as e:
            log.warning("Verifier failed: %s", e)

    # Stop hook — final verification before task completion
    hooks = _get_hooks()
    if hooks:
        hooks.run_stop()

    return final_text.strip()


def _append_assistant_message(messages: list[dict], text: str, tool_calls: list[dict]):
    """Append assistant message with tool calls in OpenAI format. Handles malformed args."""
    msg = {"role": "assistant", "content": text or None}
    if tool_calls:
        formatted = []
        for tc in tool_calls:
            try:
                args_str = json.dumps(tc["args"]) if isinstance(tc["args"], dict) else str(tc["args"])
            except (TypeError, ValueError):
                args_str = str(tc["args"])
            formatted.append({
                "id": tc.get("id", f"tc_{len(formatted)}"),
                "type": "function",
                "function": {"name": tc["name"], "arguments": args_str},
            })
        msg["tool_calls"] = formatted
    messages.append(msg)


async def _handle_sentinel_tool(name: str, args: dict) -> str:
    """Handle tools that change loop/brain state instead of executing directly."""

    if name == "EnterPlanMode":
        # Switch tools to read-only set
        _loop_state["plan_mode"] = True
        return "Plan mode activated. I will analyze and suggest changes without executing them."

    elif name == "ExitPlanMode":
        _loop_state["plan_mode"] = False
        return "Plan mode deactivated. I can now execute changes."

    elif name == "EnterWorktree":
        worktree_name = args.get("name", "jarvis-worktree")
        try:
            import subprocess
            result = await asyncio.to_thread(
                subprocess.run,
                ["git", "worktree", "add", f"/tmp/{worktree_name}", "-b", worktree_name],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                _loop_state["worktree"] = f"/tmp/{worktree_name}"
                return f"Created worktree at /tmp/{worktree_name} on branch {worktree_name}"
            return f"Failed to create worktree: {result.stderr.strip()}"
        except Exception as e:
            return f"Worktree error: {e}"

    elif name == "ExitWorktree":
        wt = _loop_state.get("worktree")
        if wt:
            try:
                import subprocess
                await asyncio.to_thread(
                    subprocess.run,
                    ["git", "worktree", "remove", wt, "--force"],
                    capture_output=True, timeout=30
                )
                _loop_state["worktree"] = None
                return f"Removed worktree {wt}"
            except Exception as e:
                return f"Worktree cleanup error: {e}"
        return "No active worktree."

    elif name == "SendMessage":
        to = args.get("to", "")
        message = args.get("message", "")
        # In single-agent mode, this is a no-op. In multi-agent (swarm), route to target.
        try:
            from src.agent.swarm import Swarm
            swarm = Swarm()
            if swarm.has_agent(to):
                return swarm.send_message(to, message)
        except Exception:
            pass
        return f"Message queued for '{to}': {message[:100]}"

    elif name == "TeamCreate":
        team_name = args.get("name", "team")
        agents = args.get("agents", [])
        return f"Team '{team_name}' created with agents: {', '.join(agents)}"

    elif name == "TeamDelete":
        team_name = args.get("name", "")
        return f"Team '{team_name}' deleted."

    elif name == "Skill":
        skill_name = args.get("name", "")
        skill_args = args.get("args", "")
        try:
            from src.skills import SkillManager
            sm = SkillManager()
            sm.discover()
            skill = sm.get(skill_name)
            if skill:
                rendered = skill.render(skill_args)
                return f"Skill '{skill_name}' prompt:\n{rendered}"
            return f"Skill '{skill_name}' not found. Available: {', '.join(s.name for s in sm.list_skills())}"
        except Exception as e:
            return f"Skill error: {e}"

    elif name == "LSP":
        action = args.get("action", "diagnostics")
        file_path = args.get("file_path", "")
        try:
            from src.lsp.manager import LspManager
            lsp = LspManager()
            if action == "diagnostics":
                diags = lsp.get_diagnostics(file_path)
                if diags:
                    return "\n".join(f"  {d['severity']}: {d['message']} (line {d.get('line', '?')})" for d in diags)
                return f"No diagnostics for {file_path}"
            elif action == "symbols":
                symbols = lsp.get_symbols(file_path)
                return "\n".join(f"  {s['kind']} {s['name']} (line {s.get('line', '?')})" for s in symbols) if symbols else "No symbols found."
            elif action == "hover":
                pos = args.get("position", {})
                info = lsp.hover(file_path, pos.get("line", 0), pos.get("character", 0))
                return info or "No hover info."
            return f"Unknown LSP action: {action}"
        except Exception as e:
            return f"LSP unavailable: {e}"

    elif name == "ScheduleCron":
        action = args.get("action", "list")
        try:
            from src.config import JARVIS_HOME
            import json
            cron_file = JARVIS_HOME / "cron_jobs.json"
            jobs = json.loads(cron_file.read_text()) if cron_file.exists() else []
            if action == "list":
                if not jobs:
                    return "No scheduled jobs."
                return "\n".join(f"  {j['id']}: {j['schedule']} — {j['command']}" for j in jobs)
            elif action == "create":
                new_job = {
                    "id": f"job-{len(jobs)+1}",
                    "schedule": args.get("schedule", "0 * * * *"),
                    "command": args.get("command", ""),
                }
                jobs.append(new_job)
                cron_file.parent.mkdir(parents=True, exist_ok=True)
                cron_file.write_text(json.dumps(jobs, indent=2))
                return f"Created job {new_job['id']}: {new_job['schedule']} — {new_job['command']}"
            elif action == "delete":
                job_id = args.get("job_id", "")
                jobs = [j for j in jobs if j["id"] != job_id]
                cron_file.write_text(json.dumps(jobs, indent=2))
                return f"Deleted job {job_id}"
            return f"Unknown cron action: {action}"
        except Exception as e:
            return f"Cron error: {e}"

    elif name == "RemoteTrigger":
        action = args.get("action", "list")
        try:
            from src.bridge.bridgeApi import BridgeApi
            api = BridgeApi()
            if action == "list":
                triggers = api.list_triggers()
                if not triggers:
                    return "No remote triggers configured."
                return "\n".join(f"  {t['id']}: {t['schedule']} — {t['prompt'][:50]}" for t in triggers)
            return f"Remote trigger action '{action}' acknowledged."
        except Exception as e:
            return f"Remote trigger: {e}"

    elif name == "ask_user":
        question = args.get("question", args.get("message", ""))
        # In non-interactive loops, return the question as prompt for the LLM
        # to reformulate. In interactive contexts, the shell layer handles this.
        return f"[Awaiting user response to: {question}]"

    return f"Unknown sentinel tool: {name}"


# Mutable loop state for sentinel tools
_loop_state: dict = {
    "plan_mode": False,
    "worktree": None,
}


def _append_tool_result(messages: list[dict], tool_id: str, result: str,
                        tool_name: str = ""):
    """Append tool result in OpenAI format, with persistence for large outputs."""
    # Try result persistence for large outputs
    limit = get_result_size_limit(tool_name) if tool_name else TOOL_RESULT_MAX
    if len(result) > limit and limit > 0:
        try:
            tool_result = persist_large_result(
                tool_name, tool_id, result, _get_session_dir()
            )
            if tool_result.persisted_path:
                result = (
                    f"Output too large ({len(result):,} chars). "
                    f"Full output saved to: {tool_result.persisted_path}\n\n"
                    f"Preview (first 2000 chars):\n{tool_result.content}"
                )
            else:
                result = tool_result.content
        except Exception:
            # Fallback to simple truncation
            result = result[:limit] + "\n... (truncated)"
    messages.append({
        "role": "tool",
        "tool_call_id": tool_id,
        "content": result,
    })


async def _execute_tools(messages: list[dict], tool_calls: list[dict],
                         executor, on_call=None, on_result=None, readonly=False,
                         failed_sigs: set | None = None,
                         ecc_fixer: "ToolFixer | None" = None):
    """Execute tool calls — parallel for concurrency-safe, sequential for others."""
    if failed_sigs is None:
        failed_sigs = set()
    hooks = _get_hooks()
    checkpoints = _get_checkpoints()
    perms = _get_permissions()

    # Use registry for concurrency classification (falls back to hardcoded set)
    parallel_calls = [tc for tc in tool_calls if tc["name"] in _CONCURRENCY_SAFE]
    sequential_calls = [tc for tc in tool_calls if tc["name"] not in _CONCURRENCY_SAFE]

    # Run read-only tools in parallel
    if len(parallel_calls) > 1:
        async def _run_one(tc):
            name, args, tid = tc["name"], tc["args"], tc["id"]
            if perms:
                allowed, reason = perms.check(name, args)
                if not allowed:
                    return tid, f"BLOCKED by permissions: {reason}"
            if hooks:
                hr = hooks.run_pre_tool_use(name, args)
                if not hr.allowed:
                    return tid, f"BLOCKED by hook: {hr.message}"
                if hr.modified_args is not None:
                    args = hr.modified_args
            if on_call:
                on_call(name, args)
            try:
                result = await asyncio.to_thread(executor, name, args)
            except Exception as exc:
                result = f"ERROR: {exc}"
            if hooks:
                post = hooks.run_post_tool_use(name, args, result)
                if post.message:
                    result += f"\n[Hook: {post.message}]"
            if on_result:
                on_result(name, result)
            return tid, result

        results = await asyncio.gather(*[_run_one(tc) for tc in parallel_calls])
        for tc_ref, (tid, result) in zip(parallel_calls, results):
            _append_tool_result(messages, tid, result, tool_name=tc_ref["name"])
    elif parallel_calls:
        # Single read-only tool — run normally
        sequential_calls = parallel_calls + sequential_calls
        parallel_calls = []

    # Run write/destructive tools sequentially
    for tc in sequential_calls:
        tool_name = tc["name"]
        tool_args = tc["args"]
        tool_id = tc["id"]
        _preview = str(tool_args)[:80].replace("\n", " ")
        log.info("  ⚙ tool: %s %s", tool_name, _preview)

        if perms:
            allowed, reason = perms.check(tool_name, tool_args)
            if not allowed:
                log.warning("Permission denied: %s — %s", tool_name, reason)
                _append_tool_result(messages, tool_id, f"BLOCKED by permissions: {reason}")
                continue

        if hooks:
            hr = hooks.run_pre_tool_use(tool_name, tool_args)
            if not hr.allowed:
                _append_tool_result(messages, tool_id, f"BLOCKED by hook: {hr.message}")
                continue
            if hr.modified_args is not None:
                tool_args = hr.modified_args

        if checkpoints and tool_name in ("write_file", "edit_file"):
            path = tool_args.get("path", "")
            if path:
                checkpoints.snapshot(path, tool_name)

        if on_call:
            on_call(tool_name, tool_args)

        _sig = _tool_call_sig(tool_name, tool_args)
        _is_repeat = _sig in failed_sigs

        try:
            result = await asyncio.to_thread(executor, tool_name, tool_args)
        except Exception as exc:
            exc_str = str(exc)
            result = f"ERROR: {exc_str}"
            # ECC-L2: try parameter mutation before marking as permanently failed
            if ecc_fixer and not _is_repeat:
                _fixed_args, _fix_desc = ecc_fixer.try_fix(tool_name, tool_args, exc_str, _sig)
                if _fixed_args is not None:
                    try:
                        _fixed_result = await asyncio.to_thread(executor, tool_name, _fixed_args)
                        if not _is_tool_failure(_fixed_result):
                            log.info("ECC-L2 (ns): fixed %s — %s", tool_name, _fix_desc)
                            result = f"{_fixed_result}\n[ECC: auto-fixed — {_fix_desc}]"
                            if hooks:
                                post = hooks.run_post_tool_use(tool_name, _fixed_args, result)
                                if post.message:
                                    result += f"\n[Hook: {post.message}]"
                            failed_sigs.add(_sig)   # original args failed
                            if on_result:
                                on_result(tool_name, result)
                            _append_tool_result(messages, tool_id, result, tool_name=tool_name)
                            continue
                    except Exception:
                        pass   # fall through to normal error handling
            # PostToolUseFailure hook
            if hooks:
                fail_hr = hooks.run_post_tool_use_failure(tool_name, tool_args, exc_str)
                if fail_hr.message:
                    result += f"\n[Hook: {fail_hr.message}]"
            failed_sigs.add(_sig)
            if _is_repeat:
                result += (
                    "\n\n⚠ SAME CALL FAILED AGAIN — do NOT retry this command. "
                    "Switch to a completely different approach or tool."
                )
            if on_result:
                on_result(tool_name, result)
            _append_tool_result(messages, tool_id, result, tool_name=tool_name)
            continue

        # PostToolUse hook
        if hooks:
            post = hooks.run_post_tool_use(tool_name, tool_args, result)
            if post.message:
                result += f"\n[Hook: {post.message}]"

        # FileChanged hook for write operations
        if hooks and tool_name in ("write_file", "edit_file"):
            path = tool_args.get("path", "")
            if path:
                change_type = "write" if tool_name == "write_file" else "edit"
                hooks.run_file_changed(path, change_type)

        # Track semantic failures — ECC-L2: try mutation if first failure
        if _is_tool_failure(result):
            if ecc_fixer and not _is_repeat:
                _fixed_args, _fix_desc = ecc_fixer.try_fix(tool_name, tool_args, result, _sig)
                if _fixed_args is not None:
                    try:
                        _fixed_result = await asyncio.to_thread(executor, tool_name, _fixed_args)
                        if not _is_tool_failure(_fixed_result):
                            log.info("ECC-L2 (ns-sem): fixed %s — %s", tool_name, _fix_desc)
                            result = f"{_fixed_result}\n[ECC: auto-fixed — {_fix_desc}]"
                    except Exception:
                        pass   # original failure stands
            failed_sigs.add(_sig)
            if _is_repeat:
                result += (
                    "\n\n⚠ SAME CALL FAILED AGAIN — do NOT retry this command. "
                    "Switch to a completely different approach or tool."
                )

        if on_result:
            on_result(tool_name, result)

        _append_tool_result(messages, tool_id, result, tool_name=tool_name)


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
    consolidate_fn: callable = None,
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
        consolidate_fn=consolidate_fn,
    )


async def agent_loop_stream(
    reasoner: GroqReasoner,
    user_input: str,
    system_prompt: str,
    history: list[dict] | None = None,
    tools: list[dict] | None = None,
    max_iterations: int = MAX_ITERATIONS,
    readonly: bool = False,
    consolidate_fn: callable = None,
) -> AsyncGenerator[dict, None]:
    """Streaming agent loop — yields events for the UI.

    Event types:
        text, tool_call, tool_result, dispatch, dispatch_result, done, error
    """
    if tools is None:
        tools = get_active_tools()

    # Build an LLM summarizer for smart compaction (uses the same reasoner)
    async def _llm_summarizer(prompt: str) -> str:
        """Use the active LLM to summarize compacted context."""
        try:
            r = await reasoner.query_with_tools(
                [{"role": "system", "content": "You are a conversation summarizer. Be concise."},
                 {"role": "user", "content": prompt}],
                [],  # no tools
            )
            return r.get("text", "")
        except Exception:
            return ""

    # Initialize AutoCompactor for smart context management
    model_name = getattr(reasoner, 'model', '') or getattr(reasoner, 'active_model_name', '') or ''
    compactor = AutoCompactor(model=model_name, summarizer=_llm_summarizer,
                              consolidate_fn=consolidate_fn)

    # Adaptive history: budget by character count, not turn count.
    # Voice conversations have many short turns — turn limits miss older context.
    from src.agent.context import MODEL_LIMITS, DEFAULT_MAX_TOKENS
    ctx_limit = MODEL_LIMITS.get(model_name, DEFAULT_MAX_TOKENS)
    max_content_len = 2000 if ctx_limit <= 32000 else 4000 if ctx_limit <= 200000 else 8000
    # Reserve ~25% of context for history (~3 chars per token)
    max_history_chars = (ctx_limit // 4) * 3

    messages = [{"role": "system", "content": system_prompt}]

    if history:
        selected = []
        char_budget = max_history_chars
        for turn in reversed(history):
            content = turn["content"]
            if len(content) > max_content_len:
                content = content[:max_content_len] + "..."
            cost = len(content) + 20
            if char_budget - cost < 0 and selected:
                break
            char_budget -= cost
            role = "assistant" if turn["role"] == "jarvis" else "user"
            selected.append({"role": role, "content": content})
        for msg in reversed(selected):
            messages.append(msg)

    messages.append({"role": "user", "content": user_input})

    full_response = ""
    iterations = 0
    iteration_budget = {"count": 0, "max": GLOBAL_ITERATION_MAX}
    cost_tracker = get_cost_tracker()

    # Session memory manager -- tracks token thresholds to trigger
    # periodic session memory extraction (persists to ~/.jarvis/session_memory.md)
    try:
        from src.services.SessionMemory.sessionMemory import init_session_memory
        session_memory_mgr = init_session_memory()
    except Exception:
        session_memory_mgr = None

    # Track failed (tool_name, args) signatures within this turn so we can warn
    # the model when it repeats the exact same failing call (semantic retry anti-pattern).
    _failed_call_sigs: set[str] = set()

    # ECC session state
    _ecc_fixer = ToolFixer()           # L2: tool parameter mutation
    _ecc_gv    = GoalVerifier()        # L3: post-task goal verification
    _ecc_tool_results: list[str] = []  # L3: accumulate tool results for verification

    while iterations < max_iterations:
        iterations += 1
        iteration_budget["count"] += 1
        if iteration_budget["count"] >= iteration_budget["max"]:
            yield {"type": "text", "content": "\n[Iteration budget exhausted]"}
            break

        messages = await _maybe_compact_async(messages, compactor=compactor)

        # For casual chat on first iteration: skip tools so JARVIS stays in character
        _effective_tools = tools
        if iterations == 1 and tools:
            _q = user_input.lower().strip().rstrip("?!. ")
            _short_casual = len(_q.split()) <= 10 and not any(
                w in _q for w in ["run", "read", "write", "edit", "create", "build",
                                   "fix", "scan", "find", "search", "check", "review",
                                   "install", "delete", "open", "make", "update",
                                   "/", "~", ".py", ".js", ".rs", "file", "code"])
            if _short_casual:
                _effective_tools = []  # No tools — JARVIS stays in personality

        # Call LLM — retry with exponential backoff + jitter
        import random as _rand
        response = None
        for attempt in range(4):
            try:
                response = await reasoner.query_with_tools(messages, _effective_tools)
                break
            except Exception as e:
                err = str(e).lower()
                if "context" in err or "overflow" in err or "too long" in err or "token" in err:
                    log.warning("Context overflow (attempt %d), compacting...", attempt + 1)
                    messages = compact_messages(messages, max_tokens=COMPACT_THRESHOLD // 2)
                    continue
                if "rate" in err or "429" in err or "overloaded" in err or "529" in err:
                    # Exponential backoff with jitter (Claude Code pattern)
                    base_delay = min(0.5 * (2 ** attempt), 32)
                    jitter = _rand.random() * 0.25 * base_delay
                    wait = base_delay + jitter
                    log.warning("Rate limited (attempt %d), waiting %.1fs...", attempt + 1, wait)
                    await asyncio.sleep(wait)
                    continue
                if attempt < 3:
                    await asyncio.sleep(1)
                    continue
                # All retries failed — feed error back as context so LLM can adjust
                error_msg = f"[Tool calling failed after {attempt + 1} attempts: {str(e)[:100]}]"
                messages.append({"role": "user", "content": error_msg})
                yield {"type": "text", "content": error_msg}
                # Try one more time with simplified prompt
                try:
                    response = await reasoner.query_with_tools(messages, [])  # No tools — just get a text response
                    break
                except Exception:
                    yield {"type": "error", "content": str(e)}
                    return

        # Feed real API token counts into compactor budget (replaces heuristic)
        if response and response.get("usage"):
            usage = response["usage"]
            in_tok = usage.get("input", 0)
            out_tok = usage.get("output", 0)
            if in_tok > 0:
                compactor.budget.used_tokens = in_tok  # Real count from API
                compactor.budget.cumulative_input_tokens += in_tok
                compactor.budget.cumulative_output_tokens += out_tok
            yield {"type": "usage",
                   "input_tokens": in_tok,
                   "output_tokens": out_tok,
                   "context_pct": int(compactor.budget.usage_pct),
                   "context_used": compactor.budget.used_tokens,
                   "context_max": compactor.budget.max_tokens,
                   "session_cost": cost_tracker.get_status_line()}

        if response is None:
            yield {"type": "error", "content": "All retry attempts failed"}
            return

        text_content = response.get("text", "")
        tool_calls = _validate_tool_calls(response.get("tool_calls", []))

        if text_content:
            # Scrub Claude identity leaks — JARVIS is JARVIS
            text_content = _scrub_identity(text_content)
            # Remove any leaked tool-call XML tags that slipped through parsing
            _fake_xml = _re.compile(
                r'</?(?:tool_use|tool_result|tool_name|tool_parameter|function_calls'
                r'|invoke|parameter|anythingllm-function-calls|anythingllm-function-result)[^>]*>',
            )
            text_content = _fake_xml.sub('', text_content)
            text_content = _re.sub(r'<(?:bash|read_file|write_file|edit_file|search_files)>.*?</(?:bash|read_file|write_file|edit_file|search_files)>', '', text_content, flags=_re.DOTALL)
            text_content = text_content.strip()

            # Detect hallucinated tool output — model wrote fake tool calls in text
            # instead of using actual tool calling API. Don't emit this as response.
            _has_fake_tools = bool(_re.search(
                r'(?:CALL:|```(?:bash|python)\n.*(?:pip install|systemctl|chmod|mkdir|write_file))',
                text_content, _re.DOTALL
            ))
            if _has_fake_tools and not tool_calls and iterations == 1:
                log.warning("Model hallucinated tool calls in text, retrying with tool nudge")
                # Add a nudge message and retry — don't emit the fake output
                messages.append({"role": "assistant", "content": text_content})
                messages.append({"role": "user", "content": (
                    "You wrote steps as text instead of actually executing them. "
                    "Use the bash, read_file, write_file tools to do the work. "
                    "Do NOT write what you would do — call the tools now."
                )})
                iterations += 1
                continue

            if text_content:
                full_response += text_content
                yield {"type": "text", "content": text_content}

        if not tool_calls:
            # Auto-continue if the response suggests more work ahead
            _forward_signals = (
                "next", "now i'll", "now i will", "i'll now", "i will now",
                "then i'll", "then i will", "let me", "i need to", "i'll create",
                "i'll write", "i'll build", "i'll implement", "i'll add",
                "moving on", "continuing", "proceeding", "step ", "phase ",
                "first,", "second,", "third,", "finally,",
            )
            _tc_lower = text_content.lower()
            _should_continue = (
                _effective_tools
                and any(sig in _tc_lower for sig in _forward_signals)
                and iterations < max_iterations
            )
            if _should_continue:
                _append_assistant_message(messages, text_content, [])
                messages.append({"role": "user", "content": "Continue."})
                continue
            break

        # Store in OpenAI format
        _append_assistant_message(messages, text_content, tool_calls)

        # Separate dispatch and sentinel tools from regular ones
        _SENTINEL_TOOLS_STREAM = {
            "EnterPlanMode", "ExitPlanMode", "EnterWorktree", "ExitWorktree",
            "SendMessage", "TeamCreate", "TeamDelete", "Skill", "LSP",
            "ScheduleCron", "RemoteTrigger", "ask_user",
        }
        dispatch_calls = []
        sentinel_calls = []
        regular_calls = []
        for tc in tool_calls:
            if tc["name"] == "dispatch":
                dispatch_calls.append(tc)
            elif tc["name"] in _SENTINEL_TOOLS_STREAM:
                sentinel_calls.append(tc)
            else:
                regular_calls.append(tc)

        # Handle sentinel tools — these change loop/brain state
        for tc in sentinel_calls:
            s_name, s_args, s_id = tc["name"], tc["args"], tc["id"]
            yield {"type": "tool_call", "name": s_name, "args": s_args}
            try:
                s_result = await _handle_sentinel_tool(s_name, s_args)
            except Exception as exc:
                s_result = f"ERROR: {exc}"
            yield {"type": "tool_result", "name": s_name, "content": s_result}
            _append_tool_result(messages, s_id, s_result, tool_name=s_name)

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
                    _append_tool_result(messages, tool_id, result, tool_name=tool_name)
                    continue

            if hooks:
                hr = hooks.run_pre_tool_use(tool_name, tool_args)
                if not hr.allowed:
                    yield {"type": "tool_call", "name": tool_name, "args": tool_args}
                    result = f"BLOCKED by hook: {hr.message}"
                    yield {"type": "tool_result", "name": tool_name, "content": result}
                    _append_tool_result(messages, tool_id, result, tool_name=tool_name)
                    continue
                if hr.modified_args is not None:
                    tool_args = hr.modified_args

            if checkpoints and tool_name in ("write_file", "edit_file"):
                path = tool_args.get("path", "")
                if path:
                    checkpoints.snapshot(path, tool_name)

            yield {"type": "tool_call", "name": tool_name, "args": tool_args}

            # Semantic-retry detection: warn the model if it's about to repeat
            # a call that already failed with identical arguments this turn.
            _sig = _tool_call_sig(tool_name, tool_args)
            _is_repeat_failure = _sig in _failed_call_sigs

            try:
                result = await asyncio.to_thread(execute_tool, tool_name, tool_args, readonly)
            except Exception as exc:
                exc_str = str(exc)
                result = f"ERROR: {exc_str}"
                # ECC-L2: try parameter mutation before giving up
                if not _is_repeat_failure:
                    _fixed_args, _fix_desc = _ecc_fixer.try_fix(tool_name, tool_args, exc_str, _sig)
                    if _fixed_args is not None:
                        try:
                            _fixed_result = await asyncio.to_thread(
                                execute_tool, tool_name, _fixed_args, readonly
                            )
                            if not _is_tool_failure(_fixed_result):
                                log.info("ECC-L2 (stream-exc): fixed %s — %s", tool_name, _fix_desc)
                                result = f"{_fixed_result}\n[ECC: auto-fixed — {_fix_desc}]"
                                if hooks:
                                    _post = hooks.run_post_tool_use(tool_name, _fixed_args, result)
                                    if _post.message:
                                        result += f"\n[Hook: {_post.message}]"
                                _failed_call_sigs.add(_sig)
                                _ecc_tool_results.append(result[:500])
                                yield {"type": "tool_result", "name": tool_name, "content": result}
                                _append_tool_result(messages, tool_id, result, tool_name=tool_name)
                                continue
                        except Exception:
                            pass   # fall through to normal error handling
                if hooks:
                    fail_hr = hooks.run_post_tool_use_failure(tool_name, tool_args, exc_str)
                    if fail_hr.message:
                        result += f"\n[Hook: {fail_hr.message}]"
                _failed_call_sigs.add(_sig)
                if _is_repeat_failure:
                    result += (
                        "\n\n⚠ SAME CALL FAILED AGAIN — do NOT retry this command. "
                        "Switch to a completely different approach or tool."
                    )
                _ecc_tool_results.append(result[:500])
                yield {"type": "tool_result", "name": tool_name, "content": result}
                _append_tool_result(messages, tool_id, result, tool_name=tool_name)
                continue

            if hooks:
                post = hooks.run_post_tool_use(tool_name, tool_args, result)
                if post.message:
                    result += f"\n[Hook: {post.message}]"

            # FileChanged hook for write operations
            if hooks and tool_name in ("write_file", "edit_file"):
                path = tool_args.get("path", "")
                if path:
                    change_type = "write" if tool_name == "write_file" else "edit"
                    hooks.run_file_changed(path, change_type)

            # Track failures — ECC-L2: try mutation on first semantic failure
            if _is_tool_failure(result):
                if not _is_repeat_failure:
                    _fixed_args, _fix_desc = _ecc_fixer.try_fix(
                        tool_name, tool_args, result, _sig
                    )
                    if _fixed_args is not None:
                        try:
                            _fixed_result = await asyncio.to_thread(
                                execute_tool, tool_name, _fixed_args, readonly
                            )
                            if not _is_tool_failure(_fixed_result):
                                log.info(
                                    "ECC-L2 (stream-sem): fixed %s — %s", tool_name, _fix_desc
                                )
                                result = f"{_fixed_result}\n[ECC: auto-fixed — {_fix_desc}]"
                        except Exception:
                            pass   # original failure stands
                _failed_call_sigs.add(_sig)
                if _is_repeat_failure:
                    result += (
                        "\n\n⚠ SAME CALL FAILED AGAIN — do NOT retry this command. "
                        "Switch to a completely different approach or tool."
                    )

            _ecc_tool_results.append(result[:500])
            yield {"type": "tool_result", "name": tool_name, "content": result}
            _append_tool_result(messages, tool_id, result, tool_name=tool_name)

            # Notify session memory manager after each tool call
            if session_memory_mgr is not None:
                try:
                    current_tokens = estimate_tokens(messages)
                    await session_memory_mgr.on_tool_call(messages, current_tokens)
                except Exception:
                    pass  # session memory is best-effort

        # Dispatch calls (with SubagentStart/Stop hooks)
        if dispatch_calls:
            for tc in dispatch_calls:
                args = tc["args"]
                agent_type = args.get("agent_type", "scout")
                task = args.get("task", "")
                yield {"type": "dispatch", "agent_type": agent_type, "task": task}
                # SubagentStart hook
                if hooks:
                    hooks.run_subagent_start(agent_type, task)

            async def run_dispatch_stream(tc):
                a = tc["args"]
                return tc["id"], a.get("agent_type", "scout"), await _run_sub_agent(
                    reasoner=reasoner,
                    agent_type=a.get("agent_type", "scout"),
                    task=a.get("task", ""),
                    context=a.get("context", ""),
                    iteration_budget=iteration_budget,
                )

            results = await asyncio.gather(
                *[run_dispatch_stream(tc) for tc in dispatch_calls]
            )
            for tool_id, agent_type, result in results:
                # SubagentStop hook
                if hooks:
                    hooks.run_subagent_stop(agent_type, "", result[:500])
                yield {"type": "dispatch_result", "agent_type": agent_type, "result": result}
                _append_tool_result(messages, tool_id, result, tool_name="dispatch")

    # ECC-L3: Goal-state verification — did we actually complete the task?
    if _ecc_tool_results and full_response:
        _vr = _ecc_gv.verify(user_input, _ecc_tool_results, full_response)
        if not _vr.complete and _vr.corrective_prompt:
            log.info("ECC-L3: task appears incomplete (%s) — running correction", _vr.missing)
            try:
                _corr_msgs = messages + [{"role": "user", "content": _vr.corrective_prompt}]
                _corr_resp = await reasoner.query_with_tools(_corr_msgs, [])
                if _corr_resp and _corr_resp.get("text"):
                    _corr_text = _scrub_identity(_corr_resp["text"])
                    full_response += f"\n\n{_corr_text}"
                    yield {"type": "text", "content": f"\n\n{_corr_text}"}
            except Exception as _e:
                log.debug("ECC-L3: correction pass failed: %s", _e)

    # Stop hook — final verification before task completion (can force continuation)
    hooks = _get_hooks()
    if hooks:
        stop_hr = hooks.run_stop()
        if not stop_hr.allowed:
            log.info("Stop hook blocked completion: %s", stop_hr.message)

    # Emit final cost + context summary
    yield {"type": "cost", "summary": cost_tracker.get_summary(), "status": cost_tracker.get_status_line()}
    yield {"type": "done", "content": full_response,
           "context_status": compactor.get_status(),
           "context_pct": int(compactor.budget.usage_pct)}
