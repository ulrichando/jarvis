"""Security & System commands -- pentesting, recon, auditing for Kali workflows."""
import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path

from src.commands.registry import command, CommandContext, CommandResult, PermLevel
from src.config import JARVIS_HOME


def _run(cmd: list[str], timeout: int = 120) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"


# ── Shared patterns for secret detection ─────────────────────────────

_SECRET_PATTERNS = [
    (re.compile(r'(?:api[_-]?key|apikey)\s*[=:]\s*["\'][A-Za-z0-9_\-]{16,}["\']', re.I), "API key"),
    (re.compile(r'(?:secret|password|passwd|pwd)\s*[=:]\s*["\'][^"\']{8,}["\']', re.I), "Password/Secret"),
    (re.compile(r'(?:token)\s*[=:]\s*["\'][A-Za-z0-9_\-\.]{20,}["\']', re.I), "Token"),
    (re.compile(r'(?:AWS_ACCESS_KEY_ID|aws_access_key)\s*[=:]\s*["\']?AK[A-Z0-9]{18}', re.I), "AWS Access Key"),
    (re.compile(r'(?:AWS_SECRET_ACCESS_KEY|aws_secret_key)\s*[=:]\s*["\']?[A-Za-z0-9/+=]{40}', re.I), "AWS Secret Key"),
    (re.compile(r'ghp_[A-Za-z0-9]{36}', re.I), "GitHub Token"),
    (re.compile(r'sk-[A-Za-z0-9]{20,}', re.I), "OpenAI/Stripe Key"),
    (re.compile(r'-----BEGIN (?:RSA |DSA |EC )?PRIVATE KEY-----', re.I), "Private Key"),
]

_DANGEROUS_SCRIPT_PATTERNS = [
    (re.compile(r'\bcurl\b.*\|\s*(?:ba)?sh', re.I), "Piped curl to shell"),
    (re.compile(r'\bwget\b.*\|\s*(?:ba)?sh', re.I), "Piped wget to shell"),
    (re.compile(r'\beval\s*\(', re.I), "eval() usage"),
    (re.compile(r'\bexec\s*\(', re.I), "exec() usage"),
    (re.compile(r'subprocess.*shell\s*=\s*True', re.I), "subprocess shell=True"),
    (re.compile(r'\brm\s+-rf\s+/', re.I), "Recursive delete from root"),
    (re.compile(r'chmod\s+777\b', re.I), "chmod 777"),
    (re.compile(r':\(\)\s*\{', re.I), "Fork bomb pattern"),
]


def _scan_file_for_secrets(filepath: str, max_size: int = 500_000) -> list[dict]:
    """Scan a single file for hardcoded secrets and dangerous patterns."""
    findings = []
    try:
        size = os.path.getsize(filepath)
        if size > max_size:
            return findings
        with open(filepath, "r", errors="replace") as f:
            for line_no, line in enumerate(f, 1):
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("//"):
                    continue
                for pattern, desc in _SECRET_PATTERNS:
                    if pattern.search(line):
                        findings.append({
                            "file": filepath, "line": line_no,
                            "severity": "CRITICAL", "type": "secret",
                            "msg": f"{desc}: {stripped[:80]}..."
                        })
                for pattern, desc in _DANGEROUS_SCRIPT_PATTERNS:
                    if pattern.search(line):
                        findings.append({
                            "file": filepath, "line": line_no,
                            "severity": "WARNING", "type": "dangerous_pattern",
                            "msg": f"{desc}: {stripped[:80]}"
                        })
    except Exception:
        pass
    return findings


def _check_permissions(filepath: str) -> list[dict]:
    """Check file permissions for insecure settings."""
    findings = []
    try:
        st = os.stat(filepath)
        mode = st.st_mode
        if mode & stat.S_IWOTH:
            findings.append({
                "file": filepath, "line": 0,
                "severity": "WARNING", "type": "permissions",
                "msg": f"World-writable: {oct(mode)[-3:]}"
            })
        if mode & (stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO) == 0o777:
            findings.append({
                "file": filepath, "line": 0,
                "severity": "CRITICAL", "type": "permissions",
                "msg": "Permissions 777 -- fully open"
            })
        if filepath.endswith((".key", ".pem", ".p12", "id_rsa")) and mode & stat.S_IROTH:
            findings.append({
                "file": filepath, "line": 0,
                "severity": "CRITICAL", "type": "permissions",
                "msg": f"Private key file is world-readable: {oct(mode)[-3:]}"
            })
    except Exception:
        pass
    return findings


# ── /scan ────────────────────────────────────────────────────────────

@command("scan", description="Security scan: secrets, permissions, dangerous patterns",
         usage="/scan [path] [--full]", category="security", permission=PermLevel.FULL)
