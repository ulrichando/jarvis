"""Leak-shape detection primitives for the pycall sanitizer.

Pure detection logic — no monkey-patching, no I/O, no state. Used by
the streaming `_parse_choice` patch in `sanitizers.pycall` to decide
whether a freshly-arrived chunk could be the start of a tool-call-as-
text leak, and to make the final suppression decision once the
buffer has accumulated enough characters.

Four leak shapes are tracked:

  1. **Meta-silence** — the LLM emits the literal WORD "silence" /
     "listening" / "standing by" instead of actually staying silent.
     Live-captured turn 993 22:52:21: user said something ambient,
     JARVIS replied with the literal text "Silence.". TTS spoke it.

  2. **XML function** — `<function=name>` / `<function>` / `<arguments>`
     envelopes leaked from llama-3.1-8b-instant on BANTER turns
     (live 2026-05-05).

  3. **JSON tool array** — the LLM bypasses the tool-call protocol
     entirely and emits `[{"name": "ext_dom_summary", "parameters": {}}]`
     as plain content (live 2026-05-05 22:16).

  4. **Python call** — `name(args)` shape where `name` is a known
     JARVIS tool. The original leak class.

Each shape has:
  * A regex matching the COMPLETE shape (final-detect)
  * A `_could_extend_to_<shape>` lookahead — true while the buffered
    prefix could STILL grow into the shape. Once all four lookaheads
    return False, the buffer is released back to TTS.

Hoisted from `sanitizers/pycall.py` 2026-05-10 (Step 7 of the audit).
"""
from __future__ import annotations

import re

from sanitizers._leak_names import KNOWN_LEAK_NAMES, is_known_leak


__all__ = [
    # Meta-silence
    "META_SILENCE_RE",
    "META_SILENCE_PHRASES",
    "META_SILENCE_MAX_BUFFER",
    "strip_silence_lead",
    # Per-shape final-detect regexes
    "PYCALL_OPEN_RE",
    "XML_FUNCTION_OPEN_RE",
    "XML_FUNCTION_BARE_OPEN_RE",
    "XML_ARGUMENTS_OPEN_RE",
    "XML_ARGUMENTS_CLOSE",
    "XML_FUNCTION_CLOSE",
    "JSON_TOOL_ARRAY_OPEN_RE",
    # Per-shape lookahead detectors
    "could_extend_to_meta_silence",
    "could_extend_to_xml_function",
    "could_extend_to_json_array",
    "could_extend_to_python_call",
    "could_extend_to_any_leak",
    # Final-detect dispatcher
    "check_buffered_leak",
]


# ── Meta-silence ─────────────────────────────────────────────────────

META_SILENCE_RE: re.Pattern[str] = re.compile(
    r"^\s*(?:"
    # Branch A — original short-form silence words (alone or simple
    # bracket wrap). Trailing class is whitespace + punctuation only,
    # so this can't accidentally swallow a sentence like
    # "Ambient lighting in the room is dim." (the body chars don't
    # fit the trailing class).
    r"\[?\(?\s*(?:silent|silence|silently|quiet|quietly|listening|just\s+listening|"
    r"observing|standing\s+by|noted|quietly\s+noted|"
    # 2026-05-06 turn 1056: prompt rule "Empty output." for ambient
    # audio was being treated as a literal-output template — JARVIS
    # voiced "empty output" 8 times in 60 s. Add the meta-output
    # phrasings here as defense-in-depth alongside the prompt rewrite.
    r"empty\s+output|no\s+reply|no\s+output|nothing\s+to\s+say|nothing|"
    r"\(\s*empty\s*\)|\(\s*silent\s*\)|\(\s*no\s+reply\s*\)|"
    # 2026-05-28: added the standalone single-word forms of the
    # new "ambient" / "staying silent / quiet" stage-directions.
    r"ambient|staying\s+silent|staying\s+quiet)"
    r"(?:[\s,—\-]+sir)?[\s.,!?\]\)]*"
    r"|"
    # Branch B — bracketed stage-direction with a silence word and an
    # optional descriptor. REQUIRES opening + closing brackets so the
    # body can be any short text without false-positiving real prose
    # that happens to contain the word "ambient" or "silent".
    # 2026-05-28: catches "(ambient — not directed at me)",
    # "(ambient — staying silent)", "(ambient)" — observed in DB
    # 03:27 series, ~10 voiced stage-directions in 2 minutes.
    r"[\[\(]\s*"
    r"(?:ambient|silent|silence|silently|quiet|quietly|"
    r"staying\s+silent|staying\s+quiet|"
    r"no\s+reply|empty\s+output|listening|observing|standing\s+by)"
    r"(?:\s*[\-—:,]\s*[^\])]{1,80})?"
    r"\s*[\]\)]\s*"
    r")$",
    re.IGNORECASE,
)

