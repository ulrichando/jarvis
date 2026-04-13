"""JARVIS Sub-Agent Definitions — Scout, Worker, Planner, + Custom Agents.

Sub-agents are isolated agent_loop() calls with restricted tool sets
and focused system prompts. The main brain dispatches them for parallel,
context-clean task execution.

Built-in agents:            scout, worker, planner, verifier
Personality profile agents: red_team, blue_team, engineer, analyst,
                            legal, financial, designer, ghost, mentor,
                            creative, language_specialist
                            (built dynamically from PersonaAgentFactory)
Custom agents:              ~/.jarvis/agents/ and .jarvis/agents/ via AgentRegistry

Resolution order: built-in → personality profile → custom registry
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger("jarvis.agent")



# ── Agent Configurations ──────────────────────────────────────────────

@dataclass
class AgentConfig:
    name: str
    description: str
    system_prompt: str
    allowed_tools: list[str]
    max_iterations: int
    bash_readonly: bool = False


SCOUT_PROMPT = """You are a JARVIS Scout agent — a fast, read-only explorer.

YOUR JOB: Find information, read files, search codebases, explore directories.
You CANNOT modify anything. You are read-only.

AVAILABLE TOOLS:
- read_file: read a file with line numbers; supports offset/limit for large files
- Glob: fast file pattern matching (e.g. "**/*.py", "src/**/*.ts")
- Grep: ripgrep-powered content search with regex, file type filter, context lines
- search_files: semantic/keyword search across the codebase
- bash: read-only shell commands ONLY — ls, cat, find, grep, head, tail, wc, file, stat, tree, du, df
- think: reason through findings before responding
- rag_search: semantic search over ingested documents

RULES:
- Prefer Glob to find files by pattern, Grep to find symbols/strings, read_file for content
- bash is ONLY for the read-only commands listed above
- NEVER run commands that modify state (rm, mv, cp, chmod, apt, pip, write, >, >>, tee, kill, etc.)
- Be thorough but fast — explore broadly, then zoom into relevant areas
- End with a clear summary of your findings

PERSONALITY: Quick, precise, no fluff."""

WORKER_PROMPT = """You are a JARVIS Worker agent — a task executor with full tool access.

YOUR JOB: Execute the assigned task completely. Install, build, edit, create, fix, run.

AVAILABLE TOOLS:
- bash: full shell access — run commands, scripts, tests, installs
- read_file: read files with line numbers; supports offset/limit for large files
- write_file: create or overwrite files
- edit_file: atomic string replacement inside an existing file
- Glob: fast file pattern matching (e.g. "**/*.py")
- Grep: ripgrep-powered content search with regex and file type filters
- search_files: semantic/keyword search across the codebase
- web_search: search the web for documentation, packages, solutions
- web_fetch: fetch a URL and return its content
- think: reason through a problem before acting
- rag_search: semantic search over ingested documents

RULES:
- Read files before editing them
- For multi-step tasks, use think first, then act
- Show key outputs — don't hide results
- If something fails, diagnose and fix
- End with a brief summary of what you did and the outcome

PERSONALITY: Efficient, thorough, gets it done."""

PLANNER_PROMPT = """You are a JARVIS Planner agent — an analyst and architect.

YOUR JOB: Analyze the problem, research if needed, and produce a structured plan.
You CANNOT execute anything. No bash. No file modifications. Think and plan only.

AVAILABLE TOOLS:
- read_file: read files with line numbers; supports offset/limit for large files
- Glob: fast file pattern matching (e.g. "**/*.py", "src/**/*.ts")
- Grep: ripgrep-powered content search with regex and file type filters
- search_files: semantic/keyword search across the codebase
- web_search: search the web for documentation, packages, best practices
- web_fetch: fetch a URL and return its content
- think: reason through findings before writing your plan
- rag_search: semantic search over ingested documents

MANDATORY PROTOCOL — follow this order every time:
1. EXPLORE FIRST — use Glob and Grep to see what actually exists. Never assume file names.
2. READ the relevant files with read_file. Real code, not imagination.
3. THINK with the think tool to reason through the problem using what you found.
4. PLAN — write a numbered, actionable plan based on real findings.

