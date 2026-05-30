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
