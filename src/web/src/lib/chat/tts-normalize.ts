/**
 * Deterministic speech normalizer for TTS text — TypeScript port of the
 * voice agent's `src/voice-agent/pipeline/tts_normalize.py::normalize_for_speech`.
 *
 * Problem (live, 2026-07): the web chat's voice mode voices symbols BY
 * NAME — the user literally hears "asterisk asterisk" for `**`, "slash"
 * for `/`, "ampersand" for `&`, "bracket", "greater than", etc. The
 * /api/tts route did no markdown stripping, so raw reply markdown went
 * to Kokoro/Edge verbatim.
 *
 * `normalizeForSpeech(text)` is the single shared answer: a pure,
 * synchronous, regex-only function (no I/O, no state, no deps) that
 *
 *   1. flattens markdown STRUCTURE — headers, emphasis, strikethrough,
 *      inline/fenced code, links/images/reference links, blockquotes,
 *      bullets, horizontal rules, tables, footnotes;
 *   2. converts standalone symbols into words where that reads naturally
 *      (`&`→"and", `50%`→"50 percent", `+`→"plus", `=`→"equals",
 *      `->`→"to", `$5.99`→"5 dollars 99 cents", `@`→"at") and drops the
 *      purely visual ones (`* _ \` # ~ | \ ^ < > [ ] { } • / ° $`…)
 *      to a space;
 *   3. NEVER touches legitimate speech: contractions (`don't`),
 *      hyphenated words (`well-known`), decimals/versions/times
 *      (`3.14`, `v4.2`, `9:30`), dotted tokens (`Node.js`, `e.g.`,
 *      `.NET`), em/en dashes (pause punctuation to both Kokoro and
 *      Edge), and ellipses (`…`/`...` are natural pauses).
 *
 * May return "" — the CALLER must guard empties (never hand an empty
 * or letterless string to a TTS engine; fall back to the original).
 *
 * Deliberate choices (mirrors the Python; each is cheap to revisit):
 *   - Fenced code blocks are DROPPED when prose surrounds them; if the
 *     code block was the ENTIRE reply, its inner text is kept instead
 *     (the code IS the answer; silence would be worse).
 *   - Strikethrough KEEPS its content (`~~x~~`→"x") — struck for visual
 *     effect, but the words were written to be read.
 *   - Ordered-list numbers are KEPT ("1. First" reads fine); unordered
 *     markers are dropped.
 *   - Currency is kept simple: "$5.99"→"5 dollars 99 cents",
 *     "$0.99"→"99 cents", "$5M"→"5 million dollars"; £→pounds/pence,
 *     €→euros/cents. No sub-cent, no exotic codes.
 *   - URLs are reduced to their host ("https://docs.x.com/a/b" →
 *     "docs.x.com"); emails read naturally via the `@`→"at" rule.
 *   - `24/7`→"24 7", `and/or`→"and or": every `/` becomes a space after
 *     link/URL handling — a voiced "slash" is never right.
 *
 * Port note: JS `\w`/`\b` are ASCII-only where Python's are Unicode-aware.
 * That only affects emphasis/boundary edge cases around non-Latin word
 * chars (e.g. `_слово_`), not any rule here; the speakable guard uses
 * `\p{L}\p{N}` so non-Latin text still counts as speech.
 */

// Any letter or digit, any script — the letterless/speakable check.
// (Python: [^\W_] with re.UNICODE.)  No `g` flag: used with .test().
const SPEAKABLE_RE = /[\p{L}\p{N}]/u

// ---------------------------------------------------------------------------
// Block level
// ---------------------------------------------------------------------------

// ```lang\n ... \n```  (closing fence optional at end-of-text so a
// stream truncated mid-block still gets cleaned). [\s\S] = Python DOTALL;
// `$` without `m` = Python \Z (absolute end).
const FENCED_BLOCK_RE = /(?:```|~~~)[^\n]*\n([\s\S]*?)(?:```+|~~~+|$)/g

