"""Suppress tool-call-as-text leaks AND inject a fallback acknowledgment
when the leak was the ENTIRE response.

Captured live 2026-05-02 12:26: Groq llama-3.3-70b emitted an entire
tool sequence as plain content text:

    browser_task_v2("go to weather.com and report the current weather for Cleveland, Ohio")  task_done(summary)

Instead of using the structured `tool_calls` field, the model dumped
the call as Python source. The TTS voiced the function-call syntax
verbatim and the user heard "browser task v two left paren quote
go to weather dot com..." which is unintelligible.

Captured live 2026-05-05 22:06–22:07 UTC (F-arch-011): two
additional leak forms not previously covered:

  Turn 904 (BANTER, llama-3.1-8b-instant), user said "Thank you":
      <function=ext_screenshot>null</function>

  Turn 907 (TASK, llama-3.3-70b-versatile), user said "Open Amazon":
      task_done("user...    [from a SUBAGENT tool name]

The XML-attribute form (`<function=name>...</function>`) was missed
by the original Python-call regex. The `task_done(...)` form WAS the
right shape, but the original `name in self._tool_ctx.function_tools`
guard only covers tools the *current* LLM has registered — `task_done`
is a per-subagent tool, never in the supervisor LLM's tool_ctx, so
the guard skipped suppression. Both leak forms are now caught:

  - Python form: `name(...)` matched against a UNION of the live
    tool_ctx + a static `_KNOWN_LEAK_NAMES` whitelist of
    subagent-internal + commonly-leaked names.
  - XML form: `<function=name>` triggers regardless — the
    angle-bracket envelope is unambiguously a tool-call leak.

Distinct from:
  - tool_name_sanitizer.py — recovers from Groq's `tool call validation
    failed` API error (the cramming-into-name shape)
  - dsml_sanitizer.py — recovers from DeepSeek's `<｜｜DSML｜｜...>`
    envelope leakage

This module patches `_parse_choice`. Idempotent — install() can be
called multiple times safely. Stacks cleanly on top of the existing
dsml + tool_name + deepseek-roundtrip patches.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("jarvis.pycall_sanitizer")


# W-018 fallback acknowledgment. Voiced when a stream's ENTIRE content
# was a tool-call leak — without this the user gets pure silence after
# a request JARVIS actually executed (the leak was suppressed but no
# real reply was produced). Short + neutral so it works regardless of
# what the actual task was. Match the persona register from
# JARVIS_INSTRUCTIONS (no archaic phrasing).
_FALLBACK_ACK = "Done."



# Leak-shape detection primitives extracted to sanitizers/_leak_shapes.py
# 2026-05-10 (Step 7 of the audit). Pure detection logic — no monkey-
# patching, no I/O, no state. Re-exported under legacy underscored
# aliases so the in-file callers in `install()` + downstream consumers
# stay untouched.
from sanitizers._leak_shapes import (
    META_SILENCE_RE          as _META_SILENCE_RE,
    META_SILENCE_PHRASES     as _META_SILENCE_PHRASES,
    META_SILENCE_MAX_BUFFER  as _META_SILENCE_MAX_BUFFER,
    strip_silence_lead       as _strip_silence_lead,
    could_extend_to_meta_silence as _could_extend_to_meta_silence,
    could_extend_to_xml_function as _could_extend_to_xml_function,
    could_extend_to_json_array   as _could_extend_to_json_array,
    could_extend_to_python_call  as _could_extend_to_python_call,
    could_extend_to_any_leak     as _could_extend_to_any_leak,
    check_buffered_leak          as _check_buffered_leak,
    PYCALL_OPEN_RE           as _PYCALL_OPEN_RE,
    XML_FUNCTION_OPEN_RE     as _XML_FUNCTION_OPEN_RE,
    XML_FUNCTION_BARE_OPEN_RE as _XML_FUNCTION_BARE_OPEN_RE,
    XML_ARGUMENTS_OPEN_RE    as _XML_ARGUMENTS_OPEN_RE,
    XML_ARGUMENTS_CLOSE      as _XML_ARGUMENTS_CLOSE,
    XML_FUNCTION_CLOSE       as _XML_FUNCTION_CLOSE,
    JSON_TOOL_ARRAY_OPEN_RE  as _JSON_TOOL_ARRAY_OPEN_RE,
)


# Known-leak registry extracted to sanitizers/_leak_names.py 2026-05-10
# (Step 7 of the audit). Re-exported under the legacy underscored alias
# so internal `_KNOWN_LEAK_NAMES` references stay working unchanged.
from sanitizers._leak_names import KNOWN_LEAK_NAMES as _KNOWN_LEAK_NAMES


def sanitize_text_for_tts(text: str) -> str:
    """Return `text` with any tool-call-as-text leak suppressed.

    Public helper for code paths that don't go through the streaming
    `_parse_choice` patch.

    If the input matches a leak shape (Python-call, XML attr/bare,
    JSON array, meta-silence), returns "". Otherwise returns the
    input unchanged. Idempotent.
    """
    if not text:
        return text
    s = text.strip()
    if not s:
        return text
    detected = _check_buffered_leak(s, live_known=set())
    if detected:
        logger.warning(
            f"[pycall] adapter-path leak suppressed ({detected}): "
            f"{s[:80]!r}"
        )
        return ""
    return text


from sanitizers._leak_names import is_known_leak as _is_known_leak


# Per-stream state. Keyed by response.id (passed to _parse_choice).
# Cleared when the envelope balances or the stream ends.
_PYCALL_STATE: dict[str, dict[str, Any]] = {}


def _try_set_content(delta: Any, value: str) -> None:
    """Best-effort mutate delta.content. Mirrors dsml_sanitizer."""
    try:
        delta.content = value
    except Exception:
        try:
            object.__setattr__(delta, "content", value)
        except Exception:
            logger.debug("[pycall] could not mutate delta.content; envelope may leak")


def install() -> None:
    """Patch LLMStream._parse_choice to suppress Python-syntax tool-call
    leaks. Idempotent. Stacks safely with the other parse_choice
    patches (dsml_sanitizer + deepseek_roundtrip)."""
    from livekit.agents.inference import llm as inf_llm

    if getattr(inf_llm.LLMStream, "_jarvis_pycall_patched", False):
        return

    orig_parse = inf_llm.LLMStream._parse_choice

    def patched(self, id, choice, thinking):
        delta = getattr(choice, "delta", None)
        if delta is not None:
            content = getattr(delta, "content", None) or ""
            state = _PYCALL_STATE.get(id)

            # 2026-05-06 — generalized leak-watch envelope handling.
            # When we've started buffering early-stream content because
            # it COULD become any of the leak forms (meta-silence /
            # XML / JSON / Python-call), every subsequent chunk goes
            # here until we can rule the leak out or recognize one.
            #
            # Also handles the legacy "meta-silence-watch" name from
            # the earlier T1 fix — same code path, kept the alias
            # for state already in flight at restart.
            if state is not None and state.get("envelope") in (
                "leak-watch", "meta-silence-watch",
            ):
                state["buffer"] += content or ""
                buf = state["buffer"]
                try:
                    live_known = set(self._tool_ctx.function_tools.keys())
                except Exception:
                    live_known = set()
                # Decision 1: any leak shape now matches → suppress.
                form = _check_buffered_leak(buf, live_known)
                if form is not None:
                    logger.warning(
                        "[pycall] leak suppressed (multi-chunk, %s): %r",
                        form, buf[:120],
                    )
                    _try_set_content(delta, "")
                    state["envelope"] = "meta-silence-suppressed"
                    state["form"] = form
                    return orig_parse(self, id, choice, thinking)
                # Decision 2: buffer too long OR can no longer extend
                # to ANY leak form → release the accumulated buffer.
                if (
                    len(buf) >= _META_SILENCE_MAX_BUFFER
                    or not _could_extend_to_any_leak(buf, live_known)
                ):
                    logger.debug(
                        "[pycall] leak-watch released (not a leak): %r",
                        buf[:80],
                    )
                    _try_set_content(delta, buf)
                    del _PYCALL_STATE[id]
                    return orig_parse(self, id, choice, thinking)
                # Still buffering — emit empty for this chunk.
                _try_set_content(delta, "")
                return orig_parse(self, id, choice, thinking)

            # Already-decided meta-silence reply: keep suppressing
            # any trailing chunks the LLM emits.
            if state is not None and state.get("envelope") == "meta-silence-suppressed":
                _try_set_content(delta, "")
                return orig_parse(self, id, choice, thinking)

            if state is None and content:
                # First chunk for this stream — peek for any leak form.
                try:
                    live_known = set(self._tool_ctx.function_tools.keys())
                except Exception:
                    live_known = set()

                # Form 0 (W-020): meta-silence reply. If the FULL content
                # of this chunk matches the meta-silence pattern (whole
                # reply is just "Silence." / "Listening." / etc.),
                # suppress unconditionally and don't open envelope state —
                # there's nothing more to come; this single chunk IS the
                # whole reply. No fallback ack injected because the
                # user-correct outcome here is silence (the user wasn't
                # addressing JARVIS).
                if _META_SILENCE_RE.match(content):
                    logger.warning(
                        "[pycall] meta-silence reply suppressed: %r",
                        content[:80],
                    )
                    _try_set_content(delta, "")
                    # 2026-05-06 — set the meta-silence-suppressed
                    # envelope so any FOLLOW-UP chunks are also
                    # suppressed. Without this, a multi-chunk reply
                    # like ['Nothing', ', sir.'] suppresses chunk 1
                    # but lets ', sir.' leak through as the spoken
                    # reply (live-captured turns 1082/1083 emitted
                    # the literal string ', sir.' to TTS).
                    _PYCALL_STATE[id] = {
                        "envelope": "meta-silence-suppressed",
                        "buffer": content,
                    }
                    return orig_parse(self, id, choice, thinking)

                # Form 1: Python call — `name(...)`.
                m = _PYCALL_OPEN_RE.match(content)
                # Form 2: HTML/XML attribute — `<function=name>...`.
                xm = _XML_FUNCTION_OPEN_RE.match(content)
                # Form 3: HTML/XML bare tag — `<function>name</function>`
                #         optionally followed by `<arguments>...</arguments>`.
                xbm = _XML_FUNCTION_BARE_OPEN_RE.match(content)
                # Form 4: JSON array of tool-call objects — `[{"name":...`.
                jm = _JSON_TOOL_ARRAY_OPEN_RE.match(content)
                # Form 5: orphaned `<arguments>...</arguments>` chunk —
                # arrives after the bare-tag form's `</function>` close.
                am = _XML_ARGUMENTS_OPEN_RE.match(content)

                if m and _is_known_leak(m.group(1), live_known):
                    name = m.group(1)
                    _PYCALL_STATE[id] = {
                        "buffer": content, "depth": 0,
                        "tool_name": name, "envelope": "pycall",
                    }
                    s = _PYCALL_STATE[id]
                    s["depth"] = content.count("(") - content.count(")")
                    logger.warning(
                        "[pycall] tool-call-as-text leak (Python form): "
                        "name=%r prefix=%r — suppressing",
                        name, content[:80],
                    )
                    _try_set_content(delta, "")
                elif xm:
                    name = xm.group(1)
                    _PYCALL_STATE[id] = {
                        "buffer": content, "depth": 0,
                        "tool_name": name, "envelope": "xml-attr",
                    }
                    logger.warning(
                        "[pycall] tool-call-as-text leak (XML attribute "
                        "form): name=%r prefix=%r — suppressing",
                        name, content[:80],
                    )
                    _try_set_content(delta, "")
                elif xbm:
                    _PYCALL_STATE[id] = {
                        "buffer": content, "depth": 0,
                        "tool_name": "?", "envelope": "xml-bare",
                    }
                    logger.warning(
                        "[pycall] tool-call-as-text leak (XML bare-tag "
                        "form): prefix=%r — suppressing",
                        content[:80],
                    )
                    _try_set_content(delta, "")
                elif jm:
                    _PYCALL_STATE[id] = {
                        "buffer": content, "depth": 0,
                        "tool_name": "?", "envelope": "json-array",
                        # Track bracket nesting so we know when ] closes.
                        "bracket_depth": (
                            content.count("[") - content.count("]")
                        ),
                    }
                    logger.warning(
                        "[pycall] tool-call-as-text leak (JSON array "
                        "form): prefix=%r — suppressing",
                        content[:80],
                    )
                    _try_set_content(delta, "")
                elif am:
                    _PYCALL_STATE[id] = {
                        "buffer": content, "depth": 0,
                        "tool_name": "?", "envelope": "xml-arguments",
                    }
                    logger.warning(
                        "[pycall] tool-call-as-text leak (orphaned "
                        "<arguments> chunk, follow-up to bare-tag form): "
                        "prefix=%r — suppressing",
                        content[:80],
                    )
                    _try_set_content(delta, "")
                # Form 0.5 (2026-05-06): chunk-1 content didn't fully
                # match any explicit form regex above, but it COULD
                # extend to one once more chunks arrive — start the
                # leak-watch envelope. Catches multi-chunk splits of:
                #   - meta-silence ("Sil" + "ence.")
                #   - XML bare tag ("<" + "function>...")
                #   - XML attribute ("<func" + "tion=name>...")
                #   - JSON tool array ("[" + "{\"name\":...}]")
                #   - Python call ("ext_" + "click(...)")
                # Must be the LAST elif — explicit form matches above
                # take precedence (they have full close-tracking).
                elif _could_extend_to_any_leak(content, live_known):
                    _PYCALL_STATE[id] = {
                        "envelope": "leak-watch",
                        "buffer": content,
                    }
                    _try_set_content(delta, "")
            elif state is not None and content:
                # Inside the envelope — keep accumulating + suppressing.
                state["buffer"] += content
                if state.get("envelope") == "pycall":
                    state["depth"] += content.count("(") - content.count(")")
                elif state.get("envelope") == "json-array":
                    state["bracket_depth"] = state.get("bracket_depth", 0) + (
                        content.count("[") - content.count("]")
                    )
                _try_set_content(delta, "")

            # End-of-envelope detection — different criterion per form.
            state = _PYCALL_STATE.get(id)
            if state is not None:
                envelope = state.get("envelope", "pycall")
                closed = False
                if envelope == "pycall":
                    closed = (
                        state["depth"] <= 0
                        and len(state["buffer"]) > len(state["tool_name"]) + 1
                    )
                elif envelope == "xml-attr":
                    closed = _XML_FUNCTION_CLOSE in state["buffer"]
                elif envelope == "xml-bare":
                    # The bare-tag form may have an `<arguments>...</arguments>`
                    # block following the closing `</function>`, but it
                    # arrives as a SEPARATE stream chunk. Close the bare-tag
                    # envelope on `</function>` alone — the orphan
                    # `<arguments>` chunk is caught by Form 5 (xml-arguments)
                    # as its own independent envelope.
                    closed = _XML_FUNCTION_CLOSE in state["buffer"]
                elif envelope == "xml-arguments":
                    closed = _XML_ARGUMENTS_CLOSE in state["buffer"]
                elif envelope == "json-array":
                    closed = state.get("bracket_depth", 1) <= 0

                if closed:
                    logger.info(
                        "[pycall] %s envelope closed (len=%d); state cleared",
                        envelope, len(state["buffer"]),
                    )
                    # W-018 (2026-05-05): if the leak was the ENTIRE
                    # response (state was opened on the first chunk
                    # and the close arrived without any non-leak
                    # content interleaved), inject a synthetic content
                    # chunk so the user hears SOMETHING. Without this,
                    # the user gets pure silence after a request that
                    # JARVIS actually completed — the supervisor LLM
                    # leaked task_done/<function>/etc. as content,
                    # we suppressed it, and there was no real reply.
                    # Live-observed 2026-05-05 22:42–22:43 UTC: three
                    # consecutive supervisor turns leaked task_done(...)
                    # as content; sanitizer caught all three; user
                    # reported "JARVIS is silent" because the only
                    # output channel was producing nothing audible.
                    try:
                        _try_set_content(delta, _FALLBACK_ACK)
                        logger.warning(
                            "[pycall] full-response leak suppressed; "
                            "injecting synthetic '%s' so user hears "
                            "SOMETHING (envelope=%s, buffer_len=%d)",
                            _FALLBACK_ACK, envelope, len(state["buffer"]),
                        )
                    except Exception as inject_err:
                        logger.warning(
                            "[pycall] could not inject fallback ack: %s; "
                            "user will hear silence on this turn",
                            inject_err,
                        )
                    del _PYCALL_STATE[id]
                elif len(state["buffer"]) > 8000:
                    logger.warning(
                        "[pycall] %s buffer overflow without close — discarding",
                        envelope,
                    )
                    del _PYCALL_STATE[id]

        return orig_parse(self, id, choice, thinking)

    inf_llm.LLMStream._parse_choice = patched
    inf_llm.LLMStream._jarvis_pycall_patched = True
    logger.warning("Pycall sanitizer installed (suppresses tool-call-as-text leaks)")
