"""
Sed command validation -- allowlist and denylist checks.
"""
from __future__ import annotations

import re
from typing import Optional

from src.tools.BashTool.modeValidation import PermissionResult


def _split_command_deprecated(command: str) -> list[str]:
    return [seg.strip() for seg in command.split("|")]


def _simple_tokenize(s: str) -> list[str]:
    """Simple shell-like tokenization."""
    tokens: list[str] = []
    current = ""
    i = 0
    while i < len(s):
        c = s[i]
        if c in (" ", "\t"):
            if current:
                tokens.append(current)
                current = ""
            i += 1
        elif c == "'":
            try:
                j = s.index("'", i + 1)
            except ValueError:
                raise ValueError("Unterminated single quote")
            current += s[i + 1:j]
            i = j + 1
        elif c == '"':
            try:
                j = s.index('"', i + 1)
            except ValueError:
                raise ValueError("Unterminated double quote")
            current += s[i + 1:j]
            i = j + 1
        elif c == "\\":
            if i + 1 < len(s):
                current += s[i + 1]
                i += 2
            else:
                current += c
                i += 1
        else:
            current += c
            i += 1
    if current:
        tokens.append(current)
    return tokens


def _validate_flags_against_allowlist(
    flags: list[str],
    allowed_flags: list[str],
) -> bool:
    """Validate flags against an allowlist."""
    for flag in flags:
        if flag.startswith("-") and not flag.startswith("--") and len(flag) > 2:
            for ch in flag[1:]:
                if f"-{ch}" not in allowed_flags:
                    return False
        else:
            if flag not in allowed_flags:
                return False
    return True


def is_print_command(cmd: str) -> bool:
    """Check if a single command is a valid print command (strict allowlist)."""
    if not cmd:
        return False
    return bool(re.match(r"^(?:\d+|\d+,\d+)?p$", cmd))


def is_line_printing_command(command: str, expressions: list[str]) -> bool:
    """Check if this is a line printing command with -n flag."""
    sed_match = re.match(r"^\s*sed\s+", command)
    if not sed_match:
        return False

    without_sed = command[sed_match.end():]
    try:
        parsed = _simple_tokenize(without_sed)
    except ValueError:
        return False

    flags = [arg for arg in parsed if isinstance(arg, str) and arg.startswith("-") and arg != "--"]

    allowed_flags = [
        "-n", "--quiet", "--silent", "-E", "--regexp-extended",
        "-r", "-z", "--zero-terminated", "--posix",
    ]

    if not _validate_flags_against_allowlist(flags, allowed_flags):
        return False

    has_n_flag = False
    for flag in flags:
        if flag in ("-n", "--quiet", "--silent"):
            has_n_flag = True
            break
        if flag.startswith("-") and not flag.startswith("--") and "n" in flag:
            has_n_flag = True
            break

    if not has_n_flag:
        return False

    if not expressions:
        return False

    for expr in expressions:
        for cmd in expr.split(";"):
            if not is_print_command(cmd.strip()):
                return False

    return True


def _is_substitution_command(
    command: str,
    expressions: list[str],
    has_file_arguments: bool,
    allow_file_writes: bool = False,
) -> bool:
    """Check if this is a substitution command."""
    if not allow_file_writes and has_file_arguments:
        return False

    sed_match = re.match(r"^\s*sed\s+", command)
    if not sed_match:
        return False

    without_sed = command[sed_match.end():]
    try:
        parsed = _simple_tokenize(without_sed)
    except ValueError:
        return False

    flags = [arg for arg in parsed if isinstance(arg, str) and arg.startswith("-") and arg != "--"]

    allowed_flags = ["-E", "--regexp-extended", "-r", "--posix"]
    if allow_file_writes:
        allowed_flags.extend(["-i", "--in-place"])

    if not _validate_flags_against_allowlist(flags, allowed_flags):
        return False

    if len(expressions) != 1:
        return False

    expr = expressions[0].strip()
    if not expr.startswith("s"):
        return False

    subst_match = re.match(r"^s/(.*?)$", expr)
    if not subst_match:
        return False

    rest = subst_match.group(1)

    delimiter_count = 0
    last_delimiter_pos = -1
    i = 0
    while i < len(rest):
        if rest[i] == "\\":
            i += 2
            continue
        if rest[i] == "/":
            delimiter_count += 1
            last_delimiter_pos = i
        i += 1

    if delimiter_count != 2:
        return False

    expr_flags = rest[last_delimiter_pos + 1:]
    if not re.match(r"^[gpimIM]*[1-9]?[gpimIM]*$", expr_flags):
        return False

    return True


