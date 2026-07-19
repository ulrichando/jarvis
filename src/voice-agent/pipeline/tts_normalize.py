"""Deterministic speech normalizer for TTS text.

Problem (live, 2026-07): Kokoro/Edge voice surviving symbols BY NAME —
the user literally hears "asterisk asterisk" for `**`, "slash" for `/`,
"ampersand" for `&`, "bracket", "greater than", etc. The framework's
`filter_markdown` only handles PAIRED markdown emphasis/links, and the
old `_MD_RESIDUE_RE` in jarvis_agent.py stripped just 5 characters
(``*_`#~``). Everything else leaked to the TTS engine verbatim.

`normalize_for_speech(text)` is the single shared answer: a pure,
synchronous, regex-only function (no I/O, no state, no imports beyond
`re`) that

  1. flattens markdown STRUCTURE — headers, emphasis, strikethrough,
     inline/fenced code, links/images/reference links, blockquotes,
     bullets, horizontal rules, tables, footnotes;
  2. converts standalone symbols into words where that reads naturally
     (`&`→"and", `50%`→"50 percent", `+`→"plus", `=`→"equals",
     `->`→"to", `$5.99`→"5 dollars 99 cents", `@`→"at") and drops the
     purely visual ones (``* _ ` # ~ | \\ ^ < > [ ] { } • / ° $``…)
     to a space;
  3. NEVER touches legitimate speech: contractions (`don't`),
     hyphenated words (`well-known`), decimals/versions/times
     (`3.14`, `v4.2`, `9:30`), dotted tokens (`Node.js`, `e.g.`,
     `.NET`), em/en dashes (`—`/`–` are pause punctuation to both
     Kokoro and Edge — converting them would break the pinned
     strip_emote_markup contract), and ellipses (`…`/`...` are
     natural pauses).

Used from:
  - jarvis_agent.strip_emote_markup — the tts_text_transforms entry
    that every voiced utterance flows through. This covers BOTH the
    LLM reply stream AND session.say() canned messages: livekit-agents'
    say() path (`agent_activity._tts_task_impl`) calls
    `perform_tts_inference(text_transforms=session.options.
    tts_text_transforms)`, i.e. the same chain.
  - any future adapter path that hands text straight to a TTS provider
    (compose with sanitizers.pycall.sanitize_text_for_tts — that one
    is leak-shape suppression only, an orthogonal concern).

The caller keeps the letterless-guard: if the result has no letters or
digits, emit NOTHING (a letterless string makes Kokoro push zero frames
→ FallbackAdapter marks the TTS dead → voice flips). This module only
promises "no symbol voiced by name", not "always speakable".

Deliberate choices (each is cheap to revisit):
  - Fenced code blocks are DROPPED when prose surrounds them — reading
    code aloud is noise and the prose explains it. If the code block
    was the ENTIRE reply, its inner text is kept instead (the code IS
    the answer; silence would be worse).
  - Strikethrough KEEPS its content (`~~x~~`→"x") — the model struck
    it for visual effect, but it wrote the words to be read. (The
    framework filter drops them; we unwrap first so it never sees it.)
  - Ordered-list numbers are KEPT ("1. First" reads "one, first" —
    fine); unordered markers are dropped.
  - Currency is kept simple: "$5.99"→"5 dollars 99 cents",
    "$0.99"→"99 cents", "$5M"/"$5 million"→"5 million dollars";
    £→pounds/pence, €→euros/cents. No sub-cent, no exotic codes.
  - URLs are reduced to their host ("https://docs.x.com/a/b" →
    "docs.x.com") — the path is unreadable noise; the host tells the
    listener where. Emails read naturally via the `@`→"at" rule.
  - `24/7`→"24 7", `and/or`→"and or": every `/` becomes a space after
    link/URL handling — a voiced "slash" is never right.
"""
from __future__ import annotations

import re

__all__ = ["normalize_for_speech"]

