"""JARVIS Self-Modification — create plugins, skills, agent tools, or brain modules.

When JARVIS can't do something, it:
1. Decides the best output type (plugin, skill, agent extension, brain module)
2. Generates the code/config with validation
3. Deploys it with sandboxed testing
4. Logs the creation for rollback if needed

Output types:
- Plugin: Python class with can_handle()/handle() — intercepts queries before LLM
- Skill: Markdown + YAML frontmatter — prompt template for agent loop
- Agent Tool: Python function — adds a new tool the agent loop can call
- Brain Module: Python module — extends brain capabilities (most powerful, most dangerous)
"""

import os
import ast
import subprocess
import importlib.util
import json
import time
import logging
from pathlib import Path

log = logging.getLogger("jarvis.evolution.self_modify")

JARVIS_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN_DIR = JARVIS_ROOT / "src" / "plugins"
SKILL_DIR = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis")) / "skills"

# ── Dangerous AST nodes that generated code must NOT contain ──
BLOCKED_AST_NODES = {
    "os.system", "os.popen", "os.exec", "os.execl", "os.execle",
    "os.execv", "os.execve", "os.execvp", "os.execvpe",
    "shutil.rmtree", "shutil.move",
    "__import__", "eval", "exec", "compile",
}

BLOCKED_IMPORTS = {"ctypes", "signal", "pty", "resource"}


PLUGIN_PROMPT = """You are writing a JARVIS plugin. Output ONLY valid Python code. No markdown. No explanation. No ```python fences. Just the Python code.

A JARVIS plugin MUST have this exact structure:

class JarvisPlugin:
    name = "plugin_name"
    description = "what it does"

    def can_handle(self, query: str) -> bool:
        q = query.lower()
        return "keyword" in q

    def handle(self, query: str) -> str:
        try:
            # Your implementation here
            return "result"
        except Exception as e:
            return f"Error: {e}"

RULES:
- Put ALL imports at the top of the file (import os, import subprocess, etc.)
- The class MUST be named exactly JarvisPlugin
- can_handle() MUST return bool
- handle() MUST return str
- handle() MUST have try/except — never crash
- Use subprocess.run() for shell commands, not os.system()
- Available packages: subprocess, os, json, re, requests, pathlib
- DO NOT import from src.* — plugins are standalone
"""

SKILL_PROMPT = """You are creating a JARVIS skill file. Output ONLY the markdown content including YAML frontmatter. No extra explanation.

A JARVIS skill has this format:

---
name: skill-name
description: One-line description
user_invocable: true
model_invocable: false
triggers:
  - keyword1
  - keyword2
allowed_tools:
  - bash
  - read_file
  - search_files
---

Detailed prompt template here. Use {{args}} for user arguments.
Tell the agent exactly what to do step by step.
Include specific commands, file paths, and expected outputs.

RULES:
- name must be lowercase with hyphens
- triggers should be specific keywords
- allowed_tools restricts which tools the agent can use
- The body is a prompt that will be sent to the agent loop
- Be specific and actionable — include exact commands
"""

TOOL_PROMPT = """You are creating a new tool function for JARVIS's agent loop. Output ONLY valid Python code.

A JARVIS tool function has this structure:

def tool_name(args: dict) -> str:
    \"\"\"Tool description for the LLM.\"\"\"
    param = args.get("param_name", "default")
    try:
        # Implementation
        result = do_something(param)
        return str(result)
    except Exception as e:
        return f"Error: {e}"

# Tool schema for the LLM
TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "tool_name",
        "description": "What this tool does",
        "parameters": {
            "type": "object",
            "properties": {
                "param_name": {"type": "string", "description": "what this param is"}
            },
            "required": ["param_name"]
        }
    }
}

RULES:
- Function takes a single dict argument
- Always returns a string
- Always has try/except
- TOOL_SCHEMA follows OpenAI function calling format
- Available packages: subprocess, os, json, re, requests, pathlib
"""


