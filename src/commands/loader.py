"""Auto-discover dict-based command modules and register them in the registry.

Scans for command dicts in:
  - src/commands/<name>.py  (root-level modules)
  - src/commands/<name>/__init__.py  (package commands)

For "local" commands: wraps the call() function as a registry handler.
For "prompt" commands: wraps get_prompt_for_command() as a registry handler.

Commands with real implementations override stubs.
Empty directories and stub-only modules are skipped.
"""

import importlib
import logging
import os
from pathlib import Path

log = logging.getLogger("jarvis.commands.loader")

_COMMANDS_DIR = Path(__file__).parent
_SKIP = {
    "__pycache__", "utils", "loader", "__init__",
    "registry", "executor", "command_types", "handlers",
}


def discover_command_modules() -> list[dict]:
    """Discover all command dicts from src/commands/."""
    commands = []

    for item in sorted(os.listdir(_COMMANDS_DIR)):
        if item.startswith("__") or item.startswith("."):
            continue
        if item in _SKIP:
            continue

        name = item.replace(".py", "")
        if name in _SKIP:
            continue

        module_path = _COMMANDS_DIR / item

        # Package-based command (directory with __init__.py)
        if module_path.is_dir():
            init_file = module_path / "__init__.py"
            if not init_file.exists():
                continue
            module_name = f"src.commands.{name}"
        # File-based command
        elif item.endswith(".py"):
            module_name = f"src.commands.{name}"
        else:
            continue

        try:
            mod = importlib.import_module(module_name)
        except Exception as e:
            log.debug("Failed to import %s: %s", module_name, e)
            continue

        # Extract command dict — look for 'command' or '<name>' attribute
        cmd_dict = getattr(mod, "command", None)
        if cmd_dict is None:
            # Some modules export the dict under the command name
            clean_name = name.replace("-", "_")
            cmd_dict = getattr(mod, clean_name, None)

        if not isinstance(cmd_dict, dict):
            continue

        if "name" not in cmd_dict:
            cmd_dict["name"] = name

        # Resolve the call function if not inline
        if cmd_dict.get("type") == "local" and "call" not in cmd_dict:
            call_fn = _find_call_function(module_path, name)
            if call_fn:
                cmd_dict["call"] = call_fn

        if cmd_dict.get("type") == "prompt" and "get_prompt_for_command" not in cmd_dict:
            prompt_fn = _find_prompt_function(module_path, name)
            if prompt_fn:
                cmd_dict["get_prompt_for_command"] = prompt_fn

        cmd_dict["_source"] = "src.commands"
        cmd_dict["_module"] = module_name
        commands.append(cmd_dict)

    return commands


def _find_call_function(module_path: Path, name: str):
    """Find the call() function in a package command's implementation file."""
    if module_path.is_dir():
        impl_file = module_path / f"{name.replace('-', '_')}.py"
        if not impl_file.exists():
            # Try the hyphenated name
            impl_file = module_path / f"{name}.py"
        if not impl_file.exists():
            # Check any .py that isn't __init__
            for f in module_path.glob("*.py"):
                if f.name != "__init__.py":
                    impl_file = f
                    break
        if impl_file.exists():
            mod_name = f"src.commands.{name.replace('-', '_')}.{impl_file.stem}"
            try:
                mod = importlib.import_module(mod_name)
                return getattr(mod, "call", None)
            except Exception:
                pass
    return None


def _find_prompt_function(module_path: Path, name: str):
    """Find get_prompt_for_command() in a module."""
    if module_path.is_dir():
        impl_file = module_path / f"{name.replace('-', '_')}.py"
        if impl_file.exists():
            mod_name = f"src.commands.{name.replace('-', '_')}.{impl_file.stem}"
            try:
                mod = importlib.import_module(mod_name)
                return getattr(mod, "get_prompt_for_command", None)
            except Exception:
                pass
    return None


def _is_stub(cmd_dict: dict) -> bool:
    """Check if a command is just a stub (no real implementation)."""
    call_fn = cmd_dict.get("call")
    prompt_fn = cmd_dict.get("get_prompt_for_command")

    if cmd_dict.get("type") == "prompt" and prompt_fn:
        return False  # Prompt commands with templates are real

    if call_fn is None and prompt_fn is None:
        return True

    # Check if call is a trivial on_done stub
    if call_fn:
        import inspect
        try:
            source = inspect.getsource(call_fn)
            # Stub pattern: just calls on_done with a string
            if len(source.splitlines()) <= 6 and "on_done(" in source:
                return True
        except (OSError, TypeError):
            pass

    return False


