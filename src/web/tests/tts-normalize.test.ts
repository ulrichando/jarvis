/**
 * Tests for src/lib/chat/tts-normalize.ts::normalizeForSpeech — the web
 * port of the voice agent's pipeline/tts_normalize.py speech normalizer.
 *
 * Problem (live, 2026-07): the web chat's voice mode voiced symbols BY
 * NAME — "asterisk asterisk" for `**`, "slash" for `/`, "ampersand" for
 * `&` — because /api/tts sent raw reply markdown to Kokoro/Edge.
 *
 * Mirrors the Python suite (tests/test_tts_normalize.py) case-for-case:
 *   - markdown flattening — every construct the normalizer claims to handle
 *   - symbol conversion — nothing voiced by its name
 *   - MUST NOT MANGLE — the adversarial block: real speech that a careless
 *     symbol strip would destroy. These are the load-bearing guarantees.
 * Plus realistic full-reply before→after assertions.
 */
import { describe, it, expect } from "vitest";
import { normalizeForSpeech as n } from "@/lib/chat/tts-normalize";

// ---------------------------------------------------------------------------
// Markdown structure flattening
// ---------------------------------------------------------------------------

describe("markdown flattening", () => {
  it("h1 header", () => {
    expect(n("# Title")).toBe("Title");
  });

  it("h6 header", () => {
    expect(n("###### Deep title")).toBe("Deep title");
  });

  it("header keeps body below", () => {
    expect(n("## Plan\nDo the thing.")).toBe("Plan\nDo the thing.");
  });

  it("bold", () => {
    expect(n("**Done.** All set.")).toBe("Done. All set.");
  });

  it("italic star", () => {
    expect(n("that was *really* fast")).toBe("that was really fast");
  });

  it("italic underscore", () => {
    expect(n("that was _really_ fast")).toBe("that was really fast");
  });

  it("bold underscore", () => {
    expect(n("__warning__ ahead")).toBe("warning ahead");
  });

  it("bold italic", () => {
    expect(n("***very*** important")).toBe("very important");
  });

  it("strikethrough keeps content", () => {
    // The model struck it for visual effect but wrote words to be read.
    expect(n("it was ~~cancelled~~ moved")).toBe("it was cancelled moved");
  });

  it("inline code keeps content", () => {
    expect(n("run `ls` now")).toBe("run ls now");
  });

  it("fenced code block dropped when prose exists", () => {
    // A code block surrounded by prose is visual noise for voice —
    // the prose explains it. Dropped entirely.
    expect(n("Run this:\n```bash\nls -la /tmp\n```\nDone.")).toBe("Run this:\nDone.");
  });

  it("fenced code block kept when it IS the reply", () => {
    // If the code block is the whole reply, the code was the answer —
    // silence would be worse than reading it.
    expect(n("```python\nprint('hi')\n```")).toBe("print('hi')");
  });

  it("unclosed fence still cleaned", () => {
    expect(n("Here:\n```js\nlet x = 1")).toBe("Here:");
  });

  it("link keeps text", () => {
    expect(n("see [the docs](https://x.com/a/b) for more")).toBe("see the docs for more");
  });

  it("image keeps alt", () => {
    expect(n("![a chart](https://x.com/c.png) shows growth")).toBe("a chart shows growth");
  });

  it("reference link keeps text", () => {
    expect(n("see [the guide][1] first")).toBe("see the guide first");
  });

  it("reference definition line dropped", () => {
    expect(n("see the guide\n[1]: https://x.com/guide")).toBe("see the guide");
  });

  it("blockquote marker dropped", () => {
    expect(n("> wisdom here")).toBe("wisdom here");
  });

  it("nested blockquote", () => {
    expect(n("> > deep quote")).toBe("deep quote");
  });

  it("unordered bullets dropped", () => {
    expect(n("- first\n* second\n+ third")).toBe("first\nsecond\nthird");
  });

  it("ordered list numbers kept", () => {
    // "1. First" reads "one, first" — fine as speech.
    expect(n("1. First\n2. Second")).toBe("1. First\n2. Second");
  });

  it("checkbox list", () => {
    expect(n("- [x] done task\n- [ ] open task")).toBe("done task\nopen task");
  });

  it("horizontal rule dropped", () => {
    expect(n("above\n---\nbelow")).toBe("above\nbelow");
  });

  it("hr variants dropped", () => {
    expect(n("a\n***\nb\n___\nc\n===\nd")).toBe("a\nb\nc\nd");
  });

  it("table cells read, pipes dropped", () => {
    expect(n("| Name | Age |\n|------|-----|\n| Bob | 42 |")).toBe("Name, Age\nBob, 42");
  });

  it("table separator with alignment dropped", () => {
    expect(n("| a | b |\n|:---|---:|\n| 1 | 2 |")).toBe("a, b\n1, 2");
  });

  it("footnote ref dropped", () => {
    expect(n("as shown[^1] earlier")).toBe("as shown earlier");
  });

  it("footnote definition keeps text", () => {
    expect(n("[^1]: the source was NASA")).toBe("the source was NASA");
  });

  it("autolink reduced to host", () => {
    expect(n("go to <https://y.io/z> now")).toBe("go to y.io now");
  });
});

