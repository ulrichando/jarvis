"""Cross-platform raw-PCM mic/speaker for the direct (Gemini Live / OpenAI
Realtime) conversation modes.

Those two "live" modes were originally Linux-only because they piped audio
through PulseAudio's ``parec`` (capture) and ``paplay`` (playback) CLIs, which
exist only on Linux.  This module keeps that exact path on Linux — unchanged,
battle-tested — and provides a ``sounddevice`` (PortAudio) backend everywhere
else (Windows / macOS) so the SAME ``FIFINE``-in / ``Echo Studio``-out device
selection the main voice client uses applies to the live modes too.

Both backends expose the identical minimal interface the tools already
consume, so the consuming loops in ``bin/jarvis-gemini-tools`` /
``bin/jarvis-gpt-tools`` don't change::

    mic = await open_mic_stream(MIC_SAMPLE_RATE)
    chunk = await mic.stdout.readexactly(n)        # -> bytes (s16le mono)

    spk = await open_speaker_stream(SPK_SAMPLE_RATE)
    spk.stdin.write(pcm_bytes); await spk.stdin.drain()

    # shutdown parity with asyncio subprocesses:
    mic.terminate(); await mic.wait()              # also .kill()

Device selection mirrors ``jarvis_voice_client._resolve_audio_device``: read
``JARVIS_AUDIO_{INPUT,OUTPUT}_DEVICE`` env (set by the Windows launcher) then
``~/.jarvis/audio-{input,output}-device`` (written by the tray picker), match
the name across PortAudio host APIs preferring MME on Windows, and fall back to
the PortAudio default when unresolved.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from typing import Optional

# Linux keeps PulseAudio; everything else (Windows/macOS) uses sounddevice.
_IS_LINUX = sys.platform.startswith("linux")


# ── Linux backend: PulseAudio parec / paplay (unchanged behaviour) ─────────

async def _open_mic_parec(sample_rate: int):
    return await asyncio.create_subprocess_exec(
        "parec",
        "--format=s16le",
        "--channels=1",
        f"--rate={sample_rate}",
        "--latency-msec=80",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _open_speaker_paplay(sample_rate: int):
    return await asyncio.create_subprocess_exec(
        "paplay",
        "--format=s16le",
        "--channels=1",
        f"--rate={sample_rate}",
        "--latency-msec=80",
        "--raw",
        stdin=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


# ── Device resolution (PortAudio / sounddevice) ────────────────────────────

def _read_device_pref(kind: str) -> str:
    """The desired device name/index for ``kind`` ∈ {input, output}.

    env (JARVIS_AUDIO_INPUT_DEVICE / _OUTPUT_DEVICE) wins, then the tray-picker
    file ~/.jarvis/audio-<kind>-device.  Empty string = PortAudio default.
    """
    env_key = "JARVIS_AUDIO_INPUT_DEVICE" if kind == "input" else "JARVIS_AUDIO_OUTPUT_DEVICE"
    val = (os.environ.get(env_key) or "").strip()
    if val:
        return val
    try:
        with open(os.path.expanduser(f"~/.jarvis/audio-{kind}-device"), encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _resolve_sd_device(kind: str) -> Optional[int]:
    """Resolve the preferred device name to a PortAudio device index.

    Returns None (PortAudio default) when nothing is configured or no match is
    found.  Substring match on the device name; on Windows the same physical
    device is exposed under several host APIs (MME / DirectSound / WASAPI /
    WDM-KS) — prefer MME, whose 31-char-truncated names are what enumeration
    surfaces and what the tray picker persists.
    """
    try:
        import sounddevice as sd
    except Exception:
        return None
    pref = _read_device_pref(kind)
    if not pref:
        return None
    if pref.isdigit():
        return int(pref)
    want = pref.lower()
    ch_key = "max_input_channels" if kind == "input" else "max_output_channels"
    try:
        devs = sd.query_devices()
    except Exception:
        return None
    try:
        hostapis = sd.query_hostapis()
    except Exception:
        hostapis = []

    def _is_mme(d) -> bool:
        try:
            return "mme" in hostapis[d["hostapi"]]["name"].lower()
        except Exception:
            return False

    matches = [
        i for i, d in enumerate(devs)
        if d.get(ch_key, 0) > 0 and want in d["name"].lower()
    ]
    if not matches:
        return None
    mme = [i for i in matches if _is_mme(devs[i])]
    return mme[0] if mme else matches[0]


# ── sounddevice backend: parec/paplay-compatible adapters ──────────────────

class _SdMicStream:
    """sounddevice ``RawInputStream`` behind a parec-compatible ``.stdout``.

    A PortAudio callback (runs on PortAudio's thread) appends raw s16le mono
    bytes to a buffer; ``stdout.readexactly(n)`` awaits until ``n`` bytes are
    available, mirroring ``asyncio.StreamReader.readexactly`` so the tool's
    ``pump_mic`` loop is unchanged.
    """

    def __init__(self, sample_rate: int, loop: asyncio.AbstractEventLoop):
        import sounddevice as sd

        self._loop = loop
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._ev = asyncio.Event()
        self._closed = False
        # 20 ms blocks keep latency low and match parec's --latency-msec=80 feel.
        blocksize = max(1, int(sample_rate * 0.02))

        def _cb(indata, frames, time_info, status):  # noqa: ANN001 — PortAudio cb
            with self._lock:
                self._buf.extend(bytes(indata))
            self._loop.call_soon_threadsafe(self._ev.set)

        self._stream = sd.RawInputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            blocksize=blocksize,
            device=_resolve_sd_device("input"),
            callback=_cb,
        )
        self._stream.start()
        # The tool reads `mic_proc.stdout.readexactly(...)`; expose self.
        self.stdout = self

    async def readexactly(self, n: int) -> bytes:
        while True:
            with self._lock:
                if len(self._buf) >= n:
                    out = bytes(self._buf[:n])
                    del self._buf[:n]
                    return out
            if self._closed:
                raise asyncio.IncompleteReadError(b"", n)
            self._ev.clear()
            await self._ev.wait()

    def terminate(self) -> None:
        self._close()

    def kill(self) -> None:
        self._close()

    async def wait(self) -> int:
        self._close()
        return 0

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass
        try:
            self._loop.call_soon_threadsafe(self._ev.set)
        except Exception:
            pass


class _SdSpeakerStream:
    """sounddevice ``RawOutputStream`` behind a paplay-compatible ``.stdin``.

    ``stdin.write(b)`` appends to a buffer the PortAudio callback drains;
    ``stdin.drain()`` yields (and lightly throttles when the buffer grows past
    ~0.5 s so playback latency stays bounded under a fast model).
    """

    def __init__(self, sample_rate: int, loop: asyncio.AbstractEventLoop):
        import sounddevice as sd

        self._loop = loop
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._closed = False
        # high-water = ~0.5 s of mono s16le audio
        self._high_water = int(sample_rate * 2 * 0.5)
        blocksize = max(1, int(sample_rate * 0.02))

        def _cb(outdata, frames, time_info, status):  # noqa: ANN001 — PortAudio cb
            need = len(outdata)
            with self._lock:
                take = bytes(self._buf[:need])
                del self._buf[:len(take)]
            if len(take) == need:
                outdata[:] = take
            else:
                # underrun → play what we have, pad the rest with silence
                outdata[:len(take)] = take
                outdata[len(take):] = b"\x00" * (need - len(take))

        self._stream = sd.RawOutputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            blocksize=blocksize,
            device=_resolve_sd_device("output"),
            callback=_cb,
        )
        self._stream.start()
        # The tool writes `spk_proc.stdin.write(...)`; expose self.
        self.stdin = self

    def write(self, data: bytes) -> None:
        if self._closed:
            return
        with self._lock:
            self._buf.extend(data)

    async def drain(self) -> None:
        while not self._closed:
            with self._lock:
                if len(self._buf) < self._high_water:
                    return
            await asyncio.sleep(0.005)

    def terminate(self) -> None:
        self._close()

    def kill(self) -> None:
        self._close()

    async def wait(self) -> int:
        self._close()
        return 0

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


# ── Public API (identical signature on every platform) ─────────────────────

async def open_mic_stream(sample_rate: int = 16000):
    """Mic capture → s16le mono PCM at ``sample_rate``.  Linux: parec.
    Windows/macOS: sounddevice (PortAudio)."""
    if _IS_LINUX:
        return await _open_mic_parec(sample_rate)
    return _SdMicStream(sample_rate, asyncio.get_running_loop())


async def open_speaker_stream(sample_rate: int = 24000):
    """Speaker playback ← s16le mono PCM at ``sample_rate``.  Linux: paplay.
    Windows/macOS: sounddevice (PortAudio)."""
    if _IS_LINUX:
        return await _open_speaker_paplay(sample_rate)
    return _SdSpeakerStream(sample_rate, asyncio.get_running_loop())
