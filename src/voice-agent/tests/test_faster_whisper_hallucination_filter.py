"""faster-whisper anti-hallucination decode config (2026-07-19 phantom-turn fix).

Regression guard for the incident where faster-whisper invented fluent "user"
turns from silence/room-tone (43 phantom turns; a 15-word sentence transcribed
verbatim 6×). `_transcribe_sync` must:
  (a) pass condition_on_previous_text=False + temperature=0.0 + vad_filter=True
      so the library stops inventing / regurgitating text from silence, and
  (b) drop per-segment output faster-whisper itself flags as near-certain
      silence / very-low-confidence — the metadata that used to be discarded.
"""

from providers.faster_whisper_stt import FasterWhisperSTT


class _Seg:
    def __init__(self, text, no_speech_prob=0.0, avg_logprob=0.0):
        self.text = text
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob


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
