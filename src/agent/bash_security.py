"""Bash command security validation system.

Comprehensive security checks for bash commands before execution.
JARVIS BashTool security validation.

Detects: command injection, substitution attacks, dangerous commands,
environment manipulation, network exfiltration, unicode obfuscation,
control character injection, and more.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional


# ── Security Violation ───────────────────────────────────────────────

@dataclass
class SecurityViolation:
    """A single security issue found in a bash command."""
    violation_id: str       # e.g. "COMMAND_SUBSTITUTION", "FORK_BOMB"
    severity: str           # "critical", "high", "medium", "low"
    description: str        # Human-readable explanation
    matched_pattern: str    # The substring or pattern that triggered it


# ── Dangerous Commands & Blocked Patterns ────────────────────────────

DANGEROUS_COMMANDS: set[str] = {
    "rm", "mkfs", "dd", "shred", "wipefs", "fdisk", "parted",
    "format", "diskutil",
}

# Compiled regex patterns for critically dangerous operations
BLOCKED_PATTERNS: list[re.Pattern] = [
    # Fork bombs
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;?\s*:"),
    re.compile(r"\.\(\)\s*\{\s*\.\s*\|\s*\.\s*&\s*\}\s*;?\s*\."),
    # Recursive delete of root or system dirs
    re.compile(r"rm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?(-[a-zA-Z]*r[a-zA-Z]*\s+)?/\s*$"),
    re.compile(r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+)?(-[a-zA-Z]*f[a-zA-Z]*\s+)?/\s*$"),
    re.compile(r"rm\s+-rf\s+/(?:\s|$)"),
    re.compile(r"rm\s+-fr\s+/(?:\s|$)"),
    re.compile(r"rm\s+--no-preserve-root"),
    # Direct disk overwrite
    re.compile(r">\s*/dev/[sh]d[a-z]"),
    re.compile(r"dd\s+.*of\s*=\s*/dev/[sh]d[a-z]"),
    # Overwriting boot/kernel
    re.compile(r">\s*/boot/"),
    re.compile(r"dd\s+.*of\s*=\s*/boot/"),
    # Recursive chmod 777 on root
    re.compile(r"chmod\s+(-[a-zA-Z]*R[a-zA-Z]*\s+)?777\s+/\s*$"),
    re.compile(r"chmod\s+-R\s+777\s+/(?:\s|$)"),
    # Pipe remote script to shell
    re.compile(r"(?:curl|wget)\s+.*\|\s*(?:ba)?sh"),
    re.compile(r"(?:curl|wget)\s+.*\|\s*(?:bash|sh|zsh|dash|ksh)"),
]


# ── Unicode & Control Character Constants ────────────────────────────

# Non-ASCII whitespace characters that could hide content
UNICODE_WHITESPACE: set[str] = {
    "\u00a0",  # NO-BREAK SPACE
    "\u2000",  # EN QUAD
    "\u2001",  # EM QUAD
    "\u2002",  # EN SPACE
    "\u2003",  # EM SPACE
    "\u2004",  # THREE-PER-EM SPACE
    "\u2005",  # FOUR-PER-EM SPACE
    "\u2006",  # SIX-PER-EM SPACE
    "\u2007",  # FIGURE SPACE
    "\u2008",  # PUNCTUATION SPACE
    "\u2009",  # THIN SPACE
    "\u200a",  # HAIR SPACE
    "\u200b",  # ZERO WIDTH SPACE
    "\u200c",  # ZERO WIDTH NON-JOINER
    "\u200d",  # ZERO WIDTH JOINER
    "\u202f",  # NARROW NO-BREAK SPACE
    "\u205f",  # MEDIUM MATHEMATICAL SPACE
    "\u2060",  # WORD JOINER
    "\u3000",  # IDEOGRAPHIC SPACE
    "\ufeff",  # ZERO WIDTH NO-BREAK SPACE (BOM)
}

# Unicode pattern for regex matching
_UNICODE_WS_PATTERN = re.compile(
    r"[\u00a0\u2000-\u200d\u202f\u205f\u2060\u3000\ufeff]"
)

# Dangerous control characters
_CONTROL_CHAR_PATTERN = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"  # Null, BS, ESC, DEL, etc.
)


# ── BashSecurityChecker ─────────────────────────────────────────────

class BashSecurityChecker:
    """Validates bash commands for security violations.

    Usage:
        checker = BashSecurityChecker()
        violations = checker.check_command("rm -rf /")
        if violations:
            for v in violations:
                print(f"[{v.severity}] {v.violation_id}: {v.description}")
    """

    def check_command(self, command: str) -> list[SecurityViolation]:
        """Run all security checks on a command. Returns list of violations."""
        violations: list[SecurityViolation] = []
        for check in [
            self._check_blocked_patterns,
            self._check_command_substitution,
            self._check_process_substitution,
            self._check_dangerous_commands,
            self._check_dangerous_redirects,
            self._check_ifs_injection,
            self._check_unicode_whitespace,
            self._check_control_characters,
            self._check_network_exfiltration,
            self._check_history_manipulation,
            self._check_environment_injection,
            self._check_proc_access,
            self._check_obfuscation,
            self._check_newline_injection,
        ]:
            violations.extend(check(command))
        return violations

    # ── Private check methods ────────────────────────────────────────

    def _check_blocked_patterns(self, cmd: str) -> list[SecurityViolation]:
        """Check against compiled BLOCKED_PATTERNS (fork bombs, disk wipes, etc.)."""
        violations = []
        for pattern in BLOCKED_PATTERNS:
            m = pattern.search(cmd)
            if m:
                violations.append(SecurityViolation(
                    violation_id="BLOCKED_PATTERN",
                    severity="critical",
                    description=f"Command matches a critically dangerous pattern",
                    matched_pattern=m.group(),
                ))
        return violations

    def _check_command_substitution(self, cmd: str) -> list[SecurityViolation]:
        """Detect $(), backticks, ${} command/parameter substitution."""
        violations = []
        # Strip content inside single quotes (no expansion there)
        unquoted = _strip_single_quoted(cmd)

        # $() command substitution
        if re.search(r"\$\(", unquoted):
            violations.append(SecurityViolation(
                violation_id="COMMAND_SUBSTITUTION",
                severity="high",
                description="Command contains $() command substitution",
                matched_pattern="$()",
            ))

        # Backtick command substitution — only unescaped backticks
        if _has_unescaped_char(unquoted, "`"):
            violations.append(SecurityViolation(
                violation_id="COMMAND_SUBSTITUTION_BACKTICK",
                severity="high",
                description="Command contains backtick command substitution",
                matched_pattern="`...`",
            ))

        # ${} parameter expansion (can execute code via ${!ref}, ${var:-$(cmd)})
        if re.search(r"\$\{", unquoted):
            violations.append(SecurityViolation(
                violation_id="PARAMETER_SUBSTITUTION",
                severity="medium",
                description="Command contains ${} parameter expansion",
                matched_pattern="${}",
            ))

        # $[] legacy arithmetic expansion
        if re.search(r"\$\[", unquoted):
            violations.append(SecurityViolation(
                violation_id="ARITHMETIC_EXPANSION",
                severity="medium",
                description="Command contains $[] legacy arithmetic expansion",
                matched_pattern="$[]",
            ))

        # ANSI-C quoting $'...' — can encode arbitrary bytes
        # Check on raw cmd since _strip_single_quoted removes the content inside '...'
        if re.search(r"\$'[^']*'", cmd):
            violations.append(SecurityViolation(
                violation_id="ANSI_C_QUOTING",
                severity="medium",
                description="Command contains ANSI-C quoting ($'...') which can encode hidden characters",
                matched_pattern="$'...'",
            ))

        return violations

    def _check_process_substitution(self, cmd: str) -> list[SecurityViolation]:
        """Detect <() and >() process substitution."""
        violations = []
        unquoted = _strip_single_quoted(cmd)

        if re.search(r"<\(", unquoted):
            violations.append(SecurityViolation(
                violation_id="PROCESS_SUBSTITUTION",
                severity="high",
                description="Command contains <() process substitution",
                matched_pattern="<()",
            ))

        if re.search(r">\(", unquoted):
            violations.append(SecurityViolation(
                violation_id="PROCESS_SUBSTITUTION",
                severity="high",
                description="Command contains >() process substitution",
                matched_pattern=">()",
            ))

        # Zsh =() process substitution
        if re.search(r"=\(", unquoted):
            violations.append(SecurityViolation(
                violation_id="PROCESS_SUBSTITUTION_ZSH",
                severity="high",
                description="Command contains =() Zsh process substitution",
                matched_pattern="=()",
            ))

        return violations

    def _check_dangerous_commands(self, cmd: str) -> list[SecurityViolation]:
        """Detect dangerous base commands: rm -rf /, dd, mkfs, fork bombs, etc."""
        violations = []
        # Extract command segments (split on ;, &&, ||, |, newlines)
        segments = re.split(r"\s*(?:&&|\|\||[;&|\n])\s*", cmd)

        for segment in segments:
            segment = segment.strip()
            if not segment:
                continue
            # Strip env-var prefixes like VAR=val
            words = segment.split()
            base_cmd = None
            for w in words:
                if "=" in w and not w.startswith("-"):
                    continue  # env assignment prefix
                base_cmd = os.path.basename(w)
                break

            if not base_cmd:
                continue

            if base_cmd in DANGEROUS_COMMANDS:
                violations.append(SecurityViolation(
                    violation_id="DANGEROUS_COMMAND",
                    severity="high",
                    description=f"Command uses dangerous program: {base_cmd}",
                    matched_pattern=base_cmd,
                ))

            # mkfs variants
            if base_cmd.startswith("mkfs."):
                violations.append(SecurityViolation(
                    violation_id="DANGEROUS_COMMAND",
                    severity="critical",
                    description=f"Command uses filesystem formatter: {base_cmd}",
                    matched_pattern=base_cmd,
                ))

        # Fork bomb patterns (also checked in BLOCKED_PATTERNS, this is defense-in-depth)
        if re.search(r":\(\)\s*\{", cmd) or re.search(r"\.\(\)\s*\{", cmd):
            violations.append(SecurityViolation(
                violation_id="FORK_BOMB",
                severity="critical",
                description="Command contains a potential fork bomb pattern",
                matched_pattern=":(){ :|:& };:",
            ))

        return violations

    def _check_dangerous_redirects(self, cmd: str) -> list[SecurityViolation]:
        """Detect dangerous output redirections to system files or devices."""
        violations = []
        unquoted = _strip_single_quoted(cmd)

        # Output redirection to system directories
        # Match >, >>, 2>, 2>>, &>, and file descriptor redirects
        _redir = r"(?:>>?|[0-9]>>?|&>>?)"
        dangerous_redirect_targets = [
            (_redir + r"\s*/etc/", "Redirect to /etc/ (system config)"),
            (_redir + r"\s*/boot/", "Redirect to /boot/ (bootloader)"),
            (_redir + r"\s*/dev/[sh]d[a-z]", "Redirect to raw disk device"),
            (_redir + r"\s*/dev/nvme", "Redirect to NVMe device"),
            (_redir + r"\s*/dev/mapper/", "Redirect to device-mapper"),
            (_redir + r"\s*/proc/", "Redirect to /proc/"),
            (_redir + r"\s*/sys/", "Redirect to /sys/"),
            (_redir + r"\s*/usr/", "Redirect to /usr/"),
            (_redir + r"\s*/lib/", "Redirect to /lib/"),
            (_redir + r"\s*/bin/", "Redirect to /bin/"),
            (_redir + r"\s*/sbin/", "Redirect to /sbin/"),
            (_redir + r"\s*~/\.bashrc", "Redirect to ~/.bashrc (RCE on next login)"),
            (_redir + r"\s*~/\.bash_profile", "Redirect to ~/.bash_profile"),
            (_redir + r"\s*~/\.profile", "Redirect to ~/.profile"),
            (_redir + r"\s*~/\.zshrc", "Redirect to ~/.zshrc"),
            (_redir + r"\s*~/\.ssh/", "Redirect to ~/.ssh/"),
        ]

        for pattern, desc in dangerous_redirect_targets:
            m = re.search(pattern, unquoted)
            if m:
                violations.append(SecurityViolation(
                    violation_id="DANGEROUS_REDIRECT",
                    severity="critical" if "/dev/" in pattern else "high",
                    description=desc,
                    matched_pattern=m.group(),
                ))

        return violations

    def _check_ifs_injection(self, cmd: str) -> list[SecurityViolation]:
        """Detect IFS variable manipulation used to bypass security checks."""
        violations = []

        # $IFS or ${...IFS...} patterns
        if re.search(r"\$IFS|\$\{[^}]*IFS", cmd):
            violations.append(SecurityViolation(
                violation_id="IFS_INJECTION",
                severity="high",
                description="Command manipulates IFS variable, which can bypass security validation",
                matched_pattern="$IFS / ${IFS}",
            ))

        # Direct IFS assignment
        if re.search(r"(?:^|;|\s)IFS\s*=", cmd):
            violations.append(SecurityViolation(
                violation_id="IFS_INJECTION",
                severity="high",
                description="Command sets IFS variable, altering word splitting behavior",
                matched_pattern="IFS=",
            ))

        return violations

    def _check_unicode_whitespace(self, cmd: str) -> list[SecurityViolation]:
        """Detect non-ASCII whitespace characters that can hide content."""
        violations = []
        m = _UNICODE_WS_PATTERN.search(cmd)
        if m:
            char = m.group()
            codepoint = f"U+{ord(char):04X}"
            violations.append(SecurityViolation(
                violation_id="UNICODE_WHITESPACE",
                severity="high",
                description=f"Command contains non-ASCII whitespace ({codepoint}) that can hide malicious content",
                matched_pattern=repr(char),
            ))
        return violations

    def _check_control_characters(self, cmd: str) -> list[SecurityViolation]:
        """Detect null bytes, backspace, escape sequences that hide content."""
        violations = []

        m = _CONTROL_CHAR_PATTERN.search(cmd)
        if m:
            char = m.group()
            byte_val = ord(char)
            names = {
                0: "NULL byte", 7: "BEL", 8: "BACKSPACE",
                0x0b: "VERTICAL TAB", 0x0c: "FORM FEED",
                0x0e: "SHIFT OUT", 0x0f: "SHIFT IN",
                0x1b: "ESCAPE", 0x7f: "DELETE",
            }
            name = names.get(byte_val, f"control char 0x{byte_val:02x}")
            violations.append(SecurityViolation(
                violation_id="CONTROL_CHARACTER",
                severity="critical" if byte_val == 0 else "high",
                description=f"Command contains {name} (0x{byte_val:02X}) which can hide malicious content",
                matched_pattern=repr(char),
            ))

        # ANSI escape sequences (even in printable form)
        if re.search(r"\x1b\[[\d;]*[a-zA-Z]", cmd):
            violations.append(SecurityViolation(
                violation_id="ANSI_ESCAPE_SEQUENCE",
                severity="high",
                description="Command contains ANSI escape sequence that can manipulate terminal display",
                matched_pattern="ESC[...",
            ))

        return violations

    def _check_network_exfiltration(self, cmd: str) -> list[SecurityViolation]:
        """Detect data exfiltration via curl, nc, wget, etc."""
        violations = []

        # curl posting file data
        exfil_patterns = [
            (r"curl\s+.*-[a-zA-Z]*d\s+@", "curl -d @file (POST file contents)"),
            (r"curl\s+.*--data[a-z-]*\s+@", "curl --data @file (POST file contents)"),
            (r"curl\s+.*-[a-zA-Z]*F\s+", "curl -F (multipart file upload)"),
            (r"curl\s+.*--upload-file", "curl --upload-file"),
            (r"curl\s+.*-[a-zA-Z]*T\s+", "curl -T (upload file)"),
        ]

        for pattern, desc in exfil_patterns:
            m = re.search(pattern, cmd)
            if m:
                violations.append(SecurityViolation(
                    violation_id="NETWORK_EXFILTRATION",
                    severity="critical",
                    description=desc,
                    matched_pattern=m.group().strip(),
                ))

        # wget posting file data
        if re.search(r"wget\s+.*--post-file", cmd):
            violations.append(SecurityViolation(
                violation_id="NETWORK_EXFILTRATION",
                severity="critical",
                description="wget --post-file (upload file contents)",
                matched_pattern="wget --post-file",
            ))

        # nc/ncat reverse shell patterns
        nc_patterns = [
            (r"(?:nc|ncat|netcat)\s+.*-[a-zA-Z]*e\s+", "nc -e (execute on connect — reverse shell)"),
            (r"(?:nc|ncat|netcat)\s+.*-[a-zA-Z]*c\s+", "nc -c (execute on connect — reverse shell)"),
            # Piping to nc (data exfiltration)
            (r"\|\s*(?:nc|ncat|netcat)\s+", "Piping data to nc (exfiltration)"),
        ]

        for pattern, desc in nc_patterns:
            m = re.search(pattern, cmd)
            if m:
                violations.append(SecurityViolation(
                    violation_id="NETWORK_EXFILTRATION",
                    severity="critical",
                    description=desc,
                    matched_pattern=m.group().strip(),
                ))

        # Piping sensitive files to network tools
        if re.search(r"(?:cat|head|tail)\s+/etc/(?:passwd|shadow|sudoers).*\|", cmd):
            violations.append(SecurityViolation(
                violation_id="NETWORK_EXFILTRATION",
                severity="critical",
                description="Piping sensitive system file to another command",
                matched_pattern="cat /etc/... | ...",
            ))

        # DNS exfiltration via dig/nslookup
        if re.search(r"(?:dig|nslookup|host)\s+.*\$\(", cmd):
            violations.append(SecurityViolation(
                violation_id="NETWORK_EXFILTRATION",
                severity="high",
                description="DNS query with command substitution (potential DNS exfiltration)",
                matched_pattern="dig/nslookup $(..)",
            ))

        return violations

    def _check_history_manipulation(self, cmd: str) -> list[SecurityViolation]:
        """Detect shell history manipulation to hide activity."""
        violations = []

        patterns = [
            (r"history\s+-[cdw]", "history clear/delete/write"),
            (r"(?:export\s+)?HISTSIZE\s*=\s*0", "HISTSIZE=0 (disable history)"),
            (r"(?:export\s+)?HISTFILESIZE\s*=\s*0", "HISTFILESIZE=0 (truncate history file)"),
            (r"unset\s+HISTFILE", "unset HISTFILE (disable history logging)"),
            (r"unset\s+HISTSIZE", "unset HISTSIZE"),
            (r"(?:export\s+)?HISTFILE\s*=\s*/dev/null", "HISTFILE=/dev/null"),
            (r"(?:export\s+)?HISTCONTROL\s*=\s*ignoreboth", "HISTCONTROL=ignoreboth"),
            (r">\s*~/\.bash_history", "Overwriting bash history file"),
            (r">\s*~/\.zsh_history", "Overwriting zsh history file"),
            (r"truncate\s+.*\.?history", "Truncating history file"),
        ]

        for pattern, desc in patterns:
            m = re.search(pattern, cmd)
            if m:
                violations.append(SecurityViolation(
                    violation_id="HISTORY_MANIPULATION",
                    severity="medium",
                    description=f"Command manipulates shell history: {desc}",
                    matched_pattern=m.group(),
                ))

        return violations

    def _check_environment_injection(self, cmd: str) -> list[SecurityViolation]:
        """Detect dangerous environment variable manipulation."""
        violations = []

        dangerous_env_patterns = [
            (r"(?:export\s+)?LD_PRELOAD\s*=", "LD_PRELOAD (inject shared library)"),
            (r"(?:export\s+)?LD_LIBRARY_PATH\s*=", "LD_LIBRARY_PATH (hijack library resolution)"),
            (r"(?:export\s+)?LD_AUDIT\s*=", "LD_AUDIT (library auditing hook)"),
            (r"(?:export\s+)?LD_DEBUG\s*=", "LD_DEBUG (leak linker info)"),
            (r"(?:^|;\s*|&&\s*|\|\|\s*)PATH\s*=", "PATH override (command hijacking)"),
            (r"(?:export\s+)?PROMPT_COMMAND\s*=", "PROMPT_COMMAND (execute on each prompt)"),
            (r"(?:export\s+)?BASH_ENV\s*=", "BASH_ENV (auto-source on non-interactive bash)"),
            (r"(?:export\s+)?ENV\s*=", "ENV (auto-source on interactive sh)"),
            (r"(?:export\s+)?PYTHONSTARTUP\s*=", "PYTHONSTARTUP (inject Python code)"),
            (r"(?:export\s+)?PERL5OPT\s*=", "PERL5OPT (inject Perl flags)"),
            (r"(?:export\s+)?RUBYOPT\s*=", "RUBYOPT (inject Ruby flags)"),
            (r"(?:export\s+)?NODE_OPTIONS\s*=", "NODE_OPTIONS (inject Node.js flags)"),
            (r"(?:export\s+)?JAVA_TOOL_OPTIONS\s*=", "JAVA_TOOL_OPTIONS (inject JVM flags)"),
        ]

        for pattern, desc in dangerous_env_patterns:
            m = re.search(pattern, cmd)
            if m:
                violations.append(SecurityViolation(
                    violation_id="ENVIRONMENT_INJECTION",
                    severity="high",
                    description=f"Command sets dangerous environment variable: {desc}",
                    matched_pattern=m.group(),
                ))

        return violations

    def _check_proc_access(self, cmd: str) -> list[SecurityViolation]:
        """Detect access to /proc/self/environ, /proc/self/fd/, etc."""
        violations = []

        # /proc/*/environ — exposes all environment variables (API keys, secrets)
        if re.search(r"/proc/[^/\s]+/environ", cmd):
            violations.append(SecurityViolation(
                violation_id="PROC_ENVIRON_ACCESS",
                severity="critical",
                description="Command accesses /proc/*/environ which exposes environment variables (API keys, secrets)",
                matched_pattern="/proc/*/environ",
            ))

        # /proc/self/fd/ — access open file descriptors
        if re.search(r"/proc/[^/\s]+/fd/", cmd):
            violations.append(SecurityViolation(
                violation_id="PROC_FD_ACCESS",
                severity="high",
                description="Command accesses /proc/*/fd/ which can read open file descriptors",
                matched_pattern="/proc/*/fd/",
            ))

        # /proc/self/mem — direct memory access
        if re.search(r"/proc/[^/\s]+/mem(?:\s|$)", cmd):
            violations.append(SecurityViolation(
                violation_id="PROC_MEM_ACCESS",
                severity="critical",
                description="Command accesses /proc/*/mem which allows direct process memory read/write",
                matched_pattern="/proc/*/mem",
            ))

        # /proc/self/exe — symlink to current executable
        if re.search(r"/proc/[^/\s]+/exe(?:\s|$)", cmd):
            violations.append(SecurityViolation(
                violation_id="PROC_EXE_ACCESS",
                severity="medium",
                description="Command accesses /proc/*/exe",
                matched_pattern="/proc/*/exe",
            ))

        # /proc/self/cmdline, /proc/self/maps — info leak
        if re.search(r"/proc/[^/\s]+/(?:cmdline|maps|status|stat\b)", cmd):
            violations.append(SecurityViolation(
                violation_id="PROC_INFO_ACCESS",
                severity="low",
                description="Command reads process information from /proc",
                matched_pattern="/proc/*/info",
            ))

        return violations

    def _check_obfuscation(self, cmd: str) -> list[SecurityViolation]:
        """Detect obfuscation techniques: quote concatenation, hex encoding, etc."""
        violations = []

        # Backslash-escaped operators that could be misinterpreted
        if re.search(r"\\[;&|]", cmd):
            violations.append(SecurityViolation(
                violation_id="ESCAPED_OPERATOR",
                severity="medium",
                description="Command contains backslash-escaped shell operators",
                matched_pattern="\\; or \\| or \\&",
            ))

        # Locale quoting $"..."
        if re.search(r'\$"[^"]*"', cmd):
            violations.append(SecurityViolation(
                violation_id="LOCALE_QUOTING",
                severity="medium",
                description="Command contains locale quoting ($\"...\") which can hide characters",
                matched_pattern='$"..."',
            ))

        # Hex/octal encoding in printf/echo that could produce dangerous commands
        if re.search(r"(?:printf|echo\s+-e)\s+.*\\x[0-9a-fA-F]{2}", cmd):
            violations.append(SecurityViolation(
                violation_id="HEX_ENCODING",
                severity="medium",
                description="Command uses hex-encoded characters that could hide malicious content",
                matched_pattern="\\xNN",
            ))

        # Base64 decode piped to shell
        if re.search(r"base64\s+(?:-d|--decode).*\|\s*(?:ba)?sh", cmd):
            violations.append(SecurityViolation(
                violation_id="ENCODED_EXECUTION",
                severity="critical",
                description="Command decodes base64 and pipes to shell (hidden command execution)",
                matched_pattern="base64 -d | sh",
            ))

        # eval with variable expansion
        if re.search(r"(?:^|;\s*|&&\s*|\|\|\s*)\s*eval\s+", cmd):
            violations.append(SecurityViolation(
                violation_id="EVAL_EXECUTION",
                severity="high",
                description="Command uses eval which executes arbitrary string as a command",
                matched_pattern="eval ...",
            ))

        # source / dot-source
        if re.search(r"(?:^|;\s*|&&\s*|\|\|\s*)\s*(?:source|\.)\s+", cmd):
            # Allow common safe patterns like ". ~/.bashrc"
            if not re.search(r"(?:source|\.)\s+~/\.(?:bashrc|bash_profile|profile|zshrc)\s*$", cmd):
                violations.append(SecurityViolation(
                    violation_id="SOURCE_EXECUTION",
                    severity="medium",
                    description="Command sources a file (executes it in current shell context)",
                    matched_pattern="source / . file",
                ))

        return violations

    def _check_newline_injection(self, cmd: str) -> list[SecurityViolation]:
        """Detect newlines and carriage returns that could separate commands."""
        violations = []

        # Carriage return can cause parser differentials between
        # shell-quote tokenizers and actual bash
        if "\r" in cmd:
            violations.append(SecurityViolation(
                violation_id="CARRIAGE_RETURN",
                severity="high",
                description="Command contains carriage return (\\r) which can cause parser differentials",
                matched_pattern="\\r",
            ))

        return violations


# ── ReadOnlyValidator ────────────────────────────────────────────────

class ReadOnlyValidator:
    """Validates whether a command is safe for read-only execution mode."""

    SAFE_COMMANDS: set[str] = {
        # File inspection
        "ls", "cat", "head", "tail", "less", "more",
        "file", "stat", "wc", "du", "df",
        # Search
        "grep", "egrep", "fgrep", "rg", "ag",
        "find", "fd", "fdfind", "locate", "which", "whereis", "type",
        # Text processing (read-only)
        "sort", "uniq", "tr", "cut", "paste", "join", "comm",
        "diff", "cmp", "colordiff",
        "awk", "sed",  # NOTE: sed without -i is read-only
        # Hashing / inspection
        "md5sum", "sha256sum", "sha1sum", "sha512sum",
        "strings", "xxd", "od", "hexdump",
        # Path utilities
        "pwd", "readlink", "realpath", "basename", "dirname",
        # System info
        "echo", "printf", "date", "whoami", "uname", "id",
        "hostname", "uptime",
        "env", "printenv",
        # Misc safe
        "true", "false", "test", "[",
        "tee",  # when piped, but we check separately
        "jq", "yq", "xq",
        "tree", "column",
        "man", "help", "info",
        "tput", "nproc", "getconf",
        "lsb_release", "arch",
    }

    SAFE_GIT_SUBCOMMANDS: set[str] = {
        "log", "diff", "status", "show", "branch", "tag",
        "remote", "config", "rev-parse", "ls-files", "ls-tree",
        "blame", "shortlog", "describe", "name-rev", "rev-list",
        "for-each-ref", "stash list", "reflog", "count-objects",
        "ls-remote", "cat-file", "verify-pack", "fsck",
    }

    SAFE_PYTHON_FLAGS: set[str] = {
        "--version", "-V", "--help", "-h",
    }

    SAFE_PYTHON_MODULES: set[str] = {
        "json.tool", "py_compile", "compileall",
        "site", "sysconfig", "platform",
    }

    def is_read_only(self, command: str) -> bool:
        """Returns True if the command is safe for read-only mode."""
        allowed, _ = self.validate(command)
        return allowed

    def validate(self, command: str) -> tuple[bool, str]:
        """Validate command for read-only safety.

        Returns (is_safe, reason).
        """
        cmd = command.strip()
        if not cmd:
            return True, "Empty command"

        # Split on pipes — every segment must be safe
        segments = _split_pipe_segments(cmd)

        for segment in segments:
            segment = segment.strip()
            if not segment:
                continue

            # Strip env-var prefixes (VAR=val cmd)
            words = segment.split()
            cmd_word = None
            cmd_idx = 0
            for i, w in enumerate(words):
                if "=" in w and not w.startswith("-") and not w.startswith("/"):
                    continue
                cmd_word = os.path.basename(w)
                cmd_idx = i
                break

            if not cmd_word:
                continue

            # Check against safe commands
            if cmd_word in self.SAFE_COMMANDS:
                # Extra check: sed -i is a write operation
                if cmd_word == "sed" and any(
                    w == "-i" or w.startswith("-i") or w == "--in-place"
                    for w in words[cmd_idx + 1:]
                ):
                    return False, "sed -i modifies files in place"
                # tee writes to files (only safe as pipe consumer to /dev/null)
                if cmd_word == "tee":
                    remaining = " ".join(words[cmd_idx + 1:])
                    if remaining.strip() and remaining.strip() != "/dev/null":
                        return False, "tee writes to files"
                continue

            # Git with safe subcommands
            if cmd_word == "git":
                rest = words[cmd_idx + 1:] if cmd_idx + 1 < len(words) else []
                # Find the subcommand (skip flags like -C, --no-pager)
                subcmd = None
                skip_next = False
                for w in rest:
                    if skip_next:
                        skip_next = False
                        continue
                    if w in ("-C", "--git-dir", "--work-tree"):
                        skip_next = True
                        continue
                    if w.startswith("-"):
                        continue
                    subcmd = w
                    break
                if subcmd and subcmd in self.SAFE_GIT_SUBCOMMANDS:
                    continue
                return False, f"git {subcmd or '?'} is not a known read-only subcommand"

            # Python with safe flags
            if cmd_word in ("python", "python3", "python3.10", "python3.11", "python3.12"):
                rest = words[cmd_idx + 1:] if cmd_idx + 1 < len(words) else []
                if not rest:
                    return False, "Interactive python is not read-only"
                first_arg = rest[0]
                if first_arg in self.SAFE_PYTHON_FLAGS:
                    continue
                if first_arg == "-c":
                    # Validate -c code for dangerous operations
                    code_arg = " ".join(rest[1:]) if len(rest) > 1 else ""
                    _dangerous_py = ("__import__", "subprocess", "os.system", "os.exec",
                                     "os.popen", "os.remove", "os.unlink", "os.rmdir",
                                     "shutil.rmtree", "open(", "exec(", "eval(")
                    if any(d in code_arg for d in _dangerous_py):
                        return False, f"python -c contains potentially dangerous operation"
                    continue
                if first_arg == "-m" and len(rest) > 1 and rest[1] in self.SAFE_PYTHON_MODULES:
                    continue
                return False, f"python {first_arg} may not be read-only"

            # docker/podman with safe subcommands
            if cmd_word in ("docker", "podman"):
                rest = words[cmd_idx + 1:] if cmd_idx + 1 < len(words) else []
                subcmd = rest[0] if rest else None
                safe_docker = {"ps", "images", "inspect", "logs", "stats", "top", "version", "info"}
                if subcmd in safe_docker:
                    continue
                return False, f"{cmd_word} {subcmd or '?'} is not read-only"

            # npm/yarn/pnpm with safe subcommands
            if cmd_word in ("npm", "yarn", "pnpm"):
                rest = words[cmd_idx + 1:] if cmd_idx + 1 < len(words) else []
                subcmd = rest[0] if rest else None
                safe_npm = {"list", "ls", "info", "view", "show", "outdated", "audit", "why", "explain"}
                if subcmd in safe_npm:
                    continue
                return False, f"{cmd_word} {subcmd or '?'} is not read-only"

            # cargo with safe subcommands
            if cmd_word == "cargo":
                rest = words[cmd_idx + 1:] if cmd_idx + 1 < len(words) else []
                subcmd = rest[0] if rest else None
                safe_cargo = {"metadata", "tree", "search", "info", "verify-project", "read-manifest"}
                if subcmd in safe_cargo:
                    continue
                return False, f"cargo {subcmd or '?'} is not read-only"

            # Unknown command
            return False, f"Command '{cmd_word}' is not in the read-only safe list"

        return True, "All segments are read-only safe"


# ── Command Semantics Classification ────────────────────────────────

_WRITE_COMMANDS: set[str] = {
    "tee", "patch", "install", "cp", "mv", "mkdir", "touch",
    "ln", "chown", "chgrp", "chmod", "sed",  # sed can be write with -i
    "git commit", "git push", "git merge", "git rebase",
    "git checkout", "git reset", "git stash",
    "pip install", "npm install", "cargo install",
    "apt install", "apt-get install", "dnf install", "yum install",
    "make", "cmake", "cargo build", "cargo run",
    "docker run", "docker build", "docker compose",
}

_DESTRUCTIVE_COMMANDS: set[str] = {
    "rm", "rmdir", "shred", "wipefs", "mkfs",
    "dd", "fdisk", "parted", "format",
    "kill", "killall", "pkill",
    "reboot", "shutdown", "poweroff", "halt", "init",
    "systemctl stop", "systemctl disable",
}


def classify_command_semantics(command: str) -> str:
    """Classify a command as 'read_only', 'write', 'destructive', or 'unknown'.

    Parses the first word (command name) and checks against known lists.
    """
    cmd = command.strip()
    if not cmd:
        return "read_only"

    # Extract base command (first meaningful word)
    words = cmd.split()
    base = None
    for w in words:
        if "=" in w and not w.startswith("-") and not w.startswith("/"):
            continue
        base = os.path.basename(w)
        break

    if not base:
        return "unknown"

    # Check two-word commands first (e.g. "git commit")
    if len(words) >= 2:
        two_word = f"{base} {words[1]}"
        if two_word in _DESTRUCTIVE_COMMANDS:
            return "destructive"
        if two_word in _WRITE_COMMANDS:
            return "write"

    # Single word
    if base in _DESTRUCTIVE_COMMANDS or base in DANGEROUS_COMMANDS:
        return "destructive"
    if base in _WRITE_COMMANDS:
        return "write"

    # Check read-only validator
    validator = ReadOnlyValidator()
    if validator.is_read_only(cmd):
        return "read_only"

    return "unknown"


# ── Integration Function ────────────────────────────────────────────

# Singleton checker instance
_checker = BashSecurityChecker()
_readonly_validator = ReadOnlyValidator()


def validate_bash_command(
    command: str,
    readonly: bool = False,
) -> tuple[bool, str, list[SecurityViolation]]:
    """Main entry point: validate a bash command for security.

    Args:
        command: The bash command to validate.
        readonly: If True, also enforce read-only constraints.

    Returns:
        (allowed, reason, violations) where:
        - allowed: True if the command should be permitted
        - reason: Human-readable explanation
        - violations: List of SecurityViolation objects found
    """
    if not command or not command.strip():
        return True, "Empty command", []

    # Run all security checks
    violations = _checker.check_command(command)

    # Check for critical violations — always block
    critical = [v for v in violations if v.severity == "critical"]
    if critical:
        descriptions = "; ".join(v.description for v in critical)
        return False, f"BLOCKED (critical): {descriptions}", violations

    # Check for high-severity violations — block by default
    high = [v for v in violations if v.severity == "high"]
    if high:
        descriptions = "; ".join(v.description for v in high)
        return False, f"BLOCKED (high risk): {descriptions}", violations

    # Read-only mode enforcement
    if readonly:
        is_safe, reason = _readonly_validator.validate(command)
        if not is_safe:
            return False, f"Read-only violation: {reason}", violations

    # Medium/low violations: allow but report
    if violations:
        descriptions = "; ".join(v.description for v in violations)
        return True, f"CAUTION: {descriptions}", violations

    return True, "OK", []


# ── Utility Functions ────────────────────────────────────────────────

def _strip_single_quoted(cmd: str) -> str:
    """Remove content inside single quotes (no expansion possible there).

    Returns the command with single-quoted sections replaced by empty strings,
    preserving structure for pattern matching on unquoted content.
    """
    result = []
    in_single = False
    i = 0
    while i < len(cmd):
        c = cmd[i]
        if c == "'" and not in_single:
            in_single = True
            i += 1
            continue
        if c == "'" and in_single:
            in_single = False
            i += 1
            continue
        if not in_single:
            result.append(c)
        i += 1
    return "".join(result)


def _has_unescaped_char(content: str, char: str) -> bool:
    """Check if content contains an unescaped occurrence of a character.

    Handles backslash escaping: \\` is an escaped backslash + unescaped backtick.
    """
    i = 0
    while i < len(content):
        if content[i] == "\\" and i + 1 < len(content):
            i += 2  # skip escaped char
            continue
        if content[i] == char:
            return True
        i += 1
    return False


def _split_pipe_segments(cmd: str) -> list[str]:
    """Split a command on unquoted pipe characters.

    Returns list of segments. Does NOT split on || (logical OR).
    """
    segments = []
    current: list[str] = []
    in_single = False
    in_double = False
    escaped = False
    i = 0

    while i < len(cmd):
        c = cmd[i]

        if escaped:
            current.append(c)
            escaped = False
            i += 1
            continue

        if c == "\\" and not in_single:
            escaped = True
            current.append(c)
            i += 1
            continue

        if c == "'" and not in_double:
            in_single = not in_single
            current.append(c)
            i += 1
            continue

        if c == '"' and not in_single:
            in_double = not in_double
            current.append(c)
            i += 1
            continue

        if c == "|" and not in_single and not in_double:
            # Check it's not ||
            if i + 1 < len(cmd) and cmd[i + 1] == "|":
                current.append("||")
                i += 2
                continue
            segments.append("".join(current))
            current = []
            i += 1
            continue

        # Also split on ; and && for compound command analysis
        if c == ";" and not in_single and not in_double:
            segments.append("".join(current))
            current = []
            i += 1
            continue

        if c == "&" and not in_single and not in_double:
            if i + 1 < len(cmd) and cmd[i + 1] == "&":
                segments.append("".join(current))
                current = []
                i += 2
                continue
            # Single & (background) — include it in current segment
            current.append(c)
            i += 1
            continue

        current.append(c)
        i += 1

    if current:
        segments.append("".join(current))

    return segments
