"""canned_phrases — loader returns None for missing/empty/unknown,
returns bytes when the file exists and is non-empty."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import canned_phrases


def test_unknown_phrase_returns_none():
    assert canned_phrases.get_phrase_bytes("nonexistent_phrase_xyz") is None


def test_is_available_false_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(canned_phrases, "CACHE_DIR", tmp_path)
    assert canned_phrases.is_available("one_second") is False


def test_is_available_true_when_file_present_and_nonempty(tmp_path, monkeypatch):
    monkeypatch.setattr(canned_phrases, "CACHE_DIR", tmp_path)
    (tmp_path / "one_second.wav").write_bytes(b"RIFF" + b"\x00" * 100)
    assert canned_phrases.is_available("one_second") is True


def test_empty_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(canned_phrases, "CACHE_DIR", tmp_path)
    (tmp_path / "one_second.wav").write_bytes(b"")
    assert canned_phrases.get_phrase_bytes("one_second") is None


def test_existing_file_returns_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(canned_phrases, "CACHE_DIR", tmp_path)
    payload = b"RIFF" + b"\x00" * 200
    (tmp_path / "try_again.wav").write_bytes(payload)
    assert canned_phrases.get_phrase_bytes("try_again") == payload
