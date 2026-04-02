"""Security & System commands — pentesting, recon, auditing for Kali workflows."""
import subprocess
from pathlib import Path

from brain.commands.registry import command, CommandContext, CommandResult, PermLevel


def _run(cmd: list[str], timeout: int = 120) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"


# ── /scan ──────────────────────────────────────────────────────────────

@command("scan", description="Run nmap scan against a target",
         usage="/scan <target> [--full]", category="security", permission=PermLevel.FULL)
async def cmd_scan(ctx: CommandContext) -> CommandResult:
    args = ctx.args.strip()
    if not args:
        return CommandResult(text="Usage: /scan <target> [--full]", success=False)

    parts = args.split()
    full_scan = "--full" in parts
    target = [p for p in parts if p != "--full"]
    if not target:
        return CommandResult(text="No target specified.", success=False)
    target = target[0]

    if full_scan:
        nmap_args = ["nmap", "-sV", "-sC", "-O", "-A", "-p-", target]
        timeout = 600
    else:
        nmap_args = ["nmap", "-sV", "-sC", target]
        timeout = 120

    scan_type = "full" if full_scan else "quick"
    header = f"Nmap {scan_type} scan: {target}\n{'─' * 40}\n"

    rc, out, err = _run(nmap_args, timeout=timeout)
    if rc != 0:
        return CommandResult(text=f"{header}Scan failed:\n{err}", success=False)
    return CommandResult(text=f"{header}{out}")


# ── /recon ─────────────────────────────────────────────────────────────

@command("recon", description="Full reconnaissance (whois, DNS, nmap, gobuster)",
         usage="/recon <target>", category="security", permission=PermLevel.FULL)
async def cmd_recon(ctx: CommandContext) -> CommandResult:
    target = ctx.args.strip()
    if not target:
        return CommandResult(text="Usage: /recon <target>", success=False)

    lines = [f"Reconnaissance: {target}", "=" * 50]

    # whois
    rc, out, _ = _run(["whois", target], timeout=30)
    lines.append(f"\n[WHOIS]\n{'─' * 40}")
    lines.append(out.strip()[:2000] if rc == 0 else "  whois unavailable or failed")

    # DNS lookup
    rc, out, _ = _run(["dig", target, "+short"], timeout=15)
    lines.append(f"\n[DNS]\n{'─' * 40}")
    lines.append(out.strip() if rc == 0 else "  dig unavailable or failed")

    # nmap service scan
    rc, out, _ = _run(["nmap", "-sV", "--top-ports", "100", target], timeout=120)
    lines.append(f"\n[NMAP TOP 100]\n{'─' * 40}")
    lines.append(out.strip()[:3000] if rc == 0 else "  nmap scan failed")

    # gobuster if port 80/443 detected
    if "80/tcp" in out or "443/tcp" in out:
        scheme = "https" if "443/tcp" in out else "http"
        wordlist = "/usr/share/wordlists/dirb/common.txt"
        if Path(wordlist).exists():
            rc_gb, out_gb, _ = _run(
                ["gobuster", "dir", "-u", f"{scheme}://{target}",
                 "-w", wordlist, "-q", "-t", "20"],
                timeout=120,
            )
            lines.append(f"\n[GOBUSTER]\n{'─' * 40}")
            lines.append(out_gb.strip()[:2000] if rc_gb == 0 else "  gobuster failed")
        else:
            lines.append(f"\n[GOBUSTER]\n{'─' * 40}")
            lines.append("  Wordlist not found — skipped")

    return CommandResult(text="\n".join(lines))


# ── /monitor ───────────────────────────────────────────────────────────

@command("monitor", aliases=["mon"], description="Monitor system resources",
         usage="/monitor [cpu|mem|net|disk]", category="security", permission=PermLevel.READ_ONLY)
