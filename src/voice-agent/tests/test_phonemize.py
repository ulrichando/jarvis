from lipsync.phonemize import text_to_visemes


def test_known_word_produces_visemes():
    vis = text_to_visemes("hello")
    assert isinstance(vis, list)
    assert len(vis) >= 3
    assert all(isinstance(v, str) for v in vis)


def test_silence_between_words():
    vis = text_to_visemes("hi there")
    assert "sil" in vis           # word boundary inserts a brief closure
    assert vis[0] != "sil"        # but never leading
    assert vis[-1] != "sil"       # nor trailing


def test_empty_text_is_empty():
    assert text_to_visemes("") == []
    assert text_to_visemes("   ") == []


def test_out_of_vocabulary_word_still_returns_visemes():
    # gibberish isn't in CMU dict -> letter fallback, must not crash/empty.
    vis = text_to_visemes("zxqwbf")
    assert len(vis) >= 1


def test_punctuation_and_case_ignored():
    a = text_to_visemes("Hello, World!")
    b = text_to_visemes("hello world")
    assert a == b