def register_in_brain_registry(commands: list[dict] | None = None):
    """Register discovered commands into the commands registry.

    For overlapping names: src/commands/ takes priority only if it has
    a real implementation (not a stub).
    """
    from src.commands.registry import registry, CommandDef, PermLevel

    if commands is None:
        commands = discover_command_modules()

    registered = 0
    skipped = 0

    for cmd_dict in commands:
        name = cmd_dict["name"]
        cmd_type = cmd_dict.get("type", "local")

        # Skip stubs
        if _is_stub(cmd_dict):
            skipped += 1
            continue

        # Don't override existing decorator-registered handlers —
        # they're already fully integrated with Brain's CommandContext
        existing = registry.resolve(name)
        if existing:
            skipped += 1
            continue

        # Build the handler wrapper
        handler = _make_handler(cmd_dict)
        if handler is None:
            skipped += 1
            continue

        # Map to registry format
        aliases = cmd_dict.get("aliases", [])
        description = cmd_dict.get("description", "")
        usage = cmd_dict.get("argument_hint", f"/{name}")
        if not usage.startswith("/"):
            usage = f"/{name} {usage}"
        category = _guess_category(name, cmd_dict)
        hidden = cmd_dict.get("is_hidden", False)

        cmd_def = CommandDef(
            name=name,
            aliases=aliases,
            description=description,
            usage=usage,
            category=category,
            handler=handler,
            permission=PermLevel.STANDARD,
            hidden=hidden,
        )
        registry.register(cmd_def)
        registered += 1

    log.info("Loaded %d commands from src/commands/ (%d stubs skipped)", registered, skipped)
    return registered


def _make_handler(cmd_dict: dict):
    """Create a commands-compatible async handler from a command dict."""
    from src.commands.registry import CommandResult

    cmd_type = cmd_dict.get("type", "local")
    call_fn = cmd_dict.get("call")
    prompt_fn = cmd_dict.get("get_prompt_for_command")

    if cmd_type == "local" and call_fn:
        async def local_handler(ctx):
            try:
                result = await call_fn(ctx.args, context=ctx.brain)
            except TypeError:
                # Some handlers use different signatures
                try:
                    result = await call_fn(args=ctx.args, context=ctx.brain)
                except TypeError:
                    result = await call_fn(ctx.args)

            if isinstance(result, dict):
                return CommandResult(
                    text=result.get("value", result.get("text", str(result))),
                    success=True,
                    data=result,
                )
            elif isinstance(result, str):
                return CommandResult(text=result, success=True)
            elif result is None:
                return CommandResult(text="Done.", success=True)
            else:
                return CommandResult(text=str(result), success=True)

        return local_handler

    elif cmd_type == "prompt" and prompt_fn:
        async def prompt_handler(ctx):
            try:
                prompt_content = await prompt_fn(ctx.args, context=ctx.brain)
            except Exception as e:
                return CommandResult(text=f"Error generating prompt: {e}", success=False)

            # Extract text from prompt content list
            if isinstance(prompt_content, list):
                text = "\n".join(
                    item.get("text", "") for item in prompt_content
                    if isinstance(item, dict)
                )
            else:
                text = str(prompt_content)

            # Run through agent loop if brain available
            if ctx.brain and hasattr(ctx.brain, "_run_agent_loop"):
                import time
                response = await ctx.brain._run_agent_loop(text, "", time.time())
                return CommandResult(text=response, success=True)

            return CommandResult(text=text, success=True)

        return prompt_handler

    return None


def _guess_category(name: str, cmd_dict: dict) -> str:
    """Guess the best category for a command based on name/description."""
    desc = (cmd_dict.get("description", "") + " " + name).lower()

    if any(w in desc for w in ["git", "commit", "branch", "diff", "pr", "push", "rewind"]):
        return "git"
    if any(w in desc for w in ["session", "resume", "compact", "export", "share"]):
        return "session"
    if any(w in desc for w in ["memory", "forget", "recall", "remember"]):
        return "memory"
    if any(w in desc for w in ["agent", "dispatch", "scout", "worker", "planner"]):
        return "agent"
    if any(w in desc for w in ["task", "todo"]):
        return "task"
    if any(w in desc for w in ["mcp", "tool", "hook", "server"]):
        return "mcp"
    if any(w in desc for w in ["plugin", "skill", "reload"]):
        return "plugin"
    if any(w in desc for w in ["security", "permission", "sandbox", "vault"]):
        return "security"
    return "core"
