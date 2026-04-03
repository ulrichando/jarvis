"""Power management commands — shutdown, reboot, sleep, lock, hibernate."""
import asyncio
import subprocess
from brain.commands.registry import command, CommandContext, CommandResult, PermLevel


def _speak_and_act(brain, message: str, action_fn, delay: float = 2.0):
    """Speak a farewell message, then execute the power action after a delay."""
    loop = asyncio.get_event_loop()
    loop.call_later(delay, action_fn)
    return CommandResult(text=message, data={"spoken": message})


@command("shutdown", aliases=["poweroff"],
         description="Shut down the computer",
         usage="/shutdown [minutes]  — immediate or scheduled",
         category="security", permission=PermLevel.DANGEROUS)
async def cmd_shutdown(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()

    from brain.agent.system_agents import SystemAgent

    if args == "cancel":
        SystemAgent.cancel_shutdown()
        return CommandResult(text="Shutdown cancelled.")

    if args.isdigit():
        minutes = int(args)
        SystemAgent.scheduled_shutdown(minutes)
        return CommandResult(
            text=f"Shutdown scheduled in {minutes} minute{'s' if minutes != 1 else ''}.",
            data={"spoken": f"Shutting down in {minutes} minutes."},
        )

    return _speak_and_act(
        ctx.brain,
        "Shutting down. Goodbye, Ulrich.",
        lambda: subprocess.Popen(["sudo", "shutdown", "-h", "now"]),
    )


@command("reboot", aliases=["restart"],
         description="Reboot the computer",
         usage="/reboot",
         category="security", permission=PermLevel.DANGEROUS)
async def cmd_reboot(ctx: CommandContext) -> CommandResult:
    return _speak_and_act(
        ctx.brain,
        "Rebooting. I'll be right back.",
        lambda: subprocess.Popen(["sudo", "reboot"]),
    )


@command("sleep", aliases=["suspend", "nap"],
         description="Put the computer to sleep",
         usage="/sleep",
         category="security", permission=PermLevel.DANGEROUS)
async def cmd_sleep(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip().lower()

    if args == "hibernate":
        return _speak_and_act(
            ctx.brain,
            "Hibernating. Wake me when you need me.",
            lambda: subprocess.Popen(["sudo", "systemctl", "hibernate"]),
        )

    # Default: hybrid-sleep (saves to RAM + disk, fastest wake)
    return _speak_and_act(
        ctx.brain,
        "Going to sleep. Wake me when you need me.",
        lambda: subprocess.Popen(["sudo", "systemctl", "hybrid-sleep"]),
    )


@command("lock", description="Lock the screen",
         usage="/lock",
         category="security", permission=PermLevel.STANDARD)
async def cmd_lock(ctx: CommandContext) -> CommandResult:
    from brain.agent.system_agents import SystemAgent
    result = SystemAgent.lock()
    if result.get("exit_code", 1) == 0:
        return CommandResult(text="Screen locked.")
    return CommandResult(text="Couldn't lock screen — no supported lock method found.", success=False)