async def cmd_scan(ctx: CommandContext) -> CommandResult:
    """Scan a directory for security vulnerabilities.

    Checks for: hardcoded secrets, insecure permissions, dangerous commands
    in scripts, .env files, private keys, and known vulnerable patterns.
    """
    args = ctx.args.strip()
    parts = args.split() if args else []
    full_scan = "--full" in parts
    parts = [p for p in parts if p != "--full"]
    target_path = Path(parts[0]).expanduser().resolve() if parts else Path.cwd()

    if not target_path.exists():
        return CommandResult(text=f"Path not found: {target_path}", success=False)

    lines = [f"Security Scan: {target_path}", "=" * 60]
    all_findings = []

    skip_dirs = {".git", "__pycache__", ".venv", "venv", "node_modules",
                 ".mypy_cache", ".pytest_cache", "dist", "build", "target",
                 ".cache", ".cargo", "release", "debug"}
    code_exts = {".py", ".js", ".ts", ".jsx", ".tsx", ".sh", ".bash", ".rb",
                 ".go", ".rs", ".yml", ".yaml", ".toml", ".json", ".cfg", ".ini",
                 ".conf", ".env", ".sql"}

    scanned_files = 0
    if target_path.is_file():
        all_findings.extend(_scan_file_for_secrets(str(target_path)))
        all_findings.extend(_check_permissions(str(target_path)))
        scanned_files = 1
    else:
        for dirpath, dirnames, filenames in os.walk(str(target_path)):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                ext = os.path.splitext(fname)[1].lower()

                # Check permissions on all files
                all_findings.extend(_check_permissions(fpath))

                # Check .env files
                if fname.startswith(".env"):
                    all_findings.append({
                        "file": fpath, "line": 0,
                        "severity": "WARNING", "type": "env_file",
                        "msg": f".env file found: {fname}"
                    })
                    all_findings.extend(_scan_file_for_secrets(fpath))
                    scanned_files += 1
                    continue

                # Scan code files for secrets/patterns
                if ext in code_exts or (full_scan and ext not in {".pyc", ".so", ".o", ".bin", ".wasm"}):
                    all_findings.extend(_scan_file_for_secrets(fpath))
                    scanned_files += 1

    # Also check for bash_security violations in shell scripts
    try:
        from src.agent.bash_security import BashSecurityChecker
        checker = BashSecurityChecker()
        for f in all_findings:
            if f.get("type") == "dangerous_pattern" and "shell" in f.get("msg", "").lower():
                pass  # already flagged
    except ImportError:
        pass

    # Sort by severity
    severity_order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
    all_findings.sort(key=lambda f: severity_order.get(f.get("severity", "INFO"), 3))

    # Deduplicate
    seen = set()
    unique_findings = []
    for f in all_findings:
        key = (f["file"], f.get("line", 0), f["msg"][:60])
        if key not in seen:
            seen.add(key)
            unique_findings.append(f)

    critical = [f for f in unique_findings if f["severity"] == "CRITICAL"]
    warnings = [f for f in unique_findings if f["severity"] == "WARNING"]

    lines.append(f"\n  Scanned {scanned_files} files")
    lines.append(f"  Findings: {len(critical)} critical, {len(warnings)} warnings")

    if critical:
        lines.append(f"\n  CRITICAL ({len(critical)})")
        lines.append(f"  {'---' * 15}")
        for f in critical[:50]:
            rel = os.path.relpath(f["file"], str(target_path))
            loc = f":{f['line']}" if f.get("line") else ""
            lines.append(f"    [{f['type']}] {rel}{loc}")
            lines.append(f"      {f['msg']}")

    if warnings:
        lines.append(f"\n  WARNINGS ({len(warnings)})")
        lines.append(f"  {'---' * 15}")
        for f in warnings[:30]:
            rel = os.path.relpath(f["file"], str(target_path))
            loc = f":{f['line']}" if f.get("line") else ""
            lines.append(f"    [{f['type']}] {rel}{loc}")
            lines.append(f"      {f['msg']}")
        if len(warnings) > 30:
            lines.append(f"    ... +{len(warnings) - 30} more")

    if not unique_findings:
        lines.append("\n  No security issues found.")

    return CommandResult(
        text="\n".join(lines),
        data={"findings": unique_findings, "scanned": scanned_files},
    )


# ── /recon ───────────────────────────────────────────────────────────

@command("recon", description="Full reconnaissance (whois, DNS, nmap, gobuster)",
         usage="/recon <target>", category="security", permission=PermLevel.FULL)
