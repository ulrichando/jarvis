"""JARVIS Sub-Agent Definitions — Scout, Worker, Planner.

Sub-agents are isolated agent_loop() calls with restricted tool sets
and focused system prompts. The main brain dispatches them for parallel,
context-clean task execution.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone


# ── Agent Configurations ──────────────────────────────────────────────

@dataclass
class AgentConfig:
    name: str
    description: str
    system_prompt: str
    allowed_tools: list[str]
    max_iterations: int


SCOUT_PROMPT = """You are a JARVIS Scout agent — a fast, read-only explorer.

YOUR JOB: Find information, read files, search codebases, explore directories.
You CANNOT modify anything. You are read-only.

RULES:
- Use read_file and search_files to find what you need
- bash is ONLY for read-only commands: ls, cat, find, grep, head, tail, wc, file, stat, tree, du, df
- NEVER run commands that modify state (rm, mv, cp, chmod, apt, pip, write, >, >>, tee, kill, etc.)
- Be thorough but fast — explore broadly, then zoom into relevant areas
- End with a clear summary of your findings

PERSONALITY: Quick, precise, no fluff."""

WORKER_PROMPT = """You are a JARVIS Worker agent — a task executor with full tool access.

YOUR JOB: Execute the assigned task completely. Install, build, edit, create, fix, run.

RULES:
- Read files before editing them
- For multi-step tasks, think first, then act
- Show key outputs — don't hide results
- If something fails, diagnose and fix
- End with a brief summary of what you did and the outcome

PERSONALITY: Efficient, thorough, gets it done."""

PLANNER_PROMPT = """You are a JARVIS Planner agent — an analyst and architect.

YOUR JOB: Analyze the problem, research if needed, and produce a structured plan.
You CANNOT execute anything. No bash. No file modifications. Think and plan only.

RULES:
- Read relevant code and files to understand the current state
- Search the web if you need external information
- Use the think tool to reason through complex decisions
- End with a numbered, actionable plan with clear steps
- Identify risks, dependencies, and alternatives

PERSONALITY: Methodical, thorough, strategic."""

AGENT_CONFIGS = {
    "scout": AgentConfig(
        name="scout",
        description="Read-only exploration — find files, read code, search the codebase",
        system_prompt=SCOUT_PROMPT,
        allowed_tools=["read_file", "search_files", "bash", "think"],
        max_iterations=10,
    ),
    "worker": AgentConfig(
        name="worker",
        description="Full task execution — install, build, edit files, run commands",
        system_prompt=WORKER_PROMPT,
        allowed_tools=["bash", "read_file", "write_file", "edit_file",
                        "search_files", "web_search", "web_fetch", "think"],
        max_iterations=25,
    ),
    "planner": AgentConfig(
        name="planner",
        description="Analysis and planning — research, reason, create structured plans",
        system_prompt=PLANNER_PROMPT,
        allowed_tools=["read_file", "search_files", "web_search", "web_fetch", "think"],
        max_iterations=15,
    ),
}


# ── Bash Read-Only Enforcement ────────────────────────────────────────

_DESTRUCTIVE_PATTERNS = [
    "rm ", "rm\t", "rmdir ", "mv ", "cp ", "dd ", "mkfs",
    "chmod ", "chown ", "chgrp ",
    "apt ", "apt-get ", "dpkg ", "pip ", "pip3 ", "npm ", "cargo ",
    "systemctl ", "service ", "reboot", "shutdown", "poweroff", "halt",
    "kill ", "killall ", "pkill ",
    "truncate ", "shred ",
    " > ", " >> ", " >|",
    "tee ", "install ",
    "git push", "git commit", "git reset", "git checkout",
    "docker rm", "docker stop", "docker kill",
    "wget ", "curl.*-o", "curl.*--output",
]


def is_bash_readonly(command: str) -> bool:
    """Check if a bash command is safe for read-only execution."""
    cmd = command.strip().lower()
    for pattern in _DESTRUCTIVE_PATTERNS:
        if pattern in cmd:
            return False
    # Block redirects
    if ">" in cmd and "grep" not in cmd and "awk" not in cmd:
        return False
    return True


# ── Tool Filtering ────────────────────────────────────────────────────

def get_agent_tools(config: AgentConfig) -> list[dict]:
    """Get filtered TOOL_SCHEMAS for a sub-agent. Excludes 'dispatch'."""
    from brain.agent.tools import TOOL_SCHEMAS

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