# Any letter or digit, any script — same class jarvis_agent uses for
# its letterless-guard.
_SPEAKABLE_RE = re.compile(r"[^\W_]", re.UNICODE)

# ---------------------------------------------------------------------------
# Block level
# ---------------------------------------------------------------------------

# ```lang\n ... \n```  (closing fence optional at end-of-text so a
# stream truncated mid-block still gets cleaned).
_FENCED_BLOCK_RE = re.compile(r"(?:```|~~~)[^\n]*\n(.*?)(?:```+|~~~+|\Z)", re.DOTALL)

# A line that is only a horizontal rule: ---, ***, ___, === (3+, spaces ok).
_HR_LINE_RE = re.compile(r"^[ \t]*([-*_=])(?:[ \t]*\1){2,}[ \t]*$")

# A table separator row: only pipes/dashes/colons/spaces, with >=1 pipe.
_TABLE_SEP_RE = re.compile(r"^[ \t]*(?=[^\n]*\|)[ \t:|\-]+$")

# Reference-link definition line: "[ref]: https://url  (optional "title")"
# — the whole line is plumbing, drop it. Requires the value to be a
# single token so prose like "[1]: see the appendix" survives.
_REF_DEF_RE = re.compile(r'^[ \t]*\[[^\]^][^\]]*\]:[ \t]+\S+[ \t]*(?:"[^"]*")?[ \t]*$')

_BLOCKQUOTE_RE = re.compile(r"^[ \t]*(?:>[ \t]?)+")
_HEADER_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+")
_BULLET_RE = re.compile(r"^[ \t]*[-*+][ \t]+")          # needs the space: "-5" is a number
_CHECKBOX_RE = re.compile(r"^\[[ xX]\][ \t]+")           # after bullet strip: "- [x] task"
_FOOTNOTE_DEF_RE = re.compile(r"^[ \t]*\[\^[^\]]+\]:[ \t]*")  # keep the definition TEXT

# ---------------------------------------------------------------------------
# Inline markdown
# ---------------------------------------------------------------------------

_FOOTNOTE_REF_RE = re.compile(r"\[\^[^\]]+\]")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")        # ![alt](url) -> alt
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")        # [text](url) -> text
_REF_LINK_RE = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")     # [text][ref] -> text
_AUTOLINK_RE = re.compile(r"<(https?://[^\s>]+)>")       # <url> -> url (host rule next)

_BOLD_ITALIC_RE = re.compile(r"\*{3}(?!\s)([^*\n]+?)(?<!\s)\*{3}")
_BOLD_RE = re.compile(r"(?<![\w*])\*\*(?!\s)([^*\n]+?)(?<!\s)\*\*(?![\w*])")
_ITALIC_RE = re.compile(r"(?<![\w*])\*(?!\s|\*)([^*\n]+?)(?<!\s)\*(?![\w*])")
_BOLD_U_RE = re.compile(r"(?<!\w)__(?!\s)([^_\n]+?)(?<!\s)__(?!\w)")
_ITALIC_U_RE = re.compile(r"(?<!\w)_(?!\s|_)([^_\n]+?)(?<!\s)_(?!\w)")
_STRIKE_RE = re.compile(r"~~(?!\s)([^~\n]+?)(?<!\s)~~")  # KEEP content
_INLINE_CODE_RE = re.compile(r"`{1,2}([^`\n]+?)`{1,2}")

# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s)\]}>'\",;]+", re.IGNORECASE)

_C_SHARP_RE = re.compile(r"\b([A-G])#(?![\w#])")         # C# / F# / A# -> "C sharp"

_DEG_C_RE = re.compile(r"(?<=\d)\s*°[ \t]*C\b")
_DEG_F_RE = re.compile(r"(?<=\d)\s*°[ \t]*F\b")
_DEG_RE = re.compile(r"(?<=\d)\s*°")

