"""Self-modification commands — let JARVIS edit its own behavior."""
import logging
from pathlib import Path

from src.commands.registry import command, CommandContext, CommandResult, PermLevel

log = logging.getLogger("jarvis.commands.habit")

# Map of behavior keywords to source file paths (relative to jarvis root)
BEHAVIOR_MAP = {
    "tone": "src/reasoning/persona.py",
    "personality": "src/reasoning/persona.py",
    "voice": "src/server/web_server.py",
    "tts": "src/server/web_server.py",
    "stt": "src/speech/stt.py",
    "whisper": "src/speech/stt.py",
    "vocabulary": "src/speech/stt.py",
    "transcription": "src/speech/stt.py",
    "tools": "src/agent/tools.py",
    "memory": "src/memory/store.py",
    "response": "src/brain.py",
    "format": "src/brain.py",
    "output": "src/brain.py",
    "system prompt": "src/brain.py",
    "agent": "src/agent/loop.py",
}

JARVIS_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _guess_target_file(description: str) -> str | None:
    """Best-guess which source file to edit based on the change description."""
    desc_lower = description.lower()
    for keyword, filepath in BEHAVIOR_MAP.items():
        if keyword in desc_lower:
            return filepath
    return None


@command(
    "habit",
    aliases=["self-modify", "selfmod", "change-habit"],
    description="Edit JARVIS's own source code to change a behavior or habit",
    usage="/habit <description of what to change>",
    category="core",
    permission=PermLevel.FULL,
)
async def cmd_habit(ctx: CommandContext) -> CommandResult:
    """Let JARVIS modify its own behavior by editing source files."""
    description = ctx.args.strip()
    if not description:
        return CommandResult(
            text=(
                "Usage: /habit <what to change>\n\n"
                "Examples:\n"
                "  /habit stop adding 'Let me know if you need anything' at the end\n"
                "  /habit always answer in French\n"
                "  /habit fix whisper misrecognizing 'Cameroon' as 'camera'\n"
                "  /habit make your tone more casual\n\n"
                "JARVIS will find the right source file and edit it."
            ),
            success=False,
        )

    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available.", success=False)

    # Get the self-modifier
    modifier = getattr(brain, "_self_modifier", None)
    if modifier is None:
        try:
            from src.evolution.self_modify import SelfModifier
            modifier = SelfModifier(reasoner=brain.reasoner)
            brain._self_modifier = modifier
        except Exception as e:
            return CommandResult(text=f"SelfModifier unavailable: {e}", success=False)

    modifier.set_reasoner(brain.reasoner)

    # Guess the target file
    target_file = _guess_target_file(description)

    if target_file:
        full_path = JARVIS_ROOT / target_file
        if not full_path.exists():
            target_file = None

    if not target_file:
        # Ask the LLM to figure out the right file
        guess_prompt = (
            f"A user asked JARVIS to change this behavior: \"{description}\"\n\n"
            f"Which source file in the JARVIS codebase controls this behavior?\n"
            f"Return ONLY a relative path like 'src/brain.py' or 'src/speech/stt.py'.\n"
            f"Options: src/brain.py, src/reasoning/persona.py, src/speech/stt.py, "
            f"src/server/web_server.py, src/agent/tools.py, src/memory/store.py, "
            f"src/agent/loop.py"
        )
        try:
            guessed = await brain.reasoner.query(guess_prompt, system_prompt="Return ONLY a file path. No explanation.", history=None)
            guessed = guessed.strip().strip("'\"").strip()
            if guessed and (JARVIS_ROOT / guessed).exists():
                target_file = guessed
        except Exception:
            pass

    if not target_file:
        return CommandResult(
            text=(
                f"Couldn't determine which source file controls: \"{description}\"\n\n"
                "Try being more specific, e.g.:\n"
                "  /habit fix the tone in persona.py — stop being so formal\n"
                "  /habit in stt.py, add 'grok' as a fix for 'groq'"
            ),
            success=False,
        )

    log.info(f"[habit] Modifying {target_file} for: {description}")

    result = await modifier.update_file(str(JARVIS_ROOT / target_file), description)

    if not result.get("success"):
        return CommandResult(
            text=f"Self-modification failed: {result.get('error', 'unknown error')}",
            success=False,
        )

    # Restart JARVIS to apply changes
    restart_msg = modifier.restart()

    return CommandResult(
        text=(
            f"Done. Changed `{target_file}`:\n"
            f"  Backup: {result.get('backup', 'none')}\n\n"
            f"{restart_msg}"
        ),
        success=True,
    )


@command(
    "self-history",
    aliases=["habit-log", "modifications"],
    description="Show recent self-modifications JARVIS has made to itself",
    usage="/self-history",
    category="core",
    permission=PermLevel.READ_ONLY,
)
async def cmd_self_history(ctx: CommandContext) -> CommandResult:
    """Show recent self-modifications."""
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available.", success=False)

    modifier = getattr(brain, "_self_modifier", None)
    if modifier is None:
        try:
            from src.evolution.self_modify import SelfModifier
            modifier = SelfModifier()
            brain._self_modifier = modifier
        except Exception as e:
            return CommandResult(text=f"SelfModifier unavailable: {e}", success=False)

    log_entries = modifier.get_creation_log(limit=10)
    if not log_entries:
        return CommandResult(text="No self-modifications recorded yet.")

    import datetime
    lines = ["Recent Self-Modifications", "=" * 50]
    for entry in reversed(log_entries):
        ts = entry.get("timestamp", 0)
        dt = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "unknown"
        kind = entry.get("type", "?")
        file_ = entry.get("file", entry.get("name", "?"))
        cap = entry.get("capability", entry.get("description", ""))
        lines.append(f"  [{dt}] {kind:8s}  {Path(file_).name}")
        if cap:
            lines.append(f"           → {cap[:80]}")

    return CommandResult(text="\n".join(lines))
