"""Plugin & Skill management commands."""
import shutil
from pathlib import Path

from brain.commands.registry import command, CommandContext, CommandResult, PermLevel


@command("plugins", aliases=["pl"], description="List installed plugins with status",
         usage="/plugins", category="plugin", permission=PermLevel.READ_ONLY)
async def cmd_plugins(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    plugins = brain.plugins.list_plugins()
    if not plugins:
        return CommandResult(text="No plugins installed.\nUse /install <path> to add one.")

    lines = ["Installed Plugins", "=" * 40]
    for p in plugins:
        name = getattr(p, "name", str(p))
        desc = getattr(p, "description", "")
        enabled = getattr(p, "enabled", True)
        status = "enabled" if enabled else "disabled"
        lines.append(f"  {name:<24s} [{status}]  {desc}")
    lines.append(f"\n  {len(plugins)} plugin(s) installed.")
    return CommandResult(text="\n".join(lines))


@command("plugin", description="Manage a plugin (install/enable/disable/remove)",
         usage="/plugin <install|enable|disable|remove> <name_or_path>",
         category="plugin", permission=PermLevel.STANDARD)
async def cmd_plugin(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    parts = ctx.args.strip().split(None, 1)
    if len(parts) < 2:
        return CommandResult(
            text="Usage: /plugin <install|enable|disable|remove> <name_or_path>",
            success=False,
        )

    action, target = parts[0].lower(), parts[1]

    if action == "install":
        path = Path(target).expanduser()
        if not path.exists():
            return CommandResult(text=f"Path not found: {path}", success=False)
        try:
            brain.plugins.install(str(path))
            return CommandResult(text=f"Plugin installed from {path}")
        except Exception as e:
            return CommandResult(text=f"Install failed: {e}", success=False)

    elif action == "enable":
        try:
            brain.plugins.enable(target)
            return CommandResult(text=f"Plugin '{target}' enabled.")
        except Exception as e:
            return CommandResult(text=f"Enable failed: {e}", success=False)

    elif action == "disable":
        try:
            brain.plugins.disable(target)
            return CommandResult(text=f"Plugin '{target}' disabled.")
        except Exception as e:
            return CommandResult(text=f"Disable failed: {e}", success=False)

    elif action == "remove":
        try:
            brain.plugins.remove(target)
            return CommandResult(text=f"Plugin '{target}' removed.")
        except Exception as e:
            return CommandResult(text=f"Remove failed: {e}", success=False)

    else:
        return CommandResult(
            text=f"Unknown action: {action}. Use install, enable, disable, or remove.",
            success=False,
        )


@command("skills", aliases=["sk"], description="List available skills with triggers",
         usage="/skills", category="plugin", permission=PermLevel.READ_ONLY)
async def cmd_skills(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    skills = brain.skills.list_skills()
    if not skills:
        return CommandResult(text="No skills registered.\nUse /install <path> to add one.")

    lines = ["Available Skills", "=" * 40]
    for s in skills:
        name = getattr(s, "name", str(s))
        triggers = getattr(s, "triggers", [])
        desc = getattr(s, "description", "")
        trigger_str = ", ".join(triggers) if triggers else "manual"
        lines.append(f"  {name:<20s} triggers=[{trigger_str}]  {desc}")
    lines.append(f"\n  {len(skills)} skill(s) available.")
    return CommandResult(text="\n".join(lines))


@command("skill", description="View a skill's details (template, triggers, hooks)",
         usage="/skill <name>", category="plugin", permission=PermLevel.READ_ONLY)
async def cmd_skill(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    name = ctx.args.strip()
    if not name:
        return CommandResult(text="Usage: /skill <name>", success=False)

    skill = brain.skills.get(name)
    if not skill:
        return CommandResult(text=f"Skill '{name}' not found. Use /skills to list.", success=False)

    lines = [f"Skill: {getattr(skill, 'name', name)}", "=" * 40]
    if hasattr(skill, "description"):
        lines.append(f"  Description: {skill.description}")
    if hasattr(skill, "triggers"):
        lines.append(f"  Triggers:    {', '.join(skill.triggers) if skill.triggers else 'manual'}")
    if hasattr(skill, "hooks"):
        lines.append(f"  Hooks:       {', '.join(skill.hooks) if skill.hooks else 'none'}")
    if hasattr(skill, "template"):
        lines.append(f"\n  Prompt Template:\n  {'─' * 36}")
        for tline in str(skill.template).splitlines():
            lines.append(f"    {tline}")
    return CommandResult(text="\n".join(lines))


@command("install", description="Install plugin or skill from file path",
         usage="/install <path>", category="plugin", permission=PermLevel.FULL)
async def cmd_install(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()
    if not args:
        return CommandResult(text="Usage: /install <path>", success=False)

    src = Path(args).expanduser().resolve()
    if not src.exists():
        return CommandResult(text=f"Path not found: {src}", success=False)

    home = Path.home() / ".jarvis"
    # Determine if it is a skill or plugin by naming convention
    if "skill" in src.name.lower():
        dest_dir = home / "skills"
    else:
        dest_dir = home / "plugins"

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name

    try:
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dest)
    except Exception as e:
        return CommandResult(text=f"Install failed: {e}", success=False)

    # Reload if brain available
    brain = ctx.brain
    if brain:
        if dest_dir.name == "plugins" and hasattr(brain.plugins, "reload"):
            brain.plugins.reload()
        elif dest_dir.name == "skills" and hasattr(brain.skills, "reload"):
            brain.skills.reload()

    return CommandResult(text=f"Installed {src.name} -> {dest}")


@command("uninstall", description="Remove a plugin or skill by name",
         usage="/uninstall <name>", category="plugin", permission=PermLevel.FULL)
async def cmd_uninstall(ctx: CommandContext) -> CommandResult:
    name = ctx.args.strip()
    if not name:
        return CommandResult(text="Usage: /uninstall <name>", success=False)

    brain = ctx.brain
    home = Path.home() / ".jarvis"

    # Try plugins first, then skills
    for sub in ("plugins", "skills"):
        target_dir = home / sub / name
        target_file = home / sub / f"{name}.py"
        removed = False
        if target_dir.is_dir():
            shutil.rmtree(target_dir)
            removed = True
        elif target_file.is_file():
            target_file.unlink()
            removed = True

        if removed:
            if brain:
                mgr = brain.plugins if sub == "plugins" else brain.skills
                if hasattr(mgr, "reload"):
                    mgr.reload()
            return CommandResult(text=f"Removed {sub.rstrip('s')} '{name}'.")

    return CommandResult(text=f"'{name}' not found in plugins or skills.", success=False)


@command("marketplace", aliases=["market"], description="Community plugin marketplace",
         usage="/marketplace", category="plugin", permission=PermLevel.READ_ONLY)
async def cmd_marketplace(ctx: CommandContext) -> CommandResult:
    return CommandResult(
        text="JARVIS Marketplace\n"
             "=" * 40 + "\n"
             "  Coming soon.\n\n"
             "  Community plugins and skills will be\n"
             "  browsable and installable from here.\n\n"
             "  For now, use /install <path> to add\n"
             "  local plugins and skills."
    )
