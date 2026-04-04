"""Power management commands — shutdown, reboot, sleep, hibernate, lock, wake."""
import asyncio
import subprocess
from src.commands.registry import command, CommandContext, CommandResult, PermLevel


def _speak_and_act(brain, message: str, action_fn, delay: float = 2.0):
    """Speak a farewell message, then execute the power action after a delay."""
    loop = asyncio.get_event_loop()
    loop.call_later(delay, action_fn)
    return CommandResult(text=message, data={"spoken": message})


@command("shutdown", aliases=["poweroff"],
         description="Shut down the computer",
         usage="/shutdown [minutes|cancel]  — immediate, scheduled, or cancel",
         category="security", permission=PermLevel.DANGEROUS)
async def cmd_shutdown(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()
    from src.agent.system_agents import SystemAgent

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


@command("hibernate",
         description="Hibernate to disk — supports Wake-on-LAN for remote wake",
         usage="/hibernate",
         category="security", permission=PermLevel.DANGEROUS)
async def cmd_hibernate(ctx: CommandContext) -> CommandResult:
    from src.agent.system_agents import SystemAgent

    # Enable WoL and get info so user knows how to wake it
    wol_info = SystemAgent.get_wol_info()
    mac_lines = []
    for iface, data in wol_info.items():
        if iface == "ip":
            continue
        mac_lines.append(f"  {iface}: MAC={data['mac']}")

    wol_note = ""
    if mac_lines:
        wol_note = (
            "\nWake-on-LAN enabled. To wake remotely:\n"
            + "\n".join(mac_lines)
            + f"\n  IP: {wol_info.get('ip', 'unknown')}"
            + "\n  Use: /wake <mac> or send a magic packet"
        )

    return _speak_and_act(
        ctx.brain,
        "Hibernating. Wake-on-LAN is active. Send a magic packet to bring me back.",
        SystemAgent.hibernate,
    )


@command("sleep", aliases=["suspend", "nap"],
         description="Put the computer to sleep (hybrid-sleep, WoL enabled)",
         usage="/sleep",
         category="security", permission=PermLevel.DANGEROUS)
async def cmd_sleep(ctx: CommandContext) -> CommandResult:
    from src.agent.system_agents import SystemAgent

    return _speak_and_act(
        ctx.brain,
        "Going to sleep. Wake me when you need me.",
        SystemAgent.hybrid_sleep,
    )


@command("lock", description="Lock the screen",
         usage="/lock",
         category="security", permission=PermLevel.STANDARD)
async def cmd_lock(ctx: CommandContext) -> CommandResult:
    from src.agent.system_agents import SystemAgent
    result = SystemAgent.lock()
    if result.get("exit_code", 1) == 0:
        return CommandResult(text="Screen locked.")
    return CommandResult(text="Couldn't lock screen — no supported lock method found.", success=False)


@command("wake", aliases=["wol"],
         description="Send Wake-on-LAN packet to wake a remote machine",
         usage="/wake <mac-address> [broadcast-ip]",
         category="security", permission=PermLevel.STANDARD)
async def cmd_wake(ctx: CommandContext) -> CommandResult:
    from src.agent.system_agents import SystemAgent, NetworkAgent

    args = ctx.args.strip()

    if not args:
        # Show known devices with MACs
        lines = ["Usage: /wake <mac-address> [broadcast-ip]", ""]
        if hasattr(NetworkAgent, 'DEVICES'):
            lines.append("Known devices:")
            for name, info in NetworkAgent.DEVICES.items():
                mac = info.get("mac", "")
                if mac:
                    lines.append(f"  {name:<15s} {info.get('ip', ''):<16s} {mac}")
        lines.append("")
        lines.append("Or: /wake info  — show this machine's WoL status")
        return CommandResult(text="\n".join(lines), success=False)

    if args.lower() == "info":
        wol_info = SystemAgent.get_wol_info()
        lines = ["Wake-on-LAN Status", "=" * 40]
        for iface, data in wol_info.items():
            if iface == "ip":
                lines.append(f"  IP:    {data}")
            else:
                lines.append(f"  {iface}: MAC={data['mac']}  {data['wol']}")
        return CommandResult(text="\n".join(lines))

    parts = args.split()
    mac = parts[0]

    # Resolve device name to MAC if it's a known device
    if hasattr(NetworkAgent, 'DEVICES') and mac.lower() in NetworkAgent.DEVICES:
        device = NetworkAgent.DEVICES[mac.lower()]
        resolved_mac = device.get("mac", "")
        if not resolved_mac:
            return CommandResult(text=f"No MAC address known for '{mac}'.", success=False)
        broadcast = device.get("ip", "255.255.255.255")
        mac = resolved_mac
    else:
        broadcast = parts[1] if len(parts) > 1 else "255.255.255.255"

    result = SystemAgent.wake(mac, broadcast)
    if result.get("success"):
        return CommandResult(
            text=f"Wake-on-LAN packet sent to {mac}.",
            data={"spoken": "Magic packet sent. The machine should wake up shortly."},
        )
    return CommandResult(text=result.get("output", "WoL failed."), success=False)
