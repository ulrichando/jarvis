"""ECC Layer 3 — Goal-State Verification.

After the agent loop finishes, quickly check whether the task was
actually completed.  Uses pure pattern matching — no LLM call, no
extra latency on the happy path.

If the task looks incomplete, returns a VerifyResult with a
corrective_prompt that the caller can inject for one bounded retry.

Design constraints:
  - Must be fast (called after every agent turn)
  - Must have low false-positive rate (don't flag successes as failures)
  - One correction pass maximum — we are not allowed to loop forever
"""

import re
import logging
from dataclasses import dataclass

log = logging.getLogger("jarvis.ecc.goal_verifier")


@dataclass
class VerifyResult:
    complete: bool
    missing: str = ""
    corrective_prompt: str = ""


# Signals that strongly indicate something went wrong in the tool results
_FAILURE_PATTERNS = [
    r"\bERROR\b",
    r"\bTraceback \(most recent",
    r"command not found",
    r"Permission denied",
    r"No such file or directory",
    r"\[Tool calling failed",
    r"failed to\b",
    r"could not\b",
    r"exit_code=[1-9]\d*",
]

# Signals that indicate the task succeeded
_SUCCESS_PATTERNS = [
    r"\bsuccessfully\b",
    r"\bdone[.!]",
    r"\bcompleted[.!]",
    r"\bcreated\b",
    r"\bwritten\b",
    r"\binstalled\b",
    r"\bsaved\b",
    r"exit_code=0\b",
    r"\bOK\b",
    r"\bpassed\b",
]

# Refusal phrases in the final response when no tools were called
_REFUSAL_RE = re.compile(
    r"I (can't|cannot|don't have|am unable|won't|will not) "
    r"(do|help|access|execute|perform|complete)",
    re.IGNORECASE,
)


class GoalVerifier:
    """Lightweight post-task completion checker."""

    def verify(
        self,
        task: str,
        tool_results: list[str],
        final_response: str,
    ) -> VerifyResult:
        """
        Returns VerifyResult.complete=False with a corrective_prompt when
        the task appears incomplete.  Only triggers when there is clear
        evidence of failure; ambiguous cases are treated as success.
        """
        # ── Refusal without any tool calls ───────────────────────────────
        if not tool_results and _REFUSAL_RE.search(final_response):
            return VerifyResult(
                complete=False,
                missing="no tool calls made despite actionable task",
                corrective_prompt=(
                    f"You did not attempt to complete the task: '{task[:150]}'. "
                    "You have full tool access — bash, read_file, write_file, etc. "
                    "Use them to complete it now."
                ),
            )

        if not tool_results:
            return VerifyResult(complete=True)   # conversation only — nothing to verify

        combined = " ".join(tool_results[-6:]) + " " + final_response

        failure_hits = sum(
            1 for p in _FAILURE_PATTERNS
            if re.search(p, combined, re.IGNORECASE)
        )
        success_hits = sum(
            1 for p in _SUCCESS_PATTERNS
            if re.search(p, combined, re.IGNORECASE)
        )

        # Only flag as incomplete when failures clearly outnumber successes.
        # Threshold raised to 3 to avoid false-positives from security/network
        # tool output (nmap, vuln scanners) which produce benign "ERROR"/"could not" noise.
        if failure_hits >= 3 and failure_hits > success_hits * 2:
            errors = []
            for p in _FAILURE_PATTERNS:
                m = re.search(p, combined, re.IGNORECASE)
                if m:
                    errors.append(m.group(0))
                    if len(errors) >= 3:
                        break
            missing = "; ".join(errors)
            log.info(
                "ECC-L3: task appears incomplete — failures=%d successes=%d (%s)",
                failure_hits, success_hits, missing,
            )
            return VerifyResult(
                complete=False,
                missing=missing,
                corrective_prompt=(
                    f"The previous attempt appears incomplete. "
                    f"Error signals detected: {missing}. "
                    "Review what failed and correct it now."
                ),
            )

        return VerifyResult(complete=True)