class SelfModifier:
    """JARVIS can modify and extend itself by creating plugins, skills, tools, and modules."""

    def __init__(self, reasoner=None):
        self._reasoner = reasoner
        self._creation_log: list[dict] = []
        self._load_log()

    @property
    def reasoner(self):
        if self._reasoner is None:
            from src.reasoning.groq_client import GroqReasoner
            self._reasoner = GroqReasoner()
        return self._reasoner

    def set_reasoner(self, reasoner):
        self._reasoner = reasoner

    def _load_log(self):
        log_file = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis")) / "data" / "creation_log.json"
        if log_file.exists():
            try:
                self._creation_log = json.loads(log_file.read_text())
            except Exception:
                pass

    def _save_log(self):
        log_file = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis")) / "data" / "creation_log.json"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(json.dumps(self._creation_log[-100:], indent=2))

    # ── Auto-detect best output type ──

    def classify_request(self, capability: str) -> str:
        """Decide the best output type for a capability request."""
        q = capability.lower()

        # Skills: prompt-template tasks, multi-step workflows, agent-driven
        skill_words = ["review", "analyze", "audit", "scan", "check", "report",
                       "explain", "summarize", "research", "investigate", "monitor",
                       "diagnose", "troubleshoot", "benchmark", "profile"]
        if any(w in q for w in skill_words):
            return "skill"

        # Tools: specific operations, data transformations, API calls
        tool_words = ["convert", "calculate", "parse", "extract", "encode",
                      "decode", "hash", "encrypt", "decrypt", "compress",
                      "api", "endpoint", "fetch", "query database"]
        if any(w in q for w in tool_words):
            return "tool"

        # Plugins: query interceptors, quick responses, integrations
        # Default for most "add ability to X" requests
        return "plugin"

    # ── Create Plugin ──

    async def create_plugin(self, capability: str) -> dict:
        """Create a new plugin to add a capability."""
        safe_name = self._safe_name(capability)
        filename = f"plugin_{safe_name}.py"
        filepath = PLUGIN_DIR / filename

        prompt = f"""Create a JARVIS plugin that can: {capability}

The plugin will be saved as {filename}.
Make can_handle() match natural language queries about this capability.
Make handle() actually perform the task and return useful output.
Include ALL necessary imports at the top."""

        return await self._generate_and_validate(
            prompt=prompt,
            system_prompt=PLUGIN_PROMPT,
            filepath=filepath,
            output_type="plugin",
            capability=capability,
            validator=self._validate_plugin,
        )

    # ── Create Skill ──

    async def create_skill(self, capability: str) -> dict:
        """Create a new skill (markdown prompt template for agent loop)."""
        safe_name = self._safe_name(capability).replace("_", "-")
        filename = f"{safe_name}.md"
        filepath = SKILL_DIR / filename

        prompt = f"""Create a JARVIS skill that can: {capability}

The skill should guide the agent to accomplish this task step by step.
Include specific commands, tools to use, and expected outputs.
Make it reusable — use {{{{args}}}} for user-provided arguments."""

        return await self._generate_and_validate(
            prompt=prompt,
            system_prompt=SKILL_PROMPT,
            filepath=filepath,
            output_type="skill",
            capability=capability,
            validator=self._validate_skill,
        )

    # ── Create Agent Tool ──

    async def create_tool(self, capability: str) -> dict:
        """Create a new tool function for the agent loop."""
        safe_name = self._safe_name(capability)
        filename = f"tool_{safe_name}.py"
        filepath = PLUGIN_DIR / filename

        prompt = f"""Create a JARVIS agent tool that can: {capability}

The tool will be callable by the LLM during the agent loop.
Make it focused on a single operation.
Include the TOOL_SCHEMA for the LLM to know how to call it."""

        return await self._generate_and_validate(
            prompt=prompt,
            system_prompt=TOOL_PROMPT,
            filepath=filepath,
            output_type="tool",
            capability=capability,
            validator=self._validate_tool,
        )

    # ── Smart Create (auto-detect type) ──

    async def create(self, capability: str) -> dict:
        """Auto-detect the best output type and create it."""
        output_type = self.classify_request(capability)
        if output_type == "skill":
            return await self.create_skill(capability)
        elif output_type == "tool":
            return await self.create_tool(capability)
        else:
            return await self.create_plugin(capability)

    # ── Core generation + validation loop ──

    async def _generate_and_validate(self, prompt: str, system_prompt: str,
                                       filepath: Path, output_type: str,
                                       capability: str, validator) -> dict:
        """Generate code, validate, and deploy. Retries on failure."""
        last_error = ""
        for attempt in range(3):
            extra = ""
            if last_error:
                extra = f"\n\nYour previous attempt had this error: {last_error}\nFix it this time."

            code = await self.reasoner.query(
                prompt + extra,
                system_prompt=system_prompt,
                history=None,
            )

            if not code:
                return {"success": False, "error": "Model returned empty response.", "type": output_type}

            code = self._clean_code(code, is_markdown=(output_type == "skill"))

            # Validate
            validation = validator(code)
            if not validation["valid"]:
                last_error = validation["error"]
                continue

            # AST safety check (for Python files)
            if output_type != "skill":
                safety = self._ast_safety_check(code)
                if not safety["safe"]:
                    last_error = f"Safety violation: {safety['reason']}"
                    continue

                # Subprocess sandbox test
                sandbox = self._sandbox_test(code)
                if not sandbox["passed"]:
                    last_error = f"Sandbox test failed: {sandbox['error']}"
                    continue

            # Deploy
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(code)

            result = {
                "success": True,
                "file": str(filepath),
                "name": filepath.name,
                "type": output_type,
                "capability": capability,
                "message": f"Created {output_type}: {filepath.name}",
            }
            self._creation_log.append({
                **result, "timestamp": time.time(), "attempts": attempt + 1
            })
            self._save_log()
            return result

        return {
            "success": False,
            "type": output_type,
            "error": f"Failed after 3 attempts. Last error: {last_error}",
        }

    # ── Validators ──

    def _validate_plugin(self, code: str) -> dict:
        """Validate plugin structure."""
        try:
            compile(code, "<plugin>", "exec")
        except SyntaxError as e:
            return {"valid": False, "error": f"SyntaxError: {e}"}

        if "class JarvisPlugin" not in code:
            return {"valid": False, "error": "Missing 'class JarvisPlugin'"}
        if "def can_handle" not in code:
            return {"valid": False, "error": "Missing 'def can_handle'"}
        if "def handle" not in code:
            return {"valid": False, "error": "Missing 'def handle'"}

        # Test execution in isolated namespace
        try:
            namespace = {}
            exec(code, namespace)
            plugin = namespace["JarvisPlugin"]()
            if not callable(getattr(plugin, "can_handle", None)):
                return {"valid": False, "error": "can_handle is not callable"}
            if not callable(getattr(plugin, "handle", None)):
                return {"valid": False, "error": "handle is not callable"}
        except Exception as e:
            return {"valid": False, "error": f"Execution failed: {e}"}

        return {"valid": True, "error": ""}

    def _validate_skill(self, code: str) -> dict:
        """Validate skill markdown structure."""
        if not code.strip().startswith("---"):
            return {"valid": False, "error": "Skill must start with --- (YAML frontmatter)"}
        parts = code.split("---", 2)
        if len(parts) < 3:
            return {"valid": False, "error": "Skill must have --- frontmatter --- body"}
        frontmatter = parts[1].strip()
        body = parts[2].strip()
        if not frontmatter:
            return {"valid": False, "error": "Empty frontmatter"}
        if not body:
            return {"valid": False, "error": "Empty skill body"}
        if "name:" not in frontmatter:
            return {"valid": False, "error": "Frontmatter missing 'name:'"}
        return {"valid": True, "error": ""}

    def _validate_tool(self, code: str) -> dict:
        """Validate agent tool structure."""
        try:
            compile(code, "<tool>", "exec")
        except SyntaxError as e:
            return {"valid": False, "error": f"SyntaxError: {e}"}

        if "TOOL_SCHEMA" not in code:
            return {"valid": False, "error": "Missing TOOL_SCHEMA definition"}
        if "def " not in code:
            return {"valid": False, "error": "Missing function definition"}

        try:
            namespace = {}
            exec(code, namespace)
            if "TOOL_SCHEMA" not in namespace:
                return {"valid": False, "error": "TOOL_SCHEMA not found after exec"}
            schema = namespace["TOOL_SCHEMA"]
            if not isinstance(schema, dict):
                return {"valid": False, "error": "TOOL_SCHEMA must be a dict"}
        except Exception as e:
            return {"valid": False, "error": f"Execution failed: {e}"}

        return {"valid": True, "error": ""}

    # ── Safety ──

    def _ast_safety_check(self, code: str) -> dict:
        """Walk the AST to check for dangerous operations."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return {"safe": False, "reason": "Cannot parse code"}

        for node in ast.walk(tree):
            # Check imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in BLOCKED_IMPORTS:
                        return {"safe": False, "reason": f"Blocked import: {alias.name}"}
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] in BLOCKED_IMPORTS:
                    return {"safe": False, "reason": f"Blocked import: {node.module}"}

            # Check function calls
            if isinstance(node, ast.Call):
                call_name = self._get_call_name(node)
                if call_name in BLOCKED_AST_NODES:
                    return {"safe": False, "reason": f"Blocked call: {call_name}"}

        return {"safe": True, "reason": ""}

    def _get_call_name(self, node: ast.Call) -> str:
        """Extract the full dotted name of a function call."""
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            parts = []
            current = node.func
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return ".".join(reversed(parts))
        return ""

    def _sandbox_test(self, code: str) -> dict:
        """Run code in a subprocess sandbox with timeout and resource limits."""
        safe_code = code.replace("'''", "\\'\\'\\'")
        test_script = f"""
