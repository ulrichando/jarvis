"""JARVIS Speech Composer — turn text into living speech.

Raw text is dead. Spoken language has rhythm, pauses, emphasis, breathing.
This module transforms JARVIS's text responses into SSML (Speech Synthesis
Markup Language) that edge-tts can render with natural prosody.

A good speaker knows:
- WHEN to pause (after a thought, before a key point, between topics)
- HOW LONG to pause (comma = short breath, period = full beat, "..." = dramatic)
- WHAT to emphasize (the answer, not the filler)
- HOW FAST to speak (faster for casual, slower for important)

JARVIS learns to speak like someone who actually thinks about what he's saying.
"""

import re


# ── Pause durations (milliseconds) ──────────────────────────────────

PAUSE_COMMA = 250          # Breath pause — between clauses
PAUSE_PERIOD = 450         # Full stop — end of thought
PAUSE_PARAGRAPH = 700      # Between topics/sections
PAUSE_DRAMATIC = 900       # Ellipsis, dash interrupts, emphasis lead-in
PAUSE_COLON = 350          # Before an explanation or list
PAUSE_QUESTION = 400       # After a question — let it land
PAUSE_EXCLAIM = 300        # After exclamation — energy beat
PAUSE_BREATH = 200         # Micro-pause — natural breathing rhythm


