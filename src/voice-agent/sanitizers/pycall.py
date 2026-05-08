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
      task_done("user...    [from a SPECIALIST tool name]

The XML-attribute form (`<function=name>...</function>`) was missed
by the original Python-call regex. The `task_done(...)` form WAS the
right shape, but the original `name in self._tool_ctx.function_tools`
guard only covers tools the *current* LLM has registered — `task_done`
is a per-specialist tool, never in the supervisor LLM's tool_ctx, so
the guard skipped suppression. Both leak forms are now caught:

  - Python form: `name(...)` matched against a UNION of the live
    tool_ctx + a static `_KNOWN_LEAK_NAMES` whitelist of
    specialist-internal + commonly-leaked names.
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
_FALLBACK_ACK = "Done, sir."


# W-020 (2026-05-05): meta-silence replies are when the LLM emits the
# WORD "silence" (or "listening", "standing by") as its response,
# instead of actually staying silent. Live-captured turn 993 22:52:21:
# user said something ambient (TV background about state laws), and
# JARVIS replied with the literal text "Silence.". TTS spoke it aloud.
# The existing `_META_SILENCE_RE` in jarvis_agent.py is recall-time
# only; this is the streaming-time twin. When detected at the very
# start of a stream and the whole stream content matches, suppress
# without injecting a fallback (silence is the user-correct outcome —
# they didn't address JARVIS).
_META_SILENCE_RE = re.compile(
    r"^\s*\[?\(?\s*"
    r"(?:silent|silence|silently|quiet|quietly|listening|just\s+listening|"
    r"observing|standing\s+by|noted|quietly\s+noted|"
    # 2026-05-06 turn 1056: prompt rule "Empty output." for ambient
    # audio was being treated as a literal-output template — JARVIS
    # voiced "empty output" 8 times in 60 s. Add the meta-output
    # phrasings here as defense-in-depth alongside the prompt rewrite.
    r"empty\s+output|no\s+reply|no\s+output|nothing\s+to\s+say|nothing|"
    r"\(\s*empty\s*\)|\(\s*silent\s*\)|\(\s*no\s+reply\s*\))"
    r"(?:[\s,—\-]+sir)?[\s.,!?\]\)]*$",
    re.IGNORECASE,
)

# 2026-05-06 turn 1063 ("Silence." voiced because Groq streamed it as
# multiple chunks): the chunk-1 _META_SILENCE_RE match misses when
# Groq breaks "Silence." into e.g. " " + "Sil" + "ence." or "Sile" +
# "nce." — neither chunk matches the FULL regex on its own. The fix
# is to buffer the first few chunks and check the assembled prefix
# at chunk N. _META_SILENCE_PHRASES is the canonical list (mirror
# of the regex) used by `_could_extend_to_meta_silence` for the
# prefix decision.
_META_SILENCE_PHRASES: tuple[str, ...] = (
    "silent", "silence", "silently",
    "quiet", "quietly",
    "listening", "just listening",
    "observing",
    "standing by",
    "noted", "quietly noted",
    "empty output", "(empty output)",
    "no reply", "(no reply)",
    "no output",
    "nothing to say", "nothing",
    "(empty)", "(silent)",
)
# Cap on how many chars to buffer before deciding. Above this, the
# stream has produced enough content that it can't be a meta-silence
# reply — release the buffer.
_META_SILENCE_MAX_BUFFER = 40


def _strip_silence_lead(s: str) -> str:
    """Strip the leading whitespace + optional [ ( bracket so the
    prefix comparison ignores wrapping. Mirrors the regex's prefix."""
    s = s.lstrip()
    while s and s[0] in "[(":
        s = s[1:].lstrip()
    return s


def _could_extend_to_meta_silence(buffer: str) -> bool:
    """Could the buffered text grow into a meta-silence phrase?

    True if the lowercased, lead-stripped buffer is a prefix of any
    canonical meta-silence phrase. We use this to decide whether to
    HOLD streaming (buffering more chunks) or RELEASE the buffer
    back to TTS as ordinary content.
    """
    s = _strip_silence_lead(buffer).lower()
    if not s:
        # Pure leading whitespace — could still be anything.
        return True
    for phrase in _META_SILENCE_PHRASES:
        if phrase.startswith(s):
            return True
    return False


# 2026-05-06 turn 1093/1097 — streaming sanitizer missed XML and
# JSON tool-call leaks because Groq split the envelope opener across
# chunks. `<function>...` arriving as `<` + `function>...` doesn't
# match the chunk-1 `^\s*<function\s*>` regex. Same for JSON-array
# `[\n  {\n    "name":...` arriving as `[` + `\n  {...`. Both forms
# get caught by the persistence sanitizer at chat_ctx save time —
# but TTS has already spoken the gibberish to the user. Voice user
# heard literal "less than function greater than" / "open bracket
# open brace" punctuation.
#
# Fix: generalize the meta-silence-watch buffer to handle ALL leak
# forms. When chunk 1 looks like it COULD be the start of any of
# (meta-silence / XML / JSON array / Python-call / XML-arguments),
# open a generic leak-watch envelope. Buffer until either we have
# enough chars to recognize the form (then suppress) or we've
# accumulated enough that none of the leak regexes can match
# (then release the buffer to TTS).


def _could_extend_to_xml_function(buffer: str) -> bool:
    """Could the buffered text grow into `<function>` or
    `<function=name>` or `<arguments>` or `<tool_call>`?
    Quick prefix check on lstripped lower-cased buffer."""
    s = buffer.lstrip().lower()
    if not s:
        return True
    # The earliest-distinguishing prefixes for each XML form. Stop
    # buffering as soon as we know the form OR rule it out.
    for prefix in ("<function>", "<function=", "<arguments>", "<tool_call>"):
        if prefix.startswith(s) or s.startswith(prefix):
            return True
    # Bare `<` could still be any of the above.
    if s == "<":
        return True
    return False


def _could_extend_to_json_array(buffer: str) -> bool:
    """Could the buffered text grow into a JSON tool-call array
    `[{"name":...`? Strict character-by-character prefix check —
    after `[` the next non-whitespace must be `{`, then `"`, then
    one of `name|tool|function`. Anything else (e.g. `[1,2,3]`)
    rules the form out immediately."""
    s = buffer.lstrip()
    if not s:
        return True
    if not s.startswith("["):
        return False
    # After `[`, allow only whitespace before `{`.
    rest = s[1:]
    rest_lstripped = rest.lstrip()
    if not rest_lstripped:
        return True
    if not rest_lstripped.startswith("{"):
        return False
    # After `{`, allow only whitespace before `"`.
    rest = rest_lstripped[1:].lstrip()
    if not rest:
        return True
    if not rest.startswith('"'):
        return False
    # After `"`, the next chars must be a prefix of one of the
    # accepted key names (case-insensitive).
    keystart = rest[1:].lower()
    if not keystart:
        return True
    for name in ("name", "tool", "function"):
        # The key chars typed so far must be a prefix of `name`.
        # E.g. `na` is a prefix of `name`. `nam` is. `xyz` isn't.
        for i in range(min(len(keystart), len(name)) + 1):
            if name[: i] == keystart[: i]:
                # If keystart matches name fully and continues with
                # `"`, it's a clear go.
                if len(keystart) >= len(name) and keystart.startswith(name):
                    return True
                if i == len(keystart):
                    return True
    return False


def _could_extend_to_python_call(buffer: str, live_known: set[str]) -> bool:
    """Could the buffered text grow into a `name(...)` Python call
    leak where `name` is a known leak target?"""
    s = buffer.lstrip()
    if not s:
        return True
    # The Python call form has shape: identifier-chars then `(`.
    # While we haven't seen the `(` yet, the prefix could be any
    # known leak name. Compare buffer against each known name's
    # prefix.
    s_lower = s.lower()
    candidates = _KNOWN_LEAK_NAMES | set(live_known or set())
    for name in candidates:
        if name.lower().startswith(s_lower) and len(s) < len(name) + 2:
            return True
    # ext_* / transfer_to_* prefix conventions.
    if "ext_".startswith(s_lower) or s_lower.startswith("ext_"):
        return True
    if "transfer_to_".startswith(s_lower) or s_lower.startswith("transfer_to_"):
        return True
    return False


def _could_extend_to_any_leak(buffer: str, live_known: set[str]) -> bool:
    """Generalized "should we keep buffering" check for the leak-
    watch envelope. True if the buffer could still grow into ANY of
    the known leak forms (meta-silence, XML, JSON array, Python
    call). False once we can rule out every form — then we release."""
    return (
        _could_extend_to_meta_silence(buffer)
        or _could_extend_to_xml_function(buffer)
        or _could_extend_to_json_array(buffer)
        or _could_extend_to_python_call(buffer, live_known)
    )


def _check_buffered_leak(buffer: str, live_known: set[str]) -> str | None:
    """Run all leak regexes against the assembled buffer. Returns the
    detected form name ("meta-silence" / "xml-attr" / "xml-bare" /
    "json-array" / "pycall" / "xml-arguments") if the buffer matches
    a leak shape; None otherwise.

    Used when releasing the leak-watch envelope to make the final
    suppression decision. If a leak shape is now visible in the
    accumulated buffer, suppress the whole stream rather than
    releasing the buffer to TTS."""
    if _META_SILENCE_RE.match(buffer):
        return "meta-silence"
    if _XML_FUNCTION_OPEN_RE.match(buffer):
        return "xml-attr"
    if _XML_FUNCTION_BARE_OPEN_RE.match(buffer):
        return "xml-bare"
    if _JSON_TOOL_ARRAY_OPEN_RE.match(buffer):
        return "json-array"
    if _XML_ARGUMENTS_OPEN_RE.match(buffer):
        return "xml-arguments"
    m = _PYCALL_OPEN_RE.match(buffer)
    if m and _is_known_leak(m.group(1), live_known):
        return "pycall"
    return None


# Match `<identifier>(`, capturing the identifier. Used to detect
# tool-call-as-text leaks at the start of a stream.
_PYCALL_OPEN_RE = re.compile(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")

# Match `<function=name>` (HTML/XML attribute form, captured live
# 2026-05-05 from llama-3.1-8b-instant on BANTER turns). The closing
# `</function>` provides a deterministic envelope boundary.
_XML_FUNCTION_OPEN_RE = re.compile(
    r"^\s*<function\s*=\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*>",
    re.IGNORECASE,
)

# Match `<function>` (bare tag, name appears as inner text or a
# subsequent `<arguments>` block). Captured live 2026-05-05 22:17 UTC
# turn 937: `<function>ext_click</function><assistant<|end_header_id|>`
# and turn 944: `<function>task_done</function><arguments>...`. Stream
# closes when `</function>` is followed by `</arguments>` OR when the
# bare `</function>` appears with no `<arguments>` open after it.
_XML_FUNCTION_BARE_OPEN_RE = re.compile(
    r"^\s*<function\s*>",
    re.IGNORECASE,
)
# `<arguments>...</arguments>` may arrive as a SEPARATE chunk after the
# bare-tag `</function>` close. Treat it as its own independent envelope:
# any chunk that starts with `<arguments>` is part of the same logical
# tool-call leak. Unambiguous (HTML/XML attribute name `arguments`
# would never start a legitimate prose response).
_XML_ARGUMENTS_OPEN_RE = re.compile(
    r"^\s*<arguments\s*>",
    re.IGNORECASE,
)
_XML_ARGUMENTS_CLOSE = "</arguments>"
_XML_FUNCTION_CLOSE = "</function>"

# Match JSON array of tool-call objects (live-captured 2026-05-05
# 22:16 UTC turn 930: `[\n  {\n    "name": "ext_dom_summary",\n
# "parameters": {}\n  }\n]`). The model is bypassing the tool-call
# protocol entirely and emitting tool dispatch as a JSON array of
# `{name, parameters}` dicts. Closes when the outer `]` arrives.
_JSON_TOOL_ARRAY_OPEN_RE = re.compile(
    r"^\s*\[\s*\{\s*\"(?:name|tool|function)\"\s*:",
    re.IGNORECASE,
)

# Specialist-internal tools + commonly-leaked names — even though the
# CURRENT LLM may not have them in its tool_ctx, the supervisor or a
# downstream LLM emitting them as plain content is unambiguously a
# leak (the user can't be saying "task_done" as legitimate prose).
# Update this set when adding a new specialist tool that's likely to
# leak from a different LLM's content stream.
_KNOWN_LEAK_NAMES = frozenset({
    # Specialist task-done sentinel — auto-attached, never in the
    # supervisor LLM's tool_ctx but supervisor-LLM-emitted as text on
    # confused turns.
    "task_done",
    # Transfer/handoff tools — should always go via tool_calls, never
    # as content text.
    "transfer_to_desktop",
    "transfer_to_browser",
    "transfer_to_browser_v2",
    "transfer_to_planner",
    "delegate",
    # Browser ext_* tools — prefixed; bulk-prevented in _is_known_leak.
    # Listed here in case the prefix-check misses anything.
    "ext_screenshot",
    "ext_navigate",
    "ext_click",
    "ext_type",
    "ext_new_tab",
    "ext_get_url",
    "ext_back",
    "ext_forward",
    "ext_wait_for_load",
    # Common runtime tools that have leaked in the past.
    "browser_task",
    "browser_task_v2",
    "run_jarvis_cli",
    "bash",
    "media_control",
    "type_in_terminal",
    "launch_app",
    "web_search",
    "read_url",
    "recall_conversation",
    "remember_this",
    "get_location",
})


def sanitize_text_for_tts(text: str) -> str:
    """Return `text` with any tool-call-as-text leak suppressed.

    Public helper for code paths that don't go through the streaming
    `_parse_choice` patch — notably `supervisor_graph.llm_adapter`,
    which constructs ChatChunks directly from LangGraph's AIMessage
    content and otherwise bypasses every sanitizer.

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


def _is_known_leak(name: str, live_known: frozenset[str] | set[str]) -> bool:
    """True if `name` is plausibly a JARVIS tool whose appearance as
    plain content text is a leak. Combines the live tool_ctx with the
    specialist-internal whitelist + the `ext_*` prefix convention."""
    if name in live_known:
        return True
    if name in _KNOWN_LEAK_NAMES:
        return True
    if name.startswith("ext_") and len(name) > 4:
        # Browser-extension tools all share this prefix; the supervisor
        # might emit any of them as text content.
        return True
    if name.startswith("transfer_to_") and len(name) > 12:
        # Future specialists' transfer tools, generated at registry
        # time — defensive cover.
        return True
    return False


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