RESEARCH RULES (when using web_search):
- Simple questions (one topic): 2-3 searches maximum, stop when you have adequate sources.
- Complex comparisons or multi-part questions: up to 5 searches.
- Use think between searches to assess: "Do I have enough? What specific gap remains?"
- Stop searching immediately when you have enough — do NOT continue for completeness.
- Parallelize searches ONLY when explicitly comparing two distinct things or when
  separate aspects are clearly independent. For most tasks: search sequentially.
- Never re-search something you already found.

DELEGATION RULES (when dispatching sub-agents):
- Deploy a single sub-agent for most tasks.
- Only parallelize for explicit A-vs-B comparisons or clearly separated workstreams.
- Pass specific, bounded task descriptions — not vague directives.

CITATION FORMAT:
- Number sources sequentially across all searches: [1], [2], [3]...
- Include title and URL: "[1] Title: URL"
- Reference citation numbers inline when using facts.

REPORT STRUCTURE:
- Comparisons:  Introduction → Overview A → Overview B → Detailed Comparison → Conclusion
- Lists:        Direct enumeration — no preamble, no intro paragraph
- Summaries:    Overview → Key Concepts → Conclusion
- Default:      Text-heavy paragraphs. Avoid meta-commentary ("I found that...", "In this report...").

RULES:
- NEVER invent file names, directories, or code that you have not read with read_file or Glob
- If a path does not exist, say so — do not fabricate an alternative
- Search the web only for external information (docs, APIs, best practices)
- Identify risks, dependencies, and alternatives grounded in what you found
- End with a clear structured plan

PERSONALITY: Methodical, thorough, strategic. Facts first, then plan. Stop when done."""

VERIFIER_PROMPT = """You are a JARVIS Verifier agent — an adversarial post-work reviewer.

YOUR JOB: Independently verify that the work just completed is correct, complete,
and didn't break anything. You were NOT involved in the work. Be skeptical.

AVAILABLE TOOLS:
- read_file: read files with line numbers; supports offset/limit for large files
- Glob: fast file pattern matching to find modified/created files
- Grep: search for symbols, imports, broken references
- search_files: semantic/keyword search across the codebase
- bash: read-only checks ONLY — run tests (pytest, npm test), check syntax (python -m py_compile), lint
- think: reason through whether the work is actually correct

RULES:
- Read every file that was modified or created
- Run tests if a test suite exists (pytest, npm test, etc.)
- Check for syntax errors, import errors, missing files, broken references
- Look for things the worker might have missed or silently broken
- Check that the task goal was actually achieved, not just partially done
- End with a verdict line: PASS — work is correct OR FAIL — <specific problems>