_APPROX_RE = re.compile(r"(?<![\w~])~(?=[$£€]?\d)")      # ~50 / ~$5 -> "about ..."

_CUR_UNITS = {"$": ("dollar", "cent"), "£": ("pound", "pence"), "€": ("euro", "cent")}
_CUR_SCALE_RE = re.compile(
    r"([$£€])\s?(\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion|trillion|[KMB])\b"
)
_CUR_SCALE_WORDS = {"K": "thousand", "M": "million", "B": "billion"}
_CUR_CENTS_RE = re.compile(r"([$£€])(\d[\d,]*)\.(\d{2})(?!\d)")
_CUR_PLAIN_RE = re.compile(r"([$£€])(\d[\d,]*(?:\.\d+)?)")

_PERCENT_RE = re.compile(r"(\d+(?:[.,]\d+)*)\s*%")

# Arrows first — they'd otherwise decompose into "- >" / "= >" pieces.
_ARROW_RE = re.compile(r"<->|-->|->|==>|=>|→|⇒|➔|➜|⟶")

_LT_NUM_RE = re.compile(r"(?<=[\d\s])<(?=\s*\d)")        # "5 < 10" keeps its meaning
_GT_NUM_RE = re.compile(r"(?<=[\d\s])>(?=\s*\d)")

# Purely visual leftovers -> space. Deliberately NOT here: em/en dashes,
# ellipsis, parentheses, quotes, apostrophes, hyphen, period, colon,
# comma — all legitimate speech or natural TTS pauses.
_RESIDUE_RE = re.compile(r"[*_`#~|\\^<>{}\[\]•·◦/%°$£€§¤]")

# Cleanup
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"[ \t]+([.,;:!?])(?=\s|$)")
_MULTI_COMMA_RE = re.compile(r",(?:[ \t]*,)+")
_BLANK_LINES_RE = re.compile(r"\n[ \t]*\n+")
_EDGE_SPACE_RE = re.compile(r"^[ \t]+|[ \t]+$", re.MULTILINE)


def _currency_scale(m: re.Match) -> str:
    sym, amount, scale = m.group(1), m.group(2), m.group(3)
    scale = _CUR_SCALE_WORDS.get(scale, scale)
    return f"{amount} {scale} {_CUR_UNITS[sym][0]}s"


def _currency_cents(m: re.Match) -> str:
    sym, whole, cents = m.group(1), m.group(2), m.group(3)
    unit, sub = _CUR_UNITS[sym]
    cents_part = f"{int(cents)} {sub}" + ("s" if sub != "pence" and int(cents) != 1 else "")
    if whole in ("0", "00"):
        return cents_part
    whole_part = f"{whole} {unit}" + ("" if whole == "1" else "s")
    if int(cents) == 0:
        return whole_part
    return f"{whole_part} {cents_part}"


def _currency_plain(m: re.Match) -> str:
    sym, amount = m.group(1), m.group(2)
    unit = _CUR_UNITS[sym][0]
    return f"{amount} {unit}" + ("" if amount == "1" else "s")


def _url_to_host(m: re.Match) -> str:
    url = m.group(0)
    host = re.sub(r"^https?://", "", url, flags=re.IGNORECASE)
    host = host.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    host = host.split("@")[-1].split(":", 1)[0]  # drop creds + port
    if host.lower().startswith("www.") and len(host) > 4:
        host = host[4:]
    return host.rstrip(".")


def _process_line(line: str) -> str | None:
    """Flatten one line of markdown structure. None = drop the line."""
    if _HR_LINE_RE.match(line):
        return None
    if _TABLE_SEP_RE.match(line):
        return None
    if _REF_DEF_RE.match(line):
        return None
    line = _BLOCKQUOTE_RE.sub("", line)
    line = _HEADER_RE.sub("", line)
    line = _BULLET_RE.sub("", line)
    line = _CHECKBOX_RE.sub("", line)
    line = _FOOTNOTE_DEF_RE.sub("", line)
    if line.count("|") >= 2:
        # Table row: read the cells as words with a comma-pause between.
        cells = [c.strip() for c in line.split("|") if c.strip()]
        line = ", ".join(cells)
    return line


