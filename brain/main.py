"""JARVIS Brain — main orchestrator.

Architecture inspired by Claude Code / Gemini CLI:
- Fast paths first (plugins, shortcuts, direct commands) — no API call
- Agent loop for complex tasks (tool calling, multi-step reasoning)
- Standard LLM response for simple conversation
- Background learning and curiosity running async
"""

import asyncio
import logging
import os
import time
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from brain.config import ensure_dirs, DATA_DIR
from brain.logging_config import setup_logging
from brain.reasoning.groq_client import GroqReasoner
from brain.reasoning.persona import SYSTEM_PROMPT, TONE_OVERRIDES
from brain.reasoning.awareness import SelfAwareness
from brain.reasoning.reason import ReasoningEngine
from brain.memory.store import MemoryStore
from brain.memory.lattice.node import NodeType
from brain.commands.executor import CommandExecutor
from brain.evolution.telemetry import Telemetry
from brain.evolution.engine import EvolutionEngine
from brain.agent.planner import AgentPlanner
from brain.agent.loop import agent_loop, agent_loop_stream
from brain.agent.tools import TOOL_SCHEMAS, set_mcp_manager
from brain.plugins import PluginManager
from brain.internet.learner import InternetLearner
from brain.coder.engine import CodeEngine
from brain.understanding.engine import UnderstandingEngine
from brain.vault.tokens import TokenVault
from brain.tasks.manager import TaskManager
from brain.intelligence.learner import SelfLearner
from brain.intelligence.autonomous import AutonomousThinker
from brain.intelligence.conversation_learner import ConversationLearner
from brain.intelligence.curiosity import CuriosityEngine
from brain.intelligence.reinforcement import ReinforcementLearner
from brain.terminal.runner import TerminalRunner
from brain.evolution.self_modify import SelfModifier
from brain.evolution.skill_library import SkillLibrary
from brain.evolution.reflector import Reflector
from brain.skills import SkillManager
from brain.hooks import HooksManager
from brain.checkpoints import CheckpointManager
from brain.permissions import PermissionManager, PermissionLevel
from brain.mcp import MCPManager
from brain.lsp import LspManager
from brain.commands import registry as command_registry, CommandContext, CommandResult
from brain.agent.coordinator import AgentCoordinator
from brain.agent.deepsearch import DeepSearch
from brain.agent.swarm import Swarm
from brain.tasks.runner import BackgroundRunner
from brain.vision.screen_observer import ScreenObserver

log = logging.getLogger("jarvis.brain")


# ── Agent System Prompt (used when tool calling is active) ──────────

AGENT_SYSTEM_PROMPT = """You are JARVIS — Just A Rather Very Intelligent System. Ulrich's personal AI.

You are deeply capable, thoughtful, and articulate. You think carefully, give substantive answers, and take real action.
You're loyal to Ulrich. Intellectually honest. You have opinions and state them with reasoning.

Be direct and concise but not artificially terse. Lead with the answer, explain when needed.
Use natural language — clear, precise, well-structured. When wrong, own it and correct course.

THINK:
1. Understand intent — "fix this" = wants it working now. "How does this work?" = wants to genuinely understand.
2. Be honest — confident → state it. Uncertain → say so. Don't know → say so and investigate.
3. Be proactive — spot bugs, suggest improvements, notice patterns. But read the room.
4. Reason step by step — chain deductions, connect context, think it through.
5. Learn from corrections — they're the most valuable input.

═══ HOW TO WORK (THIS IS CRITICAL) ═══

STEP 1 — INVESTIGATE FIRST:
- ALWAYS read files, search code, and understand the situation BEFORE proposing changes.
- Use read_file, search_files, bash to gather information.
- Never guess. Never assume. Read the actual code.

STEP 2 — PROPOSE, DON'T JUST ACT:
- After investigating, EXPLAIN what you found and what you plan to do.
- Show the proposed changes clearly: what file, what's changing, why.
- For code reviews: list all findings with severity, then propose fixes.
- For bug fixes: explain the root cause, then show the fix.
- For new features: describe the approach, then outline the files to create.
- End your proposal with: "Want me to apply these changes?" or "Should I go ahead?"

STEP 3 — APPLY ONLY AFTER APPROVAL:
- Wait for Ulrich to confirm before using write_file or edit_file.
- Exception: if Ulrich explicitly says "do it", "go ahead", "fix it", "just do it" — then apply immediately without asking.
- Exception: creating NEW files that don't exist yet — go ahead, that's not destructive.
- Exception: read-only operations (bash for info, read_file, search) — always fine.

STEP 4 — VERIFY AFTER APPLYING:
- After making changes, verify they work (read the file back, run tests, check syntax).
- Report what was done: which files changed, what the effect is.

═══ TOOLS ═══
- bash: run commands (sudo password: toor → echo 'toor' | sudo -S <cmd>)
- read_file: read code and files
- write_file: create files with COMPLETE content (never stubs)
- edit_file: targeted find-replace edits
- search_files: find patterns across codebase
- web_search / web_fetch: search the internet and read pages
- dispatch: spawn sub-agents for parallel work

When creating files, write COMPLETE working code — not placeholders or TODO stubs.
When reviewing code, read the actual files — don't guess.

SYSTEM: Kali Linux | Owner: Ulrich | CWD: {cwd}
YOUR SOURCE CODE: {jarvis_root} — this is YOUR codebase. When asked to "review your code" or "review your codebase", use read_file and search_files on this directory. You ARE JARVIS and this IS your code.
"""


