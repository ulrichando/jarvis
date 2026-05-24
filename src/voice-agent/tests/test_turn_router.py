import pytest
from pipeline.turn_router import (
    detect_emotion, AudioMeta,
    compute_speech_rate, update_baseline,
)


# ── compute_speech_rate ────────────────────────────────────────────────


def test_compute_speech_rate_basic():
    # 6 words in 2 seconds = 180 wpm
    assert compute_speech_rate("one two three four five six", 2.0) == 180.0


def test_compute_speech_rate_zero_when_too_short():
    # Floor: anything ≤ 0.3s is single-word noise
    assert compute_speech_rate("hey", 0.2) == 0.0
    assert compute_speech_rate("hey there", 0.0) == 0.0
    assert compute_speech_rate("hey there", -1.0) == 0.0


def test_compute_speech_rate_zero_when_no_words():
    assert compute_speech_rate("", 5.0) == 0.0
    assert compute_speech_rate("   ", 5.0) == 0.0


def test_compute_speech_rate_realistic():
    # Average English speech ≈ 130-160 wpm
    # 13 words in 5 seconds → 156 wpm
    rate = compute_speech_rate("the quick brown fox jumps over the lazy dog right by the gate", 5.0)
    assert 150 < rate < 160


# ── update_baseline (EMA) ──────────────────────────────────────────────


def test_update_baseline_first_sample_seeds():
    # Empty baseline + first sample → adopt the sample wholesale
    assert update_baseline(150.0, 0.0) == 150.0


def test_update_baseline_zero_current_leaves_baseline_alone():
    # Couldn't measure this turn → don't pollute the baseline
    assert update_baseline(0.0, 140.0) == 140.0


def test_update_baseline_ema_blends():
    # alpha=0.2 default → new = 0.8*prior + 0.2*current
    out = update_baseline(200.0, 100.0)
    assert out == pytest.approx(120.0)  # 0.8*100 + 0.2*200


def test_update_baseline_alpha_override():
    # alpha=0.5 → average
    out = update_baseline(200.0, 100.0, alpha=0.5)
    assert out == pytest.approx(150.0)


def test_update_baseline_converges():
    # Repeated equal samples should converge the baseline to the sample
    base = 100.0
    for _ in range(50):
        base = update_baseline(180.0, base)
    assert base == pytest.approx(180.0, rel=1e-3)


# ── Integration: speech-rate → detect_emotion ─────────────────────────


def test_acoustic_signal_drives_emotion_when_lexicon_silent():
    """A neutral-words turn delivered fast (rate >> baseline) should
    flip to 'urgent' via the speech-rate path."""
    fast_audio = AudioMeta(speech_rate_wpm=200, baseline_wpm=130)
    # No lexicon hits in this transcript — only the rate signal triggers urgency
    assert detect_emotion("just open the file", fast_audio) == "urgent"


def test_acoustic_signal_can_signal_sad_with_neutral_words():
    """Slow speech with sad-shaped lexicon → sad."""
    slow_audio = AudioMeta(speech_rate_wpm=70, baseline_wpm=140)
    assert detect_emotion("i just don't know", slow_audio) == "sad"


# ── compute_interrupt_tuning (Phase 7) ────────────────────────────────


from pipeline.turn_router import compute_interrupt_tuning


def test_route_base_neutral_emotion():
    """With neutral emotion, the route base values should pass through
    unchanged — verifies overlay doesn't perturb the baseline.
    All routes dropped to min_words=0 on 2026-05-18 — see
    pipeline/turn_router.py::_ROUTE_BASE for the Whisper-no-interims
    reasoning."""
    assert compute_interrupt_tuning("BANTER",       "neutral") == (0, 0.3)
    assert compute_interrupt_tuning("TASK_OTHER",   "neutral") == (0, 0.4)
    assert compute_interrupt_tuning("TASK_DESKTOP", "neutral") == (0, 0.4)
    assert compute_interrupt_tuning("REASONING",    "neutral") == (0, 0.5)
    assert compute_interrupt_tuning("EMOTIONAL",    "neutral") == (0, 0.6)


def test_unknown_route_defaults_to_task_base():
    assert compute_interrupt_tuning("BLARG", "neutral") == (0, 0.4)


def test_frustrated_overlay_adds_padding():
    """A frustrated user shouldn't get cut off mid-vent. Both
    min_words and min_duration go UP."""
    base = compute_interrupt_tuning("TASK_OTHER", "neutral")
    frust = compute_interrupt_tuning("TASK_OTHER", "frustrated")
    assert frust[0] > base[0]
    assert frust[1] > base[1]


def test_urgent_overlay_makes_interrupts_snappier():
    """Urgent → user wants quick replies; min_words/min_duration go DOWN."""
    base = compute_interrupt_tuning("TASK_OTHER", "neutral")
    urg = compute_interrupt_tuning("TASK_OTHER", "urgent")
    assert urg[0] <= base[0]
    assert urg[1] <= base[1]