async def cmd_monitor(ctx: CommandContext) -> CommandResult:
    resource = ctx.args.strip().lower() or "all"
    lines = ["System Monitor", "=" * 40]

    if resource in ("all", "cpu", "mem"):
        rc, out, _ = _run(["top", "-bn1", "-w", "120"], timeout=10)
        if rc == 0:
            top_lines = out.strip().splitlines()[:15]
            lines.append(f"\n[CPU / Memory]\n{'─' * 40}")
            lines.extend(f"  {l}" for l in top_lines)
        else:
            lines.append("  top command failed")

    if resource in ("all", "net"):
        rc, out, _ = _run(["ss", "-tuln"], timeout=10)
        if rc == 0:
            lines.append(f"\n[Network Listeners]\n{'─' * 40}")
            for l in out.strip().splitlines()[:20]:
                lines.append(f"  {l}")
        else:
            lines.append("  ss command failed")

    if resource in ("all", "disk"):
        rc, out, _ = _run(["df", "-h", "--total"], timeout=10)
        if rc == 0:
            lines.append(f"\n[Disk Usage]\n{'─' * 40}")
            for l in out.strip().splitlines():
                lines.append(f"  {l}")

    return CommandResult(text="\n".join(lines))


# ── /audit ─────────────────────────────────────────────────────────────

@command("audit", description="Security audit of project (secrets, vulns, deps)",
         usage="/audit [path]", category="security", permission=PermLevel.FULL)
async def cmd_audit(ctx: CommandContext) -> CommandResult:
    target_path = Path(ctx.args.strip() or ".").expanduser().resolve()
    if not target_path.exists():
        return CommandResult(text=f"Path not found: {target_path}", success=False)

    lines = [f"Security Audit: {target_path}", "=" * 50]
    issues = []

    # Check for .env files (limited depth to avoid slow traversal)
    env_files = []
    try:
        for f in target_path.rglob(".env*"):
            env_files.append(f)
            if len(env_files) >= 10:
                break
    except Exception:
        pass
    if env_files:
        issues.append(("WARNING", f"Found {len(env_files)} .env file(s):"))
        for f in env_files[:10]:
            issues.append(("", f"  {f}"))

    # Grep for hardcoded secrets patterns (quick, limited depth)
    secret_patterns = ["password", "api_key", "secret_key"]
    for pattern in secret_patterns:
        rc, out, _ = _run(
            ["grep", "-ril", "--include=*.py", "--include=*.js",
             "--max-count=3", "-m", "3",
             pattern, str(target_path)],
            timeout=5,
        )
        if rc == 0 and out.strip():
            matches = out.strip().splitlines()
            issues.append(("CHECK", f"'{pattern}' found in {len(matches)} file(s)"))

    # Check for outdated pip deps (quick check)
    rc, out, _ = _run(["pip", "list", "--outdated", "--format=columns"], timeout=5)
    if rc == 0 and out.strip():
        outdated_lines = out.strip().splitlines()
        if len(outdated_lines) > 1:
            issues.append(("INFO", f"{len(outdated_lines) - 2} outdated Python packages"))

    # Check git secrets
    rc_gs, _, _ = _run(["git", "-C", str(target_path), "log", "--oneline", "-1"], timeout=5)
    if rc_gs == 0:
        rc, out, _ = _run(
            ["git", "-C", str(target_path), "log", "--all", "--diff-filter=A",
             "--name-only", "--pretty=format:", "--", "*.pem", "*.key", "*.p12", "id_rsa"],
            timeout=15,
        )
        if out.strip():
            issues.append(("CRITICAL", "Private keys found in git history!"))

    if issues:
        lines.append(f"\n  {len(issues)} finding(s):\n")
        for severity, msg in issues:
            prefix = f"  [{severity}] " if severity else "        "
            lines.append(f"{prefix}{msg}")
    else:
        lines.append("\n  No obvious issues found.")
    lines.append(f"\n  Scanned: {target_path}")
    return CommandResult(text="\n".join(lines))


# ── /defend ────────────────────────────────────────────────────────────

@command("defend", description="Harden system (firewall, updates, suggestions)",
         usage="/defend [target]", category="security", permission=PermLevel.FULL)
