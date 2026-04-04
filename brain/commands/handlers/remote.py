"""Remote, IDE, and integration commands."""
import os
import json
import subprocess
import logging
from brain.commands.registry import command, CommandContext, CommandResult, PermLevel

log = logging.getLogger(__name__)


@command("bridge", aliases=["remote-control"],
         description="Manage remote control bridge for online JARVIS",
         usage="/bridge [start|stop|status|url]", category="core")
async def cmd_bridge(ctx: CommandContext) -> CommandResult:
    """Remote control bridge management."""
    args = ctx.args.strip().split() if ctx.args else ["status"]
    action = args[0] if args else "status"

    if action == "status":
        try:
            from brain.remote.session_manager import get_remote_session_manager
            mgr = get_remote_session_manager()
            if mgr and mgr.is_connected():
                return CommandResult(text="Bridge: CONNECTED\nSession: " + mgr._config.session_id)
            return CommandResult(text="Bridge: NOT CONNECTED\nUse /bridge start to connect.")
        except Exception:
            return CommandResult(text="Bridge: NOT AVAILABLE\nRemote module not configured.")

    elif action == "start":
        return CommandResult(text="Starting bridge... Configure remote URL in ~/.jarvis/remote.json first.")

    elif action == "stop":
        try:
            from brain.remote.session_manager import get_remote_session_manager
            mgr = get_remote_session_manager()
            if mgr:
                import asyncio
                await mgr.disconnect()
                return CommandResult(text="Bridge disconnected.")
            return CommandResult(text="No active bridge.")
        except Exception as e:
            return CommandResult(text=f"Error: {e}", success=False)

    elif action == "url":
        try:
            from brain.state import get_state
            state = get_state()
            if state.remote_server_url:
                return CommandResult(text=f"Remote URL: {state.remote_server_url}")
            return CommandResult(text="No remote URL configured.")
        except Exception:
            return CommandResult(text="State not available.")

    return CommandResult(text=f"Unknown bridge action: {action}\nUsage: /bridge [start|stop|status|url]")


@command("ide", description="Connect to IDE (VS Code, JetBrains)",
         usage="/ide [connect|disconnect|status]", category="core")
async def cmd_ide(ctx: CommandContext) -> CommandResult:
    """IDE integration management."""
    args = ctx.args.strip().split() if ctx.args else ["status"]
    action = args[0] if args else "status"

    if action == "status":
        # Check for running IDEs
        ides_found = []
        try:
            result = subprocess.run(["pgrep", "-a", "code"], capture_output=True, text=True, timeout=5)
            if result.stdout.strip():
                ides_found.append("VS Code")
        except Exception:
            pass
        try:
            result = subprocess.run(["pgrep", "-a", "idea"], capture_output=True, text=True, timeout=5)
            if result.stdout.strip():
                ides_found.append("IntelliJ IDEA")
        except Exception:
            pass

        if ides_found:
            return CommandResult(text=f"IDEs detected: {', '.join(ides_found)}\nUse /ide connect to link JARVIS.")
        return CommandResult(text="No IDEs detected. Start VS Code or JetBrains first.")

    elif action == "connect":
        return CommandResult(text="IDE connection: Configure MCP server in your IDE settings.\nSee ~/.jarvis/mcp-server-config.json for details.")

    elif action == "disconnect":
        return CommandResult(text="IDE disconnected.")

    return CommandResult(text=f"Usage: /ide [connect|disconnect|status]")


@command("terminal-setup", aliases=["shell-setup"],
         description="Set up shell integration (bash/zsh/fish)",
         usage="/terminal-setup", category="core")
async def cmd_terminal_setup(ctx: CommandContext) -> CommandResult:
    """Terminal shell integration setup."""
    import shutil

    shell = os.environ.get("SHELL", "/bin/bash")
    shell_name = os.path.basename(shell)

    lines = [f"Terminal Setup ({shell_name})", "=" * 40]

    # Check current shell
    lines.append(f"\n  Current shell: {shell}")

    # Check if jarvis is in PATH
    jarvis_path = shutil.which("jarvis")
    if jarvis_path:
        lines.append(f"  JARVIS binary: {jarvis_path}")
    else:
        lines.append(f"  JARVIS binary: NOT IN PATH")
        lines.append(f"  Fix: pip install -e . (from jarvis root)")

    # Check shell integration
    rc_file = {
        "bash": "~/.bashrc",
        "zsh": "~/.zshrc",
        "fish": "~/.config/fish/config.fish",
    }.get(shell_name, "~/.bashrc")

    rc_path = os.path.expanduser(rc_file)
    has_integration = False
    if os.path.exists(rc_path):
        content = open(rc_path).read()
        has_integration = "jarvis" in content.lower()

    if has_integration:
        lines.append(f"  Shell integration: {rc_file}")
    else:
        lines.append(f"  Shell integration: NOT SET UP")
        lines.append(f"\n  To add shell integration, add to {rc_file}:")
        if shell_name == "fish":
            lines.append(f'    alias j="jarvis"')
            lines.append(f'    alias jp="jarvis -p"')
        else:
            lines.append(f'    alias j="jarvis"')
            lines.append(f'    alias jp="jarvis -p"')
            lines.append(f'    # Optional: JARVIS as shell command helper')
            lines.append(f'    jj() {{ jarvis -p "$*"; }}')

    # Check API keys
    env_path = os.path.join(os.getcwd(), ".env")
    home_env = os.path.expanduser("~/.jarvis/.env")
    if os.path.exists(env_path) or os.path.exists(home_env):
        lines.append(f"  API keys: configured")
    else:
        lines.append(f"  API keys: NOT FOUND")
        lines.append(f"  Create .env with your API keys")

    return CommandResult(text="\n".join(lines))


