"""Pronunciation lexicon — per-word enunciation fixes for TTS.

Feature 2026-07-02 (user: "we need to fix jarvis enunciation").
Kokoro-82M can't be fine-tuned practically, but its G2P (Misaki)
honors inline pronunciation overrides — `[word](/phonemes/)` —
verified LIVE against the local kokoro-fastapi container (a wrong
phoneme override audibly changed the rendering; the canonical one was
byte-identical to default).

Store: `~/.jarvis/pronunciations.json` — a flat {word: replacement}
map, user- and tool-editable (`voice_style` action="pronounce"):

  - replacement wrapped in slashes ("/uːlʁɪk/") → Misaki phoneme
    override, injected as `[word](/…/)`. Kokoro-only precision.
  - any other replacement ("OOL-rik") → plain respelling substitution;
    works on EVERY engine (Kokoro + EdgeTTS).

Applied INSIDE the TTS providers on the synthesis payload — after all
transforms, invisible to the transcript/chat history (markup in the
committed text would teach the LLM to mimic it; live lesson from the
2026-07-01 emote incident). Whole-word, case-insensitive, mtime-cached
read so edits hot-swap without a restart.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

LEXICON_FILE: Path = Path.home() / ".jarvis" / "pronunciations.json"

_cache: dict = {"mtime": None, "lexicon": {}, "regex": None}


def _load() -> tuple[dict, re.Pattern | None]:
    try:
        mtime = LEXICON_FILE.stat().st_mtime
    except OSError:
        return {}, None
    if _cache["mtime"] == mtime:
        return _cache["lexicon"], _cache["regex"]
    try:
        raw = json.loads(LEXICON_FILE.read_text(encoding="utf-8"))
        lexicon = {
            str(k).strip().lower(): str(v).strip()
            for k, v in raw.items()
            if str(k).strip() and str(v).strip()
        }
    except Exception:
        return {}, None
    regex = None
    if lexicon:
        alts = "|".join(re.escape(w) for w in sorted(lexicon, key=len, reverse=True))
        regex = re.compile(rf"\b({alts})\b", re.IGNORECASE)
    _cache.update(mtime=mtime, lexicon=lexicon, regex=regex)
    return lexicon, regex


def apply(text: str, phonemes_ok: bool = True) -> str:
    """Substitute lexicon words in `text` for synthesis.

    phonemes_ok=True (Kokoro/Misaki): slash-wrapped entries become
    `[word](/…/)` inline overrides; respellings substitute directly.
    phonemes_ok=False (EdgeTTS etc.): phoneme entries are SKIPPED
    (Edge would read the IPA aloud); respellings still substitute.
    """
    if not text:
        return text
    lexicon, regex = _load()
    if not regex:
        return text

    def _sub(m: re.Match) -> str:
        word = m.group(0)
        repl = lexicon[word.lower()]
        if repl.startswith("/") and repl.endswith("/") and len(repl) > 2:
            if not phonemes_ok:
                return word
            return f"[{word}]({repl})"
        return repl

    return regex.sub(_sub, text)


def set_word(word: str, replacement: str) -> dict:
    """Add/update one lexicon entry; returns the live lexicon."""
    lexicon, _ = _load()
    lexicon = dict(lexicon)
    lexicon[word.strip().lower()] = replacement.strip()
    _write(lexicon)
    return lexicon


def forget_word(word: str) -> bool:
    """Remove an entry; True if it existed."""
    lexicon, _ = _load()
    lexicon = dict(lexicon)
    existed = lexicon.pop(word.strip().lower(), None) is not None
    if existed:
        _write(lexicon)
    return existed


def entries() -> dict:
    return dict(_load()[0])


def _write(lexicon: dict) -> None:
    LEXICON_FILE.parent.mkdir(parents=True, exist_ok=True)
    LEXICON_FILE.write_text(
        json.dumps(lexicon, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _cache["mtime"] = None  # force reload next apply()