def _normalize(text: str, keep_code: bool) -> str:
    # 1. Fenced code blocks — drop (or unwrap when the block IS the reply).
    if keep_code:
        text = _FENCED_BLOCK_RE.sub(lambda m: " " + m.group(1) + " ", text)
    else:
        text = _FENCED_BLOCK_RE.sub(" ", text)

    # 2. Line-level structure.
    kept = [ln for ln in (_process_line(ln) for ln in text.splitlines()) if ln is not None]
    text = "\n".join(kept)

    # 3. Inline markdown (order matters: images before links; paired
    # emphasis before the residue pass eats the markers).
    text = _FOOTNOTE_REF_RE.sub("", text)
    text = _IMAGE_RE.sub(r"\1", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _REF_LINK_RE.sub(r"\1", text)
    text = _AUTOLINK_RE.sub(r" \1 ", text)
    text = _BOLD_ITALIC_RE.sub(r"\1", text)
    text = _BOLD_RE.sub(r"\1", text)
    text = _ITALIC_RE.sub(r"\1", text)
    text = _BOLD_U_RE.sub(r"\1", text)
    text = _ITALIC_U_RE.sub(r"\1", text)
    text = _STRIKE_RE.sub(r"\1", text)
    text = _INLINE_CODE_RE.sub(r"\1", text)

    # 4. Symbol conversion — words where natural, space where visual.
    text = _URL_RE.sub(_url_to_host, text)
    text = _C_SHARP_RE.sub(r"\1 sharp", text)
    text = _DEG_C_RE.sub(" degrees Celsius", text)
    text = _DEG_F_RE.sub(" degrees Fahrenheit", text)
    text = _DEG_RE.sub(" degrees", text)
    text = _APPROX_RE.sub("about ", text)
    text = _CUR_SCALE_RE.sub(_currency_scale, text)
    text = _CUR_CENTS_RE.sub(_currency_cents, text)
    text = _CUR_PLAIN_RE.sub(_currency_plain, text)
    text = _PERCENT_RE.sub(r"\1 percent", text)
    text = _ARROW_RE.sub(" to ", text)
    text = text.replace("&amp;", " and ")
    text = re.sub(r"&(?:nbsp|lt|gt|quot|#\d+);", " ", text)  # stray HTML entities
    text = text.replace("&&", " and ").replace("&", " and ")
    text = _LT_NUM_RE.sub(" less than ", text)
    text = _GT_NUM_RE.sub(" greater than ", text)
    text = re.sub(r"={1,3}", " equals ", text)
    text = text.replace("+", " plus ")
    text = text.replace("@", " at ")
    text = _RESIDUE_RE.sub(" ", text)

    # 5. Whitespace / punctuation cleanup.
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    text = _MULTI_COMMA_RE.sub(",", text)
    text = _BLANK_LINES_RE.sub("\n", text)
    text = _EDGE_SPACE_RE.sub("", text)
    return text.strip()


def normalize_for_speech(text: str) -> str:
    """Return `text` rewritten so a TTS engine voices no symbol by name.

    Pure + deterministic + cheap (compiled regexes over an already-
    buffered reply). May return "" — the CALLER must apply the
    letterless-guard (emit nothing rather than hand a letterless
    string to Kokoro).
    """
    if not text:
        return ""
    out = _normalize(text, keep_code=False)
    if not _SPEAKABLE_RE.search(out) and _SPEAKABLE_RE.search(text):
        # Everything speakable lived inside a fenced code block — the
        # code WAS the answer. Keep its inner text rather than go silent.
        out = _normalize(text, keep_code=True)
    return out
