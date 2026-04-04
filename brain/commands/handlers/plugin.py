"""Plugin & Skill management commands."""
import shutil
from pathlib import Path

from brain.commands.registry import command, CommandContext, CommandResult, PermLevel


@command("plugins", aliases=["pl"], description="List installed plugins with status, source, and error info",
         usage="/plugins", category="plugin", permission=PermLevel.READ_ONLY)
async def cmd_plugins(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    plugins = brain.plugins.list_plugins()
    if not plugins:
        return CommandResult(text="No plugins installed.\nUse /install <path> to add one.")

    lines = ["Installed Plugins", "=" * 55]
    enabled_count = 0
    disabled_count = 0
    error_count = 0

    for p in plugins:
        name = getattr(p, "name", str(p))
        desc = getattr(p, "description", "")
        enabled = getattr(p, "enabled", True)
        errors = getattr(p, "error_count", 0) or getattr(p, "errors", 0) or 0

        # Determine source
        source = "user"
        p_path = getattr(p, "path", "") or getattr(p, "source_path", "")
        if isinstance(p_path, str):
            if ".jarvis/plugins" in p_path and "/.jarvis/" not in p_path.split(str(Path.home()))[-1] if str(Path.home()) in p_path else True:
                source = "project"
            if hasattr(p, "inline") and p.inline:
                source = "inline"

        status = "enabled" if enabled else "disabled"
        if enabled:
            enabled_count += 1
        else:
            disabled_count += 1
        if errors:
            error_count += errors

        error_str = f"  ERR:{errors}" if errors else ""
        lines.append(f"  {name:<22s} [{status:<8s}] ({source:<7s}){error_str}  {desc}")

    lines.append("")
    lines.append(f"  Total: {len(plugins)}  |  Enabled: {enabled_count}  |  Disabled: {disabled_count}  |  Errors: {error_count}")
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


@command("skills", aliases=["sk"], description="List available skills with invocability, source, and triggers",
         usage="/skills", category="plugin", permission=PermLevel.READ_ONLY)
async def cmd_skills(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    if not brain:
        return CommandResult(text="Brain not available", success=False)

    skills = brain.skills.list_skills()
    if not skills:
        return CommandResult(text="No skills registered.\nUse /install <path> to add one.")

    lines = ["Available Skills", "=" * 55]

    # Count by source and invocability
    by_source: dict[str, int] = {}
    model_invocable_count = 0
    user_invocable_count = 0

    for s in skills:
        name = getattr(s, "name", str(s))
        triggers = getattr(s, "triggers", [])
        desc = getattr(s, "description", "")
        trigger_str = ", ".join(triggers) if triggers else "manual"

        # Determine invocability
        model_inv = getattr(s, "model_invocable", False)
        user_inv = not model_inv  # user-invocable if not model_invocable
        if model_inv:
            model_invocable_count += 1
            inv_tag = "model"
        else:
            user_invocable_count += 1
            inv_tag = "user"

        # Determine source
        s_path = getattr(s, "path", "") or getattr(s, "source_path", "")
        source = "user"
        if isinstance(s_path, str):
            if ".jarvis/skills" in s_path:
                # Check if it's under project .jarvis/ or home ~/.jarvis/
                home_skills = str(Path.home() / ".jarvis" / "skills")
                if s_path.startswith(home_skills):
                    source = "user"
                else:
                    source = "project"
        by_source[source] = by_source.get(source, 0) + 1

        lines.append(f"  {name:<18s} [{inv_tag:<5s}] triggers=[{trigger_str}]  {desc}")

    lines.append("")
    source_str = ", ".join(f"{k}: {v}" for k, v in sorted(by_source.items()))
    lines.append(f"  Total: {len(skills)}  |  Model-invocable: {model_invocable_count}  |  User-invocable: {user_invocable_count}")
    lines.append(f"  Sources: {source_str}")
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


@command("install", description="Install plugin or skill from file path with manifest validation",
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
    if "skill" in src.name.lower() or src.suffix == ".md":
        dest_dir = home / "skills"
        item_type = "skill"
    else:
        dest_dir = home / "plugins"
        item_type = "plugin"

    # Validate manifest for directories
    warnings = []
    if src.is_dir():
        manifest = src / "manifest.json"
        if manifest.exists():
            import json
            try:
                data = json.loads(manifest.read_text())
                required = ["name", "version"]
                for field in required:
                    if field not in data:
                        warnings.append(f"Missing manifest field: {field}")
            except json.JSONDecodeError as e:
                warnings.append(f"Invalid manifest JSON: {e}")
        else:
            warnings.append("No manifest.json found (optional but recommended)")
    elif src.suffix == ".py":
        # Basic Python file validation
        content = src.read_text(errors="replace")
        if "def handle(" not in content and "class " not in content:
            warnings.append("Plugin file missing handle() function or class definition")

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

    lines = [f"Installed {item_type}: {src.name} -> {dest}"]
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in warnings:
            lines.append(f"  - {w}")
    return CommandResult(text="\n".join(lines))


@command("uninstall", description="Remove a plugin or skill by name, showing what was removed",
         usage="/uninstall <name>", category="plugin", permission=PermLevel.FULL)
async def cmd_uninstall(ctx: CommandContext) -> CommandResult:
    name = ctx.args.strip()
    if not name:
        return CommandResult(text="Usage: /uninstall <name>", success=False)

    brain = ctx.brain
    home = Path.home() / ".jarvis"

    # Try plugins first, then skills; also try .md for skills
    search_targets = [
        ("plugins", home / "plugins" / name, "directory"),
        ("plugins", home / "plugins" / f"{name}.py", "file"),
        ("skills", home / "skills" / name, "directory"),
        ("skills", home / "skills" / f"{name}.py", "file"),
        ("skills", home / "skills" / f"{name}.md", "file"),
    ]

    for sub, target, kind in search_targets:
        removed_path = None
        removed_size = 0

        if kind == "directory" and target.is_dir():
            # Calculate size
            for f in target.rglob("*"):
                if f.is_file():
                    removed_size += f.stat().st_size
            shutil.rmtree(target)
            removed_path = target
        elif kind == "file" and target.is_file():
            removed_size = target.stat().st_size
            target.unlink()
            removed_path = target

        if removed_path:
            if brain:
                mgr = brain.plugins if sub == "plugins" else brain.skills
                if hasattr(mgr, "reload"):
                    mgr.reload()
            size_str = f"{removed_size / 1024:.1f}KB" if removed_size > 1024 else f"{removed_size}B"
            return CommandResult(
                text=f"Removed {sub.rstrip('s')} '{name}'\n"
                     f"  Path: {removed_path}\n"
                     f"  Size: {size_str}"
            )

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
