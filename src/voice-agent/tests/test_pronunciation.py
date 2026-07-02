"""Tests for the pronunciation lexicon (pipeline/pronunciation.py).

2026-07-02 enunciation feature: per-word fixes via respelling (any
engine) or Misaki phoneme override `[word](/…/)` (Kokoro — verified
live against the container).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline import pronunciation


@pytest.fixture
def lex(tmp_path, monkeypatch):
    monkeypatch.setattr(pronunciation, "LEXICON_FILE", tmp_path / "pronunciations.json")
    pronunciation._cache["mtime"] = None
    yield tmp_path
    pronunciation._cache["mtime"] = None


class TestApply:
    def test_no_lexicon_passthrough(self, lex):
        assert pronunciation.apply("Hello Ulrich.") == "Hello Ulrich."

    def test_respelling_substitutes_any_engine(self, lex):
        pronunciation.set_word("Ulrich", "OOL-rik")
        assert pronunciation.apply("Hello Ulrich.") == "Hello OOL-rik."
        assert pronunciation.apply("Hello Ulrich.", phonemes_ok=False) == "Hello OOL-rik."

    def test_phoneme_entry_wraps_markup_for_kokoro(self, lex):
        pronunciation.set_word("Pretva", "/pɹˈɛtvə/")
        assert pronunciation.apply("Pretva is live.") == "[Pretva](/pɹˈɛtvə/) is live."

    def test_phoneme_entry_skipped_for_edge(self, lex):
        pronunciation.set_word("Pretva", "/pɹˈɛtvə/")
        assert pronunciation.apply("Pretva is live.", phonemes_ok=False) == "Pretva is live."

    def test_case_insensitive_whole_word(self, lex):
        pronunciation.set_word("Ulrich", "OOL-rik")
        assert pronunciation.apply("ULRICH said so.") == "OOL-rik said so."
        # substring must NOT match
        assert pronunciation.apply("Ulrichson said so.") == "Ulrichson said so."

    def test_hot_swap_on_file_change(self, lex):
        pronunciation.set_word("kali", "KAH-lee")
        assert pronunciation.apply("kali linux") == "KAH-lee linux"
        pronunciation.forget_word("kali")
        assert pronunciation.apply("kali linux") == "kali linux"


class TestStore:
    def test_set_and_entries(self, lex):
        pronunciation.set_word("OHADA", "oh-HAH-dah")
        assert pronunciation.entries() == {"ohada": "oh-HAH-dah"}

    def test_forget_missing_returns_false(self, lex):
        assert pronunciation.forget_word("nope") is False


class TestToolAction:
    def _call(self, **args):
        import json
        from tools.voice_style import _handle_voice_style
        return json.loads(_handle_voice_style(args))

    def test_pronounce_action(self, lex):
        out = self._call(action="pronounce", word="Ulrich", sounds_like="OOL-rik")
        assert out["pronunciations"]["ulrich"] == "OOL-rik"
        assert pronunciation.apply("Ulrich") == "OOL-rik"

    def test_pronounce_phonemes(self, lex):
        out = self._call(action="pronounce", word="Pretva", sounds_like="/pɹˈɛtvə/")
        assert out["pronunciations"]["pretva"] == "/pɹˈɛtvə/"

    def test_forget_pronunciation(self, lex):
        self._call(action="pronounce", word="kali", sounds_like="KAH-lee")
        out = self._call(action="pronounce", word="kali", sounds_like="")
        assert "kali" not in out.get("pronunciations", {})