def test_sad_overlay_increases_min_duration_most():
    """Sad users pause; we should give them lots of pause room."""
    base = compute_interrupt_tuning("EMOTIONAL", "neutral")
    sad = compute_interrupt_tuning("EMOTIONAL", "sad")
    assert sad[1] > base[1]


def test_floor_prevents_disabling_interrupts():
    """An aggressive overlay can't push min_words below 0 or
    min_duration below 0.2. LiveKit's InterruptionOptions accepts
    min_words=0 (its own default), so the floor was relaxed from
    1 → 0 on 2026-05-18 to enable VAD-only barge-in (Whisper STT
    is non-streaming so word-count-confirmed barge-in is dead)."""
    mw, md = compute_interrupt_tuning("BANTER", "urgent")
    assert mw >= 0
    assert md >= 0.2





@pytest.mark.parametrize("transcript,expected", [
    ("hey jarvis what time is it", "neutral"),
    ("WHY ISN'T THIS WORKING I tried three times", "frustrated"),
    ("oh wow that's amazing", "excited"),
    ("I just don't know what to do anymore", "sad"),
    ("quick I need this NOW", "urgent"),
    ("I've been wondering how this actually works under the hood", "curious"),
    ("ok thanks", "neutral"),
])
def test_emotion_lexical(transcript, expected):
    assert detect_emotion(transcript, AudioMeta()) == expected


def test_emotion_caps_escalates_to_frustrated():
    assert detect_emotion("WHY IS THIS BROKEN", AudioMeta()) == "frustrated"


def test_emotion_high_speech_rate_signals_urgent():
    am = AudioMeta(speech_rate_wpm=240, baseline_wpm=140)
    assert detect_emotion("I need that file now", am) == "urgent"


def test_emotion_low_speech_rate_with_keyword_signals_sad():
    am = AudioMeta(speech_rate_wpm=70, baseline_wpm=140)
    assert detect_emotion("I just don't know", am) == "sad"


def test_emotion_unknown_falls_back_to_neutral():
    assert detect_emotion("blarg foo whatever", AudioMeta()) == "neutral"


# ── Phase 10.1 — score-based lex v2: negation, intensifier, escalation ─


def test_negation_kills_frustrated_signal():
    """`I'm NOT frustrated` should not push frustrated above neutral."""
    # "frustrating" is a key in the frustrated lex; "not" in the
    # preceding 30 chars flips its sign to -1.
    assert detect_emotion("this is not frustrating at all", AudioMeta()) == "neutral"


def test_negation_can_be_outvoted_by_other_match():
    """A single negated frustrated key shouldn't suppress other genuine signals.
    Frustrated -1 (negated) + Excited +1 (real) → excited wins."""
    assert detect_emotion(
        "this is not annoying — it's actually amazing", AudioMeta()
    ) == "excited"


def test_intensifier_doubles_match_weight():
    """`really frustrating` should outscore a competing single-key match."""
    # frustrated: "really" + "frustrating" → weight 2
    # excited:    "amazing" → weight 1
    # frustrated wins 2 > 1.
    assert detect_emotion(
        "this is really frustrating but the other part is amazing",
        AudioMeta(),
    ) == "frustrated"


def test_intensifier_window_doesnt_bleed_far():
    """Intensifier 30+ chars before a match should NOT boost it.
    Without the window cap a 'really' early in a long sentence
    would inflate every later emotion match arbitrarily."""
    text = (
        "I really enjoyed the talk earlier today and then later in the "
        "afternoon I noticed something annoying"
    )
    # "annoying" is far from "really" (>30 chars); should score 1, not 2.
    # No competing emotion → frustrated still wins, but the test asserts
    # the score is the lower value.
    from pipeline.turn_router import _score_emotions
    scores = _score_emotions(text)
    assert scores["frustrated"] == 1.0  # not 2.0


def test_score_aggregates_multiple_matches():
    """Multiple frustrated keys in one turn → frustrated wins big."""
    from pipeline.turn_router import _score_emotions
    scores = _score_emotions(
        "this is annoying and frustrating and seriously not working"
    )
    # annoying + frustrating + not working + seriously → 4 hits
    # ("seriously" isn't in negation regex, "not" in "not working"
    # is *part of* the key, doesn't count as negating itself)
    assert scores["frustrated"] >= 3.0


def test_escalation_punctuation_pushes_neutral_to_urgent():
    """`why is this happening??!!` (no lex hit) → urgent via escalation."""
    # No lex match, but multi-punctuation triggers urgent.
    assert detect_emotion("hello are you there??!!", AudioMeta()) == "urgent"


def test_escalation_does_not_override_strong_lex():
    """`amazing!!!` should stay excited, not get clobbered to urgent."""
    assert detect_emotion("this is amazing!!!", AudioMeta()) == "excited"


def test_escalation_pushes_curious_to_urgent():
    """A pressing-question pattern (`how does this work??`) escalates."""
    assert detect_emotion("how does this work??", AudioMeta()) == "urgent"