def compose_ssml(text: str, voice_style: str = "default") -> str:
    """Transform plain text into SSML with natural speech prosody.

    This is the main entry point. Takes JARVIS's raw text response
    and returns SSML that edge-tts will render with human-like pacing.

    Args:
        text: Raw response text from JARVIS
        voice_style: One of "default", "focused", "gentle", "thoughtful", "urgent"

    Returns:
        SSML string ready for edge-tts
    """
    # Clean the text first — remove stuff that shouldn't be spoken
    text = _clean_for_speech(text)
    if not text.strip():
        return ""

    # Split into speakable chunks (sentences/phrases)
    chunks = _split_into_chunks(text)
    if not chunks:
        return ""

    # Get prosody settings for this style
    rate, pitch, volume = _get_prosody(voice_style)

    # Build SSML
    ssml_parts = [f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">']
    ssml_parts.append(f'<prosody rate="{rate}" pitch="{pitch}" volume="{volume}">')

    for i, chunk in enumerate(chunks):
        # Apply emphasis to key words in this chunk
        processed = _apply_emphasis(chunk.text)

        # Add the spoken text
        ssml_parts.append(processed)

        # Add appropriate pause after this chunk
        if chunk.pause_after > 0:
            ssml_parts.append(f'<break time="{chunk.pause_after}ms"/>')

    ssml_parts.append('</prosody>')
    ssml_parts.append('</speak>')

    return "".join(ssml_parts)


def compose_chunks(text: str, voice_style: str = "default") -> list[dict]:
    """Split text into spoken chunks with pause metadata.

    Returns a list of dicts, each containing:
    - "text": the text to speak
    - "ssml": SSML version of that text
    - "pause_after_ms": how long to pause after this chunk
    - "is_important": whether this chunk contains key info

    The frontend uses this to play chunks sequentially with
    natural silence between them.
    """
    text = _clean_for_speech(text)
    if not text.strip():
        return []

    chunks = _split_into_chunks(text)
    rate, pitch, volume = _get_prosody(voice_style)

    result = []
    for chunk in chunks:
        processed = _apply_emphasis(chunk.text)
        ssml = (
            f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
            f'<prosody rate="{rate}" pitch="{pitch}" volume="{volume}">'
            f'{processed}'
            f'</prosody></speak>'
        )
        result.append({
            "text": chunk.text,
            "ssml": ssml,
            "pause_after_ms": chunk.pause_after,
            "is_important": chunk.important,
        })

    return result


# ── Internal types ──────────────────────────────────────────────────

class SpeechChunk:
    """A piece of text with speech metadata."""
    def __init__(self, text: str, pause_after: int = PAUSE_PERIOD, important: bool = False):
        self.text = text.strip()
        self.pause_after = pause_after
        self.important = important


# ── Text cleaning ───────────────────────────────────────────────────

def _clean_for_speech(text: str) -> str:
    """Remove anything that shouldn't be spoken aloud."""
    # Strip JARVIS command tags
    text = re.sub(r'\[show:\w+\]', '', text)
    text = re.sub(r'\[/show\]', '', text)
    text = re.sub(r'\[run:.*?\]', '', text)
    text = re.sub(r'\[display:\w+\]', '', text)

    # Strip code blocks and inline code
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`[^`]+`', '', text)

    # Strip URLs
    text = re.sub(r'https?://\S+', '', text)

    # Strip file paths (but not normal words with slashes)
    text = re.sub(r'(?<!\w)/[\w/.\-]+', '', text)

    # Strip CLI flags
    text = re.sub(r'\s--?\w[\w-]*', '', text)

    # Collapse whitespace
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text)

    return text.strip()


# ── Chunking — where to breathe ─────────────────────────────────────

def _split_into_chunks(text: str) -> list[SpeechChunk]:
    """Split text into natural speech chunks.

    The key insight: humans don't speak in sentences. They speak in
    BREATH GROUPS — phrases separated by natural pause points.

    Pause hierarchy:
    1. Paragraph breaks → long pause (topic change)
    2. Sentences ending with . → full beat
    3. Sentences ending with ? → question beat (slightly longer)
    4. Sentences ending with ! → energy beat
    5. Semicolons, colons → mid pause
    6. Commas in long sentences → breath pause
    7. Ellipsis (...) → dramatic pause
    8. Dashes (—, --) → thought interrupt pause
    """
    chunks = []

    # First split by paragraph-level breaks
    paragraphs = re.split(r'\n\s*\n|\. (?=[A-Z])', text)
    # The regex above splits on double newlines OR period-space-capital (new sentence)
    # But we want sentence-level splitting, so let's do it properly

    # Actually, split the whole text into sentences first
    sentences = _split_sentences(text)

    for i, sentence in enumerate(sentences):
        sentence = sentence.strip()
        if not sentence:
            continue

        # Determine the pause after this sentence
        pause = _determine_pause(sentence, i, len(sentences))

        # Determine if this is "important" (contains the actual answer)
        important = _is_important(sentence, i, len(sentences))

        # If the sentence is long, split at natural breath points
        if len(sentence) > 80:
            sub_chunks = _split_at_breath_points(sentence)
            for j, sub in enumerate(sub_chunks):
                is_last = (j == len(sub_chunks) - 1)
                sub_pause = pause if is_last else PAUSE_COMMA
                chunks.append(SpeechChunk(sub, sub_pause, important and is_last))
        else:
            chunks.append(SpeechChunk(sentence, pause, important))

    return chunks


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, respecting abbreviations and edge cases."""
    # Handle ellipsis first — preserve them
    text = text.replace("...", "⟨ELLIPSIS⟩")

    # Split on sentence-ending punctuation followed by space or end
    # But not on Mr. Mrs. Dr. etc.
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z"])|(?<=[.!?])$', text)

    # Restore ellipsis
    return [p.replace("⟨ELLIPSIS⟩", "...").strip() for p in parts if p.strip()]


def _split_at_breath_points(sentence: str) -> list[str]:
    """Split a long sentence at natural breathing points.

    Breath points: commas, semicolons, colons, dashes, "and", "but", "or"
    But only if the resulting phrases are long enough to be natural.
    """
    # Split at commas, semicolons, colons, and coordinating conjunctions
    parts = re.split(r'(?<=[,;:])\s+|(?<=\s)(?:—|--)\s*|\s+(?=(?:and|but|or|so|yet|because|however|then)\s)', sentence)

    # Merge very short fragments back together (< 15 chars is too short to be its own chunk)
    merged = []
    buffer = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if buffer and len(buffer) + len(part) < 40:
            buffer += " " + part
        elif buffer:
            merged.append(buffer)
            buffer = part
        else:
            buffer = part

    if buffer:
        merged.append(buffer)

    return merged if merged else [sentence]


def _determine_pause(sentence: str, index: int, total: int) -> int:
    """Determine how long to pause after this sentence.

    Adds ±12% Gaussian jitter so no two pauses sound identical.
    """
    s = sentence.strip()

    # Last sentence — no trailing pause needed
    if index == total - 1:
        return 0

    if s.endswith("..."):
        base = PAUSE_DRAMATIC
    elif s.endswith("?"):
        base = PAUSE_QUESTION
    elif s.endswith("!"):
        base = PAUSE_EXCLAIM
    elif s.endswith(":"):
        base = PAUSE_COLON
    elif s.endswith("—") or s.endswith("--"):
        base = PAUSE_DRAMATIC
    elif s.endswith("."):
        base = PAUSE_PERIOD
    else:
        base = PAUSE_PERIOD

    # Natural jitter — ±12% so no two pauses sound identical
    jitter = _random.gauss(1.0, 0.08)
    return max(50, int(base * jitter))


def _is_important(sentence: str, index: int, total: int) -> bool:
    """Detect if this sentence contains the key answer/action.

    The first sentence of a response is usually the most important.
    Sentences with action words or direct answers are important.
    """
    s = sentence.lower()

    # First sentence is usually the answer
    if index == 0:
        return True

    # Action indicators
    if any(w in s for w in ["done", "got it", "here", "found", "fixed", "running",
                             "created", "deleted", "installed", "yes", "no"]):
        return True

    # Warning indicators
    if any(w in s for w in ["warning", "careful", "danger", "risk", "heads up"]):
        return True

    return False


# ── Emphasis — what to stress ───────────────────────────────────────

def _apply_emphasis(text: str) -> str:
    """Apply SSML emphasis to key words in the text.

    Doesn't over-emphasize — just the words that carry the meaning.
    A good speaker emphasizes sparingly.
    """
    # Emphasize words in ALL CAPS (JARVIS sometimes does this for emphasis)
    def _caps_emphasis(match):
        word = match.group(0)
        # Skip common acronyms
        if word in ("I", "OK", "IP", "OS", "ID", "CLI", "API", "URL", "CPU",
                     "GPU", "RAM", "SSH", "SQL", "DNS", "TLS", "SSL", "HTTP",
                     "HTTPS", "HTML", "CSS", "JSON", "XML", "YAML", "SSML",
                     "TCP", "UDP", "FTP", "SMTP", "IMAP", "POP", "USB", "HDMI"):
            return word
        if len(word) >= 2 and word.isupper() and word.isalpha():
            return f'<emphasis level="strong">{word.capitalize()}</emphasis>'
        return word

    text = re.sub(r'\b[A-Z]{2,}\b', _caps_emphasis, text)

    # Emphasize words after "not" or "never" (the negated thing is important)
    text = re.sub(
        r'\b(not|never|don\'t|can\'t|won\'t|shouldn\'t)\s+(\w+)',
        lambda m: f'{m.group(1)} <emphasis level="moderate">{m.group(2)}</emphasis>',
        text,
        flags=re.IGNORECASE,
    )

    return text


# ── Prosody styles ──────────────────────────────────────────────────

import random as _random

# Base prosody values per style (rate_pct, pitch_hz, volume_pct)
_PROSODY_BASE: dict[str, tuple[int, int, int]] = {
    "default":    (0,    0,   0),
    "focused":    (5,    0,   5),
    "gentle":     (-8,  -5,  -5),
    "thoughtful": (-5,   0,   0),
    "urgent":     (12,  10,  10),
    "matching":   (3,    5,   5),
    "receptive":  (-3,   0,   0),
    "empathetic": (-5,  -3,  -3),
}

def _get_prosody(style: str) -> tuple[str, str, str]:
    """Get rate, pitch, volume for a voice style with slight natural variation.

    Each call adds small Gaussian noise so the voice never sounds mechanical
    or like the same pattern repeating.  Variation is bounded so it stays
    intelligible and pleasant.
    """
    base_rate, base_pitch, base_vol = _PROSODY_BASE.get(style, _PROSODY_BASE["default"])

    # Add Gaussian noise: small σ so variation is subtle
    rate  = base_rate  + int(_random.gauss(0, 1.2))
    pitch = base_pitch + int(_random.gauss(0, 1.0))
    vol   = base_vol   + int(_random.gauss(0, 0.8))

    # Clamp to reasonable bounds
    rate  = max(-15, min(rate,  20))
    pitch = max(-10, min(pitch, 15))
    vol   = max(-10, min(vol,   15))

    rate_str  = f"{rate:+d}%" if rate != 0 else "0%"
    pitch_str = f"{pitch:+d}Hz"
    vol_str   = f"{vol:+d}%"  if vol  != 0 else "+0%"

    return rate_str, pitch_str, vol_str


# ── Convenience ─────────────────────────────────────────────────────

def plain_with_pauses(text: str) -> list[dict]:
    """For TTS engines that don't support SSML (like piper-tts).

    Returns a list of plain text chunks with pause durations.
    The caller plays each chunk, then waits the specified time.
    """
    text = _clean_for_speech(text)
    if not text.strip():
        return []

    chunks = _split_into_chunks(text)
    return [
        {"text": c.text, "pause_after_ms": c.pause_after}
        for c in chunks if c.text
    ]