// ---------------------------------------------------------------------------
// Symbol conversion — nothing voiced by its name
// ---------------------------------------------------------------------------

describe("symbol conversion", () => {
  it("ampersand between words", () => {
    expect(n("AT&T")).toBe("AT and T");
  });

  it("ampersand spaced", () => {
    expect(n("salt & pepper")).toBe("salt and pepper");
  });

  it("double ampersand", () => {
    expect(n("build && test")).toBe("build and test");
  });

  it("percent with number", () => {
    expect(n("50% off")).toBe("50 percent off");
  });

  it("percent with decimal", () => {
    expect(n("3.5% rate")).toBe("3.5 percent rate");
  });

  it("bare percent dropped", () => {
    expect(n("the % symbol")).toBe("the symbol");
  });

  it("at handle", () => {
    expect(n("ping @channel")).toBe("ping at channel");
  });

  it("at in email", () => {
    expect(n("mail user@example.com")).toBe("mail user at example.com");
  });

  it("equals", () => {
    expect(n("x = 5")).toBe("x equals 5");
  });

  it("plus", () => {
    expect(n("2+2=4")).toBe("2 plus 2 equals 4");
  });

  it("C plus plus", () => {
    expect(n("C++ code")).toBe("C plus plus code");
  });

  it("C sharp", () => {
    expect(n("C# and F#")).toBe("C sharp and F sharp");
  });

  it("slash between numbers", () => {
    expect(n("open 24/7")).toBe("open 24 7");
  });

  it("slash between words", () => {
    expect(n("and/or")).toBe("and or");
  });

  it("bare slash dropped", () => {
    expect(n("this / that")).toBe("this that");
  });

  it("path reads words", () => {
    expect(n("check /usr/local/bin please")).toBe("check usr local bin please");
  });

  it("url reduced to host", () => {
    expect(n("see https://docs.example.com/a/b?q=1 now")).toBe("see docs.example.com now");
  });

  it("www url reduced", () => {
    expect(n("visit www.foo.org today")).toBe("visit foo.org today");
  });

  it("arrow reads to", () => {
    expect(n("a -> b")).toBe("a to b");
  });

  it("fat arrow reads to", () => {
    expect(n("input => output")).toBe("input to output");
  });

  it("unicode arrow reads to", () => {
    expect(n("Paris → Lyon")).toBe("Paris to Lyon");
  });

  it("angle brackets dropped", () => {
    expect(n("use the <b>tag</b> here")).toBe("use the b tag b here");
  });

  it("numeric comparison keeps meaning", () => {
    expect(n("5 < 10 and 10 > 3")).toBe("5 less than 10 and 10 greater than 3");
  });

  it("brackets and braces dropped", () => {
    expect(n("list [a] and {b}")).toBe("list a and b");
  });

  it("pipe dropped", () => {
    expect(n("either A | B")).toBe("either A B");
  });

  it("backslash dropped", () => {
    expect(n("C:\\Users\\me")).toBe("C: Users me");
  });

  it("caret dropped", () => {
    expect(n("2^10 power")).toBe("2 10 power");
  });

  it("hash tag reads word", () => {
    expect(n("#tag")).toBe("tag");
  });

  it("issue number reads number", () => {
    expect(n("issue #42")).toBe("issue 42");
  });

  it("bullet char dropped", () => {
    expect(n("a • b")).toBe("a b");
  });

  it("degrees Celsius", () => {
    expect(n("it's 25°C out")).toBe("it's 25 degrees Celsius out");
  });

  it("degrees Fahrenheit", () => {
    expect(n("about 78°F")).toBe("about 78 degrees Fahrenheit");
  });

  it("degrees plain", () => {
    expect(n("rotate 90°")).toBe("rotate 90 degrees");
  });

  it("tilde approx", () => {
    expect(n("~50 people")).toBe("about 50 people");
  });

  it("tilde approx currency", () => {
    expect(n("costs ~$5.99")).toBe("costs about 5 dollars 99 cents");
  });

  it("dollars and cents", () => {
    expect(n("$5.99")).toBe("5 dollars 99 cents");
  });

  it("cents only", () => {
    expect(n("$0.99")).toBe("99 cents");
  });

  it("dollar singular", () => {
    expect(n("$1")).toBe("1 dollar");
  });

  it("dollars whole", () => {
    expect(n("$20")).toBe("20 dollars");
  });

  it("dollars scale word", () => {
    expect(n("$3.5 billion")).toBe("3.5 billion dollars");
  });

  it("dollars scale letter", () => {
    expect(n("$5M")).toBe("5 million dollars");
  });

  it("pounds and pence", () => {
    expect(n("£3.50")).toBe("3 pounds 50 pence");
  });

  it("euro singular", () => {
    expect(n("€1")).toBe("1 euro");
  });

  it("underscore token reads words", () => {
    expect(n("user_name")).toBe("user name");
  });

  it("html entity nbsp dropped", () => {
    expect(n("a&nbsp;b")).toBe("a b");
  });

  it("html entity amp reads and", () => {
    expect(n("a &amp; b")).toBe("a and b");
  });
});