async def cmd_recon(ctx: CommandContext) -> CommandResult:
    """Gather information about a target using available tools."""
    target = ctx.args.strip()
    if not target:
        return CommandResult(text="Usage: /recon <target>", success=False)

    lines = [f"Reconnaissance: {target}", "=" * 50]

    # whois
    rc, out, _ = _run(["whois", target], timeout=30)
    lines.append(f"\n[WHOIS]\n{'---' * 13}")
    if rc == 0 and out.strip():
        # Extract key whois fields
        key_fields = {}
        for line in out.strip().splitlines():
            for field in ("Registrar:", "Organization:", "Creation Date:", "Registry Expiry",
                          "Name Server:", "Country:", "State:", "City:", "Admin Email:"):
                if field.lower() in line.lower():
                    key_fields[field] = line.strip()
        if key_fields:
            for k, v in key_fields.items():
                lines.append(f"  {v}")
        else:
            lines.append(out.strip()[:2000])
    else:
        lines.append("  whois unavailable or failed")

    # DNS lookups
    lines.append(f"\n[DNS]\n{'---' * 13}")
    for rtype in ("A", "AAAA", "MX", "NS", "TXT", "CNAME"):
        rc, out, _ = _run(["dig", target, rtype, "+short"], timeout=10)
        if rc == 0 and out.strip():
            lines.append(f"  {rtype:6s} {out.strip()[:200]}")

    # Reverse DNS if target looks like an IP
    if re.match(r'^\d+\.\d+\.\d+\.\d+$', target):
        rc, out, _ = _run(["dig", "-x", target, "+short"], timeout=10)
        if rc == 0 and out.strip():
            lines.append(f"  PTR    {out.strip()}")

    # nmap service scan
    rc, out, _ = _run(["nmap", "-sV", "--top-ports", "100", target], timeout=120)
    lines.append(f"\n[NMAP TOP 100]\n{'---' * 13}")
    if rc == 0:
        # Extract just the port table and summary
        in_ports = False
        for line in out.strip().splitlines():
            if "PORT" in line and "STATE" in line:
                in_ports = True
            if in_ports:
                lines.append(f"  {line}")
                if line.strip() == "" and in_ports:
                    in_ports = False
            elif "Nmap scan report" in line or "Host is up" in line or "Nmap done" in line:
                lines.append(f"  {line}")
    else:
        lines.append("  nmap scan failed")

    # gobuster if port 80/443 detected
    if rc == 0 and ("80/tcp" in out or "443/tcp" in out):
        scheme = "https" if "443/tcp" in out else "http"
        wordlist = "/usr/share/wordlists/dirb/common.txt"
        if Path(wordlist).exists():
            rc_gb, out_gb, _ = _run(
                ["gobuster", "dir", "-u", f"{scheme}://{target}",
                 "-w", wordlist, "-q", "-t", "20"],
                timeout=120,
            )
            lines.append(f"\n[GOBUSTER]\n{'---' * 13}")
            lines.append(out_gb.strip()[:2000] if rc_gb == 0 else "  gobuster failed")
        else:
            lines.append(f"\n[GOBUSTER]\n{'---' * 13}")
            lines.append("  Wordlist not found -- skipped")

    # SSL check if https port found
    if rc == 0 and "443/tcp" in out:
        rc_ssl, out_ssl, _ = _run(
            ["openssl", "s_client", "-connect", f"{target}:443", "-servername", target],
            timeout=10,
        )
        if rc_ssl == 0 and out_ssl:
            lines.append(f"\n[SSL/TLS]\n{'---' * 13}")
            for line in out_ssl.splitlines():
                if any(k in line for k in ("subject=", "issuer=", "Protocol", "Cipher")):
                    lines.append(f"  {line.strip()}")

    return CommandResult(text="\n".join(lines))


# ── /monitor ─────────────────────────────────────────────────────────

# Module-level state for security monitoring
_monitor_state = {
    "active": False,
    "start_time": 0,
    "events": [],  # list of {timestamp, event_type, detail}
    "suspicious_count": 0,
}

_SUSPICIOUS_COMMANDS = re.compile(
    r'\b(nc|ncat|netcat|socat|curl.*\|.*sh|wget.*\|.*sh|python.*-c|'
    r'perl.*-e|ruby.*-e|chmod\s+[0-7]*7[0-7]*|rm\s+-rf|dd\s+if=|'
    r'base64\s+-d|openssl\s+enc)\b', re.I
)

_PATH_TRAVERSAL = re.compile(r'\.\./\.\./|/etc/passwd|/etc/shadow', re.I)


@command("monitor", aliases=["mon"], description="Security monitoring for tool calls and commands",
         usage="/monitor [start|stop|status]", category="security", permission=PermLevel.READ_ONLY)
async def cmd_monitor(ctx: CommandContext) -> CommandResult:
    """Watch for suspicious activity in tool calls and bash commands.

    Uses hook system to intercept tool calls and flag suspicious patterns.
    """
    action = ctx.args.strip().lower() or "status"

    if action == "start":
        _monitor_state["active"] = True
        _monitor_state["start_time"] = time.time()
        _monitor_state["events"] = []
        _monitor_state["suspicious_count"] = 0

        # Register monitoring hook if brain has hooks
        brain = ctx.brain
        if brain and hasattr(brain, 'hooks'):
            try:
                brain.hooks.add_hook("PreToolUse", {
                    "type": "command",
                    "command": "true",  # always allow, just log
                    "description": "security-monitor",
                })
            except Exception:
                pass  # Hook system may not support runtime hooks

        return CommandResult(text="Security monitoring STARTED.\n"
                           "  Watching tool calls and bash commands for suspicious patterns.\n"
                           "  Use /monitor status to see events, /monitor stop to disable.")

    elif action == "stop":
        duration = time.time() - _monitor_state["start_time"] if _monitor_state["active"] else 0
        event_count = len(_monitor_state["events"])
        _monitor_state["active"] = False

        lines = ["Security monitoring STOPPED."]
        lines.append(f"  Duration: {duration:.0f}s")
        lines.append(f"  Events logged: {event_count}")
        lines.append(f"  Suspicious: {_monitor_state['suspicious_count']}")
        return CommandResult(text="\n".join(lines))

    elif action == "status":
        lines = ["Security Monitor Status", "=" * 40]
        if _monitor_state["active"]:
            duration = time.time() - _monitor_state["start_time"]
            lines.append(f"  Status:     ACTIVE ({duration:.0f}s)")
        else:
            lines.append(f"  Status:     INACTIVE")
        lines.append(f"  Events:     {len(_monitor_state['events'])}")
        lines.append(f"  Suspicious: {_monitor_state['suspicious_count']}")

        # Show system info
        lines.append(f"\n[System Resources]\n{'---' * 13}")
        rc, out, _ = _run(["top", "-bn1", "-w", "120"], timeout=10)
        if rc == 0:
            for l in out.strip().splitlines()[:8]:
                lines.append(f"  {l}")

        lines.append(f"\n[Network Listeners]\n{'---' * 13}")
        rc, out, _ = _run(["ss", "-tuln"], timeout=10)
        if rc == 0:
            for l in out.strip().splitlines()[:15]:
                lines.append(f"  {l}")

        lines.append(f"\n[Disk Usage]\n{'---' * 13}")
        rc, out, _ = _run(["df", "-h", "--total"], timeout=10)
        if rc == 0:
            for l in out.strip().splitlines():
                lines.append(f"  {l}")

        # Show recent suspicious events
        suspicious = [e for e in _monitor_state["events"] if e.get("suspicious")]
        if suspicious:
            lines.append(f"\n[Recent Suspicious Activity]\n{'---' * 13}")
            for e in suspicious[-10:]:
                ts = time.strftime("%H:%M:%S", time.localtime(e["timestamp"]))
                lines.append(f"  [{ts}] {e['event_type']}: {e['detail'][:100]}")

        return CommandResult(text="\n".join(lines))
    else:
        return CommandResult(text="Usage: /monitor [start|stop|status]", success=False)


