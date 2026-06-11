"""Tests for the realtime → kiosk-face bridge.

Covers the units that let Gemini Live / OpenAI Realtime (the
speech-to-speech modes that play their own audio outside LiveKit) drive the
kiosk face:

  * face_feed_client.rms_from_pcm16 — normalized RMS of the output audio.
  * face_feed_client.FaceFeedPusher — the playback clock: levels/speaking are
                                      reported at the time the audio PLAYS,
                                      not the time its bytes arrived.
  * VoiceClientHttpApi.face_feed     — POST /face/feed stashing {text, level,
                                       speaking} on `state` for the ticker.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field

import numpy as np

from face_feed_client import rms_from_pcm16
from voice_client_http_api import VoiceClientHttpApi


# ── rms_from_pcm16 ──────────────────────────────────────────────────────


def test_rms_silence_is_zero():
    assert rms_from_pcm16((np.zeros(256, dtype=np.int16)).tobytes()) == 0.0


def test_rms_loud_matches_normalized_scale():
    # Constant 10000 → RMS 10000, normalized 10000/32768 ≈ 0.305. Same scale
    # the playback loop produces, so the viseme engine's _RMS_FULL=0.18 reads it.
    pcm = (np.full(256, 10000, dtype=np.int16)).tobytes()
    assert abs(rms_from_pcm16(pcm) - (10000 / 32768)) < 1e-3


def test_rms_empty_and_odd_length_are_safe():
    assert rms_from_pcm16(b"") == 0.0
    assert rms_from_pcm16(b"\x01") == 0.0          # < 2 bytes
    assert rms_from_pcm16(b"\x00\x10\x05") >= 0.0  # odd byte dropped, no raise


# ── POST /face/feed ─────────────────────────────────────────────────────


@dataclass
class _FakeState:
    output_level: float = 0.0
    face_weights: dict = field(default_factory=dict)
    ext_face_text: str = ""
    ext_face_level: float = 0.0
    ext_face_speaking: bool = False
    ext_face_ts: float = 0.0


def _make_api(state):
    return VoiceClientHttpApi(
        state=state,
        get_mic_pub=lambda: None,
        get_room=lambda: None,
        restart_agent_unit=lambda: asyncio.sleep(0),
        log=logging.getLogger("test"),
    )


class _JsonReq:
    """Minimal stand-in for web.Request — face_feed only calls .json()."""
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def test_face_feed_route_registered():
    api = _make_api(_FakeState())
    paths = {r.resource.canonical for r in api.build_app().router.routes()}
    assert "/face/feed" in paths


def test_face_feed_stashes_inputs_and_stamps_ts():
    state = _FakeState()
    api = _make_api(state)

    async def go():
        resp = await api.face_feed(_JsonReq(
            {"text": "Hello there!", "level": 0.2, "speaking": True}
        ))
        import json
        return json.loads(resp.body.decode())

    body = asyncio.run(go())
    assert body == {"ok": True}
    assert state.ext_face_text == "Hello there!"
    assert state.ext_face_level == 0.2
    assert state.ext_face_speaking is True
    assert state.ext_face_ts > 0.0     # monotonic stamp written


def test_face_feed_clamps_level_and_tolerates_garbage():
    state = _FakeState()
    api = _make_api(state)

    async def feed(payload):
        await api.face_feed(_JsonReq(payload))

    asyncio.run(feed({"level": 5.0}))        # over-range → clamped to 1.0
    assert state.ext_face_level == 1.0
    asyncio.run(feed({"level": -3.0}))       # under-range → clamped to 0.0
    assert state.ext_face_level == 0.0
    asyncio.run(feed({"level": "not-a-num"}))  # garbage → ignored, no raise
    assert state.ext_face_level == 0.0


def test_face_feed_empty_body_is_noop():
    state = _FakeState()
    state.ext_face_ts = 0.0
    api = _make_api(state)
    asyncio.run(api.face_feed(_JsonReq({})))   # no keys
    # Only the timestamp is touched (so the playback loop yields); the rest stay.
    assert state.ext_face_text == ""
    assert state.ext_face_ts > 0.0


# ── FaceFeedPusher playback clock ───────────────────────────────────────
#
# The realtime APIs deliver audio faster than realtime: a whole reply can
# arrive while the speakers have played <1s of it. These tests pin the
# core contract: face state follows the SCHEDULED playback time of the fed
# bytes, not their arrival time. Fast tick rate + tiny sample rate keep
# each test well under a second of wall time.


def _capture_pusher(**kw):
    """Pusher with `_post` swapped for an in-memory recorder. The recorder
    is installed while the pusher is idle (it never POSTs before audio is
    fed), so no real HTTP request can slip out first."""
    from face_feed_client import FaceFeedPusher
    posts: list = []
    p = FaceFeedPusher(url="http://127.0.0.1:9/unused", **kw)
    p._post = lambda payload: posts.append((time.monotonic(), dict(payload)))
    return p, posts


# sample_rate=1000 → 2000 B/s mono s16le; 40-byte sub-frames = 20 ms each.
_PUSHER_KW = dict(
    hz=200.0, sample_rate=1000, start_latency_s=0.05, hold_s=0.05, tail_s=0.02
)


def test_idle_pusher_posts_nothing():
    p, posts = _capture_pusher(**_PUSHER_KW)
    try:
        time.sleep(0.1)
        assert posts == []
    finally:
        p.close()


def test_feed_audio_schedules_subframes_on_playback_clock():
    p, posts = _capture_pusher(**_PUSHER_KW)
    try:
        now = time.monotonic()
        pcm = np.full(400, 10000, dtype=np.int16).tobytes()  # 0.4 s at 1 kHz
        p.feed_audio(pcm)
        with p._lock:
            n_frames = len(p._frames)
            end = p._playhead_end
        assert n_frames == 20                       # 0.4 s / 20 ms sub-frames
        # Ends ≈ now + start_latency + duration (generous sched tolerance).
        assert abs(end - (now + 0.05 + 0.4)) < 0.05
    finally:
        p.close()


def test_speaking_follows_playback_not_receive_time():
    """The regression this design exists for: the burst arrives instantly,
    but the mouth must keep moving until the playhead drains (~0.45 s),
    then close — never freeze the moment the network goes quiet."""
    p, posts = _capture_pusher(**_PUSHER_KW)
    try:
        t0 = time.monotonic()
        p.feed_audio(np.full(400, 10000, dtype=np.int16).tobytes())  # 0.4 s
        time.sleep(0.25)            # mid-playback, long after receive ended
        assert any(pl["speaking"] for _, pl in posts), \
            "no speaking frames during scheduled playback"
        assert any(pl["level"] > 0.2 for _, pl in posts), \
            "level should reflect the loud PCM during its play window"
        time.sleep(0.45)            # past playhead end + hold + closing burst
        last_ts, last = posts[-1]
        assert last["speaking"] is False
        assert last["level"] == 0.0
        # speaking persisted through the ~0.4 s play window, not just the
        # instant of the network burst.
        spk_span = max(ts for ts, pl in posts if pl["speaking"]) - t0
        assert spk_span > 0.3
        # …and went idle after the closing burst (no inter-utterance spam).
        n = len(posts)
        time.sleep(0.1)
        assert len(posts) == n
    finally:
        p.close()


def test_flush_stops_the_face_immediately():
    # Longer tail than the other tests so the closing burst spans an
    # observable window (~100 ms) after the flush instant.
    p, posts = _capture_pusher(**{**_PUSHER_KW, "tail_s": 0.1})
    try:
        p.feed_audio(np.full(1000, 10000, dtype=np.int16).tobytes())  # 1.0 s
        time.sleep(0.15)            # audibly mid-playback
        p.flush()
        t_flush = time.monotonic()
        time.sleep(0.15)
        # Skip 2 tick-periods of margin: a tick that computed its state just
        # before flush() may legitimately post speaking=True just after it.
        after = [(ts, pl) for ts, pl in posts if ts > t_flush + 0.012]
        assert after, "closing frames must still be posted after flush"
        assert all(pl["speaking"] is False for _, pl in after)
        assert posts[-1][1]["level"] == 0.0
    finally:
        p.close()


def test_text_accumulates_and_resets():
    p, posts = _capture_pusher(**_PUSHER_KW)
    try:
        p.feed_text("Hello th")
        p.feed_text("Hello there!")
        with p._lock:
            assert p._text == "Hello there!"
        p.reset_text()
        with p._lock:
            assert p._text == ""
    finally:
        p.close()


def test_realtime_inputs_produce_a_moving_mouth():
    """End-to-end contract the ticker relies on: text + speaking + amplitude
    yield non-empty morph weights (mouth/eyes move), and silence closes it."""
    from lipsync import VisemeEngine, ExpressionEngine
    import time as _t

    ve, ee = VisemeEngine(), ExpressionEngine()
    ve.set_pending_text("That's wonderful news!")
    ee.set_pending_text("That's wonderful news!")
    now = _t.monotonic()
    weights = {**ve.frame(now=now, speaking=True, rms=0.12),
               **ee.frame(True)}
    assert weights, "speaking with text + amplitude must drive morphs"
    assert all(0.0 <= w <= 1.0 for w in weights.values())

    # Not speaking → mouth at rest (kiosk eases to closed).
    assert ve.frame(now=now + 1, speaking=False, rms=0.0) == {}
    assert ee.frame(False) == {}
