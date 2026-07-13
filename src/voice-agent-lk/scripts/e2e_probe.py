"""Headless end-to-end probe for the JARVIS voice agent (no phone needed).

Joins a fresh LiveKit room as a fake user, speaks a question (Edge TTS
-> PCM -> published mic track), and verifies the agent answers with
audio. Run inside the agent image on the VPS:

    docker run --rm --network host --env-file .env \
        jarvis-voice-agent-lk python scripts/e2e_probe.py

Pass criteria (stdout):  PROBE PASS — agent audio received
Cross-check the agent container logs for the matching [stt]/[llm]
lines to see the transcript and the Claude reply for the same room.
"""
from __future__ import annotations

import asyncio
import io
import os
import time

QUESTION = "What is two plus two? Answer in one short sentence."
SAMPLE_RATE = 48000
AGENT_JOIN_TIMEOUT_S = 60
REPLY_TIMEOUT_S = 90
# ~ this much agent audio proves a real spoken reply (not a click).
PASS_THRESHOLD_S = 1.0


async def synth_question_pcm() -> bytes:
    """Edge TTS -> mp3 -> mono s16 PCM @48k (what we publish to the room)."""
    import av
    import edge_tts

    mp3 = bytearray()
    communicate = edge_tts.Communicate(QUESTION, "en-US-JennyNeural")
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            mp3.extend(chunk["data"])
    print(f"[probe] edge-tts question synthesized: {len(mp3)} mp3 bytes")

    container = av.open(io.BytesIO(bytes(mp3)))
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    pcm = bytearray()
    for frame in container.decode(audio=0):
        for out in resampler.resample(frame):
            pcm.extend(bytes(out.planes[0])[: out.samples * 2])
    print(f"[probe] decoded to {len(pcm)} pcm bytes ({len(pcm)/2/SAMPLE_RATE:.1f}s)")
    return bytes(pcm)


async def main() -> int:
    from livekit import api, rtc

    url = os.environ["LIVEKIT_URL"]
    key = os.environ["LIVEKIT_API_KEY"]
    secret = os.environ["LIVEKIT_API_SECRET"]

    room_name = f"voice-e2e-{int(time.time())}"
    token = (
        api.AccessToken(key, secret)
        .with_identity("probe")
        .with_name("probe")
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
        .to_jwt()
    )

    pcm = await synth_question_pcm()

    room = rtc.Room()
    agent_joined = asyncio.Event()
    agent_audio: dict = {"frames": 0, "samples": 0}
    reply_done = asyncio.Event()
    stream_tasks: list[asyncio.Task] = []

    @room.on("participant_connected")
    def _on_participant(p: rtc.RemoteParticipant) -> None:
        print(f"[probe] participant joined: {p.identity} (kind={p.kind})")
        agent_joined.set()

    @room.on("track_subscribed")
    def _on_track(
        track: rtc.Track,
        pub: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        print(f"[probe] subscribed to {participant.identity} track kind={track.kind}")
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            stream_tasks.append(asyncio.create_task(_drain_audio(track)))

    async def _drain_audio(track: rtc.Track) -> None:
        stream = rtc.AudioStream(track)
        last_frame_at = None
        async for ev in stream:
            frame = ev.frame
            agent_audio["frames"] += 1
            # Count only non-silent audio — the agent publishes a
            # continuous track that idles on silence.
            if any(frame.data):
                agent_audio["samples"] += frame.samples_per_channel
                last_frame_at = time.monotonic()
            got_s = agent_audio["samples"] / frame.sample_rate
            if got_s >= PASS_THRESHOLD_S and last_frame_at is not None:
                reply_done.set()

    await room.connect(url, token)
    print(f"[probe] connected to room {room_name}")

    # publish the "microphone"
    source = rtc.AudioSource(SAMPLE_RATE, 1)
    track = rtc.LocalAudioTrack.create_audio_track("probe-mic", source)
    await room.local_participant.publish_track(
        track,
        rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
    )
    print("[probe] mic track published; waiting for the agent to join…")

    if not room.remote_participants:
        try:
            await asyncio.wait_for(agent_joined.wait(), AGENT_JOIN_TIMEOUT_S)
        except asyncio.TimeoutError:
            print("PROBE FAIL — agent never joined the room")
            await room.disconnect()
            return 1
    # let the agent's session subscribe + settle
    await asyncio.sleep(2.0)

    print("[probe] speaking the question into the room…")
    frame_samples = SAMPLE_RATE // 100  # 10 ms
    frame_bytes = frame_samples * 2
    for off in range(0, len(pcm) - frame_bytes + 1, frame_bytes):
        frame = rtc.AudioFrame.create(SAMPLE_RATE, 1, frame_samples)
        # frame.data is an int16 memoryview; write raw bytes via a cast.
        frame.data.cast("B")[:] = pcm[off : off + frame_bytes]
        await source.capture_frame(frame)
    # silence tail so the VAD sees end-of-speech
    silence = rtc.AudioFrame.create(SAMPLE_RATE, 1, frame_samples)
    for _ in range(200):  # 2 s
        await source.capture_frame(silence)
    print("[probe] question sent; waiting for the agent's spoken reply…")

    try:
        await asyncio.wait_for(reply_done.wait(), REPLY_TIMEOUT_S)
    except asyncio.TimeoutError:
        pass

    got_s = agent_audio["samples"] / 48000
    print(
        f"[probe] agent audio received: {agent_audio['frames']} frames, "
        f"~{got_s:.2f}s of non-silent audio"
    )
    ok = reply_done.is_set()
    print("PROBE PASS — agent audio received" if ok else "PROBE FAIL — no agent reply audio")

    for t in stream_tasks:
        t.cancel()
    await room.disconnect()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