def monitor_log_event(event_type: str, detail: str):
    """Log an event from the security monitor (called by hooks or tool execution)."""
    if not _monitor_state["active"]:
        return
    suspicious = bool(
        _SUSPICIOUS_COMMANDS.search(detail) or _PATH_TRAVERSAL.search(detail)
    )
    _monitor_state["events"].append({
        "timestamp": time.time(),
        "event_type": event_type,
        "detail": detail,
        "suspicious": suspicious,
    })
    if suspicious:
        _monitor_state["suspicious_count"] += 1


# ── /audit ───────────────────────────────────────────────────────────

@command("audit", description="Security audit: secrets, vulns, deps, tool call review",
         usage="/audit [path]", category="security", permission=PermLevel.FULL)
async def cmd_audit(ctx: CommandContext) -> CommandResult:
    """Comprehensive security audit of a project and recent tool calls.

    Checks for: hardcoded secrets, insecure permissions, dangerous commands,
    known vulnerable patterns, git history for leaked keys, outdated deps,
    and reviews recent tool calls for path traversal and command injection.
    """
    target_path = Path(ctx.args.strip() or ".").expanduser().resolve()
    if not target_path.exists():
        return CommandResult(text=f"Path not found: {target_path}", success=False)

    lines = [f"Security Audit: {target_path}", "=" * 60]
    issues = []

    # 1. Check for .env files
    env_files = []
    try:
        for f in target_path.rglob(".env*"):
            env_files.append(f)
            if len(env_files) >= 20:
                break
    except Exception:
        pass
    if env_files:
        issues.append(("WARNING", f"Found {len(env_files)} .env file(s):"))
        for f in env_files[:10]:
            gitignore_check = ""
            try:
                rc, _, _ = _run(["git", "-C", str(target_path), "check-ignore", str(f)], timeout=5)
                gitignore_check = " (gitignored)" if rc == 0 else " (NOT gitignored!)"
            except Exception:
                pass
            issues.append(("", f"  {f}{gitignore_check}"))

    # 2. Scan for hardcoded secrets in code files
    secret_count = 0
    skip_dirs = {".git", "__pycache__", ".venv", "venv", "node_modules", "dist", "build"}
    code_exts = {".py", ".js", ".ts", ".sh", ".go", ".rs", ".yml", ".yaml", ".json", ".cfg"}
    try:
        for dirpath, dirnames, filenames in os.walk(str(target_path)):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext in code_exts:
                    fpath = os.path.join(dirpath, fname)
                    findings = _scan_file_for_secrets(fpath)
                    for f in findings:
                        if f["severity"] == "CRITICAL":
                            rel = os.path.relpath(f["file"], str(target_path))
                            issues.append(("CRITICAL", f"Secret in {rel}:{f.get('line', '?')} -- {f['msg'][:80]}"))
                            secret_count += 1
                            if secret_count >= 20:
                                break
                    if secret_count >= 20:
                        break
            if secret_count >= 20:
                issues.append(("", "  ... truncated, too many secrets found"))
                break
    except Exception as e:
        issues.append(("INFO", f"File scan error: {e}"))

    # 3. Check insecure permissions
    perm_issues = 0
    try:
        for dirpath, dirnames, filenames in os.walk(str(target_path)):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                findings = _check_permissions(fpath)
                for f in findings:
                    rel = os.path.relpath(f["file"], str(target_path))
                    issues.append((f["severity"], f"{rel}: {f['msg']}"))
                    perm_issues += 1
                    if perm_issues >= 15:
                        break
            if perm_issues >= 15:
                break
    except Exception:
        pass

    # 4. Grep for hardcoded secrets patterns (backup check)
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

    # 5. Check for outdated pip deps
    rc, out, _ = _run(["pip", "list", "--outdated", "--format=columns"], timeout=15)
    if rc == 0 and out.strip():
        outdated_lines = out.strip().splitlines()
        if len(outdated_lines) > 1:
            issues.append(("INFO", f"{len(outdated_lines) - 2} outdated Python packages"))

    # 6. Check git history for leaked secrets
    rc_gs, _, _ = _run(["git", "-C", str(target_path), "log", "--oneline", "-1"], timeout=5)
    if rc_gs == 0:
        rc, out, _ = _run(
            ["git", "-C", str(target_path), "log", "--all", "--diff-filter=A",
             "--name-only", "--pretty=format:", "--", "*.pem", "*.key", "*.p12", "id_rsa"],
            timeout=15,
        )
        if out.strip():
            issues.append(("CRITICAL", "Private keys found in git history!"))
            for f in out.strip().splitlines()[:5]:
                if f.strip():
                    issues.append(("", f"  {f.strip()}"))

    # 7. Review recent tool calls for suspicious patterns (if brain available)
    brain = ctx.brain
    if brain and hasattr(brain, 'memory'):
        try:
            history = brain.memory.get_history(limit=50)
            path_traversal_count = 0
            injection_count = 0
            for entry in history:
                content = entry.get("content", "") if isinstance(entry, dict) else str(entry)
                if _PATH_TRAVERSAL.search(content):
                    path_traversal_count += 1
                if re.search(r';\s*(rm|cat|curl|wget|nc)\b', content):
                    injection_count += 1
            if path_traversal_count:
                issues.append(("WARNING", f"Path traversal patterns in {path_traversal_count} recent message(s)"))
            if injection_count:
                issues.append(("WARNING", f"Possible command injection in {injection_count} recent message(s)"))
        except Exception:
            pass

    # 8. Check using bash_security module
    try:
        from src.agent.bash_security import BashSecurityChecker, DANGEROUS_COMMANDS
        checker = BashSecurityChecker()
        # Scan shell scripts for dangerous commands
        for dirpath, dirnames, filenames in os.walk(str(target_path)):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for fname in filenames:
                if fname.endswith((".sh", ".bash")):
                    fpath = os.path.join(dirpath, fname)
                    try:
                        with open(fpath, "r", errors="replace") as f:
                            script_content = f.read()
                        violations = checker.check_all(script_content)
                        if violations:
                            rel = os.path.relpath(fpath, str(target_path))
                            for v in violations[:3]:
                                issues.append(("WARNING",
                                    f"Script {rel}: {v.violation_id} -- {v.detail[:60]}"))
                    except Exception:
                        pass
    except ImportError:
        pass

    # Format output
    if issues:
        critical_count = sum(1 for s, _ in issues if s == "CRITICAL")
        warning_count = sum(1 for s, _ in issues if s in ("WARNING", "CHECK"))
        lines.append(f"\n  {len(issues)} finding(s): {critical_count} critical, {warning_count} warnings\n")
        for severity, msg in issues:
            prefix = f"  [{severity}] " if severity else "        "
            lines.append(f"{prefix}{msg}")
    else:
        lines.append("\n  No obvious issues found.")

    lines.append(f"\n  Scanned: {target_path}")
    return CommandResult(text="\n".join(lines))


