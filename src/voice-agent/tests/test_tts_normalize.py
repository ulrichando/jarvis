"""Tests for pipeline/tts_normalize.py::normalize_for_speech — the
comprehensive speech normalizer for TTS text.

Problem (live, 2026-07): Kokoro/Edge voice surviving symbols BY NAME —
"asterisk asterisk" for `**`, "slash" for `/`, "ampersand" for `&`.
The normalizer flattens markdown structure and converts/drops standalone
symbols so NONE is voiced by name, while never mangling legitimate
speech (contractions, hyphenated words, decimals, versions, times,
tech tokens).

Two test groups:
  - TestMarkdownFlattening / TestSymbolConversion — every construct
    the normalizer claims to handle.
  - TestMustNotMangle — the adversarial block: real speech that a
    careless symbol strip would destroy. These are the load-bearing
    guarantees.

Plus integration tests through jarvis_agent.strip_emote_markup (the
tts_text_transforms entry that calls the normalizer) proving the
letterless-guard and emote handling still compose.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.tts_normalize import normalize_for_speech as n


# ---------------------------------------------------------------------------
# Markdown structure flattening
# ---------------------------------------------------------------------------

class TestMarkdownFlattening:
    def test_h1_header(self):
        assert n("# Title") == "Title"

    def test_h6_header(self):
        assert n("###### Deep title") == "Deep title"

    def test_header_keeps_body_below(self):
        assert n("## Plan\nDo the thing.") == "Plan\nDo the thing."

    def test_bold(self):
        assert n("**Done.** All set.") == "Done. All set."

    def test_italic_star(self):
        assert n("that was *really* fast") == "that was really fast"

    def test_italic_underscore(self):
        assert n("that was _really_ fast") == "that was really fast"

    def test_bold_underscore(self):
        assert n("__warning__ ahead") == "warning ahead"

    def test_bold_italic(self):
        assert n("***very*** important") == "very important"

    def test_strikethrough_keeps_content(self):
        # The model struck it for visual effect but wrote words to be
        # read; framework filter_markdown would DROP the content, we
        # unwrap it first so it never gets the chance.
        assert n("it was ~~cancelled~~ moved") == "it was cancelled moved"

    def test_inline_code_keeps_content(self):
        assert n("run `ls` now") == "run ls now"

    def test_fenced_code_block_dropped_when_prose_exists(self):
        # A code block surrounded by prose is visual noise for voice —
        # the prose explains it. Dropped entirely.
        assert n("Run this:\n```bash\nls -la /tmp\n```\nDone.") == "Run this:\nDone."

    def test_fenced_code_block_kept_when_it_is_the_reply(self):
        # If the code block IS the whole reply, the code was the
        # answer — silence would be worse than reading it.
        assert n("```python\nprint('hi')\n```") == "print('hi')"

    def test_unclosed_fence_still_cleaned(self):
        assert n("Here:\n```js\nlet x = 1") == "Here:"

    def test_link_keeps_text(self):
        assert n("see [the docs](https://x.com/a/b) for more") == "see the docs for more"

    def test_image_keeps_alt(self):
        assert n("![a chart](https://x.com/c.png) shows growth") == "a chart shows growth"

    def test_reference_link_keeps_text(self):
        assert n("see [the guide][1] first") == "see the guide first"

    def test_reference_definition_line_dropped(self):
        assert n("see the guide\n[1]: https://x.com/guide") == "see the guide"

    def test_blockquote_marker_dropped(self):
        assert n("> wisdom here") == "wisdom here"

    def test_nested_blockquote(self):
        assert n("> > deep quote") == "deep quote"

    def test_unordered_bullets_dropped(self):
        assert n("- first\n* second\n+ third") == "first\nsecond\nthird"

    def test_ordered_list_numbers_kept(self):
        # "1. First" reads "one, first" — fine as speech.
        assert n("1. First\n2. Second") == "1. First\n2. Second"

    def test_checkbox_list(self):
        assert n("- [x] done task\n- [ ] open task") == "done task\nopen task"

    def test_horizontal_rule_dropped(self):
        assert n("above\n---\nbelow") == "above\nbelow"

    def test_hr_variants_dropped(self):
        assert n("a\n***\nb\n___\nc\n===\nd") == "a\nb\nc\nd"

    def test_table_cells_read_pipes_dropped(self):
        got = n("| Name | Age |\n|------|-----|\n| Bob | 42 |")
        assert got == "Name, Age\nBob, 42"

    def test_table_separator_with_alignment_dropped(self):
        got = n("| a | b |\n|:---|---:|\n| 1 | 2 |")
        assert got == "a, b\n1, 2"

    def test_footnote_ref_dropped(self):
        assert n("as shown[^1] earlier") == "as shown earlier"

    def test_footnote_definition_keeps_text(self):
        assert n("[^1]: the source was NASA") == "the source was NASA"

    def test_autolink_reduced_to_host(self):
        assert n("go to <https://y.io/z> now") == "go to y.io now"


# ---------------------------------------------------------------------------
# Symbol conversion — nothing voiced by its name
# ---------------------------------------------------------------------------

class TestSymbolConversion:
    def test_ampersand_between_words(self):
        assert n("AT&T") == "AT and T"

    def test_ampersand_spaced(self):
        assert n("salt & pepper") == "salt and pepper"

    def test_double_ampersand(self):
        assert n("build && test") == "build and test"

    def test_percent_with_number(self):
        assert n("50% off") == "50 percent off"

    def test_percent_with_decimal(self):
        assert n("3.5% rate") == "3.5 percent rate"

    def test_bare_percent_dropped(self):
        assert n("the % symbol") == "the symbol"

    def test_at_handle(self):
        assert n("ping @channel") == "ping at channel"

    def test_at_in_email(self):
        assert n("mail user@example.com") == "mail user at example.com"

    def test_equals(self):
        assert n("x = 5") == "x equals 5"

    def test_plus(self):
        assert n("2+2=4") == "2 plus 2 equals 4"

    def test_c_plus_plus(self):
        assert n("C++ code") == "C plus plus code"

    def test_c_sharp(self):
        assert n("C# and F#") == "C sharp and F sharp"

    def test_slash_between_numbers(self):
        assert n("open 24/7") == "open 24 7"

    def test_slash_between_words(self):
        assert n("and/or") == "and or"

    def test_bare_slash_dropped(self):
        assert n("this / that") == "this that"

    def test_path_reads_words(self):
        assert n("check /usr/local/bin please") == "check usr local bin please"

    def test_url_reduced_to_host(self):
        assert n("see https://docs.example.com/a/b?q=1 now") == "see docs.example.com now"

    def test_www_url_reduced(self):
        assert n("visit www.foo.org today") == "visit foo.org today"

    def test_arrow_to(self):
        assert n("a -> b") == "a to b"

    def test_fat_arrow_to(self):
        assert n("input => output") == "input to output"

    def test_unicode_arrow_to(self):
        assert n("Paris → Lyon") == "Paris to Lyon"

    def test_angle_brackets_dropped(self):
        assert n("use the <b>tag</b> here") == "use the b tag b here"

    def test_numeric_comparison_keeps_meaning(self):
        assert n("5 < 10 and 10 > 3") == "5 less than 10 and 10 greater than 3"

    def test_brackets_braces_dropped(self):
        assert n("list [a] and {b}") == "list a and b"

    def test_pipe_dropped(self):
        assert n("either A | B") == "either A B"

    def test_backslash_dropped(self):
        assert n("C:\\Users\\me") == "C: Users me"

    def test_caret_dropped(self):
        assert n("2^10 power") == "2 10 power"

    def test_hash_tag_reads_word(self):
        assert n("#tag") == "tag"

    def test_issue_number_reads_number(self):
        assert n("issue #42") == "issue 42"

    def test_bullet_char_dropped(self):
        assert n("a • b") == "a b"

    def test_degrees_celsius(self):
        assert n("it's 25°C out") == "it's 25 degrees Celsius out"

    def test_degrees_fahrenheit(self):
        assert n("about 78°F") == "about 78 degrees Fahrenheit"

    def test_degrees_plain(self):
        assert n("rotate 90°") == "rotate 90 degrees"

    def test_tilde_approx(self):
        assert n("~50 people") == "about 50 people"

    def test_tilde_approx_currency(self):
        assert n("costs ~$5.99") == "costs about 5 dollars 99 cents"

    def test_dollars_cents(self):
        assert n("$5.99") == "5 dollars 99 cents"

    def test_cents_only(self):
        assert n("$0.99") == "99 cents"

    def test_dollar_singular(self):
        assert n("$1") == "1 dollar"

    def test_dollars_whole(self):
        assert n("$20") == "20 dollars"

    def test_dollars_scale_word(self):
        assert n("$3.5 billion") == "3.5 billion dollars"

    def test_dollars_scale_letter(self):
        assert n("$5M") == "5 million dollars"

    def test_pounds_pence(self):
        assert n("£3.50") == "3 pounds 50 pence"

    def test_euro_singular(self):
        assert n("€1") == "1 euro"

    def test_underscore_token_reads_words(self):
        assert n("user_name") == "user name"

    def test_html_entity_nbsp_dropped(self):
        assert n("a&nbsp;b") == "a b"

    def test_html_entity_amp_reads_and(self):
        assert n("a &amp; b") == "a and b"


# ---------------------------------------------------------------------------
# MUST NOT MANGLE — legitimate speech survives untouched
# ---------------------------------------------------------------------------

class TestMustNotMangle:
    def test_contractions(self):
        s = "don't, it's, won't, y'all"
        assert n(s) == s

    def test_rock_n_roll(self):
        s = "rock 'n' roll"
        assert n(s) == s

    def test_hyphenated_words(self):
        s = "a well-known, state-of-the-art fix"
        assert n(s) == s

    def test_mid_line_hyphen_not_a_bullet(self):
        s = "check a-b-c first"
        assert n(s) == s

    def test_decimal(self):
        s = "pi is 3.14 roughly"
        assert n(s) == s

    def test_version(self):
        s = "upgrade to v4.2 now"
        assert n(s) == s

    def test_time(self):
        s = "meet at 9:30 sharp"
        assert n(s) == s

    def test_node_js(self):
        s = "Node.js is fine"
        assert n(s) == s

    def test_dot_net(self):
        s = "the .NET runtime"
        assert n(s) == s

    def test_abbreviations(self):
        s = "e.g. the U.S. market"
        assert n(s) == s

    def test_em_dash_preserved(self):
        # Em/en dashes are pause punctuation to Kokoro and Edge — they
        # are never voiced as "dash". Converting them would also break
        # the pinned strip_emote_markup contract ("Sure — done.").
        s = "Sure — done, mostly – yes"
        assert n(s) == s

    def test_ellipsis_preserved(self):
        s = "Wait… really"
        assert n(s) == s

    def test_dot_ellipsis_preserved(self):
        s = "Well... maybe"
        assert n(s) == s

    def test_plain_parenthetical_preserved(self):
        s = "That's about (sixty) euros."
        assert n(s) == s

    def test_question_exclamation_preserved(self):
        s = "Really?! Go now!"
        assert n(s) == s

    def test_quotes_preserved(self):
        s = 'he said "stop" twice'
        assert n(s) == s

    def test_non_latin_untouched(self):
        s = "Хорошо."
        assert n(s) == s

    def test_negative_number_at_line_start(self):
        # "-5" is a number, not a bullet (bullet needs a space after).
        s = "-5 degrees tonight"
        assert n(s) == s

    def test_ratio_colon(self):
        s = "a 16:9 screen"
        assert n(s) == s

    def test_spaced_thousands_left_for_normalize_numbers(self):
        # "4 000" is normalize_numbers' job (runs later in the chain)
        # — the normalizer must not join or split digit groups.
        s = "about 4 000 units"
        assert n(s) == s

    def test_empty_string(self):
        assert n("") == ""

    def test_punctuation_only_passthrough(self):
        # The letterless-guard lives in the CALLER (strip_emote_markup)
        # — the normalizer just must not invent content.
        assert n("... !!") == "... !!"

    def test_kitchen_sink(self):
        got = n("**Done.** See [docs](https://x.com/d) — 50% off & 24/7 support")
        assert got == "Done. See docs — 50 percent off and 24 7 support"


# ---------------------------------------------------------------------------
# Integration through jarvis_agent.strip_emote_markup (the actual
# tts_text_transforms entry — also covers session.say(), which the
# framework routes through the same transform chain).
# ---------------------------------------------------------------------------

import jarvis_agent  # noqa: E402


def run_transform(text_in: str):
    """Drive the async transform with a single-chunk stream; None = dropped."""
    async def _gen():
        yield text_in

    async def _collect():
        out = []
        async for chunk in jarvis_agent.strip_emote_markup(_gen()):
            out.append(chunk)
        return "".join(out) if out else None

    return asyncio.run(_collect())


class TestStripEmoteMarkupIntegration:
    def test_emote_plus_markdown(self):
        assert run_transform("*(chuckles)* **Nice.** See [this](http://x.io/p).") == \
            "Nice. See this."

    def test_bullet_list_through_chain(self):
        assert run_transform("- alpha\n- beta") == "alpha\nbeta"

    def test_symbols_through_chain(self):
        assert run_transform("R&D is 50% done -> good") == "R and D is 50 percent done to good"

    def test_letterless_after_normalize_dropped(self):
        # Symbol-only reply normalizes to nothing speakable → the guard
        # must emit NOTHING (a letterless string zero-frames Kokoro and
        # poisons the FallbackAdapter).
        assert run_transform("*** | ---") is None

    def test_code_only_reply_still_voiced(self):
        assert run_transform("```sh\necho hi\n```") == "echo hi"

    def test_while_you_were_away_summary(self):
        # The session.say() "While you were away:" digest shape.
        got = run_transform("While you were away: **2 alerts** — see [log](http://x.io/l).")
        assert got == "While you were away: 2 alerts — see log."