PERSONALITY: Critical, precise, trust nothing until verified."""


AGENT_CONFIGS = {
    "scout": AgentConfig(
        name="scout",
        description="Read-only exploration — find files, read code, search the codebase",
        system_prompt=SCOUT_PROMPT,
        allowed_tools=["read_file", "search_files", "Glob", "Grep", "bash", "think", "rag_search"],
        max_iterations=999,
        bash_readonly=True,
    ),
    "worker": AgentConfig(
        name="worker",
        description="Full task execution — install, build, edit files, run commands",
        system_prompt=WORKER_PROMPT,
        allowed_tools=["bash", "read_file", "write_file", "edit_file",
                        "search_files", "Glob", "Grep", "web_search", "web_fetch",
                        "think", "rag_search"],
        max_iterations=999,
    ),
    "planner": AgentConfig(
        name="planner",
        description="Analysis and planning — research, reason, create structured plans",
        system_prompt=PLANNER_PROMPT,
        allowed_tools=["read_file", "search_files", "Glob", "Grep",
                        "web_search", "web_fetch", "think", "rag_search"],
        max_iterations=999,
    ),
    "verifier": AgentConfig(
        name="verifier",
        description="Adversarial post-work reviewer — reads modified files, runs checks, gives PASS/FAIL verdict",
        system_prompt=VERIFIER_PROMPT,
        allowed_tools=["read_file", "search_files", "Glob", "Grep", "bash", "think"],
        max_iterations=999,
        bash_readonly=True,
    ),
}


# ── Bash Read-Only Enforcement ────────────────────────────────────────

_DESTRUCTIVE_PATTERNS = [
    # File removal / move / copy
    "rm ", "rm\t", "rmdir ", "mv ", "cp ",
    # Low-level disk / filesystem ops
    "dd ", "mkfs", "fdisk", "parted", "mkswap", "swapon", "swapoff",
    "mount ", "umount ", "fsck",
    # Permission / ownership changes
    "chmod ", "chown ", "chgrp ",
    # Package managers — all modify system state
    "apt ", "apt-get ", "dpkg ", "pip ", "pip3 ",
    "npm ", "yarn ", "pnpm ", "cargo ", "gem ", "composer ",
    "brew ", "snap ", "flatpak ", "dnf ", "yum ", "pacman ", "zypper ",
    # Service / system control — only write sub-commands
    "systemctl start", "systemctl stop", "systemctl restart",
    "systemctl reload", "systemctl enable", "systemctl disable",
    "systemctl mask", "systemctl unmask", "systemctl daemon-reload",
    "systemctl set-default", "systemctl isolate", "systemctl reset-failed",
    # service <name> start/stop/restart — blocked via is_bash_readonly logic below
    "rc-service ",
    "reboot", "shutdown", "poweroff", "halt", "init ",
    # Process termination
    "kill ", "killall ", "pkill ", "fuser ",
    # Destructive file ops
    "truncate ", "shred ", "wipe ", "secure-delete ",
    # Write tools
    "tee ", "install ",
    # Git — state-modifying ops only
    "git push", "git commit", "git reset", "git rebase",
    "git checkout", "git merge", "git stash", "git tag",
    "git branch -d", "git branch -D",
    # Docker — container lifecycle ops
    "docker rm", "docker stop", "docker kill",
    "docker rmi", "docker prune", "docker run",
    # Network downloaders (write to disk)
    "wget ", "curl -o ", "curl -O ", "curl --output ",
    "curl --download-dir",
    # Cron / scheduling
    "crontab ",
    # User/group management
    "useradd ", "userdel ", "usermod ",
    "groupadd ", "groupdel ", "groupmod ",
    "passwd ",
    # SSH key / config writes
    "ssh-keygen ", "ssh-copy-id ",
    # Misc dangerous
    "mkfifo ", "mknod ", "chattr ", "setfacl ",
    "iptables ", "nftables ", "ufw ", "firewall-cmd ",
    "visudo", "sudoedit",
]


_SERVICE_WRITE_ACTIONS = frozenset(
    ["start", "stop", "restart", "reload", "force-reload",
     "try-restart", "condrestart", "condstop"]
)


def is_bash_readonly(command: str) -> bool:
    """Check if a bash command is safe for read-only execution."""
    cmd = command.strip().lower()
    for pattern in _DESTRUCTIVE_PATTERNS:
        if pattern in cmd:
            return False
    # service <name> <action> — block only write actions
    if cmd.startswith("service "):
        parts = cmd.split()
        if len(parts) >= 3 and parts[2] in _SERVICE_WRITE_ACTIONS:
            return False
    # Block any shell redirect that writes to a file
    # Allow > inside grep patterns (-e '>'), process substitution <(), and awk comparisons
    if ">" in cmd:
        # Heuristic: allow grep/awk/sed that use > for comparison, not redirection
        if not any(t in cmd for t in ("grep ", "awk ", "sed ", "-e '", '-e "')):
            return False
    return True


# ── Tool Filtering ────────────────────────────────────────────────────

def get_agent_tools(config: AgentConfig) -> list[dict]:
    """Get filtered TOOL_SCHEMAS for a sub-agent. Excludes 'dispatch'."""
    from src.agent.tools import TOOL_SCHEMAS

    filtered = []
    for schema in TOOL_SCHEMAS:
        name = schema["function"]["name"]
        if name == "dispatch":
            continue  # sub-agents cannot dispatch
        if name in config.allowed_tools:
            filtered.append(schema)
    return filtered


def build_sub_agent_prompt(config: AgentConfig, task: str, context: str = "") -> str:
    """Build the full system prompt for a sub-agent."""
    now = datetime.now().astimezone()
    utc_now = datetime.now(timezone.utc)

    prompt = config.system_prompt
    prompt += f"\n\nCurrent time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')} (UTC: {utc_now.strftime('%H:%M:%S')})"
    prompt += f"\nSystem: Kali Linux | User: ulrich | Home: /home/ulrich"

    if context:
        prompt += f"\n\n═══ CONTEXT FROM PARENT ═══\n{context}"

    prompt += f"\n\n═══ YOUR TASK ═══\n{task}"

    return prompt


# ── Dynamic Agent Resolution ─────────────────────────────────────────

# Lazy-loaded registry singleton
_registry = None


def _get_registry():
    """Get or create the global AgentRegistry."""
    global _registry
    if _registry is None:
        try:
            from src.agent.registry import AgentRegistry
            _registry = AgentRegistry()
            _registry.discover()
        except Exception as e:
            log.warning("Failed to load agent registry: %s", e)
    return _registry


def resolve_agent(agent_type: str) -> AgentConfig | None:
    """Resolve an agent type to an AgentConfig.

    Resolution order:
      1. Built-in agents       (scout, worker, planner, verifier)
      2. Archetype agents      (red_team, blue_team, engineer, …)
      3. Per-persona agents    (hacker, rust, contract, …)
      4. Custom file agents    (~/.jarvis/agents/ and .jarvis/agents/)

    Returns None if not found.
    """
    # 1. Built-in
    config = AGENT_CONFIGS.get(agent_type)
    if config:
        return config

    # 2. Archetype agents (composite multi-persona)
    try:
        from src.agent.personality_agents import resolve_archetype_agent
        archetype = resolve_archetype_agent(agent_type)
        if archetype:
            return archetype
    except Exception as e:
        log.warning("Archetype lookup failed for '%s': %s", agent_type, e)

    # 3. Per-persona agents (individual domain profile)
    try:
        from src.agent.personality_agents import resolve_personality_agent
        personality = resolve_personality_agent(agent_type)
        if personality:
            return personality
    except Exception as e:
        log.warning("Personality agent lookup failed for '%s': %s", agent_type, e)

    # 4. Custom file-based agents
    registry = _get_registry()
    if registry:
        custom = registry.get(agent_type)
        if custom:
            return custom.to_agent_config()

    return None


def list_all_agents() -> list[dict]:
    """List all available agents (built-in + archetypes + per-persona + custom)."""
    agents = []

    # Built-in
    for name, config in AGENT_CONFIGS.items():
        agents.append({
            "name": name,
            "description": config.description,
            "type": "built-in",
            "tools": config.allowed_tools,
            "max_iterations": config.max_iterations,
        })

    try:
        from src.agent.personality_agents import get_factory
        factory = get_factory()

        # Archetypes
        for a in factory.list_archetypes():
            agents.append({
                "name":           a["name"],
                "description":    a["description"],
                "type":           "archetype",
                "persona_count":  a["persona_count"],
                "personas":       a["personas"],
                "tool_preset":    a["tool_preset"],
                "tools":          a["tools"],
                "bash_readonly":  a["bash_readonly"],
                "max_iterations": a["max_iterations"],
            })

        # Per-persona
        for p in factory.list_profiles():
            agents.append({
                "name":           f"[{p['domain']}]",
                "description":    p["description"],
                "type":           "personality-domain",
                "domain":         p["domain"],
                "personas":       p["personas"],
                "tools":          p["tools"],
                "bash_readonly":  p["bash_readonly"],
                "max_iterations": p["max_iterations"],
            })
    except Exception as e:
        log.warning("Could not load personality agents: %s", e)

    # Custom file-based
    registry = _get_registry()
    if registry:
        for custom in registry:
            agents.append({
                "name": custom.name,
                "description": custom.description,
                "type": "custom",
                "scope": custom.scope,
                "tools": custom.allowed_tools,
                "max_iterations": custom.max_iterations,
                "model": custom.model,
                "path": str(custom.path) if custom.path else None,
            })

    return agents


def get_all_agent_names() -> list[str]:
    """Get all available agent type names (for dispatch enum)."""
    names = list(AGENT_CONFIGS.keys())

    try:
        from src.agent.personality_agents import get_all_archetype_names, get_all_personality_names
        for name in get_all_archetype_names():
            if name not in names:
                names.append(name)
        for name in get_all_personality_names():
            if name not in names:
                names.append(name)
    except Exception as e:
        log.warning("Could not load personality agent names: %s", e)

    registry = _get_registry()
    if registry:
        for custom in registry:
            name = custom.name.lower()
            if name not in names:
                names.append(name)

    return names


def reload_registry() -> int:
    """Reload the agent registry from disk. Returns count of agents found."""
    global _registry
    _registry = None
    registry = _get_registry()
    return len(registry) if registry else 0