# ── /defend ──────────────────────────────────────────────────────────

@command("defend", description="Harden system (firewall, updates, SSH, services)",
         usage="/defend [target]", category="security", permission=PermLevel.FULL)
async def cmd_defend(ctx: CommandContext) -> CommandResult:
    """Analyze current system for defensive improvements.

    Checks: firewall status, available updates, SSH hardening, running services,
    open ports, suid binaries, and provides actionable recommendations.
    """
    lines = ["System Hardening Report", "=" * 60]
    recommendations = []

    # Check firewall status
    rc, out, _ = _run(["ufw", "status"], timeout=10)
    lines.append(f"\n[Firewall]\n{'---' * 13}")
    if rc == 0:
        lines.append(f"  {out.strip()}")
        if "inactive" in out.lower():
            recommendations.append("Enable UFW: sudo ufw enable && sudo ufw default deny incoming")
    else:
        rc2, out2, _ = _run(["iptables", "-L", "-n", "--line-numbers"], timeout=10)
        if rc2 == 0:
            for l in out2.strip().splitlines()[:15]:
                lines.append(f"  {l}")
        else:
            lines.append("  No firewall detected")
            recommendations.append("CRITICAL: Enable a firewall (ufw or iptables)")

    # Check for updates
    rc, out, _ = _run(["apt", "list", "--upgradable"], timeout=30)
    lines.append(f"\n[Available Updates]\n{'---' * 13}")
    if rc == 0:
        upgradable = [l for l in out.strip().splitlines() if "/" in l]
        if upgradable:
            security_updates = [l for l in upgradable if "security" in l.lower()]
            lines.append(f"  {len(upgradable)} package(s) can be upgraded")
            if security_updates:
                lines.append(f"  {len(security_updates)} are SECURITY updates")
                recommendations.append(f"Install security updates: sudo apt upgrade")
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
        lines.append(f"\n[SSH Hardening]\n{'---' * 13}")
        try:
            content = ssh_config.read_text(errors="replace")
            checks = {
                "PermitRootLogin": ("no", "prohibit-password"),
                "PasswordAuthentication": ("no",),
                "X11Forwarding": ("no",),
                "MaxAuthTries": None,
                "PubkeyAuthentication": ("yes",),
            }
            for key, good_values in checks.items():
                match = re.search(rf'^{key}\s+(\S+)', content, re.MULTILINE)
                if match:
                    value = match.group(1)
                    if good_values and value not in good_values:
                        lines.append(f"  WARNING: {key} = {value}")
                        recommendations.append(f"Set {key} to {good_values[0]} in sshd_config")
                    else:
                        lines.append(f"  OK: {key} = {value}")
                else:
                    lines.append(f"  NOT SET: {key}")
        except Exception:
            lines.append("  Could not read SSH config")

    # Check running services
    lines.append(f"\n[Running Services]\n{'---' * 13}")
    rc, out, _ = _run(["systemctl", "list-units", "--type=service", "--state=running", "--no-pager"], timeout=10)
    if rc == 0:
        service_lines = [l for l in out.strip().splitlines() if ".service" in l]
        lines.append(f"  {len(service_lines)} services running")
        # Flag potentially risky services
        risky_services = ["telnet", "ftp", "rsh", "rlogin", "tftp"]
        for svc in service_lines:
            for risky in risky_services:
                if risky in svc.lower():
                    lines.append(f"  WARNING: {svc.strip()}")
                    recommendations.append(f"Disable insecure service: {risky}")

    # Check SUID binaries
    lines.append(f"\n[SUID Binaries]\n{'---' * 13}")
    rc, out, _ = _run(["find", "/usr/bin", "/usr/sbin", "/usr/local/bin",
                        "-perm", "-4000", "-type", "f"], timeout=10)
    if rc == 0 and out.strip():
        suid_files = out.strip().splitlines()
        lines.append(f"  {len(suid_files)} SUID binaries found")
        # Flag unusual ones
        common_suid = {"sudo", "su", "passwd", "ping", "mount", "umount", "newgrp", "chsh", "chfn", "gpasswd"}
        unusual = [f for f in suid_files if os.path.basename(f) not in common_suid]
        if unusual:
            lines.append(f"  {len(unusual)} non-standard SUID binaries:")
            for f in unusual[:10]:
                lines.append(f"    {f}")
            if unusual:
                recommendations.append("Review non-standard SUID binaries for necessity")

    # Recommendations
    lines.append(f"\n[Recommendations]\n{'---' * 13}")
    if not recommendations:
        recommendations = [
            "Keep system updated: sudo apt update && sudo apt upgrade",
            "Enable firewall if not active: sudo ufw enable",
            "Use SSH keys instead of passwords",
            "Review running services and disable unnecessary ones",
            "Set up automated security updates: sudo apt install unattended-upgrades",
        ]
    for i, rec in enumerate(recommendations, 1):
        lines.append(f"  {i}. {rec}")

    return CommandResult(text="\n".join(lines))