def test_score_returns_neutral_when_only_negated_matches():
    """All matches negated → no positive score → neutral."""
    assert detect_emotion(
        "this isn't frustrating and it's not annoying either", AudioMeta()
    ) == "neutral"


def test_expanded_lex_catches_new_phrases():
    """Phase 10.1 added ~20 keys per emotion — spot-check a few."""
    assert detect_emotion("I'm completely burnt out", AudioMeta()) == "sad"
    assert detect_emotion("this is fantastic", AudioMeta()) == "excited"
    assert detect_emotion("I'm running late", AudioMeta()) == "urgent"
    assert detect_emotion("tell me about quantum tunneling", AudioMeta()) == "curious"
    assert detect_emotion("come on, every time?", AudioMeta()) == "frustrated"


# ── Phase 10.3 — acoustic prosody (RMS-energy delta) ──────────────────


def test_loud_neutral_lex_pushes_to_frustrated():
    """+8 dB above baseline on a lex-silent turn → frustrated."""
    am = AudioMeta(rms_db=-22.0, rms_baseline_db=-30.0)  # diff = +8 dB
    assert detect_emotion("just open the file", am) == "frustrated"


def test_quiet_neutral_lex_pushes_to_sad():
    """-8 dB below baseline on a lex-silent turn → sad."""
    am = AudioMeta(rms_db=-38.0, rms_baseline_db=-30.0)  # diff = -8 dB
    assert detect_emotion("just open the file", am) == "sad"


def test_quiet_already_sad_stays_sad():
    """-8 dB below baseline reinforces an existing sad lex match."""
    am = AudioMeta(rms_db=-38.0, rms_baseline_db=-30.0)
    assert detect_emotion("i don't know", am) == "sad"


def test_loud_excited_does_not_clobber_to_frustrated():
    """+8 dB on `amazing` should NOT downgrade excited to frustrated.
    Only neutral bases are escalated by RMS, not strong lex hits."""
    am = AudioMeta(rms_db=-22.0, rms_baseline_db=-30.0)
    assert detect_emotion("this is amazing", am) == "excited"


def test_small_rms_delta_doesnt_trigger():
    """+3 dB is within normal speaking variance — should stay neutral."""
    am = AudioMeta(rms_db=-27.0, rms_baseline_db=-30.0)  # diff = +3 dB
    assert detect_emotion("just open the file", am) == "neutral"


def test_zero_rms_baseline_no_signal():
    """First turn ever — baseline is 0 → don't divide-by-zero or
    treat as catastrophic delta. Pure-lex result stands."""
    am = AudioMeta(rms_db=-25.0, rms_baseline_db=0.0)
    assert detect_emotion("just open the file", am) == "neutral"


def test_zero_rms_current_no_signal():
    """Tap returned 0 (e.g. mic muted, no samples in window) — skip
    the RMS branch."""
    am = AudioMeta(rms_db=0.0, rms_baseline_db=-30.0)
    assert detect_emotion("just open the file", am) == "neutral"


def test_rms_and_speech_rate_combine():
    """Loud + fast (both above thresholds) → speech-rate path runs
    first and returns urgent before RMS branch fires."""
    am = AudioMeta(
        speech_rate_wpm=240, baseline_wpm=140,
        rms_db=-22.0, rms_baseline_db=-30.0,
    )
    assert detect_emotion("just open the file", am) == "urgent"


import asyncio
from unittest.mock import AsyncMock, patch

from pipeline.turn_router import (
    route_from_classifier_output,
    classify_turn,
)


@pytest.mark.parametrize("raw,expected", [
    ("BANTER", "BANTER"),
    # Bare "task" (legacy 4-route label) normalizes to TASK_OTHER as of
    # 2026-05-24's sub-route split.
    ("  task  ", "TASK_OTHER"),
    ("REASONING\nplus extra", "REASONING"),
    ("EMOTIONAL.", "EMOTIONAL"),
    ("garbage", "TASK_OTHER"),
    ("", "TASK_OTHER"),
    # Sub-route labels round-trip cleanly.
    ("TASK_DESKTOP", "TASK_DESKTOP"),
    ("task_browser", "TASK_BROWSER"),
])
def test_route_from_classifier_output(raw, expected):
    assert route_from_classifier_output(raw) == expected


def test_classify_turn_uses_groq_response():
    fake_groq = AsyncMock(return_value="REASONING")
    out = asyncio.run(
        classify_turn(
            history=[("user", "walk me through how http2 multiplexing works")],
            emotion="curious",
            groq_call=fake_groq,
            timeout_ms=500,
        )
    )
    assert out == "REASONING"
    assert fake_groq.await_count == 1


def test_classify_turn_falls_back_on_timeout():
    async def slow(*_a, **_k):
        await asyncio.sleep(2.0)
        return "BANTER"

    out = asyncio.run(
        classify_turn(
            history=[("user", "hey")],
            emotion="neutral",
            groq_call=slow,
            timeout_ms=100,
        )
    )
    assert out == "TASK_OTHER"  # fallback (was bare "TASK" pre-2026-05-24)
