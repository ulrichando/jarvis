"""ECC Layer 2 — Tool Parameter Mutation.

When a tool call fails, automatically derive a corrected invocation and
retry before the model ever sees the error.  Handles the most common
failure patterns: permission errors, missing directories, unknown
commands, read-only file-system, etc.

Each rule is a 4-tuple:
    (tool_name, error_regex, mutator_fn, description)

mutator_fn receives the original args dict and returns a new args dict.
If the mutation cannot be applied (e.g. the command already has sudo),
returning the SAME dict is treated as "no applicable fix".

Limits:
  - MAX_MUTATIONS attempts per unique (tool, args) signature per turn.
  - Only runs when _is_repeat_failure is False (first failure).
"""

import re
import logging

log = logging.getLogger("jarvis.ecc.tool_fixer")

MAX_MUTATIONS = 3   # Maximum auto-fix retries per unique call signature


def _already_sudo(cmd: str) -> bool:
    return cmd.strip().startswith("sudo ")


class ToolFixer:
    """Applies mutation rules to failed tool calls and tracks attempt counts."""

    # Rules evaluated top-to-bottom; first match wins per call.
    RULES: list[tuple] = [
        # ── bash: Permission denied → prefix sudo ────────────────────────
        (
            "bash",
            r"Permission denied|EACCES|Operation not permitted",
            lambda args: (
                {**args, "command": f"sudo {args['command']}"}
                if not _already_sudo(args.get("command", ""))
                else args
            ),
            "prefix sudo",
        ),
        # ── bash: No such file → create missing parent dirs first ────────
        (
            "bash",
            r"No such file or directory|ENOENT",
            lambda args: (
                {
                    **args,
                    "command": (
                        f"mkdir -p \"$(dirname '{args['command'].split()[-1]}')\" "
                        f"2>/dev/null; {args['command']}"
                    ),
                }
                if any(w in args.get("command", "")
                       for w in [">", "tee", "cp ", "mv ", "touch "])
                else args
            ),
            "create missing parent directory",
        ),
        # ── bash: command not found → locate or offer install ────────────
        (
            "bash",
            r"command not found|not found|No such file.*bin",
            lambda args: {
                **args,
                "command": (
                    f"which {args['command'].split()[0]} "
                    f"|| type {args['command'].split()[0]} "
                    f"|| echo 'NOT_INSTALLED'"
                ),
            },
            "locate missing command",
        ),
        # ── bash: Read-only filesystem → remount rw ──────────────────────
        (
            "bash",
            r"Read-only file system|EROFS",
            lambda args: {
                **args,
                "command": (
                    f"sudo mount -o remount,rw $(df --output=target "
                    f"'{args['command'].split()[-1]}' | tail -1) 2>/dev/null; "
                    f"{args['command']}"
                ),
            },
            "remount filesystem read-write",
        ),
        # ── read_file: Permission denied ─────────────────────────────────
        (
            "read_file",
            r"Permission denied|EACCES",
            lambda args: {**args, "_sudo": True},
            "read file with elevated permissions",
        ),
        # ── write_file: Permission denied / read-only ────────────────────
        (
            "write_file",
            r"Permission denied|EACCES|Read-only file system",
            lambda args: {**args, "_sudo": True},
            "write file with elevated permissions",
        ),
        # ── edit_file: Permission denied ─────────────────────────────────
        (
            "edit_file",
            r"Permission denied|EACCES",
            lambda args: {**args, "_sudo": True},
            "edit file with elevated permissions",
        ),
    ]

    def __init__(self):
        # sig → number of mutation attempts already made this turn
        self._mutation_counts: dict[str, int] = {}

    def try_fix(
        self,
        tool_name: str,
        tool_args: dict,
        error_output: str,
        sig: str,
    ) -> tuple[dict | None, str]:
        """Return (mutated_args, description) or (None, '') if no fix applies.

        Respects MAX_MUTATIONS cap per unique call signature so we never
        loop forever on a broken tool.
        """
        if self._mutation_counts.get(sig, 0) >= MAX_MUTATIONS:
            log.debug("ECC-L2: mutation cap reached for sig %s", sig[:8])
            return None, ""

        for (t_name, pattern, mutator, desc) in self.RULES:
            if t_name != tool_name:
                continue
            if not re.search(pattern, error_output, re.IGNORECASE):
                continue
            try:
                new_args = mutator(tool_args)
            except Exception as exc:
                log.debug("ECC-L2: mutator raised: %s", exc)
                continue
            if new_args == tool_args:
                continue   # mutator decided it couldn't help
            self._mutation_counts[sig] = self._mutation_counts.get(sig, 0) + 1
            log.info(
                "ECC-L2: fix #%d for %s — %s",
                self._mutation_counts[sig], tool_name, desc,
            )
            return new_args, desc

        return None, ""

    def reset(self):
        self._mutation_counts.clear()