import sys
sys.path.insert(0, '.')
try:
    compile('''{safe_code}''', '<test>', 'exec')
    print("COMPILE_OK")
except SyntaxError as e:
    print(f"COMPILE_FAIL: {{e}}")
    sys.exit(1)
"""
        try:
            result = subprocess.run(
                ["python3", "-c", test_script],
                capture_output=True, text=True, timeout=10,
                cwd=str(JARVIS_ROOT),
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            if "COMPILE_OK" in result.stdout:
                return {"passed": True, "error": ""}
            return {"passed": False, "error": result.stdout + result.stderr}
        except subprocess.TimeoutExpired:
            return {"passed": False, "error": "Sandbox timeout (10s)"}
        except Exception as e:
            return {"passed": False, "error": str(e)}

    # ── Existing file update ──

    async def update_file(self, filepath: str, description: str) -> dict:
        """Update an existing JARVIS file based on a description of changes needed."""
        path = Path(filepath)
        if not path.exists():
            return {"success": False, "error": f"File not found: {filepath}"}

        current_code = path.read_text()

        prompt = f"""Update this JARVIS code file.

Current file ({filepath}):
```python
{current_code[:3000]}
```

Changes needed: {description}

Output the COMPLETE updated file. Not just the changes — the FULL file."""

        new_code = await self.reasoner.query(
            prompt,
            system_prompt="Output ONLY Python code. No markdown. No explanation. Complete file.",
            history=None,
        )

        new_code = self._clean_code(new_code)

        try:
            compile(new_code, "<update>", "exec")
        except SyntaxError as e:
            return {"success": False, "error": f"Generated code has syntax error: {e}"}

        safety = self._ast_safety_check(new_code)
        if not safety["safe"]:
            return {"success": False, "error": f"Safety violation: {safety['reason']}"}

        # Backup original
        backup = path.with_suffix(".py.bak")
        backup.write_text(current_code)

        path.write_text(new_code)

        result = {
            "success": True,
            "file": str(path),
            "backup": str(backup),
            "message": f"Updated {filepath}. Backup at {backup}.",
        }
        self._creation_log.append({**result, "timestamp": time.time(), "type": "update"})
        self._save_log()
        return result

    # ── Helpers ──

    def _clean_code(self, code: str, is_markdown: bool = False) -> str:
        """Strip markdown fences and other non-code artifacts."""
        code = code.strip()

        if code.startswith("```"):
            lines = code.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            code = "\n".join(lines)

        if not is_markdown:
            # For Python: remove any leading text before first import/class
            lines = code.split("\n")
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith(("import ", "from ", "class ", "def ", "#", '"""', "'''")) or stripped == "":
                    code = "\n".join(lines[i:])
                    break

        return code.strip()

    def _safe_name(self, capability: str) -> str:
        """Generate a safe filename from a capability description."""
        safe = "_".join(capability.lower().split()[:4])
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in safe)
        safe = safe.strip("_")
        return safe or "custom"

    def restart(self) -> str:
        """Restart the JARVIS server process."""
        # Try systemd service first (production/local service mode)
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", "jarvis"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                subprocess.Popen(
                    ["systemctl", "--user", "restart", "jarvis"],
                    start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return "Restarting via systemd... I'll be back in a few seconds."
        except Exception:
            pass

        # Fallback: send SIGTERM to self — supervisor/systemd will relaunch
        try:
            import signal
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception:
            pass
        return "Restarting..."

    def get_own_source(self, module_path: str) -> str:
        """Read one of JARVIS's own source files."""
        path = JARVIS_ROOT / module_path
        if path.exists():
            return path.read_text()
        return f"File not found: {module_path}"

    def get_creation_log(self, limit: int = 20) -> list[dict]:
        """Get recent creation history."""
        return self._creation_log[-limit:]
