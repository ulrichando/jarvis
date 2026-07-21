"""Directedness gate (voice-setup todo #3) — the fused score that replaces the
binary ambient time-window, plus its wiring into _addressing_decision /
_is_unaddressed_ambient (shadow-default; JARVIS_DIR_GATE=1 to act)."""
import time

from pipeline import directedness as d
import jarvis_agent as ja


# ---- module: detection + score ----------------------------------------------

def test_command_and_question_detection():
    for t in ("what time is it?", "how do I do this", "open the browser",
              "mute yourself", "play some music", "is it raining"):
        assert d.looks_like_command_or_question(t), t
    for t in ("yeah okay", "mm hmm", "the weather is nice today", "cool thanks"):
        assert not d.looks_like_command_or_question(t), t


def test_score_directed_command_cold_scores_high():
    # A command with NO recent interaction must clear the default 0.45 bar —
    # this is the exact case the legacy time-window wrongly dropped.
    s, feats = d.score("open the browser and search for flights", recency_elapsed_s=9999)
    assert s >= d.threshold(), feats


def test_score_short_fragment_cold_scores_low():
    s, feats = d.score("yeah", recency_elapsed_s=9999)
    assert s < d.threshold(), feats


def test_recency_boosts_score():
    lo, _ = d.score("the movie was really good", recency_elapsed_s=9999)
    hi, _ = d.score("the movie was really good", recency_elapsed_s=1)
    assert hi > lo


def test_score_always_in_unit_range():
    for t, e in [("", 0), ("x", 5), ("open the door now please my friend", 10)]:
        s, _ = d.score(t, e)
        assert 0.0 <= s <= 1.0, (t, s)


def test_threshold_and_weights_env_tunable(monkeypatch):
    assert d.threshold() == 0.45
    monkeypatch.setenv("JARVIS_DIR_THRESHOLD", "0.6")
    assert d.threshold() == 0.6
    monkeypatch.setenv("JARVIS_DIR_THRESHOLD", "junk")
    assert d.threshold() == 0.45  # malformed -> default
    # zero the recency weight: a recent fragment no longer coasts on recency
    monkeypatch.setenv("JARVIS_DIR_W_RECENCY", "0")
    s, _ = d.score("yeah", recency_elapsed_s=1)
    assert s < 0.45


def test_acting_default_off(monkeypatch):
    monkeypatch.delenv("JARVIS_DIR_GATE", raising=False)
    assert d.acting() is False
    monkeypatch.setenv("JARVIS_DIR_GATE", "1")
    assert d.acting() is True


# ---- integration: gate wiring ------------------------------------------------

def _cold():
    """No recent interaction — the legacy window would drop a non-vocative."""
    ja._last_real_interaction = time.monotonic() - 100000


def test_hard_accept_in_both_modes():
    _cold()
    for mode in (True, False):
        drop, dbg = ja._addressing_decision("jarvis what time is it", use_directedness=mode)
        assert drop is False and dbg["reason"] == "hard_accept", mode


def test_flip_accepts_cold_command_that_window_drops(monkeypatch):
    # THE FIX: a directed command with no recent interaction.
    _cold()
    monkeypatch.delenv("JARVIS_DIR_GATE", raising=False)
    assert ja._is_unaddressed_ambient("open the browser and search flights") is True   # legacy drops
    monkeypatch.setenv("JARVIS_DIR_GATE", "1")
    assert ja._is_unaddressed_ambient("open the browser and search flights") is False  # score accepts


def test_flip_still_drops_cold_ambient_fragment(monkeypatch):
    _cold()
    monkeypatch.setenv("JARVIS_DIR_GATE", "1")
    assert ja._is_unaddressed_ambient("yeah") is True


def test_shadow_default_uses_window(monkeypatch):
    _cold()
    monkeypatch.delenv("JARVIS_DIR_GATE", raising=False)
    win_drop, dbg = ja._addressing_decision("open the browser", use_directedness=False)
    assert dbg["reason"] == "window"
    # default _is_unaddressed_ambient must match the window decision (shadow mode)
    assert ja._is_unaddressed_ambient("open the browser") is win_drop