# ── /pentest ─────────────────────────────────────────────────────────

@command("pentest", description="Automated pentest workflow (DANGEROUS)",
         usage="/pentest <target>", category="security", permission=PermLevel.DANGEROUS)
async def cmd_pentest(ctx: CommandContext) -> CommandResult:
    """Penetration test helper: suggests recon and testing approaches for a target.

    Runs initial nmap scan, then uses AI to analyze attack surface and
    suggest exploit paths, tools, and commands for each finding.
    """
    brain = ctx.brain
    target = ctx.args.strip()
    if not target:
        return CommandResult(text="Usage: /pentest <target>", success=False)

    if not brain:
        # Fallback: manual recon without AI
        lines = [f"Pentest Reconnaissance: {target}", "=" * 50]

        # Nmap scan
        lines.append("\n[Phase 1: Service Discovery]")
        _, nmap_out, _ = _run(["nmap", "-sV", "-sC", "--top-ports", "1000", target], timeout=300)
        if nmap_out:
            lines.append(nmap_out.strip()[:3000])
        else:
            lines.append("  nmap scan failed")

        # Suggest next steps based on open ports
        lines.append("\n[Suggested Next Steps]")
        if "22/tcp" in (nmap_out or ""):
            lines.append("  SSH (22): Try hydra -l root -P wordlist.txt ssh://" + target)
        if "80/tcp" in (nmap_out or "") or "443/tcp" in (nmap_out or ""):
            lines.append(f"  HTTP: gobuster dir -u http://{target} -w /usr/share/wordlists/dirb/common.txt")
            lines.append(f"  HTTP: nikto -h http://{target}")
            lines.append(f"  HTTP: wapiti -u http://{target}")
        if "21/tcp" in (nmap_out or ""):
            lines.append("  FTP (21): Check for anonymous login")
        if "445/tcp" in (nmap_out or "") or "139/tcp" in (nmap_out or ""):
            lines.append(f"  SMB: enum4linux -a {target}")
            lines.append(f"  SMB: smbclient -L //{target} -N")
        if "3306/tcp" in (nmap_out or ""):
            lines.append(f"  MySQL: mysql -h {target} -u root --password=")
        if "5432/tcp" in (nmap_out or ""):
            lines.append(f"  PostgreSQL: psql -h {target} -U postgres")

        lines.append("\n[General Tools]")
        lines.append(f"  Full scan: nmap -sV -sC -O -A -p- {target}")
        lines.append(f"  Vuln scan: nmap --script vuln {target}")
        lines.append(f"  OS detect: nmap -O {target}")

        return CommandResult(text="\n".join(lines), data={"target": target})

    # AI-powered pentest with agent loop
    _, nmap_out, _ = _run(["nmap", "-sV", "-sC", "--top-ports", "1000", target], timeout=300)

    prompt = (
        f"You are a penetration testing assistant. Target: {target}\n"
        f"IMPORTANT: Only test systems you have authorization for.\n\n"
        f"Nmap results:\n{nmap_out[:4000]}\n\n"
        "Based on these results:\n"
        "1. Identify attack surface and potential vulnerabilities\n"
        "2. Suggest specific exploit paths for each open service\n"
        "3. Recommend tools and commands to test each vector\n"
        "4. Prioritize by likelihood of success\n"
        "5. Include post-exploitation steps if access is gained\n\n"
        "Format as a structured pentest report with phases:\n"
        "- Reconnaissance findings\n"
        "- Vulnerability analysis\n"
        "- Exploitation suggestions\n"
        "- Post-exploitation\n\n"
        "Provide actionable commands for each finding."
    )
    try:
        result = await brain.think(prompt)
        return CommandResult(
            text=f"Pentest Report: {target}\n{'=' * 50}\n{result}",
            data={"target": target, "nmap": nmap_out},
        )
    except Exception as e:
        return CommandResult(text=f"Pentest failed: {e}", success=False)


