"""Relax livekit-agents' strict tool-schema generator so optional
parameters (Python defaults) are NOT marked `required` in the JSON
schema sent to the LLM provider.

Background
==========
livekit-agents builds tool schemas in two modes:
  - legacy (`build_legacy_openai_schema`)  — `required` lists only
    params without Python defaults; old, permissive
  - strict (`build_strict_openai_schema`)  — `required` lists every
    property, per OpenAI's structured-outputs spec; requires the LLM
    to emit `null` for optionals if it wants to omit them

`to_fnc_ctx(..., strict=True)` is the default. Groq, Moonshot, and
some OpenAI endpoints honor strict mode server-side, validating the
LLM's tool_call args against the schema's `required` list — and
rejecting calls where an optional was omitted instead of sent as
`null`.

Symptom captured live 2026-05-05 17:13–17:14 UTC, post-Stage-B:
six `tool call validation failed: parameters for tool ext_new_tab
did not match schema: errors: [missing properties: 'url']` events
on a single user session — Groq generated `ext_new_tab()` with no
args (the natural way to call an optional-only tool), strict-mode
validation rejected, and the FallbackAdapter retried via DeepSeek.
The user got an answer but paid Groq→DeepSeek latency on every
browser-tool turn.

The 2026-05-02 attempted fix (changing `url: str = ""` to
`url: Optional[str] = None`) didn't help — strict mode adds the
property to `required` regardless of default value. Verified by
reading `to_fnc_ctx` source 2026-05-05.

Fix
===
After the strict-mode generator runs, walk `function.parameters` and
remove any property name from `required` if its schema entry has a
`default` field. The strict-mode shape is preserved otherwise
(`additionalProperties: false`, full property list, `null`-allowed
types via anyOf), so providers that rely on strict mode for safe
tool-call generation aren't weakened — only the spurious
required-ness of defaulted params is dropped.

Idempotent: reinstalling this patch is a no-op (guards on a flag
attribute). Compatible with the deepseek_roundtrip + tool_name +
pycall + dsml + handoff_text patches stack — install order doesn't
matter since none of them touch the same call site.

Alternative considered (rejected): swap every `groq.LLM(...)` to
`lk_openai.LLM(model=..., base_url='https://api.groq.com/openai/v1',
_strict_tool_schema=False)`. That works, but loses any Groq-plugin-
specific behavior (parallel_tool_calls handling, headers, future
features) and only fixes Groq — not Kimi or any other strict-mode
endpoint. The schema patch is provider-agnostic.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("jarvis.strict_schema_relax")


def install() -> None:
    """Patch livekit.agents.llm.utils.build_strict_openai_schema so
    properties with a `default` are not marked `required`.

    Idempotent — re-call from forked workers is a no-op.
    """
    from livekit.agents.llm import utils as _lk_utils

    if getattr(_lk_utils.build_strict_openai_schema, "_jarvis_relaxed", False):
        return

    _orig_strict = _lk_utils.build_strict_openai_schema
    _orig_legacy = _lk_utils.build_legacy_openai_schema

    def _patched(tool: Any) -> dict[str, Any]:
        """Always return the LEGACY schema, regardless of whether the
        tool has defaulted params. Strict mode is fundamentally
        incompatible with how the LLM produces tool calls in our
        live setup:

          - Tools with defaults: LLM omits the optional field; strict
            mode requires all fields → reject. Tried adjusting the
            schema (drop required, drop additionalProperties:false) —
            Groq rejects every variant short of the full strict shape.

          - Tools with no defaults: per-tool legacy/strict schema is
            fine, BUT Groq's chat-completions endpoint refuses a mix
            (live-observed: a strict-shape `bash` schema in the same
            request as a legacy-shape `ext_new_tab` rejects bash with
            `'additionalProperties:false' must be set on every object`).

        The cleanest exit: force EVERY tool to legacy schema. No
        per-tool strict flag is sent, no `additionalProperties: false`
        appears anywhere, and Groq accepts the request as a non-strict
        tool-call request. We lose strict-mode safety guarantees
        (LLM may produce unknown args, may type-mismatch). The
        downstream pydantic validation that runs when livekit dispatches
        the tool catches those — strict-mode at the LLM layer was
        defense-in-depth, not the only line.

        See POSTMORTEM-001 for the full iteration history.
        """
        try:
            return _orig_legacy(tool)
        except Exception as e:
            logger.warning(
                "[strict-schema-relax] legacy schema failed (%s); "
                "falling back to strict generator: %r",
                type(e).__name__, e,
            )
            return _orig_strict(tool)

    _patched._jarvis_relaxed = True  # type: ignore[attr-defined]
    _lk_utils.build_strict_openai_schema = _patched
    logger.info("[strict-schema-relax] installed — every tool uses "
                "legacy schema (strict mode disabled at the schema layer)")