def _contains_dangerous_operations(expression: str) -> bool:
    """Check if a sed expression contains dangerous operations (denylist)."""
    cmd = expression.strip()
    if not cmd:
        return False

    # Reject non-ASCII characters
    try:
        cmd.encode("ascii")
    except UnicodeEncodeError:
        return True

    if "{" in cmd or "}" in cmd:
        return True
    if "\n" in cmd:
        return True

    # Reject comments
    hash_idx = cmd.find("#")
    if hash_idx != -1 and not (hash_idx > 0 and cmd[hash_idx - 1] == "s"):
        return True

    # Reject negation
    if re.match(r"^!", cmd) or re.search(r"[/\d$]!", cmd):
        return True

    # Reject tilde in GNU step address format
    if re.search(r"\d\s*~\s*\d|,\s*~\s*\d|\$\s*~\s*\d", cmd):
        return True

    if re.match(r"^,", cmd):
        return True
    if re.search(r",\s*[+-]", cmd):
        return True

    # Reject backslash tricks
    if re.search(r"s\\", cmd) or re.search(r"\\[|#%@]", cmd):
        return True
    if re.search(r"\\\\/.*[wW]", cmd):
        return True
    if re.search(r"/[^/]*\s+[wWeE]", cmd):
        return True

    # Reject malformed substitution
    if re.match(r"^s/", cmd) and not re.match(r"^s/[^/]*/[^/]*/[^/]*$", cmd):
        return True

    # Reject s commands ending with dangerous chars
    if re.match(r"^s.", cmd) and re.search(r"[wWeE]$", cmd):
        proper_subst = re.match(r"^s([^\\\n]).*?\1.*?\1[^wWeE]*$", cmd)
        if not proper_subst:
            return True

    # Write commands
    write_patterns = [
        r"^[wW]\s*\S+",
        r"^\d+\s*[wW]\s*\S+",
        r"^\$\s*[wW]\s*\S+",
        r"^/[^/]*/[IMim]*\s*[wW]\s*\S+",
        r"^\d+,\d+\s*[wW]\s*\S+",
        r"^\d+,\$\s*[wW]\s*\S+",
        r"^/[^/]*/[IMim]*,/[^/]*/[IMim]*\s*[wW]\s*\S+",
    ]
    for pat in write_patterns:
        if re.search(pat, cmd):
            return True

    # Execute commands
    exec_patterns = [
        r"^e",
        r"^\d+\s*e",
        r"^\$\s*e",
        r"^/[^/]*/[IMim]*\s*e",
        r"^\d+,\d+\s*e",
        r"^\d+,\$\s*e",
        r"^/[^/]*/[IMim]*,/[^/]*/[IMim]*\s*e",
    ]
    for pat in exec_patterns:
        if re.search(pat, cmd):
            return True

    # Substitution with dangerous flags
    subst_match = re.search(r"s([^\\\n]).*?\1.*?\1(.*?)$", cmd)
    if subst_match:
        flags = subst_match.group(2) or ""
        if "w" in flags or "W" in flags or "e" in flags or "E" in flags:
            return True

    # y command with w/W/e/E
    y_match = re.search(r"y([^\\\n])", cmd)
    if y_match and re.search(r"[wWeE]", cmd):
        return True

    return False