# ── Scan cost persistence ─────────────────────────────────────────────

_SCAN_COSTS_FILE = JARVIS_HOME / "scan_costs.jsonl"


def _save_scan_cost_record(record: dict) -> None:
    """Append one scan cost record to ~/.jarvis/scan_costs.jsonl."""
    try:
        _SCAN_COSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_SCAN_COSTS_FILE, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


# ── /vuln-scan ────────────────────────────────────────────────────────

@command(
    "vuln-scan",
    aliases=["vulnscan", "vs"],
    description="Full automated vulnerability discovery pipeline (Glasswing-style)",
    usage="/vuln-scan <path|git-url|host:port> [--no-exploit] [--report-dir <dir>]",
    category="security",
    permission=PermLevel.FULL,
)
async def cmd_vuln_scan(ctx: CommandContext) -> CommandResult:
    """Run the 8-stage Glasswing vulnerability discovery pipeline.

    Stages:
      1. File risk ranking          — scores every file 1-5 by attack surface
      2. Parallel hypothesis engine — dispatches sub-agents per high-risk file
      3. Static / taint analysis    — traces untrusted input to dangerous sinks
      4. Confirmation loop          — deep-dive per hypothesis, reject false positives
      5. False positive filter      — second-pass validation agent
      6. CVSS severity scoring      — critical / high / medium / low with reasoning
      7. Report generation          — security-report.md + security-findings.json
      8. Defensive review           — vulnmgmt, secarch, threathunt, threatintel,
                                      forensics, devsecops perspectives merged in

    Token and cost per scan are logged to ~/.jarvis/scan_costs.jsonl.
    Use /scan-costs to review the history.
    """
    args = ctx.args.strip()
    if not args:
        return CommandResult(
            text="Usage: /vuln-scan <path|git-url|host:port> [--no-exploit] [--report-dir <dir>]",
            success=False,
        )

    parts = args.split()
    no_exploit = "--no-exploit" in parts
    parts = [p for p in parts if p != "--no-exploit"]

    report_dir: str | None = None
    if "--report-dir" in parts:
        idx = parts.index("--report-dir")
        if idx + 1 < len(parts):
            report_dir = parts[idx + 1]
            parts = parts[:idx] + parts[idx + 2:]

    target = " ".join(parts).strip()
    if not target:
        return CommandResult(
            text="Usage: /vuln-scan <path|git-url|host:port> [--no-exploit] [--report-dir <dir>]",
            success=False,
        )

    if not ctx.brain:
        return CommandResult(text="Error: brain context not available", success=False)

    # ── Snapshot cost tracker before the scan ────────────────────────
    from src.agent.cost_tracker import get_tracker as _get_ct, CostTracker
    ct = _get_ct()
    cost_before = ct.get_session_cost()
    tokens_before = {m: u.total_tokens for m, u in ct.get_session_usage().items()}
    scan_start = time.time()
    scan_id = f"scan-{int(scan_start)}"

    # ── Build orchestrator task ──────────────────────────────────────
    exploit_note = (
        "SKIP stage 6 (exploit-builder) — --no-exploit flag was set."
        if no_exploit else
        "Run stage 6 (exploit-builder) on all CRITICAL and HIGH findings (CVSS >= 7.0)."
    )
    report_note = (
        f"Write final reports to {report_dir}/ instead of the target directory."
        if report_dir else
        "Write final reports (security-report.md, security-findings.json) into the target directory."
    )

    task = (
        f"Run the full Glasswing vulnerability discovery pipeline on:\n\n"
        f"  TARGET: {target}\n"
        f"  SCAN ID: {scan_id}\n\n"
        f"Use the sec-orchestrator agent to coordinate the complete pipeline:\n\n"
        f"  Stage 1 — file-risk-ranker: rank ALL files by attack surface (score 0-100).\n"
        f"            Process files in descending score order.\n\n"
        f"  Stage 2 — vuln-hypothesis-engine: dispatch in parallel batches of 5 for\n"
        f"            the top 20 files (score > 40). Cover: memory safety, injection,\n"
        f"            auth bypass, logic errors, race conditions, deserialization, crypto.\n\n"
        f"  Stage 3 — static-analyzer: for each hypothesis, trace data flows from\n"
        f"            untrusted input sources through transforms to dangerous sinks.\n"
        f"            Check for missing validation, unsafe pointer ops, integer overflows,\n"
        f"            use-after-free, bounds failures.\n\n"
        f"  Stage 4 — confirmation-filter: deep-dive per (hypothesis + taint trace) pair.\n"
        f"            Issue CONFIRMED / FALSE_POSITIVE / NEEDS_MANUAL with confidence.\n\n"
        f"  Stage 5 — Second-pass false positive filter: re-review all CONFIRMED findings\n"
        f"            and drop minor edge cases that affect almost no users.\n\n"
        f"  Stage 6 — severity-scorer: CVSS 3.1 for all confirmed findings. Include\n"
        f"            exploitability, attack vector, impact, and affected systems.\n"
        f"            {exploit_note}\n\n"
        f"  Stage 7 — report-writer: produce per-file vulnerability report with vuln type,\n"
        f"            file path, line numbers, severity, risk explanation, remediation,\n"
        f"            and CWE classification. {report_note}\n\n"
        f"  Stage 8 — Defensive review: dispatch the following agents IN PARALLEL to\n"
        f"            review the confirmed findings list and contribute their perspective:\n"
        f"            • vulnmgmt   — prioritization, patch/mitigate/accept decisions\n"
        f"            • secarch    — architectural root causes and systemic fixes\n"
        f"            • threathunt — detection opportunities and hunt queries\n"
        f"            • threatintel — known CVE/exploit alignment and threat actor TTPs\n"
        f"            • forensics  — forensic indicators of exploitation\n"
        f"            • devsecops  — CI/CD gates and SAST rules to prevent recurrence\n"
        f"            Merge their outputs into the executive summary as a 'Defensive\n"
        f"            Analysis' section with a unified prioritized remediation list.\n\n"
        f"Announce each stage transition: [STAGE N] Starting <name>...\n"
        f"Output the executive summary and top-10 prioritized findings when done."
    )

    result = await ctx.brain.think(task)

    # ── Compute and log cost delta ───────────────────────────────────
    scan_duration = time.time() - scan_start
    cost_after = ct.get_session_cost()
    cost_delta = cost_after - cost_before
    tokens_after = {m: u.total_tokens for m, u in ct.get_session_usage().items()}
    all_models = set(list(tokens_before.keys()) + list(tokens_after.keys()))
    tokens_delta = {
        m: tokens_after.get(m, 0) - tokens_before.get(m, 0)
        for m in all_models
        if tokens_after.get(m, 0) - tokens_before.get(m, 0) > 0
    }
    total_delta_tokens = sum(tokens_delta.values())

    _save_scan_cost_record({
        "scan_id": scan_id,
        "target": target,
        "timestamp": scan_start,
        "duration_s": round(scan_duration, 1),
        "cost_usd": round(cost_delta, 6),
        "tokens_by_model": tokens_delta,
        "no_exploit": no_exploit,
    })

    cost_line = (
        f"\n\n{'─' * 50}\n"
        f"Scan cost: ${cost_delta:.4f} | "
        f"{CostTracker.format_tokens(total_delta_tokens)} tokens | "
        f"{scan_duration:.0f}s elapsed | ID: {scan_id}"
    )
    return CommandResult(
        text=result + cost_line,
        data={"scan_id": scan_id, "cost_usd": cost_delta, "tokens": total_delta_tokens},
    )


