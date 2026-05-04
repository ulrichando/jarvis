# JARVIS Voice Resilience — Design

**Date:** 2026-05-04
**Status:** approved (auto-mode)
**Scope:** Make the voice stack survive network blips, upstream-API outages, and WebRTC track-state desyncs without manual intervention. Patterns borrowed from how Discord, Twilio, LiveKit, and the AWS Well-Architected playbook handle the same failure class on cloud-scale systems.
**Goal:** When DNS / Groq / LiveKit hiccups for ≤30 seconds, JARVIS continues responding (or fails gracefully) and self-recovers afterwards — without the user having to restart `jarvis-voice-client.service` by hand.

## Background

Today's failure (2026-05-04 ~05:50 UTC):

1. DNS resolution for `api.groq.com` failed for ~30 seconds
2. STT, TTS, and LLM all errored simultaneously (shared upstream)
3. LiveKit room dropped (`session close: ConnectionTimeout Resume`)
4. Voice client tried to recover; mid-flight `track_unpublished` event arrived for a track that was already gone
5. `room.py:680` did `self.local_participant.track_publications[sid]` — `KeyError: 'TR_AMMxN69RnMdE3e'` — listener task crashed
6. Process stayed alive; systemd reported "active"; the agent had no peer to talk to
7. JARVIS appeared dead until the user manually `systemctl --user restart jarvis-voice-client.service`

This isn't an isolated bug. The research summary (research dispatched 2026-05-04, see chat-history note) covered eight production voice-AI providers. The convergent patterns:

- **Liveness must be at the listener task, not the process.** Discord, Twilio, and the systemd watchdog docs all call this out. An `asyncio` task can die while the process keeps running. Process-level health checks lie.
- **Two-tier reconnect ladder, not single-retry.** LiveKit splits ICE-restart (state preserved) from full-reconnect (`Reconnecting` → tracks unpublished/republished, SIDs change). A single retry path violates resume invariants, surfacing as the `KeyError` above. Discord's Opcode 7 / Opcode 9 protocol explicitly distinguishes the two.
- **Defensive `dict.get()` at every event boundary.** During reconnect, server and client state diverge for ~50–500ms. Every track / participant event handler must guard for missing entries. Costs nothing; eliminates the KeyError class permanently.
- **Per-upstream circuit breakers.** Portkey + Maxim's LLM-app guides + AWS REL05-BP01: each upstream gets its own breaker (closed/open/half-open) so a Groq STT outage doesn't drag TTS and LLM down with it. Cached canned responses ("one second, sir") fill the gap when the breaker is open.
- **Local DNS cache.** systemd-resolved with `Cache=yes` and a non-zero `CACHE_MIN_NEGATIVE_TTL_SEC` decorrelates the three upstream failures: even when `api.groq.com` becomes unresolvable for the resolver, recent successful lookups stay in cache.
- **Discord's silence-frame trick.** When a TTS stream is interrupted, emit ~5 frames of Opus silence before closing the audio track; prevents the next utterance from being eaten by decoder state.