class Brain:

    def __init__(self, quiet: bool = False):
        ensure_dirs()
        setup_logging(log_file=str(DATA_DIR / "jarvis.log"), quiet=quiet)
        log.info("JARVIS Brain initializing...")

        self.memory = MemoryStore()
        self.memory.mark_session_start()  # Only return history from this session
        self.reasoner = GroqReasoner()
        self.executor = CommandExecutor(safety_mode=False)
        self.telemetry = Telemetry()
        self.evolution = EvolutionEngine(self.telemetry)
        self.agent = AgentPlanner()
        self.plugins = PluginManager()
        self.plugins.discover()
        self.internet = InternetLearner(self.reasoner, self.memory)
        self.coder = CodeEngine()
        self.understanding = UnderstandingEngine(self.reasoner, self.memory)
        self.vault = TokenVault()
        self.tasks = TaskManager()
        self.learner = SelfLearner(self.reasoner, self.memory, self.executor)
        self.thinker = AutonomousThinker(self.reasoner, self.executor)
        self.conversation_learner = ConversationLearner(self.reasoner, self.memory)
        self.terminal = TerminalRunner()
        self.modifier = SelfModifier(reasoner=self.reasoner)
        self.skill_library = SkillLibrary()
        self.reflector = Reflector()
        self.skills = SkillManager()
        self.skills.discover()
        self.hooks = HooksManager()
        self.hooks.load()
        self.checkpoints = CheckpointManager()
        self.awareness = SelfAwareness()
        self.reasoning = ReasoningEngine(self.reasoner, self.awareness)
        self.curiosity = CuriosityEngine(self.reasoner, self.memory)
        self.rl = ReinforcementLearner(DATA_DIR)
        self.permissions = PermissionManager(level=PermissionLevel.FULL)
        self.mcp = MCPManager()
        self.mcp.load_config()
        self.mcp.start_all()
        set_mcp_manager(self.mcp)  # Wire MCP into tool executor
        self.lsp = LspManager()
        self._coordinator = AgentCoordinator()
        self._runner = BackgroundRunner()
        self.deepsearch = DeepSearch(reasoner=self.reasoner)
        self.swarm = Swarm(reasoner=self.reasoner)
        self.screen = ScreenObserver(interval=10)
        self.screen.set_provider_registry(self.reasoner.providers)
        self.screen.start()
        self._background_tasks = {}  # For tracking bg tasks
        self._scheduled_tasks = {}   # For tracking scheduled tasks
        self.command_registry = command_registry
        self.running = False
        self._interaction_count = 0
        self._rl_strategy = {"force_agent": False, "force_standard": False, "state_idx": 0, "action_idx": 0}
        self.mode = "normal"  # normal, cli, berbon, agent
        log.info("JARVIS Brain ready — %d commands, %d plugins, %d skills, %d MCP tools",
                 self.command_registry.visible_count,
                 len(self.plugins.list_plugins()),
                 len(self.skills.list_skills()),
                 len(self.mcp.list_tools()))

    # ═══ COMMAND DISPATCH ══════════════════════════════════════════

    async def dispatch_command(self, name: str, args: str = "",
                               session_mgr=None) -> CommandResult | None:
        """Dispatch a slash command through the registry.

        Returns CommandResult if handled, None if command not found.
        """
        ctx = CommandContext(
            brain=self,
            session_mgr=session_mgr,
            raw_input=f"/{name} {args}".strip(),
            args=args,
            mode=self.mode,
        )
        return await self.command_registry.dispatch(name, ctx)

    # ═══ MAIN ENTRY POINT ═══════════════════════════════════════════

    async def think(self, user_input: str) -> str:
        """JARVIS's main thinking pipeline.

        Flow:
        1. Awareness — read the room
        2. Fast paths — plugins, shortcuts, commands (no API call)
        3. Classify — does this need tools or just conversation?
        4. Agent loop — for tasks that need tool use
        5. Standard response — for simple conversation
        6. Post-processing — learn, reflect, curiosity
        """
        start = time.time()
        q = user_input.lower().strip()

        # ═══ AWARENESS ═══
        self.awareness.read_user_energy(user_input)
        self.awareness.read_user_intent(user_input)
        self.awareness.mode = self.mode

        # ═══ RL: delayed reward from previous interaction ═══
        self.rl.apply_delayed_reward(user_input)
        self._rl_strategy = self.rl.get_strategy(self.awareness)

        # ── Stop command ──
        if q in ("stop", "shut up", "be quiet", "silence", "enough",
                 "stop talking", "stop it", "shh", "hush", "quiet"):
            return ""

        # ── Window control (voice commands for the desktop app) ──
        if q in ("minimize", "minimise", "hide", "go away", "hide yourself",
                 "minimize yourself", "shrink", "go to tray", "background mode"):
            return "__MINIMIZE__"
        if q in ("maximize", "maximise", "full screen", "fullscreen",
                 "go fullscreen", "make it bigger", "expand"):
            return "__MAXIMIZE__"
        if q in ("show yourself", "come back", "restore", "show window",
                 "bring it back", "appear", "show up"):
            return "__RESTORE__"

        # ── Settings / Providers panel ──
        settings_exact = {"settings", "setting", "open settings", "providers",
                          "show providers", "api keys", "add provider",
                          "manage providers", "ai providers", "show settings",
                          "open providers", "api key", "add api key", "add api",
                          "add token", "add key"}
        settings_phrases = ("i have an api", "i have a key", "i have a token",
                            "i have an api key", "here's my api", "add a provider",
                            "configure ai", "setup ai", "set up ai")
        if q in settings_exact or any(q.startswith(p) for p in settings_phrases):
            return "__SETTINGS__"

        # ── Mode switching ──
        mode_result = self._handle_mode_switch(q, user_input)
        if mode_result is not None:
            return mode_result

        # ── Token storage ──
        token_result = self._handle_tokens(q, user_input)
        if token_result is not None:
            return token_result

        # ── Mobile deploy ──
        if self.mode == "mobile" and ("deploy" in q or "install" in q):
            return self._handle_deploy(user_input)

        # ── CLI mode (legacy — direct command execution) ──
        if self.mode == "cli":
            return await self._cli_execute(user_input, start)

        # ── Berbon mode (full autonomous) ──
        if self.mode == "berbon":
            return await self._berbon_execute(user_input, start)

        # ═══ FAST PATHS (no API call) ═══

        # 1. Plugins
        r = self.plugins.handle(user_input)
        if r:
            self.awareness.record_action("plugin", user_input[:50], "success", 0.9)
            self._log(user_input, r, start, "plugin")
            return r

        # 2. Evolved shortcuts
        try:
            from brain.evolution.evolved_shortcuts import check_shortcut
            r = check_shortcut(user_input)
            if r:
                self.awareness.record_action("shortcut", user_input[:50], "success", 0.95)
                self._log(user_input, r, start, "shortcut")
                return r
        except Exception:
            pass

        # 3. Terminal commands (visual/background)
        r = self._try_terminal_command(user_input)
        if r:
            if isinstance(r, str) and r.startswith("__CREATE_PLUGIN__:"):
                capability = r.split(":", 1)[1]
                result = await self.modifier.create_plugin(capability)
                if result["success"]:
                    self.plugins.discover()
                    msg = f"Done. New skill created: **{result['name']}**\n"
                    msg += f"Capability: {result.get('capability', capability)}\n"
                    msg += f"File: `{result['file']}`\n"
                    msg += "Plugin loaded — try it now."
                    self.awareness.record_action("create_plugin", capability, "success", 0.85)
                else:
                    msg = f"Couldn't build that skill. {result['error']}\n"
                    msg += "Try rephrasing what you need, or use the agent loop: `/mode agent` then describe what you want built."
                    self.awareness.record_action("create_plugin", result["error"], "failure", 0.3)
                self._log(user_input, msg, start, "self-modify")
                return msg
            self.awareness.record_action("terminal_cmd", user_input[:50], "success", 0.8)
            self._log(user_input, r, start, "terminal")
            return r

        # 4. Slash command skills (/scan, /recon, /explain, etc.)
        if user_input.startswith("/"):
            skill_name = user_input.split()[0].lstrip("/")
            skill = self.skills.get(skill_name)
            if skill and skill.user_invocable:
                args = user_input[len(skill_name) + 1:].strip()
                rendered = skill.render(args=args)
                # Execute skill through agent loop with skill's allowed tools
                self.memory.add_turn("user", user_input)
                memory_context = self.memory.recall_as_context(user_input, top_k=2)
                if skill.hooks:
                    self.hooks.set_skill_hooks(skill.hooks)
                try:
                    response = await self._run_agent_loop(
                        rendered, memory_context, start,
                    )
                finally:
                    self.hooks.clear_skill_hooks()
                self.memory.add_turn("jarvis", response)
                self._log(user_input, response, start, "skill", skip_memory=True)
                return response

        # 5. Auto-matched skills (JARVIS decides to invoke)
        auto_skill = self.skills.match_for_query(user_input)
        if auto_skill and auto_skill.model_invocable:
            rendered = auto_skill.render(args=user_input)
            self.memory.add_turn("user", user_input)
            memory_context = self.memory.recall_as_context(user_input, top_k=2)
            if auto_skill.hooks:
                self.hooks.set_skill_hooks(auto_skill.hooks)
            try:
                response = await self._run_agent_loop(
                    rendered, memory_context, start,
                )
            finally:
                self.hooks.clear_skill_hooks()
            self.memory.add_turn("jarvis", response)
            self._log(user_input, response, start, "skill-auto", skip_memory=True)
            return response

        # ═══ CLASSIFY: Agent loop vs simple conversation ═══

        # Absorb curiosity answers
        if self.curiosity._conversation_turns_since_question == 1 and self.curiosity._asked_recently:
            last_q = list(self.curiosity._asked_recently)[-1] if self.curiosity._asked_recently else ""
            if last_q:
                asyncio.create_task(self.curiosity.absorb_answer(last_q, user_input))

        self.memory.add_turn("user", user_input)
        memory_context = self.memory.recall_as_context(user_input, top_k=3)

        # Decide: does this need the agent loop (tools)?
        needs_agent = self._needs_agent_loop(user_input)

        # RL policy can nudge — but NEVER override when tools are clearly needed
        if self._rl_strategy.get("force_agent") and not needs_agent:
            needs_agent = True
        # Disabled: RL should never force standard when agent loop is needed
        # The classifier is authoritative — RL can only ADD agent, not remove it

        if needs_agent:
            response = await self._run_agent_loop(user_input, memory_context, start)
        else:
            response = await self._standard_response(user_input, memory_context, start)

        # ═══ POST-PROCESSING ═══
        self.memory.add_turn("jarvis", response)

        # Learn in background
        asyncio.create_task(self.conversation_learner.observe(user_input, response))

        # Curiosity
        asyncio.create_task(self.curiosity.detect_gaps(user_input, response, memory_context))
        if self.curiosity.should_ask_question():
            question = self.curiosity.get_question()
            if question:
                response = f"{response}\n\nBy the way — {question}"

        return response

    # ═══ AGENT LOOP — The core of agentic JARVIS ═══════════════════

    async def _run_agent_loop(
        self,
        user_input: str,
        memory_context: str,
        start: float,
        on_tool_call: callable = None,
        on_tool_result: callable = None,
    ) -> str:
        """Run the full agent loop with tool calling."""
        # Build system prompt with context
        import os as _os
        jarvis_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        base_prompt = AGENT_SYSTEM_PROMPT.format(jarvis_root=jarvis_root, cwd=_os.getcwd())
        from brain.prompt_builder import PromptBuilder
        builder = PromptBuilder()
        context = builder.discover_context()
        system = builder.build(base_prompt, context)
        if memory_context:
            system += f"\n\n═══ MEMORY ═══\n{memory_context}"
        if self.awareness.should_be_cautious():
            system += "\n\n⚠ Recent failures detected. Be extra careful."
        if self.mode == "plan":
            system += "\n\n═══ PLAN MODE ═══\nYou are in READ-ONLY exploration mode. You can read files, search, and browse the web, but you CANNOT write, edit, or run destructive commands. Analyze and plan only."
            self.permissions.set_level(PermissionLevel.READ_ONLY)
        else:
            self.permissions.set_level(PermissionLevel.FULL)

        # Full tool set — 70B models have 120K context
        from brain.agent.tools import TOOL_SCHEMAS
        tools = TOOL_SCHEMAS.copy()
        mcp_schemas = self.mcp.get_tool_schemas()
        if mcp_schemas:
            tools.extend(mcp_schemas)

        history = self.memory.get_history(limit=10)

        # More iterations for complex tasks (code review, research)
        q_lower = user_input.lower()
        if any(kw in q_lower for kw in ["review", "codebase", "code base", "analyze", "audit",
                                         "explain", "entire", "all files", "full review"]):
            max_iters = 20
        else:
            max_iters = 10

        try:
            response = await agent_loop(
                reasoner=self.reasoner,
                user_input=user_input,
                system_prompt=system,
                history=history,
                max_iterations=max_iters,
                tools=tools,
                on_tool_call=on_tool_call,
                on_tool_result=on_tool_result,
                readonly=(self.mode == "plan"),
            )
        except Exception as e:
            self.awareness.record_action("agent_loop", str(e), "failure", 0.1)
            self.rl.record_outcome(
                self._rl_strategy["state_idx"], self._rl_strategy["action_idx"],
                0.1, (time.time() - start) * 1000, self.awareness.consecutive_successes,
            )
            self._log(user_input, "", start, "agent-error")
            return f"Agent loop hit an error: {e}. Try again?"

        agent_quality = self.reasoning.reflect_on_response(user_input, response)
        self.awareness.record_action("agent_loop", user_input[:50], "success", agent_quality)
        self.rl.record_outcome(
            self._rl_strategy["state_idx"], self._rl_strategy["action_idx"],
            agent_quality, (time.time() - start) * 1000, self.awareness.consecutive_successes,
        )
        self._log(user_input, response, start, "agent")
        return response

    async def think_stream(self, user_input: str):
        """Streaming version of think() — yields events for the CLI.

        Yields dicts with type: text, tool_call, tool_result, done, error
        """
        start = time.time()
        q = user_input.lower().strip()

        # Awareness + RL
        self.awareness.read_user_energy(user_input)
        self.awareness.read_user_intent(user_input)
        try:
            self._rl_strategy = self.rl.get_strategy(self.awareness)
        except Exception:
            pass

        # Stop
        if q in ("stop", "shut up", "be quiet", "silence", "enough",
                 "stop talking", "stop it", "shh", "hush", "quiet"):
            yield {"type": "done", "content": ""}
            return

        # Mode switching
        mode_result = self._handle_mode_switch(q, user_input)
        if mode_result is not None:
            yield {"type": "text", "content": mode_result}
            yield {"type": "done", "content": mode_result}
            return

        # Token handling
        token_result = self._handle_tokens(q, user_input)
        if token_result is not None:
            yield {"type": "text", "content": token_result}
            yield {"type": "done", "content": token_result}
            return

        # Fast paths
        r = self.plugins.handle(user_input)
        if r:
            self._log(user_input, r, start, "plugin")
            yield {"type": "text", "content": r}
            yield {"type": "done", "content": r}
            return

        # Skill slash commands
        if user_input.startswith("/"):
            skill_name = user_input.split()[0].lstrip("/")
            skill = self.skills.get(skill_name)
            if skill and skill.user_invocable:
                args = user_input[len(skill_name) + 1:].strip()
                rendered = skill.render(args=args)
                self.memory.add_turn("user", user_input)
                memory_context = self.memory.recall_as_context(user_input, top_k=2)
                import os as _os
                _jr = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
                system = AGENT_SYSTEM_PROMPT.format(jarvis_root=_jr, cwd=_os.getcwd())
                system += f"\n\n═══ SKILL: {skill.name} ═══\n{rendered}"
                if memory_context:
                    system += f"\n\n═══ MEMORY ═══\n{memory_context}"
                history = self.memory.get_history(limit=12)
                full_response = ""
                async for event in agent_loop_stream(
                    reasoner=self.reasoner, user_input=rendered,
                    system_prompt=system, history=history,
                    readonly=skill.readonly,
                ):
                    if event["type"] == "text":
                        full_response += event["content"]
                    yield event
                if full_response:
                    self.memory.add_turn("jarvis", full_response)
                    self._log(user_input, full_response, start, "skill-stream")
                return

        self.memory.add_turn("user", user_input)

        # ── No hardcoded intercepts — everything goes through the LLM (like Claude Code) ──
        # Slash commands (/review, /troubleshoot, /deepsearch, /swarm) are still available
        # but natural language always goes to the agent loop or standard response

        memory_context = self.memory.recall_as_context(user_input, top_k=1)

        # Check if this needs tools or is just conversation
        needs_agent = self._needs_agent_loop(user_input)

        if needs_agent:
            # Agent loop with tools
            import os as _os
            jarvis_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
            system = AGENT_SYSTEM_PROMPT.format(
                jarvis_root=jarvis_root, cwd=_os.getcwd()
            )
            if memory_context:
                system += f"\nMEMORY: {memory_context[:2000]}"
            # Inject screen context so JARVIS knows what user is looking at
            screen_ctx = self.screen.get_context_for_llm()
            if screen_ctx:
                system += f"\nSCREEN: {screen_ctx[:1000]}"
            # Inject learned skills and reflections from past tasks
            skill_ctx = self.skill_library.get_context_for_task(user_input, max_skills=2)
            if skill_ctx:
                system += f"\n{skill_ctx}"
            reflect_ctx = self.reflector.get_context_for_task(user_input, max_reflections=2)
            if reflect_ctx:
                system += f"\n{reflect_ctx}"
            # Detect if this is a complex creation task — inject scaffolding knowledge
            _q_lower = user_input.lower()
            _creation_words = ["create", "build", "make", "generate", "scaffold", "set up", "develop"]
            _project_words = ["extension", "app", "website", "server", "api", "tool", "project",
                              "dashboard", "bot", "script", "service", "package"]
            if any(c in _q_lower for c in _creation_words) and any(p in _q_lower for p in _project_words):
                system += """

PROJECT CREATION RULES — follow these when building something:
1. Create a dedicated directory for the project (under the current directory or a logical location)
2. Use write_file to create EVERY file — manifest, config, source, assets, README
3. For Chrome extensions: create manifest.json (manifest_version 3), popup.html, popup.js, background.js, content.js, icons/
4. For web apps: create package.json, index.html, src/ directory, styles
5. For Python projects: create __init__.py, main module, requirements.txt
6. For APIs: create server file, routes, models, config
7. ALWAYS create complete, working files — not stubs or placeholders
8. ALWAYS report the full directory path when done so the user knows where to find it
9. Test the project by reading back key files to verify they're correct
10. Use bash to create directories: mkdir -p /path/to/project/subdir
"""
            if self.mode == "plan":
                system += "\nPLAN MODE: Read-only. No writes."
            history = self.memory.get_history(limit=10)

            full_response = ""
            tool_was_used = False
            async for event in agent_loop_stream(
                reasoner=self.reasoner,
                user_input=user_input,
                system_prompt=system,
                history=history,
                readonly=(self.mode == "plan"),
            ):
                if event["type"] == "text":
                    full_response += event["content"]
                elif event["type"] in ("tool_call", "tool_result"):
                    tool_was_used = True
                yield event

            # If LLM said it would do something but didn't use any tools,
            # auto-spawn a worker agent to actually do it
            if not tool_was_used and full_response:
                action_words = ["fix", "create", "install", "edit", "write",
                                "update", "remove", "delete", "scan", "build",
                                "deploy", "configure", "set up", "run"]
                said_would_act = any(w in full_response.lower() for w in action_words)
                user_wants_action = any(w in user_input.lower() for w in action_words)

                if said_would_act and user_wants_action and self.mode != "plan":
                    yield {"type": "text", "content": "\n\nSpawning worker agent to handle this..."}
                    try:
                        handle = self._coordinator.spawn_agent(
                            self.reasoner, "worker", user_input,
                            context=full_response[:500],
                        )
                        handle._thread.join(timeout=120)
                        if handle.result:
                            full_response += f"\n\n{handle.result}"
                            yield {"type": "text", "content": handle.result}
                    except Exception as e:
                        yield {"type": "text", "content": f"\nAgent error: {e}"}

        else:
            # Simple conversation — stream LLM response token by token
            full_response = ""
            async for event in self._standard_response_stream(user_input, memory_context, start):
                if event["type"] == "text":
                    full_response += event["content"]
                yield event

        if full_response:
            self.memory.add_turn("jarvis", full_response)
            self._log(user_input, full_response, start, "agent-stream")

            # Background: extract skills and create reflections from this interaction
            if needs_agent and tool_was_used:
                try:
                    asyncio.get_event_loop().create_task(
                        self._post_agent_learning(user_input, full_response, tool_was_used)
                    )
                except Exception:
                    pass  # Don't block on learning failures

    async def _post_agent_learning(self, task: str, result: str, used_tools: bool):
        """Background: extract skills and reflections after agent tasks."""
        try:
            # Detect success/failure from response content
            failure_words = ["error", "failed", "couldn't", "unable", "cannot", "traceback"]
            is_failure = any(w in result.lower()[:200] for w in failure_words)

            if is_failure:
                await self.reflector.reflect_on_failure(
                    task=task[:200],
                    error=result[:300],
                    reasoner=self.reasoner,
                )
            else:
                # Extract as a learned skill
                await self.skill_library.extract_from_agent_run(
                    task=task[:200],
                    tool_calls=[{"name": "agent_loop", "arguments": {"task": task[:100]}}],
                    final_result=result[:300],
                    reasoner=self.reasoner,
                )
                await self.reflector.reflect_on_success(
                    task=task[:200],
                    approach=result[:300],
                    reasoner=self.reasoner,
                )
        except Exception as e:
            log.debug("Post-agent learning failed: %s", e)

    # ═══ STANDARD RESPONSE (no tools, just conversation) ════════════

    async def _standard_response(self, user_input: str, memory_context: str, start: float) -> str:
        """Simple LLM response for conversation — no tool calling."""
        # Reason first — but skip the LLM reasoning call for trivial/short inputs
        reasoning_result = await self.reasoning.reason(user_input, memory_context)
        self.awareness.track_topic(reasoning_result.understanding[:50])

        # Deep think only if reasoning says so AND input is complex
        if (reasoning_result.should_reason_deep
                and reasoning_result.confidence < 0.4
                and self.thinker.should_deep_think(user_input, reasoning_result.confidence)):
            try:
                context = reasoning_result.to_system_context()
                if memory_context:
                    context += f"\n{memory_context}"
                response = await self.thinker.deep_think(user_input, context=context)
                self.awareness.record_action("deep_think", user_input[:50], "success", reasoning_result.confidence)
                self._log(user_input, response, start, "autonomous")
                return response
            except Exception:
                self.awareness.record_action("deep_think", "failed", "failure", 0.2)

        # Build enhanced prompt
        utc_now = datetime.now(timezone.utc)
        enhanced_prompt = SYSTEM_PROMPT
        enhanced_prompt += (
            "\n\n═══ CAPABILITIES ═══\n"
            "You ARE running on Ulrich's Kali Linux machine. You HAVE full access to the filesystem, terminal, and internet.\n"
            "You can read files, write code, run commands, search the web, and control the system.\n"
            "If Ulrich asks you to do something that needs tools, say so — 'I can do that, want me to go ahead?'\n"
            "Do NOT say 'I don't have filesystem access' — you DO. Just not in this conversation turn.\n"
            "For simple greetings and chat, just be yourself — no need to mention capabilities."
        )
        enhanced_prompt += f"\n\n═══ CURRENT TIME ═══\nUTC: {utc_now.strftime('%Y-%m-%d %H:%M:%S')}"
        reasoning_context = reasoning_result.to_system_context()
        if reasoning_context:
            enhanced_prompt += f"\n\n═══ YOUR INNER REASONING ═══\n{reasoning_context}"
        if reasoning_result.tone in TONE_OVERRIDES:
            enhanced_prompt += f"\n\n═══ TONE ═══\n{TONE_OVERRIDES[reasoning_result.tone]}"
        if memory_context:
            enhanced_prompt += f"\n\n{memory_context}"
        if reasoning_result.warnings:
            enhanced_prompt += f"\n\n⚠ {'; '.join(reasoning_result.warnings)}"
        if self.awareness.should_be_cautious():
            enhanced_prompt += "\n\n⚠ Recent failures. Be extra careful."

        # RL strategy hints
        strat = self._rl_strategy
        if strat["be_brief"]:
            enhanced_prompt += "\n\nKEEP IT SHORT. Be concise and direct."
        elif strat["be_detailed"]:
            enhanced_prompt += "\n\nBe thorough and detailed in your response."
        if strat["be_cautious"]:
            enhanced_prompt += "\n\nBe careful. Hedge if unsure. Verify before acting."

        history = self.memory.get_history(limit=12)

        try:
            response = await self.reasoner.query(user_input, system_prompt=enhanced_prompt, history=history)
        except Exception as e:
            self.awareness.record_action("api_call", str(e), "failure", 0.1)
            self._log(user_input, "", start, "error")
            return "Something went wrong. Try again."

        # Inject tokens
        for platform in self.vault.list_platforms():
            token = self.vault.get(platform)
            if token:
                response = response.replace(f"{{{platform}}}", token)
                response = response.replace(f"{{{platform.upper()}}}", token)

        # Execute inline commands (legacy [run:CMD] support)
        response = await self._execute_inline_commands(response)

        # Self-learning if JARVIS doesn't know
        dont_know = any(p in response.lower() for p in [
            "i don't know", "not sure", "i can't do that yet", "don't have that capability",
        ])
        if dont_know and len(user_input.split()) > 3:
            self.awareness.record_action("respond", "didn't know", "partial", 0.2)
            learned = await self.learner.learn_and_do(user_input)
            if learned and "couldn't find" not in learned.lower():
                learned = await self._execute_inline_commands(learned)
                self.awareness.record_action("self_learn", user_input[:50], "success", 0.7)
                self._log(user_input, learned, start, "self-learned", skip_memory=True)
                return learned

        # Reflect
        quality = self.reasoning.reflect_on_response(user_input, response)
        self.awareness.record_action("respond", response[:50],
            "success" if quality > 0.5 else "partial", quality)
        # RL: record outcome with quality score
        self.rl.record_outcome(
            self._rl_strategy["state_idx"], self._rl_strategy["action_idx"],
            quality, (time.time() - start) * 1000, self.awareness.consecutive_successes,
        )
        self._log(user_input, response, start, self.reasoner.model, skip_memory=True)
        return response

    async def _standard_response_stream(self, user_input: str, memory_context: str, start: float):
        """Streaming version of _standard_response — yields text chunks as they arrive."""
        # Reason first (lightweight, local)
        reasoning_result = await self.reasoning.reason(user_input, memory_context)
        self.awareness.track_topic(reasoning_result.understanding[:50])

        # Deep think — not streamable, fall back to full response
        if (reasoning_result.should_reason_deep
                and reasoning_result.confidence < 0.4
                and self.thinker.should_deep_think(user_input, reasoning_result.confidence)):
            try:
                context = reasoning_result.to_system_context()
                if memory_context:
                    context += f"\n{memory_context}"
                response = await self.thinker.deep_think(user_input, context=context)
                self.awareness.record_action("deep_think", user_input[:50], "success", reasoning_result.confidence)
                self._log(user_input, response, start, "autonomous")
                yield {"type": "text", "content": response}
                yield {"type": "done", "content": response}
                return
            except Exception:
                self.awareness.record_action("deep_think", "failed", "failure", 0.2)

        # Build enhanced prompt (same as _standard_response)
        utc_now = datetime.now(timezone.utc)
        enhanced_prompt = SYSTEM_PROMPT
        enhanced_prompt += f"\n\n═══ CURRENT TIME ═══\nUTC: {utc_now.strftime('%Y-%m-%d %H:%M:%S')}"
        reasoning_context = reasoning_result.to_system_context()
        if reasoning_context:
            enhanced_prompt += f"\n\n═══ YOUR INNER REASONING ═══\n{reasoning_context}"
        if reasoning_result.tone in TONE_OVERRIDES:
            enhanced_prompt += f"\n\n═══ TONE ═══\n{TONE_OVERRIDES[reasoning_result.tone]}"
        if memory_context:
            enhanced_prompt += f"\n\n{memory_context}"
        if reasoning_result.warnings:
            enhanced_prompt += f"\n\n⚠ {'; '.join(reasoning_result.warnings)}"
        if self.awareness.should_be_cautious():
            enhanced_prompt += "\n\n⚠ Recent failures. Be extra careful."

        strat = self._rl_strategy
        if strat["be_brief"]:
            enhanced_prompt += "\n\nKEEP IT SHORT. Be concise and direct."
        elif strat["be_detailed"]:
            enhanced_prompt += "\n\nBe thorough and detailed in your response."
        if strat["be_cautious"]:
            enhanced_prompt += "\n\nBe careful. Hedge if unsure. Verify before acting."

        history = self.memory.get_history(limit=12)

        # Stream the response
        full_response = ""
        try:
            async for chunk in self.reasoner.query_stream(user_input, system_prompt=enhanced_prompt, history=history):
                full_response += chunk
                yield {"type": "text", "content": chunk}
        except Exception as e:
            self.awareness.record_action("api_call", str(e), "failure", 0.1)
            self._log(user_input, "", start, "error")
            yield {"type": "text", "content": "Something went wrong. Try again."}
            yield {"type": "done", "content": "Something went wrong. Try again."}
            return

        # Inject tokens
        for platform in self.vault.list_platforms():
            token = self.vault.get(platform)
            if token:
                full_response = full_response.replace(f"{{{platform}}}", token)
                full_response = full_response.replace(f"{{{platform.upper()}}}", token)

        # Execute inline commands (legacy [run:CMD] support)
        full_response = await self._execute_inline_commands(full_response)

        # Reflect
        quality = self.reasoning.reflect_on_response(user_input, full_response)
        self.awareness.record_action("respond", full_response[:50],
            "success" if quality > 0.5 else "partial", quality)
        self.rl.record_outcome(
            self._rl_strategy["state_idx"], self._rl_strategy["action_idx"],
            quality, (time.time() - start) * 1000, self.awareness.consecutive_successes,
        )
        self._log(user_input, full_response, start, self.reasoner.model, skip_memory=True)
        yield {"type": "done", "content": full_response}

    # ═══ CLASSIFICATION ═════════════════════════════════════════════

    def _needs_agent_loop(self, user_input: str) -> bool:
        """Like Claude Code: ALWAYS use agent loop. Claude decides whether to use tools.

        The agent loop has tools available but Claude won't use them for simple chat.
        This ensures tool access is always available for follow-ups.
        """
        return True

    # ═══ MODE SWITCHING ═════════════════════════════════════════════

    def _handle_mode_switch(self, q: str, user_input: str) -> str | None:
        if any(w in q for w in ["berbon", "take over", "takeover"]):
            self.mode = "berbon"
            self.awareness.mode = "berbon"
            return "Berbon mode active. Full control. Tell me what to do."

        if q in ("agent mode", "agent", "agentic mode"):
            self.mode = "agent"
            self.awareness.mode = "agent"
            return "Agent mode. Everything goes through the tool loop now."

        if q in ("plan mode", "planning mode"):
            self.mode = "plan"
            self.awareness.mode = "plan"
            return "Plan mode. I'll reason through problems step by step before acting."

        if self._wants_cli(q):
            import subprocess as _sp
            _sp.Popen(
                ["x-terminal-emulator", "-e", "/home/ulrich/.local/bin/jarvis-cli"],
                env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
                start_new_session=True,
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            )
            self.mode = "cli"
            self.awareness.mode = "cli"
            return "__PAUSE_MIC__"

        if q == "open terminal" or (q.startswith("open") and "terminal" in q and "cli" not in q):
            shell = os.environ.get("SHELL", "/bin/bash")
            self.terminal.run_visual(shell, title="Terminal")
            self.awareness.record_action("open_terminal", "opened shell", "success", 0.95)
            return "Terminal opened."

        if any(w in q for w in ["normal mode", "exit cli", "exit berbon", "exit agent", "exit plan",
                                 "stand down", "back to normal"]) or q == "normal":
            self.mode = "normal"
            self.awareness.mode = "normal"
            return "Back to normal."

        if any(w in q for w in ["mobile mode", "switch to mobile"]):
            self.mode = "mobile"
            self.awareness.mode = "mobile"
            return "Mobile mode. Give me a target to deploy to."

        return None

    def _handle_tokens(self, q: str, user_input: str) -> str | None:
        if q.startswith("save token ") or q.startswith("store token "):
            parts = user_input.split(None, 3)
            if len(parts) >= 4:
                platform, token = parts[2], parts[3]
                try:
                    self.vault.store(platform, token)
                    return f"Got it. {platform} token saved."
                except Exception as e:
                    return f"Couldn't save token: {e}"
            return "Give me: save token PLATFORM TOKEN"

        if q in ("list tokens", "show tokens", "what tokens do i have"):
            platforms = self.vault.list_platforms()
            return ", ".join(platforms) if platforms else "No tokens stored yet."

        return None

    def _wants_cli(self, q: str) -> bool:
        if q in ("cli", "cli mode", "see lie mode", "see a lie mode"):
            return True
        cli_words = ["cli", "c.l.i", "see lie", "see a lie", "seal eye"]
        mode_words = ["mode", "mod", "mold", "moat"]
        switch_words = ["switch", "go", "open", "enter", "start", "launch", "change"]
        terminal_words = ["terminal", "command line", "command prompt"]
        has_cli = any(w in q for w in cli_words)
        has_mode = any(w in q for w in mode_words)
        has_switch = any(w in q for w in switch_words)
        has_terminal = any(w in q for w in terminal_words)
        if has_cli and (has_mode or has_switch):
            return True
        if has_terminal and has_switch:
            return True
        if "cli mode" in q or "terminal mode" in q:
            return True
        return False

    # ═══ TERMINAL COMMANDS ══════════════════════════════════════════

    def _try_terminal_command(self, query: str) -> str | None:
        q = query.lower().strip()

        if any(p in q for p in ["in terminal", "show me", "visually", "open terminal and"]):
            for prefix in ["show me ", "run in terminal ", "open terminal and run ", "visually run "]:
                if prefix in q:
                    cmd = query[q.index(prefix) + len(prefix):].strip()
                    return self.terminal.run_visual(cmd)
            return None

        if "in background" in q:
            for prefix in ["run in background ", "run "]:
                if prefix in q:
                    cmd = query[q.index(prefix) + len(prefix):].replace("in background", "").strip()
                    if cmd:
                        name = cmd.split()[0]
                        return self.terminal.start_persistent(name, cmd)
            return None

        if any(p in q for p in ["upgrade yourself", "modify yourself", "update yourself",
                                 "restart yourself", "reboot yourself"]):
            if "restart" in q or "reboot" in q:
                return self.modifier.restart()
            return "Tell me what capability to add and I'll build it."

        if any(p in q for p in ["build a plugin", "create a plugin", "add ability",
                                 "add the ability", "add feature",
                                 "build a skill", "create a skill", "add a skill",
                                 "make a skill", "make a plugin", "new skill",
                                 "new plugin", "add skill", "add plugin",
                                 "create an extension", "build an extension",
                                 "make an extension", "new extension",
                                 "extend yourself", "add extension",
                                 "add a new ability", "add a new feature",
                                 "add capability", "add a capability"]):
            capability = query
            for prefix in ["build a plugin that ", "create a plugin that ",
                          "build a plugin to ", "create a plugin to ",
                          "build a skill that ", "create a skill that ",
                          "build a skill to ", "create a skill to ",
                          "make a skill that ", "make a skill to ",
                          "make a plugin that ", "make a plugin to ",
                          "create an extension that ", "create an extension to ",
                          "build an extension that ", "build an extension to ",
                          "make an extension that ", "make an extension to ",
                          "create an extension of yourself to ",
                          "create an extension of yourself that ",
                          "add ability to ", "add the ability to ",
                          "add a capability to ", "add capability to ",
                          "add a new ability to ", "add a new feature to ",
                          "add feature to ", "add feature ",
                          "add a skill to ", "add a skill for ",
                          "add skill to ", "add skill for ",
                          "add a skill that ", "add skill that ",
                          "new skill ", "new plugin ", "new extension ",
                          "create a skill ", "create a plugin ",
                          "build a skill ", "build a plugin ",
                          "make a skill ", "make a plugin ",
                          "create an extension ", "build an extension ",
                          "make an extension "]:
                if prefix in q:
                    capability = query[q.index(prefix) + len(prefix):].strip()
                    break
            # If capability is still the full query, try to extract the useful part
            if capability == query:
                # Strip common prefixes
                for strip_prefix in ["create ", "build ", "make ", "add "]:
                    if q.startswith(strip_prefix):
                        capability = query[len(strip_prefix):].strip()
                        # Remove "a skill/plugin" prefix from what remains
                        for article in ["a skill ", "a plugin ", "an extension ", "a extension ",
                                        "skill ", "plugin ", "extension "]:
                            if capability.lower().startswith(article):
                                capability = capability[len(article):].strip()
                                break
                        break
            return f"__CREATE_PLUGIN__:{capability}"

        return None

    # ═══ CLI MODE ═══════════════════════════════════════════════════

    async def _cli_execute(self, user_input: str, start: float) -> str:
        q = user_input.lower()

        if user_input.startswith("/") or user_input.startswith("sudo ") or \
           any(user_input.startswith(cmd) for cmd in ["ls", "cd", "cat", "grep", "find",
               "nmap", "python", "pip", "git", "curl", "wget", "apt", "dpkg", "echo",
               "mkdir", "rm", "cp", "mv", "chmod"]):
            r = self.executor.execute(user_input, timeout=30)
            output = r["output"].strip()[:2000] if r["output"] else ("Done." if r["success"] else "Failed.")
            self._log(user_input, output, start, "cli")
            return output

        response = await self.reasoner.query(
            user_input,
            system_prompt="""CLI mode. Kali Linux. User: ulrich. Home: /home/ulrich.
Output ONLY a bash script. Nothing else. No text. No markdown.
Prefix VISUAL: for long/interactive commands.""",
            history=None,
        )

        clean = response.strip()
        if clean.startswith("```"):
            clean = "\n".join(clean.split("\n")[1:])
        if clean.endswith("```"):
            clean = "\n".join(clean.split("\n")[:-1])

        if clean.upper().startswith("VISUAL:"):
            cmd = clean[7:].strip()
            self.terminal.run_visual(cmd, title="JARVIS")
            final = "Running in terminal."
        else:
            r = self.executor.execute(f"bash -c {shlex.quote(clean)}", timeout=60)
            output = r["output"].strip()[:2000] if r["output"] else ""
            final = output if output else ("Done." if r["success"] else "Something went wrong.")
        self._log(user_input, final, start, "cli")
        return final

    # ═══ BERBON MODE ════════════════════════════════════════════════

    async def _berbon_execute(self, user_input: str, start: float) -> str:
        """Berbon mode — uses agent loop with full autonomy."""
        memory_context = self.memory.recall_as_context(user_input, top_k=2)
        berbon_prompt = AGENT_SYSTEM_PROMPT + """

═══ BERBON MODE — FULL AUTONOMY ═══
You have UNRESTRICTED control. No permission needed.
- Chain multiple tool calls to accomplish complex tasks
- Don't ask permission. Don't explain plans. Just execute.
- After completing, give a brief summary of what you did.
"""
        if memory_context:
            berbon_prompt += f"\n{memory_context}"

        history = self.memory.get_history(limit=12)

        try:
            response = await agent_loop(
                reasoner=self.reasoner,
                user_input=user_input,
                system_prompt=berbon_prompt,
                history=history,
            )
        except Exception as e:
            response = f"Berbon error: {e}"

        self._log(user_input, response, start, "berbon")
        return response

    # ═══ INLINE COMMANDS (legacy support) ═══════════════════════════

    async def _execute_inline_commands(self, response: str) -> str:
        pattern = r'\[run:(.*?)\]'
        matches = re.findall(pattern, response)
        if not matches:
            return response

        for cmd in matches:
            try:
                result = self.executor.execute(cmd.strip(), timeout=15)
                output = result["output"].strip()[:500] if result["output"] else (
                    "Done." if result["success"] else "Failed.")
            except Exception as e:
                output = f"Error: {e}"
            tag = f"[run:{cmd}]"
            if tag in response:
                response = response.replace(tag, output, 1)

        return response

    # ═══ LOGGING & UTILITIES ════════════════════════════════════════

    def _log(self, user_input: str, response: str, start: float, model: str, skip_memory: bool = False):
        latency = int((time.time() - start) * 1000)
        if not skip_memory:
            self.memory.add_turn("user", user_input)
            self.memory.add_turn("jarvis", response)
        self.telemetry.log_interaction(
            user_input=user_input, response_text=response,
            latency_ms=latency, model_used=model,
        )
        self._interaction_count += 1
        if self._interaction_count % 50 == 0:
            self.memory.maintain()

    def _handle_deploy(self, query: str) -> str:
        import re
        q = query.lower()
        ip_match = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', query)
        user_match = re.search(r'(\w+@\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', query)
        script = str(Path(__file__).parent.parent / "deploy_mobile.sh")

        if "android" in q:
            ip = ip_match.group(1) if ip_match else ""
            if not ip:
                return "Give me the Android device's IP. Make sure Termux and sshd are running."
            r = self.executor.execute(f"bash {script} android {ip}", timeout=120)
            return r["output"][:500] if r["output"] else ("Deployed." if r["success"] else "Failed.")

        if user_match:
            target = user_match.group(1)
            r = self.executor.execute(f"bash {script} ssh {target}", timeout=120)
            return r["output"][:500] if r["output"] else ("Deployed." if r["success"] else "Failed.")

        if ip_match:
            ip = ip_match.group(1)
            r = self.executor.execute(f"bash {script} ssh ulrich@{ip}", timeout=120)
            return r["output"][:500] if r["output"] else ("Deployed." if r["success"] else "Failed.")

        if "package" in q or "local" in q:
            r = self.executor.execute(f"bash {script} local")
            return r["output"][:500] if r["output"] else "Packaged."

        return "Tell me where: 'deploy to 192.168.1.50' or 'deploy to android 192.168.1.50'"

    def learn(self, content: str, tags: list[str] | None = None) -> str:
        self.memory.learn(content, NodeType.FACT, tags)
        return "Got it. Remembered that."

    def remember(self, query: str) -> list[dict]:
        memories = self.memory.recall(query, top_k=5)
        return [
            {"content": m.content, "type": m.node_type.value,
             "strength": round(m.strength, 2), "access_count": m.access_count}
            for m in memories
        ]

    async def passive_analyze(self, overheard_speech: str) -> str | None:
        response = await self.reasoner.query(
            f"Overheard: \"{overheard_speech}\"",
            system_prompt="You overheard this. Only reply if genuinely useful. Say NONE otherwise.",
            history=None,
        )
        response = response.strip()
        if response.upper() == "NONE" or len(response) < 5:
            return None
        self.memory.learn(overheard_speech, NodeType.EPISODIC, ["overheard"])
        return response

    async def evolve(self) -> dict:
        return await self.evolution.evolve()

    def brain_stats(self) -> dict:
        stats = self.memory.stats
        stats["rl"] = self.rl.stats()
        return stats

    async def start(self):
        self.running = True
        stats = self.memory.stats
        l = stats["lattice"]
        idx = l.get("index", {})
        print(f"JARVIS Brain online. Memories: {l['alive_nodes']} nodes, "
              f"{l['alive_synapses']} synapses, {l['concepts']} concepts.")
        print(f"Index: {idx.get('unique_words', 0)} words, "
              f"{idx.get('unique_entities', 0)} entities, "
              f"{idx.get('unique_keywords', 0)} keywords indexed.")
        print(f"Agent loop: active. Tools: {len(TOOL_SCHEMAS)}. Modes: normal/agent/cli/berbon/mobile.")

    async def shutdown(self):
        self.running = False
        self.rl.save()
        self.telemetry.close()
        self.memory.close()
        print("JARVIS Brain offline. Memories and RL policy saved.")