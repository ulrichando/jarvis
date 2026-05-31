from lipsync.viseme_engine import VisemeEngine


def test_silent_returns_empty():
    eng = VisemeEngine()
    assert eng.frame(now=0.0, speaking=False, rms=0.0) == {}


def test_speaking_without_text_falls_back_to_amplitude_jaw():
    eng = VisemeEngine()
    out = eng.frame(now=0.0, speaking=True, rms=0.1)
    assert set(out) == {"target_24"}
    assert out["target_24"] > 0.0


def test_speaking_with_text_drives_mouth_visemes():
    eng = VisemeEngine()
    eng.set_pending_text("hello world")
    eng.frame(now=0.0, speaking=True, rms=0.2)          # rising edge -> t0
    out = eng.frame(now=0.15, speaking=True, rms=0.2)   # 150 ms in
    assert "target_24" in out
    assert all(k.startswith("target_") for k in out)
    assert all(0.0 <= v <= 1.0 for v in out.values())


def test_openness_tracks_rms():
    eng = VisemeEngine()
    eng.set_pending_text("aaaa")
    eng.frame(now=0.0, speaking=True, rms=0.4)
    loud = eng.frame(now=0.05, speaking=True, rms=0.4)["target_24"]
    eng.reset()
    eng.set_pending_text("aaaa")
    eng.frame(now=0.0, speaking=True, rms=0.05)
    quiet = eng.frame(now=0.05, speaking=True, rms=0.05)["target_24"]
    assert loud > quiet


def test_falling_edge_resets():
    eng = VisemeEngine()
    eng.set_pending_text("hello")
    eng.frame(now=0.0, speaking=True, rms=0.2)
    eng.frame(now=0.1, speaking=True, rms=0.2)
    assert eng.frame(now=0.2, speaking=False, rms=0.0) == {}
    eng.set_pending_text("hi")
    out = eng.frame(now=1.0, speaking=True, rms=0.2)
    assert out


def test_cursor_advances_over_time_even_from_t0_zero():
    # Guards the _t0==0.0 falsy trap: with t0=0.0 the cursor must still
    # advance, so the pose early in the utterance differs from later.
    eng = VisemeEngine()
    eng.set_pending_text("hello world")
    eng.frame(now=0.0, speaking=True, rms=0.2)        # rising edge, t0 = 0.0
    early = eng.frame(now=0.02, speaking=True, rms=0.2)
    late = eng.frame(now=0.6, speaking=True, rms=0.2)
    assert early != late


def test_reset_clears_pending_text_so_no_stale_replay():
    eng = VisemeEngine()
    eng.set_pending_text("hello world")
    eng.frame(now=0.0, speaking=True, rms=0.2)
    eng.frame(now=0.3, speaking=False, rms=0.0)       # falling edge -> reset
    # No new text set; next utterance must fall back to amplitude jaw, NOT
    # replay "hello world".
    out = eng.frame(now=1.0, speaking=True, rms=0.1)
    assert set(out) == {"target_24"}


def test_text_arriving_after_speech_start_engages_visemes():
    # The live case: audio starts, THEN the transcript streams in. The first
    # frames are amplitude jaw (no text yet); once text lands the engine must
    # switch to real visemes (more than just the jaw morph) mid-utterance.
    eng = VisemeEngine()
    early = eng.frame(now=0.0, speaking=True, rms=0.2)   # no text yet
    assert set(early) == {"target_24"}                   # amplitude fallback
    eng.set_pending_text("food")                         # rounded vowel -> pucker/funnel
    out = eng.frame(now=0.1, speaking=True, rms=0.2)
    # a real viseme pose drives more than the jaw alone
    assert len(out) > 1
    assert all(0.0 <= v <= 1.0 for v in out.values())


def test_growing_transcript_extends_the_sequence():
    # Words stream in; the sequence must extend (so later words are reachable)
    # while keeping the original t0 so the timeline stays continuous.
    eng = VisemeEngine()
    eng.set_pending_text("the")
    eng.frame(now=0.0, speaking=True, rms=0.2)
    short_len = len(eng._seq)
    eng.set_pending_text("the quick brown fox jumps over")
    eng.frame(now=0.1, speaking=True, rms=0.2)
    assert len(eng._seq) > short_len      # extended, not replaced-from-scratch
    assert eng._t0 == 0.0                 # t0 preserved across the extend