// A line that is only a horizontal rule: ---, ***, ___, === (3+, spaces ok).
const HR_LINE_RE = /^[ \t]*([-*_=])(?:[ \t]*\1){2,}[ \t]*$/

// A table separator row: only pipes/dashes/colons/spaces, with >=1 pipe.
const TABLE_SEP_RE = /^[ \t]*(?=[^\n]*\|)[ \t:|-]+$/

// Reference-link definition line: "[ref]: https://url  (optional "title")"
// — the whole line is plumbing, drop it. Requires the value to be a
// single token so prose like "[1]: see the appendix" survives.
const REF_DEF_RE = /^[ \t]*\[[^\]^][^\]]*\]:[ \t]+\S+[ \t]*(?:"[^"]*")?[ \t]*$/

const BLOCKQUOTE_RE = /^[ \t]*(?:>[ \t]?)+/
const HEADER_RE = /^[ \t]{0,3}#{1,6}[ \t]+/
const BULLET_RE = /^[ \t]*[-*+][ \t]+/ // needs the space: "-5" is a number
const CHECKBOX_RE = /^\[[ xX]\][ \t]+/ // after bullet strip: "- [x] task"
const FOOTNOTE_DEF_RE = /^[ \t]*\[\^[^\]]+\]:[ \t]*/ // keep the definition TEXT

// ---------------------------------------------------------------------------
// Inline markdown
// ---------------------------------------------------------------------------

const FOOTNOTE_REF_RE = /\[\^[^\]]+\]/g
const IMAGE_RE = /!\[([^\]]*)\]\([^)]*\)/g // ![alt](url) -> alt
const LINK_RE = /\[([^\]]*)\]\(([^)]*)\)/g // [text](url) -> text
const REF_LINK_RE = /\[([^\]]+)\]\[[^\]]*\]/g // [text][ref] -> text
const AUTOLINK_RE = /<(https?:\/\/[^\s>]+)>/g // <url> -> url (host rule next)

const BOLD_ITALIC_RE = /\*{3}(?!\s)([^*\n]+?)(?<!\s)\*{3}/g
const BOLD_RE = /(?<![\w*])\*\*(?!\s)([^*\n]+?)(?<!\s)\*\*(?![\w*])/g
const ITALIC_RE = /(?<![\w*])\*(?!\s|\*)([^*\n]+?)(?<!\s)\*(?![\w*])/g
const BOLD_U_RE = /(?<!\w)__(?!\s)([^_\n]+?)(?<!\s)__(?!\w)/g
const ITALIC_U_RE = /(?<!\w)_(?!\s|_)([^_\n]+?)(?<!\s)_(?!\w)/g
const STRIKE_RE = /~~(?!\s)([^~\n]+?)(?<!\s)~~/g // KEEP content
const INLINE_CODE_RE = /`{1,2}([^`\n]+?)`{1,2}/g

// ---------------------------------------------------------------------------
// Symbols
// ---------------------------------------------------------------------------

const URL_RE = /\b(?:https?:\/\/|www\.)[^\s)\]}>'",;]+/gi

const C_SHARP_RE = /\b([A-G])#(?![\w#])/g // C# / F# / A# -> "C sharp"

const DEG_C_RE = /(?<=\d)\s*°[ \t]*C\b/g
const DEG_F_RE = /(?<=\d)\s*°[ \t]*F\b/g
const DEG_RE = /(?<=\d)\s*°/g

const APPROX_RE = /(?<![\w~])~(?=[$£€]?\d)/g // ~50 / ~$5 -> "about ..."

const CUR_UNITS: Record<string, [string, string]> = {
  $: ['dollar', 'cent'],
  '£': ['pound', 'pence'],
  '€': ['euro', 'cent'],
}
const CUR_SCALE_RE = /([$£€])\s?(\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion|trillion|[KMB])\b/g
const CUR_SCALE_WORDS: Record<string, string> = { K: 'thousand', M: 'million', B: 'billion' }
const CUR_CENTS_RE = /([$£€])(\d[\d,]*)\.(\d{2})(?!\d)/g
const CUR_PLAIN_RE = /([$£€])(\d[\d,]*(?:\.\d+)?)/g

const PERCENT_RE = /(\d+(?:[.,]\d+)*)\s*%/g

// Arrows first — they'd otherwise decompose into "- >" / "= >" pieces.
const ARROW_RE = /<->|-->|->|==>|=>|→|⇒|➔|➜|⟶/g

const LT_NUM_RE = /(?<=[\d\s])<(?=\s*\d)/g // "5 < 10" keeps its meaning
const GT_NUM_RE = /(?<=[\d\s])>(?=\s*\d)/g

// Purely visual leftovers -> space. Deliberately NOT here: em/en dashes,
// ellipsis, parentheses, quotes, apostrophes, hyphen, period, colon,
// comma — all legitimate speech or natural TTS pauses.
const RESIDUE_RE = /[*_`#~|\\^<>{}[\]•·◦/%°$£€§¤]/g

