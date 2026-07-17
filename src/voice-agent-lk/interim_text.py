"""Pure text/bookkeeping logic for streaming-STT interim transcripts.

No livekit / faster-whisper imports on purpose: everything here is unit-
testable without audio hardware or models (see test_interim_text.py).

Two pieces:

1. LocalAgreement — the canonical low-flicker streaming-whisper display
   algorithm (LocalAgreement-2, from UFAL whisper_streaming: "Turning
   Whisper into Real-Time Transcription System", Macháček et al. 2023).
   Each re-decode of the growing utterance audio yields a full hypothesis;
   words that AGREE across two consecutive hypotheses are committed
   (frozen for display), the disagreeing tail keeps revising. The phone
   renders `display()` as the live user partial, so the user sees
   "wut" -> "what" -> "what's the" style self-revision with a stable,
   non-flickering committed prefix.

   IMPORTANT: this is display-only smoothing. The FINAL transcript is a
   separate clean decode of the whole utterance with the production
   anti-hallucination guards (see stt_local._transcribe_sync) and fully
   replaces whatever the interims showed.

2. plan_trim — bounded-window bookkeeping for long utterances. The
   interim decoder re-decodes the accumulated utterance audio each tick;
   to bound CPU we cap that window. When the utterance outgrows the
   window we cut old audio at (ideally) a whisper segment boundary from
   the last interim decode, freeze the text for the cut span, and let
   LocalAgreement restart over the remaining window. Freezing at a
   segment boundary keeps the display seam mostly word-aligned.
"""
from __future__ import annotations

_STRIP_CHARS = ".,!?;:…\"'`()[]{}<>-—"


def _norm(word: str) -> str:
    """Normalization for agreement comparison: case- and edge-punctuation-
    insensitive, so "Hello," agrees with "hello" (interior apostrophes are
    kept: "what's" != "what")."""
    return word.lower().strip(_STRIP_CHARS)


def compose(prefix: str, rest: str) -> str:
    """Join two display fragments, tolerating either being empty."""
    if not prefix:
        return rest
    if not rest:
        return prefix
    return f"{prefix} {rest}"


class LocalAgreement:
    """LocalAgreement-2 over word lists (positional alignment, no timestamps).

    update(words) ingests the newest full hypothesis for the CURRENT decode
    window and returns the display string: committed prefix + newest
    unconfirmed tail. A word is committed once two consecutive hypotheses
    agree on it (normalized comparison); the newer surface form wins, so
    later punctuation/casing fixes land before the word freezes.
    """

    def __init__(self) -> None:
        self._committed: list[str] = []
        self._prev_tail: list[str] = []

    @property
    def committed_words(self) -> list[str]:
        return list(self._committed)

    def committed_text(self) -> str:
        return " ".join(self._committed)

    def reset(self) -> None:
        self._committed = []
        self._prev_tail = []

    def update(self, words: list[str]) -> str:
        if len(words) < len(self._committed):
            # Hypothesis shrank below the committed prefix (transient decoder
            # wobble, e.g. mid-breath). Don't regress the display; drop the
            # stale tail so nothing spurious commits next round.
            self._prev_tail = []
            return self.committed_text()

        tail = words[len(self._committed) :]
        n = 0
        limit = min(len(tail), len(self._prev_tail))
        while n < limit and _norm(tail[n]) == _norm(self._prev_tail[n]):
            n += 1
        # Commit the agreed run using the NEWER hypothesis's surface forms.
        self._committed.extend(tail[:n])
        self._prev_tail = tail[n:]
        return " ".join(self._committed + self._prev_tail)


def pick_cut_time(
    segments: list[tuple[float, float, str]],
    target: float,
    tolerance: float = 1.5,
) -> float:
    """Choose where to cut old audio out of the interim decode window.

    Prefers the whisper segment END (from the last interim decode; absolute
    utterance-time) closest to `target` within +/- `tolerance` seconds, so
    the cut lands on a natural phrase boundary instead of mid-word. Falls
    back to `target` itself when no boundary is near.
    """
    best: float | None = None
    for _start, end, _text in segments:
        if abs(end - target) <= tolerance and (best is None or abs(end - target) < abs(best - target)):
            best = end
    return best if best is not None else target


def freeze_text(segments: list[tuple[float, float, str]], cut: float) -> str:
    """Display text for the audio span being cut: every segment that ends at
    or before the cut (small slack for float wobble)."""
    return " ".join(t for _s, e, t in segments if e <= cut + 0.05 and t)


def plan_trim(
    segments: list[tuple[float, float, str]],
    buf_start: float,
    total_dur: float,
    window: float,
    keep_recent: float,
    tolerance: float = 1.5,
) -> tuple[float, str] | None:
    """Decide whether/where to trim the interim decode window.

    Args:
        segments: [(abs_start, abs_end, text)] from the last interim decode.
        buf_start: absolute utterance-time of the buffered audio's head.
        total_dur: absolute utterance-time of the buffered audio's end.
        window: max seconds of audio we're willing to re-decode per tick.
        keep_recent: seconds of recent audio to keep after a trim (< window
            so trims happen in chunks, not every tick).
        tolerance: how far the cut may move to snap to a segment boundary.

    Returns None when the buffer still fits the window, else
    (cut_time, frozen_text): drop audio before cut_time and append
    frozen_text to the frozen display prefix.
    """
    if total_dur - buf_start <= window:
        return None
    target = total_dur - keep_recent
    cut = pick_cut_time(segments, target, tolerance)
    cut = max(cut, buf_start)  # never cut before the buffer head
    frozen = freeze_text([s for s in segments if s[1] > buf_start], cut)
    return cut, frozen
