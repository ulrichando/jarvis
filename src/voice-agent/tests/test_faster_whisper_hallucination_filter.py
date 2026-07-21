"""faster-whisper anti-hallucination decode config (2026-07-19 phantom-turn fix).

Regression guard for the incident where faster-whisper invented fluent "user"
turns from silence/room-tone (43 phantom turns; a 15-word sentence transcribed
verbatim 6×). `_transcribe_sync` must:
  (a) pass condition_on_previous_text=False + temperature=0.0 + vad_filter=True
      so the library stops inventing / regurgitating text from silence, and
  (b) drop per-segment output faster-whisper itself flags as near-certain
      silence / very-low-confidence / pathologically repetitive (high
      compression_ratio) — the metadata that used to be discarded. Thresholds
      are env-tunable (JARVIS_STT_NO_SPEECH_CEIL / _LOGPROB_FLOOR /
      _COMPRESSION_CEIL) so the operating point calibrates to a real mic/room.
"""

from providers.faster_whisper_stt import FasterWhisperSTT


class _Seg:
    def __init__(self, text, no_speech_prob=0.0, avg_logprob=0.0, compression_ratio=1.5):
        self.text = text
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob
        self.compression_ratio = compression_ratio


class _Info:
    language = "en"


class _CaptureModel:
    def __init__(self, segs):
        self._segs = segs
        self.kwargs = None

    def transcribe(self, *a, **kw):
        self.kwargs = kw
        return iter(self._segs), _Info()  # generator, like the real lib


def _inst():
    # Lazy — no real WhisperModel loads until _ensure_model(); _transcribe_sync
    # takes the model as an arg, so we never touch that path.
    return FasterWhisperSTT(
        model="large-v3-turbo", device="cuda",
        compute_type="int8_float16", language="en",
    )


def test_applies_anti_hallucination_decode_params():
    m = _CaptureModel([_Seg("hello")])
    _inst()._transcribe_sync(m, b"wav", "en")
    assert m.kwargs["condition_on_previous_text"] is False
    assert m.kwargs["temperature"] == 0.0
    assert m.kwargs["vad_filter"] is True


def test_drops_silence_hallucination_keeps_real_speech():
    m = _CaptureModel([
        _Seg("real question here", no_speech_prob=0.02, avg_logprob=-0.3),
        _Seg(" thank you, Dr. Jean-Balucy.", no_speech_prob=0.96, avg_logprob=-1.6),
    ])
    text, lang = _inst()._transcribe_sync(m, b"wav", "en")
    assert "real question here" in text
    assert "Jean-Balucy" not in text
    assert lang == "en"


def test_very_low_confidence_segment_dropped():
    m = _CaptureModel([_Seg("garbled invention", no_speech_prob=0.1, avg_logprob=-2.0)])
    text, _ = _inst()._transcribe_sync(m, b"wav", "en")
    assert text == ""


def test_missing_metadata_defaults_to_kept():
    # Segments without the attrs (older code / other backends) must not be
    # silently dropped — getattr defaults keep them.
    class _Bare:
        text = "keep me"
    m = _CaptureModel([_Bare()])
    text, _ = _inst()._transcribe_sync(m, b"wav", "en")
    assert text == "keep me"


def test_high_compression_ratio_dropped():
    # A CONFIDENT hallucination: good logprob + low no_speech_prob, so the two
    # confidence signals keep it — but it's pathologically repetitive
    # (compression_ratio above the 2.4 ceiling), which is exactly the babble the
    # compression signal exists to catch.
    m = _CaptureModel([
        _Seg("real answer", no_speech_prob=0.02, avg_logprob=-0.2, compression_ratio=1.6),
        _Seg(" yeah yeah yeah yeah yeah yeah", no_speech_prob=0.05,
             avg_logprob=-0.4, compression_ratio=3.1),
    ])
    text, _ = _inst()._transcribe_sync(m, b"wav", "en")
    assert "real answer" in text
    assert "yeah" not in text


def test_normal_compression_ratio_kept():
    # Ordinary speech sits well under the ceiling and must survive.
    m = _CaptureModel([_Seg("what's the weather like today",
                            no_speech_prob=0.03, avg_logprob=-0.3, compression_ratio=2.0)])
    text, _ = _inst()._transcribe_sync(m, b"wav", "en")
    assert "weather" in text


def test_thresholds_default():
    assert FasterWhisperSTT._hallucination_thresholds() == (0.85, -1.2, 2.4)


def test_thresholds_env_tunable(monkeypatch):
    monkeypatch.setenv("JARVIS_STT_NO_SPEECH_CEIL", "0.5")
    monkeypatch.setenv("JARVIS_STT_LOGPROB_FLOOR", "-0.8")
    monkeypatch.setenv("JARVIS_STT_COMPRESSION_CEIL", "2.0")
    assert FasterWhisperSTT._hallucination_thresholds() == (0.5, -0.8, 2.0)
    # And the tightened ceiling actually changes behaviour: a segment kept at the
    # default compression ceiling (2.4) is now dropped under the 2.0 override.
    m = _CaptureModel([_Seg("borderline", no_speech_prob=0.1, avg_logprob=-0.5,
                            compression_ratio=2.2)])
    text, _ = _inst()._transcribe_sync(m, b"wav", "en")
    assert text == ""


def test_malformed_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("JARVIS_STT_COMPRESSION_CEIL", "not-a-number")
    assert FasterWhisperSTT._hallucination_thresholds()[2] == 2.4