// ---------------------------------------------------------------------------
// MUST NOT MANGLE — legitimate speech survives untouched
// ---------------------------------------------------------------------------

describe("must not mangle", () => {
  it("contractions", () => {
    const s = "don't, it's, won't, y'all";
    expect(n(s)).toBe(s);
  });

  it("rock 'n' roll", () => {
    const s = "rock 'n' roll";
    expect(n(s)).toBe(s);
  });

  it("hyphenated words", () => {
    const s = "a well-known, state-of-the-art fix";
    expect(n(s)).toBe(s);
  });

  it("mid-line hyphen is not a bullet", () => {
    const s = "check a-b-c first";
    expect(n(s)).toBe(s);
  });

  it("decimal", () => {
    const s = "pi is 3.14 roughly";
    expect(n(s)).toBe(s);
  });

  it("version", () => {
    const s = "upgrade to v4.2 now";
    expect(n(s)).toBe(s);
  });

  it("time", () => {
    const s = "meet at 9:30 sharp";
    expect(n(s)).toBe(s);
  });

  it("Node.js", () => {
    const s = "Node.js is fine";
    expect(n(s)).toBe(s);
  });

  it(".NET", () => {
    const s = "the .NET runtime";
    expect(n(s)).toBe(s);
  });

  it("abbreviations", () => {
    const s = "e.g. the U.S. market";
    expect(n(s)).toBe(s);
  });

  it("em/en dash preserved", () => {
    // Em/en dashes are pause punctuation to Kokoro and Edge — they are
    // never voiced as "dash".
    const s = "Sure — done, mostly – yes";
    expect(n(s)).toBe(s);
  });

  it("ellipsis preserved", () => {
    const s = "Wait… really";
    expect(n(s)).toBe(s);
  });

  it("dot ellipsis preserved", () => {
    const s = "Well... maybe";
    expect(n(s)).toBe(s);
  });

  it("plain parenthetical preserved", () => {
    const s = "That's about (sixty) euros.";
    expect(n(s)).toBe(s);
  });

  it("question/exclamation preserved", () => {
    const s = "Really?! Go now!";
    expect(n(s)).toBe(s);
  });

  it("quotes preserved", () => {
    const s = 'he said "stop" twice';
    expect(n(s)).toBe(s);
  });

  it("non-Latin untouched", () => {
    const s = "Хорошо.";
    expect(n(s)).toBe(s);
  });

  it("negative number at line start", () => {
    // "-5" is a number, not a bullet (bullet needs a space after).
    const s = "-5 degrees tonight";
    expect(n(s)).toBe(s);
  });

  it("ratio colon", () => {
    const s = "a 16:9 screen";
    expect(n(s)).toBe(s);
  });

  it("spaced digit groups not joined or split", () => {
    const s = "about 4 000 units";
    expect(n(s)).toBe(s);
  });

  it("empty string", () => {
    expect(n("")).toBe("");
  });

  it("punctuation-only passthrough", () => {
    // The empty/letterless guard lives in the CALLER (/api/tts falls
    // back to the original) — the normalizer must not invent content.
    expect(n("... !!")).toBe("... !!");
  });

  it("kitchen sink", () => {
    expect(n("**Done.** See [docs](https://x.com/d) — 50% off & 24/7 support")).toBe(
      "Done. See docs — 50 percent off and 24 7 support",
    );
  });
});

// ---------------------------------------------------------------------------
// Realistic replies — full before→after (the shapes the chat LLM actually
// produces, run through the same single entry point /api/tts uses)
// ---------------------------------------------------------------------------

describe("realistic replies", () => {
  it("status reply with bold + link + symbols", () => {
    expect(n("**Done.** See [docs](https://x.com/d) — 50% off & 24/7 support")).toBe(
      "Done. See docs — 50 percent off and 24 7 support",
    );
  });

  it("explanation with a fenced code block and inline code", () => {
    expect(n("Here's the fix:\n```js\nconst x = 1;\n```\nRun `npm test` after.")).toBe(
      "Here's the fix:\nRun npm test after.",
    );
  });

  it("bulleted status update", () => {
    expect(n("Status update:\n- **Build**: passing\n- Tests: 672/672 green\n- Coverage: ~85%")).toBe(
      "Status update:\nBuild: passing\nTests: 672 672 green\nCoverage: about 85 percent",
    );
  });

  it("header + table + arrow + currency", () => {
    expect(
      n("## Pricing\n| Plan | Price |\n|------|-------|\n| Pro | $5.99 |\n\nUpgrade -> save 20%."),
    ).toBe("Pricing\nPlan, Price\nPro, 5 dollars 99 cents\nUpgrade to save 20 percent.");
  });

  it("code-only reply still voiced", () => {
    expect(n("```sh\necho hi\n```")).toBe("echo hi");
  });
});