def has_file_args(command: str) -> bool:
    """Check if a sed command has file arguments (not just stdin)."""
    sed_match = re.match(r"^\s*sed\s+", command)
    if not sed_match:
        return False

    without_sed = command[sed_match.end():]
    try:
        parsed = _simple_tokenize(without_sed)
    except ValueError:
        return True

    arg_count = 0
    has_e_flag = False

    i = 0
    while i < len(parsed):
        arg = parsed[i]

        if (arg in ("-e", "--expression")) and i + 1 < len(parsed):
            has_e_flag = True
            i += 2
            continue
        if arg.startswith("--expression=") or arg.startswith("-e="):
            has_e_flag = True
            i += 1
            continue
        if arg.startswith("-"):
            i += 1
            continue

        arg_count += 1

        if has_e_flag:
            return True
        if arg_count > 1:
            return True

        i += 1

    return False


def extract_sed_expressions(command: str) -> list[str]:
    """Extract sed expressions from command, ignoring flags and filenames."""
    expressions: list[str] = []

    sed_match = re.match(r"^\s*sed\s+", command)
    if not sed_match:
        return expressions

    without_sed = command[sed_match.end():]

    # Reject dangerous flag combinations
    if re.search(r"-e[wWe]", without_sed) or re.search(r"-w[eE]", without_sed):
        raise ValueError("Dangerous flag combination detected")

    try:
        parsed = _simple_tokenize(without_sed)
    except ValueError as e:
        raise ValueError(f"Malformed shell syntax: {e}")

    found_e_flag = False
    found_expression = False

    i = 0
    while i < len(parsed):
        arg = parsed[i]

        if (arg in ("-e", "--expression")) and i + 1 < len(parsed):
            found_e_flag = True
            expressions.append(parsed[i + 1])
            i += 2
            continue
        if arg.startswith("--expression="):
            found_e_flag = True
            expressions.append(arg[len("--expression="):])
            i += 1
            continue
        if arg.startswith("-e="):
            found_e_flag = True
            expressions.append(arg[len("-e="):])
            i += 1
            continue
        if arg.startswith("-"):
            i += 1
            continue

        if not found_e_flag and not found_expression:
            expressions.append(arg)
            found_expression = True
            i += 1
            continue

        break

    return expressions


def sed_command_is_allowed_by_allowlist(
    command: str,
    allow_file_writes: bool = False,
) -> bool:
    """Checks if a sed command is allowed by the allowlist."""
    try:
        expressions = extract_sed_expressions(command)
    except ValueError:
        return False

    has_file_arguments = has_file_args(command)

    is_pattern1 = False
    is_pattern2 = False

    if allow_file_writes:
        is_pattern2 = _is_substitution_command(
            command, expressions, has_file_arguments, allow_file_writes=True
        )
    else:
        is_pattern1 = is_line_printing_command(command, expressions)
        is_pattern2 = _is_substitution_command(command, expressions, has_file_arguments)

    if not is_pattern1 and not is_pattern2:
        return False

    for expr in expressions:
        if is_pattern2 and ";" in expr:
            return False

    for expr in expressions:
        if _contains_dangerous_operations(expr):
            return False

    return True


def check_sed_constraints(
    command: str,
    mode: str = "default",
) -> PermissionResult:
    """Cross-cutting validation step for sed commands."""
    commands = _split_command_deprecated(command)

    for cmd in commands:
        trimmed = cmd.strip()
        base_cmd = trimmed.split()[0] if trimmed.split() else ""
        if base_cmd != "sed":
            continue

        allow_file_writes = mode == "acceptEdits"
        is_allowed = sed_command_is_allowed_by_allowlist(
            trimmed, allow_file_writes=allow_file_writes
        )

        if not is_allowed:
            return PermissionResult(
                behavior="ask",
                message="sed command requires approval (contains potentially dangerous operations)",
            )

    return PermissionResult(
        behavior="passthrough",
        message="No dangerous sed operations detected",
    )