What does NOT transfer (cloud tricks we can't do on a single laptop): edge selection, multi-region failover, anycast DNS, load-balancer health checks, MMR redundancy. Spec focuses only on patterns that survive when you don't have those.

## Scope

**In scope:**

- `jarvis_voice_client.py`: defensive `track_publications.get(sid)` patches; `sd_notify(WATCHDOG=1)` from inside the listener loop; two-tier reconnect ladder with backoff
- `jarvis_agent.py`: per-upstream circuit breaker around STT, TTS, LLM (closed/open/half-open with timeout + cooldown); cached canned-phrase fallback when LLM breaker is open
- `dispatching_tts.py` (or wherever the TTS pipe lives): emit ~5 silence frames before closing an interrupted TTS stream
- systemd unit edits: `WatchdogSec=10s` on both `jarvis-voice-agent.service` and `jarvis-voice-client.service`; `NotifyAccess=main`
- systemd-resolved: enable cache (Cache=yes), set min positive TTL to 60s
- Test surface: pytest cases for circuit-breaker state transitions, watchdog-emit cadence, and dict.get() guards (mocked event payloads)

**Out of scope (deferred, see Future Work):**

- Siri-style local-first ack (pre-recorded "Yes, sir?" WAV) — 1-hour add-on whenever wanted
- Local Piper TTS as last-resort fallback — adds CPU + dep cost; revisit if cloud TTS dies more than 1×/week
- Cross-region failover, anycast, multi-laptop hub
- Discord-style RTP buffering (requires owning the SFU media path)

## Architecture

### Component map

```
┌──────────────────────────────────────────────────────────────────────┐
│  jarvis_voice_client.py (peer process)                               │
│  ┌─────────────────────┐   ┌──────────────────────────────────────┐  │
│  │ LiveKit listener    │──▶│ NEW: WatchdogTask                    │  │
│  │ (room events)       │   │   sd_notify('WATCHDOG=1') every 5s   │  │
│  │ + track.get() guard │   │   only fires while listener is alive │  │
│  └─────────────────────┘   └──────────────────────────────────────┘  │
│           │                                                          │
│           ▼                                                          │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │ NEW: ReconnectLadder                                         │    │
│  │   Tier 1 (resume): rejoin with current token                 │    │
│  │     backoff: 0.5/1/2/4/10s + jitter, 5 attempts max          │    │
│  │   Tier 2 (full):   teardown room, fresh connect()            │    │
│  │     fires when Tier 1 exhausted                              │    │
│  └──────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼ WebRTC room
┌──────────────────────────────────────────────────────────────────────┐
│  jarvis_agent.py (LiveKit worker process)                            │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │ NEW: CircuitBreaker (one per upstream)                       │    │
│  │   STT  breaker → wraps groq.STT calls                        │    │
│  │   TTS  breaker → wraps _LoggingGroqTTS calls                 │    │
│  │   LLM  breaker → wraps DispatchingLLM upstream calls         │    │
│  │                                                              │    │
│  │   Each: closed → open (after N consecutive failures)         │    │
│  │         open  → half-open (after cooldown)                   │    │
│  │         half-open → closed (after first success)             │    │
│  │   When OPEN: fail-fast, return cached fallback               │    │
│  └──────────────────────────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │ NEW: silence_frames_on_close()                               │    │
│  │   ~5 frames of Opus silence before TTS stream teardown       │    │
│  └──────────────────────────────────────────────────────────────┘    │
│  + WatchdogTask (same shape as voice-client's)                       │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼ DNS
┌──────────────────────────────────────────────────────────────────────┐
│  systemd-resolved (NEW: Cache=yes, min positive TTL = 60s)           │
│    api.groq.com lookups stay in cache for 60s after last success     │
│    A 30s blip no longer correlates STT/TTS/LLM failure               │
└──────────────────────────────────────────────────────────────────────┘
```

### Defensive event-handler pattern

Every LiveKit event handler that reads a track/participant SID from a local dict gets the same shape:

```python
@room.on("track_unpublished")
def _on_track_unpublished(pub: rtc.RemoteTrackPublication, *_) -> None:
    sid = pub.sid if pub else None
    if not sid:
        log.debug("[room] track_unpublished with empty sid — ignoring")
        return
    cached = room.local_participant.track_publications.get(sid)
    if cached is None:
        # Race: server emitted the event AFTER we already removed the
        # track locally during a reconnect. Idempotent no-op.
        log.debug(f"[room] track_unpublished for unknown sid={sid} — ignored")
        return
    # … real handling …
```

Apply the same pattern to: `track_published`, `track_subscribed`, `track_unsubscribed`, `participant_connected`, `participant_disconnected`. Every handler that touches `track_publications`, `tracks`, or `remote_participants` by SID.

### Watchdog task

```python
async def _watchdog_loop(stop: asyncio.Event):
    """Notify systemd while the listener loop is alive. systemd's
    WatchdogSec=10s setting kills + restarts us if we miss two pings."""
    notifier = sdnotify.SystemdNotifier()
    notifier.notify("READY=1")
    while not stop.is_set():
        notifier.notify("WATCHDOG=1")
        try:
            await asyncio.wait_for(stop.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
    notifier.notify("STOPPING=1")
```

Critical: this task runs in the SAME `asyncio` loop as the LiveKit listener. If the listener crashes and the loop stalls, the watchdog stops firing → systemd restarts us within 10s. If we used a separate thread, a stalled listener wouldn't trigger restart.

### Two-tier reconnect ladder (voice-client side)

```python
async def _supervised_session():
    backoffs = [0.5, 1, 2, 4, 10]  # tier-1 resume attempts
    full_reconnect_count = 0
    while True:
        try:
            await _run_one_session()  # connects + listens until disconnect
            full_reconnect_count = 0  # clean exit
        except RoomDroppedError:
            for delay in backoffs:
                jitter = random.uniform(0, delay * 0.3)
                await asyncio.sleep(delay + jitter)
                if await _try_resume():
                    break
            else:
                # All resume attempts failed → full teardown + fresh connect
                full_reconnect_count += 1
                if full_reconnect_count > 3:
                    log.error("3 full reconnects in a row — bailing for systemd")
                    raise SystemExit(1)
                await _full_teardown_and_reconnect()
```

Maps to LiveKit's published model: ICE-restart for tier-1 (cheap, state preserved); full reconnect with new SIDs for tier-2.

### Circuit-breaker (per upstream)

```python
class CircuitBreaker:
    """Wraps an awaitable. closed → open after N consecutive failures
    or one timeout. open → half-open after `cooldown_s`. half-open →
    closed on first success, → open on first failure."""
    def __init__(self, name, fail_threshold=3, cooldown_s=20, timeout_s=8):
        self.name = name
        self.state = "closed"
        self.failures = 0
        self.opened_at = 0.0
        self.fail_threshold = fail_threshold
        self.cooldown_s = cooldown_s
        self.timeout_s = timeout_s

    async def call(self, fn, *args, fallback=None, **kw):
        if self.state == "open":
            if time.time() - self.opened_at < self.cooldown_s:
                if fallback is not None:
                    return await fallback()
                raise CircuitOpenError(self.name)
            self.state = "half-open"
        try:
            result = await asyncio.wait_for(fn(*args, **kw), timeout=self.timeout_s)
            self._reset()
            return result
        except Exception:
            self._record_failure()
            raise

    def _record_failure(self):
        self.failures += 1
        if self.failures >= self.fail_threshold or self.state == "half-open":
            self.state = "open"
            self.opened_at = time.time()
            log.warning(f"[breaker:{self.name}] opened")

    def _reset(self):
        if self.state != "closed":
            log.info(f"[breaker:{self.name}] closed")
        self.state = "closed"
        self.failures = 0
```

Three instances live at module scope in `jarvis_agent.py`:
- `_STT_BREAKER` (cooldown 20s, threshold 3)
- `_TTS_BREAKER` (cooldown 20s, threshold 3)
- `_LLM_BREAKER` (cooldown 30s, threshold 2 — LLM failures are usually slower to recover)

**Wiring strategy.** Each breaker wraps the upstream HTTP call site, not the LiveKit-level component. Concretely:

- **STT.** Subclass `groq.STT` (mirroring the existing `_LoggingGroqTTS` shim at [jarvis_agent.py:339](src/voice-agent/jarvis_agent.py#L339)) — override `_recognize_impl`, route through `_STT_BREAKER.call()`. On `CircuitOpenError`, raise the same `APIConnectionError` the FallbackAdapter already knows how to handle, so the existing fallback chain takes over fast.
- **TTS.** Extend `_LoggingGroqTTS._run` (already wraps the HTTP post) to call through `_TTS_BREAKER.call()`. Same `CircuitOpenError` → `APIConnectionError` mapping. The existing `FallbackAdapter` chain (Groq → EdgeTTS) absorbs the rest.
- **LLM.** Wrap the `groq.LLM.chat` (or `DispatchingLLM` invocation) call site with `_LLM_BREAKER.call()`. When open, return the cached canned-phrase WAV via the agent's reply pipeline instead of calling Groq.

The principle: breakers fail FAST (8s timeout instead of LiveKit's default 30s) and surface `APIConnectionError`, which lets `FallbackAdapter` and the user-facing canned-phrase path activate within seconds, not tens-of-seconds.

### Cached canned-phrase fallback

When the LLM breaker is open AND TTS is still working, JARVIS speaks a cached canned phrase rather than going silent. Three pre-rendered WAVs at `~/.jarvis/cache/voice/`:

- `one_second.wav` — "One second, sir."
- `connection_unstable.wav` — "Connection unstable, sir."
- `try_again.wav` — "Could you try that again, sir?"

These are rendered by a one-shot script (`scripts/render-canned-phrases.py`) that uses Groq TTS while it's healthy and saves the output. If they don't exist on disk, the breaker-open path falls back to silence (no synthesis attempt, fail-fast).

### Silence frames on TTS interrupt

In `dispatching_tts.py` (or wherever the TTS pipe is torn down), before closing an Opus stream:

```python
SILENCE_FRAME = b'\xf8\xff\xfe'  # Opus 20ms silence
async def _close_tts_stream(stream):
    for _ in range(5):
        try:
            await stream.push_frame(SILENCE_FRAME)
        except Exception:
            break
    await stream.close()
```

5 × 20ms = 100ms of silence; long enough for the decoder to flush state, short enough that the user doesn't perceive lag.

### systemd unit changes

`jarvis-voice-agent.service` and `jarvis-voice-client.service` both add:

```ini
[Service]
Type=notify
NotifyAccess=main
WatchdogSec=10s
```

Plus a Python dependency: `sdnotify` (pure-Python, no compiled deps).

### systemd-resolved cache

`/etc/systemd/resolved.conf` (or drop-in at `/etc/systemd/resolved.conf.d/jarvis.conf`):

```ini
[Resolve]
Cache=yes
DNSStubListener=yes
CacheFromLocalhost=no
```

systemd-resolved's default cache TTL respects DNS records; we don't override beyond enabling caching.

## Data flow (failure-recovery walkthroughs)

### Walkthrough A: 30s DNS blip (today's exact scenario)

```
T+0s:  DNS to api.groq.com fails
T+0s:  STT call → timeout in 8s → STT breaker counts 1 failure
T+0s:  TTS call → timeout in 8s → TTS breaker counts 1 failure
T+0s:  LLM call → timeout in 8s → LLM breaker counts 1 failure
T+8s:  Each breaker fires; STT/TTS/LLM all OPEN
T+8s:  Next user turn: LLM breaker open → speak "one second, sir" via cached WAV
T+15s: LiveKit room session drops (ConnectionTimeout)
T+15s: voice-client receives `disconnected` event
T+15s: ReconnectLadder enters tier-1 (resume); backoff 0.5s + jitter
T+16s: First resume attempt fails (DNS still blipping); back off 1s
T+17s: Second attempt fails; back off 2s
T+19s: Third attempt fails; back off 4s
T+23s: Fourth attempt fails; back off 10s
T+33s: DNS recovered; fifth attempt succeeds
T+33s: Track events fire; defensive .get() handles any stale SIDs
T+33s: Watchdog still pinging (loop never stalled)
T+38s: STT breaker cooldown elapses → half-open; first user audio succeeds → closed
T+40s: TTS, LLM breakers similarly recover
T+45s: Normal operation; user never had to restart anything
```

### Walkthrough B: Listener task crashes silently (the original bug)

```
T+0s:   track_unpublished event arrives for already-removed SID
T+0s:   Old code: KeyError → listener task crashes → no more events processed
T+5s:   Watchdog loop tries to fire — but it's in the same asyncio loop
        as the listener, and the loop is wedged trying to handle the
        unhandled exception. WATCHDOG=1 not sent.
T+10s:  systemd's WatchdogSec=10s expires → SIGTERM → process restart
T+15s:  Voice-client back up, fresh listener, JARVIS responds again

NEW code with .get() guard: the KeyError never happens; listener stays
healthy; watchdog keeps firing; no restart needed.
```

## Trade-offs

**Why three independent breakers instead of one shared "Groq is down" breaker?**
- The three upstreams (STT, TTS, LLM) hit different Groq endpoints behind different paths. They DO often share fate (DNS), but not always (one model in the LLM pool can throttle while STT/TTS are fine). Independent breakers let TTS stay "closed" when only LLM is rate-limited; the user gets to hear the canned phrase rather than full silence.
- Cost: 3× state to manage. Verdict: worth it.

**Why systemd watchdog instead of a Python-level supervisor?**
- A Python supervisor that watches the listener has the same bug class — what watches the watcher?
- systemd is the OS-level supervisor; it can't be killed by anything inside the agent process.
- Cost: requires `Type=notify` + `sdnotify` dep. Trivial.

**Why pre-rendered canned WAVs instead of local TTS (e.g. Piper)?**
- Piper adds a 200MB voice model + ~10% steady CPU + a process restart on model swap. Heavy for a fallback path.
- 3 × ~50KB WAVs in `~/.jarvis/cache/voice/` cost nothing. Limited vocabulary is fine — the breaker-open state should be rare and brief.
- If the user wants more fallback variety later, that's the deferred Piper work.

**Why a 60s minimum DNS cache instead of a longer one?**
- Longer caches help during outages but hurt when an upstream IP genuinely changes. 60s is the sweet spot for our usage.
- Recent versions of `systemd-resolved` already cache aggressively; the change is mostly enabling it explicitly + verifying.

**Why backoffs of 0.5/1/2/4/10s?**
- Matches Twilio JS SDK published guidance + LiveKit's documented reconnect cadence.
- 5 attempts cover ~17s total + jitter, comfortably absorbing a 30s outage when combined with the watchdog kickstart.

## Testing

Eight new pytest cases:

1. `test_circuit_breaker_opens_after_threshold_failures` — call() raising N times → state=open
2. `test_circuit_breaker_half_open_after_cooldown` — open → wait > cooldown_s → call() → state=half-open
3. `test_circuit_breaker_closes_on_half_open_success` — half-open → call succeeds → state=closed
4. `test_circuit_breaker_reopens_on_half_open_failure` — half-open → call fails → state=open
5. `test_circuit_breaker_returns_fallback_when_open` — open + fallback provided → returns fallback result
6. `test_track_unpublished_with_unknown_sid_no_crash` — defensive guard, mocked LiveKit Room
7. `test_watchdog_emits_while_loop_alive` — sd_notify mock; verify WATCHDOG=1 cadence under healthy + stalled-listener simulations
8. `test_reconnect_ladder_falls_through_to_full_after_max_resume_attempts` — mock LiveKit room with 5 resume failures → assert full_teardown was called

Live verification (manual, post-deploy):

- Block DNS for 30s with `iptables -A OUTPUT -p udp --dport 53 -j DROP` (revert with `-D`); confirm JARVIS speaks the cached canned phrase, recovers, and the user never has to restart anything.
- `kill -STOP $(pgrep -f jarvis_voice_client)` for 15 seconds; confirm systemd watchdog kills + restarts.
- Restart `jarvis-hub.service` mid-session; confirm voice-client reconnects via the ladder.

## Future work (out of scope)

- Siri-style local ack: pre-recorded `yes_sir.wav` for bare-vocative response, served without touching cloud STT/TTS. ~1h work.
- Local Piper TTS for breaker-open fallback richness. Adds dep + CPU cost; revisit when the canned-phrase set proves insufficient.
- Per-call latency telemetry → adaptive `timeout_s` (today: hardcoded 8s).
- Discord-style RTP receive-buffer for 1–2s of mid-flight media recovery (requires owning the SFU; we use upstream LiveKit binary).
- Cross-laptop hub failover. N/A until there's a second laptop.