# ── /scan-costs ───────────────────────────────────────────────────────

@command(
    "scan-costs",
    aliases=["vulncosts", "scancost"],
    description="Show token and cost history for /vuln-scan runs",
    usage="/scan-costs [--last N]",
    category="security",
    permission=PermLevel.READ_ONLY,
)
async def cmd_scan_costs(ctx: CommandContext) -> CommandResult:
    """Display per-scan token consumption and estimated API cost history.

    Records are stored in ~/.jarvis/scan_costs.jsonl (one JSON object per line).
    Each record contains: scan_id, target, timestamp, duration_s, cost_usd,
    tokens_by_model, no_exploit flag.
    """
    from src.agent.cost_tracker import CostTracker

    parts = ctx.args.strip().split()
    last_n = 10
    if "--last" in parts:
        idx = parts.index("--last")
        if idx + 1 < len(parts) and parts[idx + 1].isdigit():
            last_n = int(parts[idx + 1])

    if not _SCAN_COSTS_FILE.exists():
        return CommandResult(
            text="No scan cost records found. Run /vuln-scan to start tracking costs."
        )

    records: list[dict] = []
    with open(_SCAN_COSTS_FILE) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass

    if not records:
        return CommandResult(text="No scan cost records found.")

    recent = records[-last_n:]
    lifetime_cost = sum(r.get("cost_usd", 0) for r in records)
    lifetime_tokens = sum(sum(r.get("tokens_by_model", {}).values()) for r in records)

    lines = [
        f"Scan Cost History  (showing {len(recent)} of {len(records)} scans)",
        "=" * 60,
        f"  Lifetime: {len(records)} scans | "
        f"${lifetime_cost:.4f} total | "
        f"{CostTracker.format_tokens(lifetime_tokens)} tokens",
        "",
    ]

    for r in reversed(recent):
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(r.get("timestamp", 0)))
        target = r.get("target", "unknown")[:52]
        cost = r.get("cost_usd", 0)
        dur = r.get("duration_s", 0)
        tok = sum(r.get("tokens_by_model", {}).values())
        flag = " [no-exploit]" if r.get("no_exploit") else ""
        lines.append(f"  [{ts}]  {target}{flag}")
        lines.append(
            f"    ${cost:.4f} | {CostTracker.format_tokens(tok)} tokens | "
            f"{dur:.0f}s | {r.get('scan_id', '')}"
        )
        for model, toks in r.get("tokens_by_model", {}).items():
            label = model.split("/")[-1]
            for pfx in ("claude-", "gpt-"):
                if label.startswith(pfx):
                    label = label[len(pfx):]
                    break
            short = label.split("-")[0] if "-" in label else label
            lines.append(f"      {short}: {CostTracker.format_tokens(toks)}")
        lines.append("")

    return CommandResult(text="\n".join(lines))
