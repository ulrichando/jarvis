"""
Tool error formatting utilities.
"""

from typing import Any, Dict, List, Optional


INTERRUPT_MESSAGE_FOR_TOOL_USE = "Tool use was interrupted by user"


class AbortError(Exception):
    pass


class ShellError(Exception):
    def __init__(
        self,
        message: str,
        code: int = 1,
        stdout: str = "",
        stderr: str = "",
        interrupted: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.stdout = stdout
        self.stderr = stderr
        self.interrupted = interrupted


def format_error(error: Any) -> str:
    """Format an error into a human-readable string."""
    if isinstance(error, AbortError):
        return str(error) or INTERRUPT_MESSAGE_FOR_TOOL_USE

    if not isinstance(error, Exception):
        return str(error)

    parts = get_error_parts(error)
    full_message = "\n".join(p for p in parts if p).strip()
    if not full_message:
        full_message = "Command failed with no output"

    if len(full_message) <= 10000:
        return full_message

    half_length = 5000
    start = full_message[:half_length]
    end = full_message[-half_length:]
    truncated = len(full_message) - 10000
    return f"{start}\n\n... [{truncated} characters truncated] ...\n\n{end}"


def get_error_parts(error: Exception) -> List[str]:
    """Extract error message parts from an exception."""
    if isinstance(error, ShellError):
        parts = [
            f"Exit code {error.code}",
            INTERRUPT_MESSAGE_FOR_TOOL_USE if error.interrupted else "",
            error.stderr,
            error.stdout,
        ]
        return parts

    parts = [str(error)]
    if hasattr(error, "stderr") and isinstance(error.stderr, str):
        parts.append(error.stderr)
    if hasattr(error, "stdout") and isinstance(error.stdout, str):
        parts.append(error.stdout)
    return parts


def format_validation_path(path: List[Any]) -> str:
    """
    Formats a validation path into a readable string.
    e.g., ['todos', 0, 'activeForm'] => 'todos[0].activeForm'
    """
    if not path:
        return ""

    result = ""
    for index, segment in enumerate(path):
        segment_str = str(segment)
        if isinstance(segment, int):
            result = f"{result}[{segment_str}]"
        elif index == 0:
            result = segment_str
        else:
            result = f"{result}.{segment_str}"
    return result


def format_zod_validation_error(
    tool_name: str,
    issues: List[Dict[str, Any]],
) -> str:
    """
    Converts validation errors into a human-readable and LLM friendly error message.

    Args:
        tool_name: The name of the tool that failed validation
        issues: List of validation issue dicts with 'code', 'message', 'path', etc.
    Returns:
        A formatted error message string
    """
    missing_params = [
        format_validation_path(err.get("path", []))
        for err in issues
        if err.get("code") == "invalid_type"
        and "received undefined" in err.get("message", "")
    ]

    unexpected_params = []
    for err in issues:
        if err.get("code") == "unrecognized_keys":
            unexpected_params.extend(err.get("keys", []))

    type_mismatch_params = []
    for err in issues:
        if (
            err.get("code") == "invalid_type"
            and "received undefined" not in err.get("message", "")
        ):
            expected = err.get("expected", "unknown")
            import re
            received_match = re.search(r"received (\w+)", err.get("message", ""))
            received = received_match.group(1) if received_match else "unknown"
            type_mismatch_params.append({
                "param": format_validation_path(err.get("path", [])),
                "expected": expected,
                "received": received,
            })

    error_parts: List[str] = []

    if missing_params:
        error_parts.extend(
            f"The required parameter `{param}` is missing"
            for param in missing_params
        )

    if unexpected_params:
        error_parts.extend(
            f"An unexpected parameter `{param}` was provided"
            for param in unexpected_params
        )

    if type_mismatch_params:
        error_parts.extend(
            f"The parameter `{p['param']}` type is expected as `{p['expected']}` "
            f"but provided as `{p['received']}`"
            for p in type_mismatch_params
        )

    if error_parts:
        issue_word = "issues" if len(error_parts) > 1 else "issue"
        return (
            f"{tool_name} failed due to the following {issue_word}:\n"
            + "\n".join(error_parts)
        )

    # Fallback: join all error messages
    return "\n".join(err.get("message", str(err)) for err in issues)