@command("chrome", description="Chrome extension integration",
         usage="/chrome [status|install]", category="core")
async def cmd_chrome(ctx: CommandContext) -> CommandResult:
    """Chrome extension integration."""
    return CommandResult(text="Chrome integration:\n"
                        "  JARVIS can control Chrome via the computer_use tool.\n"
                        "  For MCP-based Chrome control, configure in ~/.jarvis/mcp.json:\n"
                        '  { "chrome": { "command": "npx", "args": ["@anthropic/chrome-mcp"] } }')


@command("mobile", description="Mobile access setup for JARVIS",
         usage="/mobile", category="core")
async def cmd_mobile(ctx: CommandContext) -> CommandResult:
    """Mobile access configuration."""
    try:
        from brain.state import get_state
        state = get_state()
        server_url = state.remote_server_url or "http://localhost:8765"
    except Exception:
        server_url = "http://localhost:8765"

    lines = [
        "Mobile Access Setup", "=" * 40,
        "",
        "  JARVIS web interface is accessible from any device.",
        f"  Local URL: {server_url}",
        "",
        "  For remote access:",
        "  1. Start JARVIS web server: jarvis-web",
        "  2. Expose via tunnel: ssh -R 80:localhost:8765 tunnel.example.com",
        "  3. Or use ngrok: ngrok http 8765",
        "",
        "  For secure access, configure authentication in ~/.jarvis/settings.json:",
        '  { "auth": { "enabled": true, "token": "your-secret-token" } }',
    ]
    return CommandResult(text="\n".join(lines))


@command("add-dir", aliases=["adddir"],
         description="Add a working directory for JARVIS access",
         usage="/add-dir <path>", category="core")
async def cmd_add_dir(ctx: CommandContext) -> CommandResult:
    """Add an additional working directory."""
    path = ctx.args.strip() if ctx.args else ""
    if not path:
        return CommandResult(text="Usage: /add-dir <path>")

    path = os.path.expanduser(path)
    path = os.path.realpath(path)

    if not os.path.isdir(path):
        return CommandResult(text=f"Not a directory: {path}", success=False)

    try:
        from brain.state import get_state_manager
        mgr = get_state_manager()
        dirs = list(mgr.state.additional_dirs)
        if path in dirs:
            return CommandResult(text=f"Already added: {path}")
        dirs.append(path)
        mgr.set("additional_dirs", dirs)
        return CommandResult(text=f"Added directory: {path}\nJARVIS can now access files in this directory.")
    except Exception as e:
        return CommandResult(text=f"Error: {e}", success=False)


@command("thinkback", aliases=["reflect"],
         description="Review and reflect on extended thinking from recent conversation",
         usage="/thinkback", category="core")
async def cmd_thinkback(ctx: CommandContext) -> CommandResult:
    """Review thinking blocks from recent conversation."""
    if not ctx.brain:
        return CommandResult(text="Brain not available.")

    # Search recent conversation for thinking content
    history = ctx.brain.memory.get_history(limit=20)
    thinking_blocks = []

    for turn in history:
        if turn.get("role") == "jarvis":
            content = turn.get("content", "")
            # Look for thinking markers
            if "<thinking>" in content or "**Thinking:**" in content:
                thinking_blocks.append(content[:500])

    if not thinking_blocks:
        return CommandResult(text="No thinking blocks found in recent conversation.\nThinking is captured when the model uses extended reasoning.")

    lines = ["Recent Thinking Blocks", "=" * 40]
    for i, block in enumerate(thinking_blocks[-5:], 1):
        lines.append(f"\n--- Block {i} ---")
        lines.append(block)

    return CommandResult(text="\n".join(lines))


@command("login", description="Authenticate with API provider",
         usage="/login [provider]", category="core")
async def cmd_login(ctx: CommandContext) -> CommandResult:
    """Authentication flow."""
    provider = ctx.args.strip() if ctx.args else ""

    if not provider:
        return CommandResult(text="Usage: /login <provider>\n"
                           "Providers: anthropic, openai, groq, ollama\n"
                           "Or set API keys in .env file.")

    env_path = os.path.expanduser("~/.jarvis/.env")

    key_names = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "groq": "GROQ_API_KEY",
        "xai": "XAI_API_KEY",
    }

    key_name = key_names.get(provider.lower())
    if not key_name:
        return CommandResult(text=f"Unknown provider: {provider}\nSupported: {', '.join(key_names.keys())}")

    current = os.environ.get(key_name, "")
    if current:
        masked = current[:8] + "..." + current[-4:] if len(current) > 12 else "***"
        return CommandResult(text=f"Already authenticated with {provider}.\nKey: {masked}\nTo change, update {key_name} in .env")

    return CommandResult(text=f"Set {key_name} in {env_path} or export it:\n  export {key_name}=your-key-here")


@command("logout", description="Clear authentication credentials",
         usage="/logout [provider]", category="core")
async def cmd_logout(ctx: CommandContext) -> CommandResult:
    """Clear auth credentials."""
    return CommandResult(text="Credentials are stored in .env files.\n"
                        "To logout, remove the API key from your .env file.\n"
                        "Cached tokens will be cleared on next restart.")