async def cmd_defend(ctx: CommandContext) -> CommandResult:
    lines = ["System Hardening Report", "=" * 50]

    # Check firewall status
    rc, out, _ = _run(["ufw", "status"], timeout=10)
    lines.append(f"\n[Firewall]\n{'─' * 40}")
    if rc == 0:
        lines.append(f"  {out.strip()}")
    else:
        rc2, out2, _ = _run(["iptables", "-L", "-n", "--line-numbers"], timeout=10)
        if rc2 == 0:
            for l in out2.strip().splitlines()[:15]:
                lines.append(f"  {l}")
        else:
            lines.append("  No firewall detected — CONSIDER ENABLING UFW")

    # Check for updates
    rc, out, _ = _run(["apt", "list", "--upgradable"], timeout=30)
    lines.append(f"\n[Available Updates]\n{'─' * 40}")
    if rc == 0:
        upgradable = [l for l in out.strip().splitlines() if "/" in l]
        if upgradable:
            lines.append(f"  {len(upgradable)} package(s) can be upgraded")
            for l in upgradable[:10]:
                lines.append(f"    {l}")
            if len(upgradable) > 10:
                lines.append(f"    ... and {len(upgradable) - 10} more")
        else:
            lines.append("  System is up to date")
    else:
        lines.append("  Could not check updates")

    # Check SSH config
    ssh_config = Path("/etc/ssh/sshd_config")
    if ssh_config.exists():
        lines.append(f"\n[SSH Hardening]\n{'─' * 40}")
        content = ssh_config.read_text(errors="replace")
        if "PermitRootLogin yes" in content:
            lines.append("  WARNING: Root login is permitted")
        if "PasswordAuthentication yes" in content:
            lines.append("  WARNING: Password auth is enabled (prefer keys)")
        if "PermitRootLogin no" in content:
            lines.append("  OK: Root login disabled")

    # Suggestions
    lines.append(f"\n[Recommendations]\n{'─' * 40}")
    lines.append("  1. Enable UFW:        sudo ufw enable")
    lines.append("  2. Default deny:      sudo ufw default deny incoming")
    lines.append("  3. Allow SSH:         sudo ufw allow ssh")
    lines.append("  4. Update system:     sudo apt update && sudo apt upgrade")
    lines.append("  5. Disable root SSH:  PermitRootLogin no in sshd_config")

    return CommandResult(text="\n".join(lines))


# ── /pentest ───────────────────────────────────────────────────────────

@command("pentest", description="Automated pentest workflow (DANGEROUS)",
         usage="/pentest <target>", category="security", permission=PermLevel.DANGEROUS)
async def cmd_pentest(ctx: CommandContext) -> CommandResult:
    brain = ctx.brain
    target = ctx.args.strip()
    if not target:
        return CommandResult(text="Usage: /pentest <target>", success=False)

    if not brain or not hasattr(brain, "agent_loop"):
        return CommandResult(text="Agent not available for pentesting.", success=False)

    # Gather initial recon data
    _, nmap_out, _ = _run(["nmap", "-sV", "-sC", "--top-ports", "1000", target], timeout=300)

    prompt = (
        f"You are a penetration testing assistant. Target: {target}\n"
        f"IMPORTANT: Only test systems you have authorization for.\n\n"
        f"Nmap results:\n{nmap_out[:4000]}\n\n"
        "Based on these results:\n"
        "1. Identify attack surface and potential vulnerabilities\n"
        "2. Suggest specific exploit paths for each open service\n"
        "3. Recommend tools and commands to test each vector\n"
        "4. Prioritize by likelihood of success\n\n"
        "Provide actionable commands for each finding."
    )
    try:
        result = await brain.agent_loop(prompt, max_steps=10)
        return CommandResult(
            text=f"Pentest Report: {target}\n{'=' * 50}\n{result}",
            data={"target": target, "nmap": nmap_out},
        )
    except Exception as e:
        return CommandResult(text=f"Pentest failed: {e}", success=False)