# 2026-05-06 turn 1063 ("Silence." voiced because Groq streamed it as
# multiple chunks): the chunk-1 regex match misses when Groq breaks
# "Silence." into e.g. " " + "Sil" + "ence." or "Sile" + "nce." —
# neither chunk matches the FULL regex on its own. The fix is to
# buffer the first few chunks and check the assembled prefix at
# chunk N. META_SILENCE_PHRASES is the canonical list (mirror of
# the regex) used by the lookahead for the prefix decision.
META_SILENCE_PHRASES: tuple[str, ...] = (
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
    # Added 2026-05-28 to catch the "(ambient — …)" / "(staying silent)"
    # stage-direction patterns. The prefix lookahead buffers until the
    # full reply is in hand; the META_SILENCE_RE regex above does the
    # final decision.
    "ambient", "(ambient",
    "staying silent", "(staying silent",
    "staying quiet", "(staying quiet",
)

# Cap on how many chars to buffer before deciding. Above this, the
# stream has produced enough content that it can't be a meta-silence
# reply — release the buffer.
META_SILENCE_MAX_BUFFER: int = 40


def strip_silence_lead(s: str) -> str:
    """Strip the leading whitespace + optional [ ( bracket so the
    prefix comparison ignores wrapping. Mirrors the regex's prefix."""
    s = s.lstrip()
    while s and s[0] in "[(":
        s = s[1:].lstrip()
    return s


def could_extend_to_meta_silence(buffer: str) -> bool:
    """Could the buffered text grow into a meta-silence phrase?

    True if the lowercased, lead-stripped buffer is a prefix of any
    canonical meta-silence phrase. We use this to decide whether to
    HOLD streaming (buffering more chunks) or RELEASE the buffer
    back to TTS as ordinary content.
    """
    s = strip_silence_lead(buffer).lower()
    if not s:
        # Pure leading whitespace — could still be anything.
        return True
    for phrase in META_SILENCE_PHRASES:
        if phrase.startswith(s):
            return True
    return False


# ── Per-shape final-detect regexes ──────────────────────────────────

# Match `<identifier>(` (bare) OR `<ns>.<ns>.<identifier>(` (dotted).
# Captures the FINAL identifier (the actual tool name). Used to detect
# tool-call-as-text leaks at the start of a stream.
#
# Bare-name only was the original 2026-05-02 design. Live capture
# 2026-05-18T15:36:10 surfaced the dotted form: supervisor LLM emitted
# `computer.screenshot()` after narrating "I'll take a screenshot"
# (the `computer.` namespace is the Anthropic computer-use SUBAGENT
# tool, not a supervisor tool — pure hallucination). TTS read the
# literal string aloud; user heard "computer dot screenshot open paren
# close paren" → robotic. Generalizing to dotted forms catches this
# and any future `tools.X()` / `ns.X()` leaks. The final-identifier
# capture means `is_known_leak` still filters on the live tool name
# (`John.Smith(university)` captures `Smith`, which isn't a tool, so
# natural prose isn't false-positively suppressed).
PYCALL_OPEN_RE: re.Pattern[str] = re.compile(
    r"^\s*(?:[a-zA-Z_][a-zA-Z0-9_]*\.)*([a-zA-Z_][a-zA-Z0-9_]*)\s*\("
)

# Match `<function=name>` (HTML/XML attribute form, captured live
# 2026-05-05 from llama-3.1-8b-instant on BANTER turns). The closing
# `</function>` provides a deterministic envelope boundary.
XML_FUNCTION_OPEN_RE: re.Pattern[str] = re.compile(
    r"^\s*<function\s*=\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*>",
    re.IGNORECASE,
)

