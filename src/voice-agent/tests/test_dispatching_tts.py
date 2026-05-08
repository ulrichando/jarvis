from unittest.mock import MagicMock

from pipeline.dispatching_tts import DispatchingTTS


def _stub(voice_id: str):
    m = MagicMock(name=f"tts-{voice_id}")
    m.voice_id = voice_id
    return m


def test_dispatcher_picks_correct_voice_per_route():
    inners = {
        "BANTER":    _stub("am_michael"),
        "TASK":      _stub("bm_george"),
        "REASONING": _stub("bm_george"),
        "EMOTIONAL": _stub("bm_lewis"),
    }
    d = DispatchingTTS(inners=inners, fallback=inners["TASK"])
    assert d.pick("BANTER").voice_id == "am_michael"
    assert d.pick("EMOTIONAL").voice_id == "bm_lewis"


def test_dispatcher_unknown_route_uses_fallback():
    inners = {"TASK": _stub("bm_george")}
    d = DispatchingTTS(inners=inners, fallback=inners["TASK"])
    assert d.pick("ZZZ").voice_id == "bm_george"


def test_dispatcher_records_last_voice_used():
    inners = {"BANTER": _stub("am_michael"), "TASK": _stub("bm_george")}
    d = DispatchingTTS(inners=inners, fallback=inners["TASK"])
    d.pick("BANTER")
    assert d.last_voice_id == "am_michael"