const HTML_ENTITY_RE = /&(?:nbsp|lt|gt|quot|#\d+);/g
const EQUALS_RE = /={1,3}/g

// Cleanup
const MULTI_SPACE_RE = /[ \t]{2,}/g
const SPACE_BEFORE_PUNCT_RE = /[ \t]+([.,;:!?])(?=\s|$)/g
const MULTI_COMMA_RE = /,(?:[ \t]*,)+/g
const BLANK_LINES_RE = /\n[ \t]*\n+/g
const EDGE_SPACE_RE = /^[ \t]+|[ \t]+$/gm

function currencyScale(_m: string, sym: string, amount: string, scale: string): string {
  const word = CUR_SCALE_WORDS[scale] ?? scale
  return `${amount} ${word} ${CUR_UNITS[sym][0]}s`
}

function currencyCents(_m: string, sym: string, whole: string, cents: string): string {
  const [unit, sub] = CUR_UNITS[sym]
  const centsNum = parseInt(cents, 10)
  const centsPart = `${centsNum} ${sub}` + (sub !== 'pence' && centsNum !== 1 ? 's' : '')
  if (whole === '0' || whole === '00') return centsPart
  const wholePart = `${whole} ${unit}` + (whole === '1' ? '' : 's')
  if (centsNum === 0) return wholePart
  return `${wholePart} ${centsPart}`
}

function currencyPlain(_m: string, sym: string, amount: string): string {
  const unit = CUR_UNITS[sym][0]
  return `${amount} ${unit}` + (amount === '1' ? '' : 's')
}

function urlToHost(url: string): string {
  let host = url.replace(/^https?:\/\//i, '')
  host = host.split('/', 1)[0].split('?', 1)[0].split('#', 1)[0]
  const atParts = host.split('@') // drop creds
  host = atParts[atParts.length - 1].split(':', 1)[0] // drop port
  if (host.toLowerCase().startsWith('www.') && host.length > 4) host = host.slice(4)
  return host.replace(/\.+$/, '')
}

/** Flatten one line of markdown structure. null = drop the line. */
function processLine(line: string): string | null {
  if (HR_LINE_RE.test(line)) return null
  if (TABLE_SEP_RE.test(line)) return null
  if (REF_DEF_RE.test(line)) return null
  line = line.replace(BLOCKQUOTE_RE, '')
  line = line.replace(HEADER_RE, '')
  line = line.replace(BULLET_RE, '')
  line = line.replace(CHECKBOX_RE, '')
  line = line.replace(FOOTNOTE_DEF_RE, '')
  if ((line.match(/\|/g)?.length ?? 0) >= 2) {
    // Table row: read the cells as words with a comma-pause between.
    const cells = line
      .split('|')
      .map((c) => c.trim())
      .filter(Boolean)
    line = cells.join(', ')
  }
  return line
}

function normalize(text: string, keepCode: boolean): string {
  // 1. Fenced code blocks — drop (or unwrap when the block IS the reply).
  if (keepCode) {
    text = text.replace(FENCED_BLOCK_RE, (_m, inner: string) => ` ${inner} `)
  } else {
    text = text.replace(FENCED_BLOCK_RE, ' ')
  }

  // 2. Line-level structure.
  const kept: string[] = []
  for (const ln of text.split(/\r\n|\r|\n/)) {
    const processed = processLine(ln)
    if (processed !== null) kept.push(processed)
  }
  text = kept.join('\n')

  // 3. Inline markdown (order matters: images before links; paired
  // emphasis before the residue pass eats the markers).
  text = text.replace(FOOTNOTE_REF_RE, '')
  text = text.replace(IMAGE_RE, '$1')
  text = text.replace(LINK_RE, '$1')
  text = text.replace(REF_LINK_RE, '$1')
  text = text.replace(AUTOLINK_RE, ' $1 ')
  text = text.replace(BOLD_ITALIC_RE, '$1')
  text = text.replace(BOLD_RE, '$1')
  text = text.replace(ITALIC_RE, '$1')
  text = text.replace(BOLD_U_RE, '$1')
  text = text.replace(ITALIC_U_RE, '$1')
  text = text.replace(STRIKE_RE, '$1')
  text = text.replace(INLINE_CODE_RE, '$1')

  // 4. Symbol conversion — words where natural, space where visual.
  text = text.replace(URL_RE, urlToHost)
  text = text.replace(C_SHARP_RE, '$1 sharp')
  text = text.replace(DEG_C_RE, ' degrees Celsius')
  text = text.replace(DEG_F_RE, ' degrees Fahrenheit')
  text = text.replace(DEG_RE, ' degrees')
  text = text.replace(APPROX_RE, 'about ')
  text = text.replace(CUR_SCALE_RE, currencyScale)
  text = text.replace(CUR_CENTS_RE, currencyCents)
  text = text.replace(CUR_PLAIN_RE, currencyPlain)
  text = text.replace(PERCENT_RE, '$1 percent')
  text = text.replace(ARROW_RE, ' to ')
  text = text.replaceAll('&amp;', ' and ')
  text = text.replace(HTML_ENTITY_RE, ' ') // stray HTML entities
  text = text.replaceAll('&&', ' and ').replaceAll('&', ' and ')
  text = text.replace(LT_NUM_RE, ' less than ')
  text = text.replace(GT_NUM_RE, ' greater than ')
  text = text.replace(EQUALS_RE, ' equals ')
  text = text.replaceAll('+', ' plus ')
  text = text.replaceAll('@', ' at ')
  text = text.replace(RESIDUE_RE, ' ')

  // 5. Whitespace / punctuation cleanup.
  text = text.replace(MULTI_SPACE_RE, ' ')
  text = text.replace(SPACE_BEFORE_PUNCT_RE, '$1')
  text = text.replace(MULTI_COMMA_RE, ',')
  text = text.replace(BLANK_LINES_RE, '\n')
  text = text.replace(EDGE_SPACE_RE, '')
  return text.trim()
}

/**
 * Return `text` rewritten so a TTS engine voices no symbol by name.
 *
 * Pure + deterministic + cheap (regexes over an already-buffered reply).
 * May return "" — the CALLER must guard empties (fall back to the
 * original text rather than send nothing to the TTS engine).
 */
export function normalizeForSpeech(text: string): string {
  if (!text) return ''
  let out = normalize(text, false)
  if (!SPEAKABLE_RE.test(out) && SPEAKABLE_RE.test(text)) {
    // Everything speakable lived inside a fenced code block — the
    // code WAS the answer. Keep its inner text rather than go silent.
    out = normalize(text, true)
  }
  return out
}