# Match `<function>` (bare tag, name appears as inner text or a
# subsequent `<arguments>` block). Captured live 2026-05-05 22:17 UTC
# turn 937: `<function>ext_click</function><assistant<|end_header_id|>`
# and turn 944: `<function>task_done</function><arguments>...`. Stream
# closes when `</function>` is followed by `</arguments>` OR when the
# bare `</function>` appears with no `<arguments>` open after it.
XML_FUNCTION_BARE_OPEN_RE: re.Pattern[str] = re.compile(
    r"^\s*<function\s*>",
    re.IGNORECASE,
)

# `<arguments>...</arguments>` may arrive as a SEPARATE chunk after
# the bare-tag `</function>` close. Treat it as its own independent
# envelope: any chunk that starts with `<arguments>` is part of the
# same logical tool-call leak.
XML_ARGUMENTS_OPEN_RE: re.Pattern[str] = re.compile(
    r"^\s*<arguments\s*>",
    re.IGNORECASE,
)
XML_ARGUMENTS_CLOSE: str = "</arguments>"
XML_FUNCTION_CLOSE: str = "</function>"

# Match JSON array of tool-call objects (live-captured 2026-05-05
# 22:16 UTC turn 930). The model is bypassing the tool-call protocol
# entirely and emitting tool dispatch as a JSON array of
# `{name, parameters}` dicts. Closes when the outer `]` arrives.
JSON_TOOL_ARRAY_OPEN_RE: re.Pattern[str] = re.compile(
    r"^\s*\[\s*\{\s*\"(?:name|tool|function)\"\s*:",
    re.IGNORECASE,
)


# ── Per-shape lookahead detectors ───────────────────────────────────

def could_extend_to_xml_function(buffer: str) -> bool:
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


def could_extend_to_json_array(buffer: str) -> bool:
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
        for i in range(min(len(keystart), len(name)) + 1):
            if name[: i] == keystart[: i]:
                if len(keystart) >= len(name) and keystart.startswith(name):
                    return True
                if i == len(keystart):
                    return True
    return False


def could_extend_to_python_call(buffer: str, live_known: set[str]) -> bool:
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
    candidates = KNOWN_LEAK_NAMES | set(live_known or set())
    for name in candidates:
        if name.lower().startswith(s_lower) and len(s) < len(name) + 2:
            return True
    # ext_* / transfer_to_* prefix conventions.
    if "ext_".startswith(s_lower) or s_lower.startswith("ext_"):
        return True
    if "transfer_to_".startswith(s_lower) or s_lower.startswith("transfer_to_"):
        return True
    return False


def could_extend_to_any_leak(buffer: str, live_known: set[str]) -> bool:
    """Generalized "should we keep buffering" check for the leak-
    watch envelope. True if the buffer could still grow into ANY of
    the known leak forms (meta-silence, XML, JSON array, Python
    call). False once we can rule out every form — then we release."""
    return (
        could_extend_to_meta_silence(buffer)
        or could_extend_to_xml_function(buffer)
        or could_extend_to_json_array(buffer)
        or could_extend_to_python_call(buffer, live_known)
    )


# ── Final-detect dispatcher ─────────────────────────────────────────

def check_buffered_leak(buffer: str, live_known: set[str]) -> str | None:
    """Run all leak regexes against the assembled buffer. Returns
    the detected form name ("meta-silence" / "xml-attr" / "xml-bare"
    / "json-array" / "pycall" / "xml-arguments") if the buffer matches
    a leak shape; None otherwise.

    Used when releasing the leak-watch envelope to make the final
    suppression decision. If a leak shape is now visible in the
    accumulated buffer, suppress the whole stream rather than
    releasing the buffer to TTS."""
    if META_SILENCE_RE.match(buffer):
        return "meta-silence"
    if XML_FUNCTION_OPEN_RE.match(buffer):
        return "xml-attr"
    if XML_FUNCTION_BARE_OPEN_RE.match(buffer):
        return "xml-bare"
    if JSON_TOOL_ARRAY_OPEN_RE.match(buffer):
        return "json-array"
    if XML_ARGUMENTS_OPEN_RE.match(buffer):
        return "xml-arguments"
    m = PYCALL_OPEN_RE.match(buffer)
    if m and is_known_leak(m.group(1), live_known):
        return "pycall"
    return None
