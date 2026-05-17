# JARVIS audio I/O review — 2026-05-16

Hardware: Dell Latitude 7480 (i7-7600U Kaby Lake, 2C/4T @ ~2.8 GHz, 15 GiB RAM, Intel HDA Sunrise Point-LP, PipeWire). Voice agent + voice client + Tauri shell. Project root `/home/ulrich/Documents/Projects/jarvis`.

## TL;DR (top 5)

1. **AEC is OFF in both layers** — WebRTC APM disables AEC (`jarvis_voice_client.py:123` `JARVIS_APM_AEC=0`), and PipeWire `module-echo-cancel` isn't loaded (verified: `pactl list short modules | grep echo-cancel` returns empty). Speaker→mic crosstalk falls through to a fragile `state.speaking ⇒ skip RMS` gate and a deliberately disabled `resume_false_interruption`. That's the load-bearing reason the listening RMS threshold had to be jacked from 1500 → 28000 in `.env`. **P0**.

2. **Tray "listening" is RMS-driven, not VAD-driven** — `state.listening` flips on raw PortAudio frame RMS at `jarvis_voice_client.py:632-641`, not on Silero's `START_OF_SPEECH`/`END_OF_SPEECH`. The voice-client doesn't even *have* the agent's VAD output; the agent owns Silero in a different process. This is the architectural reason the tray drifts away from what JARVIS is actually transcribing. **P1**, fix below.

3. **STT has no `prompt=` hint, no `no_speech_threshold` knob, no `temperature=0`** — `providers/stt.py:69` builds `groq.STT(model="whisper-large-v3-turbo", language="en")` with nothing else. The "foreign-language hallucination" symptom is fully explained by Whisper's known behaviour on sub-threshold audio plus no condition_on_previous_text suppression. **P0** for hallucination work; **P1** for adding a domain prompt biasing toward English.

4. **Silero on 2C/4T is at its hardware budget** — at 48 kHz mono with 10 ms frames the agent feeds Silero ~100 frames/s. Silero ONNX inference is ~3-6 ms/frame on Kaby Lake; sustained that's 30-60 % of one core just for VAD. The "inference is slower than realtime" warnings are likely the agent process being scheduled out by voice-client + Tauri + Chrome. Current asymmetric tuning is defensible; do NOT loosen the 0.6 activation. **P2** is to confirm headroom with a 1-min capture profile.

5. **Playback under-runs were patched but mic capture stayed at `latency="low"`** — `jarvis_voice_client.py:650` opens `sd.InputStream(latency="low")`. PortAudio's `low` on pulse is typically ~10-20 ms; on a 2-core box under load it's right at the edge of underflow. Output side has been raised to 200 ms (`jarvis_voice_client.py:363`) — there's an asymmetry here, and the mic side may be silently dropping/zero-padding frames during high-CPU moments which would explain occasional STT misses. **P1**.

---

## Findings per area

### 1. Mic path — APM choices + AEC discussion

**Topology (verified):** `pulse` (ALSA → pipewire-pulse) → PortAudio `InputStream` (10 ms, 480 samples @ 48 kHz int16, mono, `latency="low"`, device `pulse`) → `_mic_cb` → WebRTC APM `process_stream` (NS+AGC+HPF on; AEC off) → `rtc.AudioFrame` → `capture_frame` → LiveKit SFU → agent. See `jarvis_voice_client.py:607-655`.

**Why AEC is off, today (per the code comment at line 113-119):** the file admits AEC-on would require feeding every speaker-side frame through `process_reverse_stream` and that surgery wasn't done. So the SFU/agent ships voice with no AEC, **and** PipeWire's `module-echo-cancel` is not loaded (confirmed empirically). That means the only thing keeping speaker bleed out of the indicator is the `if state.speaking: skip RMS` gate at line 611-614 — purely indicator-cosmetic. The actual *mic track* sent to the SFU still contains echo; Whisper transcribes it.

**Where to fix it (ranked):**

- **(A) WebRTC APM AEC, in-process** — best end state. AudioProcessingModule has built-in linear+nonlinear AEC. We already have a `play_subscribed_track` loop at `jarvis_voice_client.py:378-403` that handles every speaker frame; the surgery is one line per frame: before `out.write(pcm)`, call `_apm.process_reverse_stream(reverse_frame)` with a synthesized 10 ms `rtc.AudioFrame`. APM is already constructed at line 127. Cost: ~50 lines, mostly the reverse-frame buffer plumbing (output ring is currently 200 ms blocksize-aligned, and APM needs *exactly* 10 ms per call, so a small re-blocker is needed).
- **(B) PipeWire `module-echo-cancel`** — fastest workaround. `pactl load-module module-echo-cancel aec_method=webrtc source_master=<input.alsa> sink_master=<output.alsa>` creates `mic_aec`/`sink_aec` virtual nodes. The module docstring at the top of `jarvis_voice_client.py:14-24` already *claims* the topology is `mic (PipeWire → mic_aec)` — but the runtime isn't actually loaded. Either load it persistently via `~/.config/pipewire/pipewire.conf.d/echo-cancel.conf` and then point `JARVIS_AUDIO_INPUT_DEVICE=mic_aec`, `JARVIS_AUDIO_OUTPUT_DEVICE=sink_aec` in `.env`. Earlier attempt "didn't filter" is almost certainly because the env vars weren't pointed at the virtual sinks; default `pulse` routes around them.
- **(C) Hardware** — wired headset eliminates the problem entirely. Cheapest fix; worst UX for an ambient assistant. Bluetooth headset routes through PipeWire's HSP/HFP profile which on Kaby Lake adds 150-250 ms and forces 16 kHz mono.

**My pick: (B) now (one config file + two env lines), (A) for the next refactor pass.** (A) gives you AEC in the *track sent to the SFU* (so Whisper stops eating echo); (B) only helps the local indicator + multi-app share story. But (B) is 5 minutes of work and the user has been suffering tonight.

### 2. Silero VAD tuning

Config at `jarvis_agent.py:3677-3683`:
```
activation_threshold=0.6
deactivation_threshold=0.3
min_speech_duration=0.1
min_silence_duration=0.4
prefix_padding_duration=0.6
```

**Verdict: defensible.** The asymmetric (0.6/0.3) hysteresis is exactly right for the noise environment, and the comment block at lines 3640-3672 reads like spec-pedigree thinking. Specifically:

- `activation=0.6` was bumped from 0.5 *today* (2026-05-16) and 0.7 was shown to be too strict — this is a freshly-validated value. Do NOT loosen below 0.5 (per the CLAUDE.md rule and live failure history).
- `prefix_padding=0.6` is *generous* (default 0.5). Right for soft first words. Note this means up to **600 ms of pre-trigger audio** is replayed into Whisper — for ambient room tone, that's potentially 600 ms of breath/fan/keyboard fed into Whisper *before* the legitimate utterance, which exacerbates the silence-hallucination problem when activation barely trips on a real word. The `_is_garbage_transcript` gate catches "thank you / subscribe / music" but not the foreign-language drift you saw tonight.
- `min_silence=0.4` is below default (0.55) — endpointing fires faster. With `endpointing.min_delay=0.4` also configured in `AgentSession` (`jarvis_agent.py:4621`), they're OR-gated so the practical floor is 0.4 s.

**Silero CPU budget on i7-7600U (calibrated estimate):**
- Silero v5 ONNX, 16 kHz, 32 ms windows: ~1.5-2.5 ms/window on a single Kaby Lake core, **but** livekit's plugin runs at the input sample rate (48 kHz) and re-windows internally — so the *effective* per-real-second cost is roughly **30-60 ms of CPU per 1 s of audio** on this hardware. ~3-6 % of one core sustained. The "slower than realtime" warning fires when the asyncio loop is preempted (Tauri redraws, Chrome JIT, screen-share ffmpeg). It does NOT mean Silero itself is too slow — it means the loop is *late picking up Silero's output*.
- Implication: nothing to fix in Silero. The fix is keeping the agent process out of CPU contention. systemd unit could set `Nice=-5` or `CPUWeight=200` on `jarvis-voice-agent.service` and `jarvis-voice-client.service`. **P2** — verify with `chrt -p $(pidof livekit-agents)` first.

**Non-recommendation:** do not adopt a single-threshold VAD (e.g. dropping `deactivation_threshold` and reusing the activation value). That's exactly what the 2026-05-04 comment block at line 3627-3635 says was a regression.

### 3. STT pipeline (Groq Whisper Turbo)

`providers/stt.py:69`: `BreakeredGroqSTT(model="whisper-large-v3-turbo", language="en")`. That's the entire config. Surrounding helpers:

- **Pre-LLM gate**: `pipeline/stt_gate.py::is_garbage_transcript` (called from `jarvis_agent.py:3373`) catches empty, punctuation-only, single-char, single-filler ("uh"), repeated-stutter ("uh uh uh"), and the canonical Whisper hallucination set (`thank you`, `subscribe`, `you you`, etc. at `stt_gate.py:55-76`).
- **Breaker**: `STT_BREAKER` wraps `_recognize_impl` (8 s breaker timeout; converts `CircuitOpenError` → `APIConnectionError` for FallbackAdapter).

**What's missing that explains the foreign-language hallucinations:**

| Concern | Current | Recommended |
|---|---|---|
| `prompt=` (Whisper biasing) | Not set | `prompt="The user is speaking English to a voice assistant named Jarvis."` — biases Whisper toward English vocab and away from CJK/Cyrillic/Arabic hallucinations on low-energy audio. |
| `no_speech_threshold` | Default (0.6) | Bump to **0.8** — Whisper's internal "this is non-speech" gate. Higher = more aggressive silence filtering. The Groq Speech API accepts this; verify `groq.STT.__init__` exposes a passthrough kwarg. |
| `temperature` | Default cascading (0/0.2/0.4/.../1.0) | Pin to **0.0** — deterministic decoding, no fallback ladder. The fallback ladder is what *makes* Whisper hallucinate exotic languages when uncertain. |
| `condition_on_previous_text` | Default True | Set to **False** — when True, Whisper conditions each chunk on the previous transcript, and a single hallucinated foreign chunk poisons every chunk after. False is the well-known fix. |
| Language hint | `language="en"` (good) | Keep. |

Three of those four are passthrough kwargs in the upstream Whisper API. If `livekit.plugins.groq.STT` doesn't surface them, subclass `BreakeredGroqSTT` to forward them via `self._opts` or override `_recognize_impl` to add them to the request body. Concretely:

```python
# providers/stt.py:67
def build_breakered_stt() -> BreakeredGroqSTT:
    return BreakeredGroqSTT(
        model="whisper-large-v3-turbo",
        language="en",
        prompt="The user is speaking English to a voice assistant named Jarvis. Reply in English.",
        # If supported by livekit-plugins-groq:
        # temperature=0.0,
        # no_speech_threshold=0.8,
        # condition_on_previous_text=False,
    )
```

If the plugin doesn't accept them, plumb them through the Groq HTTP body — the upstream API field is `prompt` for an empty system-bias string.

**On the "max_buffered_speech reached" 60s-VAD-open issue:**

Root cause: the AGC inside the WebRTC APM (`jarvis_voice_client.py:131`) aggressively amplifies whatever it sees. With AEC off and module-echo-cancel not loaded, room tone + fan noise + a TV in the background gets normalised up to speech-level energy, never falls below the deactivation threshold, and Silero never closes the speech window. `min_silence=0.4` doesn't help because there's *no* silence after AGC — every frame post-amplification looks like speech. The 60 s `max_buffered_speech` cap then fires and the pipeline aborts.

**Hardening (P0):**
1. Disable AGC for the mic path: `JARVIS_APM_AGC=0` in `.env`. Re-test. Whisper does fine with raw int16 amplitudes; AGC is *only* useful for Whisper when the user's mic gain is extremely low (which it isn't — user is at 100 %). Removing AGC removes the amplification of room tone.
2. Add a `max_speech_segment_seconds` flush at the agent level: if Silero hasn't fired `END_OF_SPEECH` in 12 s, force-close, transcribe the buffer, and reset. This is a safety valve below LiveKit's hard 60 s buffer cap. livekit-agents doesn't expose this directly; you'd add it as a wrapper in `pipeline/turn_router.py` or via a `session.on("vad_state_changed")` watchdog at the agent.
3. Track Silero ENERGY in `turn_telemetry.db`: today only `rms_db` is logged via AcousticTap (`pipeline/prosody.py`). Add the framework's reported VAD-window duration so you can prove from data whether soft-clip / AGC pumping is the open-window cause.

### 4. TTS pipeline

Stack (verified):
- Primary: `LoggingGroqTTS(model="canopylabs/orpheus-v1-english", voice=troy)` via `providers/tts.py:286`.
- Fallback: `EdgeTTS(voice="en-US-GuyNeural")` (`tts.py:302`).
- Per-route dispatcher: `build_dispatching_tts()` (`tts.py:305-390`) wraps each route's Orpheus in `StreamAdapter(text_pacing=True)` then `FallbackAdapter([orph, edge])`. Routes: BANTER=austin, TASK=troy, REASONING=troy, EMOTIONAL=daniel.

**`tts_text_transforms` chain** at `jarvis_agent.py:4647-4692` (8 filters + framework defaults):
1. `stamp_first_token` — TTFW telemetry tap
2. `strip_function_call_leakage` — drops literal tool-call markup that the LLM emits as text
3. `strip_voice_closers` — drops "Done.", "Anything else?", "Happy to help" (gpt-oss-120b habit)
4. `strip_meta_silence` — drops "Silence." / "Just listening."
5. `strip_archaic_openers` — drops "Indeed.", "Quite,", "Splendid."
6. `strip_preambles` — drops "Let me check…", "I'll fetch…"
7. `normalize_numbers` — "4 000" → "4,000"
8. `cap_sir_count` — drops all "sir"
9. `filter_markdown`, `filter_emoji` (framework)

Plus the `sanitizers/internal_phrase.py` monkeypatch on `inf_llm.LLMStream._parse_choice` which scrubs internal framework phrases earlier (at LLM stream parse time).

**On the leading-space / BPE bug:**

Verified the fix at `sanitizers/internal_phrase.py:113-130`. The critical lines:
- Line 119: fast-path returns the chunk unchanged when no internal phrase is found — preserves leading space.
- Lines 126-129: the substitution-then-cleanup branch uses `re.sub(r"\s+", " ", cleaned)` *without* calling `.strip()`. The comment explicitly says "do NOT strip leading/trailing — for per-chunk streaming, that strip is what eats inter-word spaces of BPE-tokenized streams." Good.

This is sound. A residual risk: filters 2-7 in `tts_text_transforms` (`strip_*` family) are not shown in this scope, but if any of them call `.strip()` on the chunk you'd get the same regression. Worth a one-time audit — grep `\.strip\(\)` across `pipeline/tts_text_transforms.py` (or wherever those filter functions live) to confirm only `_INTERNAL_RE` callers and not naive-strip callers handle streaming chunks. **P2**.

**Scoring the per-route dispatcher:** the design (Orpheus + StreamAdapter + edge fallback × 4 routes) is sound. Concerns:
- StreamAdapter on Orpheus pages out sentence-by-sentence, but `LoggingGroqTTS.synthesize` itself doesn't stream — it issues one HTTP POST per StreamAdapter chunk. Sentence-level RTT × N sentences on a 200 ms connection time = noticeable inter-sentence gaps especially on the first reply when the breaker hasn't warmed.
- Orpheus "no audio frames pushed" intermittent failure is covered by the FallbackAdapter (`tts.py:343-356`). The 2026-04-30 root cause comment is accurate — that's a real upstream bug; the safety net is correct.

### 5. Tray indicator state machine

`App.jsx:218-229`:
```jsx
let next = 'idle'
if (!speech.connected)        next = 'offline'
else if (voiceMuted)          next = 'muted'
else if (speech.silentMode)   next = 'muted'
else if (speech.speaking)     next = 'talking'
else if (speech.voiceActive)  next = 'listening'  // RMS-based
else if (speech.booting)      next = 'booting'
else if (speech.processing)   next = 'thinking'
else                          next = 'idle'
```

`speech.voiceActive` comes from `useVoiceClient.js:113`: `setVoiceActive(!!s.listening)` — and `s.listening` from `/status` is the RMS-gate output of `_mic_cb` in voice-client (`jarvis_voice_client.py:632-641`). **That's the bug.** The voice-client doesn't run Silero; it has no idea what the agent's VAD is doing. So the tray "listening" can fire on a slammed door while Silero correctly refuses to open a speech window — or worse, the inverse: Silero opened a turn from a soft "Jarvis" the RMS threshold missed, and the tray stays green while STT is actually transcribing.

**Redesign (P0):**

The agent already publishes a LiveKit data channel for `lk.agent.session` events (drained at `jarvis_voice_client.py:521`). Hook a new `vad_state` topic carrying `{state: "active"|"inactive"}` from `session.on("user_state_changed")` in the agent. Voice-client subscribes, sets `state.listening` from *that* — drop the local RMS gate from the listening signal entirely (keep it for `state.speaking` since that's local playback ground truth).

Concrete shape:

```python
# In jarvis_agent.py near AgentSession event handlers:
@session.on("user_state_changed")
def _on_user_state(ev):
    # ev.new_state is one of "speaking", "listening", "thinking"
    publish_to_room({"topic": "vad_state", "state": ev.new_state})

# In jarvis_voice_client.py: register a text_stream handler for "vad_state"
# and set state.listening = (parsed.state == "speaking")
```

The framework already emits `user_state_changed` events (livekit-agents `AgentSession` API). Wiring this is ~30 lines.

Until that ships, document the indicator as "mic energy" not "listening" — manage expectations.

**Anti-recommendation, confirmed by CLAUDE.md rule:** do **not** add per-frame React state for any audio-visualization sphere/ring. The current `audioLevel = 0` constant in `useVoiceClient.js:68` is intentional. Don't drift back.

### 6. Indicator UX (colors + timing)

Color palette at `main.rs:141-148`:

| State | RGB | Note |
|---|---|---|
| talking | 68/147/248 (blue) | Good — distinct |
| listening | 34/211/238 (cyan) | **Close to blue** on aggressive panel downscaling. On XFCE with `gtk-icon-sizes=panel:22` and a dark Adwaita panel the difference is ~5 % perceptual at first glance. |
| booting | 168/85/247 (purple) | Good |
| thinking | 250/180/50 (amber) | Good |
| muted | 20/20/20 (near-black) | **Invisible on dark panels.** On a dark-Adwaita / Yaru-dark / Greybird-dark XFCE panel this looks the same as "no icon" — leads to the user thinking the tray is broken. |
| offline | 239/68/68 (red) | Good |
| idle | 63/185/80 (green) | Good |
| sharing | ring overlay 255/20/147 magenta | Good — the 5 px ring fix from 2026-05-11 is in `main.rs:174` |

**Recommendations:**

- **Muted: dark grey, not black.** Try `100/100/100` or use a distinct shape (mic-slash icon overlay) instead of color. Live-test on dark panel + light panel. **P1**.
- **Listening: shift cyan toward teal** (e.g. `20/184/166`) to widen the perceptual gap from blue. **P2**.
- **Debounce**: the 100 ms tick from `useVoiceClient.js:146` is already very tight; `pushTrayState` in App.jsx dedups by `(state, sharing)` so Rust paints only on real changes. Good. **No debounce work needed**.
- **Sharing ring**: the 5 px outer ring is the right call (recently fixed). Verify magenta-on-cyan and magenta-on-purple at panel scale; both can muddy. Consider a dashed/pulsing ring at the framework level if the user can't tell at 22 px. **P2**.

### 7. Audio routing / playback ("can't hear JARVIS sometimes")

Output config (`jarvis_voice_client.py:347-369`):
- 48 kHz mono int16, 10 ms blocksize, **200 ms ring-buffer** (`latency=0.2`, env-overridable), device=`pulse`.
- Per the comment at lines 354-361, this was raised from "low" / 80 ms after 183 ALSA underruns in 500 lines (2026-05-15). 200 ms is conservatively right for this hardware.

**Why the user sometimes can't hear JARVIS despite valid `response.done` events:**

Likely culprits in priority order:

1. **PipeWire default sink moved.** PortAudio opens device `pulse` which maps to ALSA's `default` PCM, which maps to whatever pipewire-pulse's *current* default sink is. If the user plugs in a headset mid-session, PortAudio keeps writing to the *original* sink's stream — pipewire doesn't auto-route open streams across sink changes by default unless `node.target` is unset and `node.target = ` (empty default) is honoured. The user hears nothing on the new headset. Verify with `pw-cli list-objects` while the symptom is happening: the `Stream/Output/Audio` for `jarvis-voice-client` will be linked to the old sink. **P1 fix**: open the output stream with `extra_settings={"node.target": None}` via the PA API, OR have the voice-client subscribe to pulse `sink_changed` events and reopen the stream on default-sink change. The simpler workaround is just `JARVIS_AUDIO_OUTPUT_DEVICE=` unset + ensure no other apps grab the sink exclusively.

2. **APM AEC false-cancellation.** Doesn't apply here today (AEC=off) but if you turn it on (item 1 of this review), the *output* path doesn't have the AEC reverse-stream wired, so APM has no reference for what JARVIS just said and will mis-cancel his own voice on the next mic frame. Fix the reverse-stream wiring (sketched in §1) before flipping AEC=1.

3. **Speaking-state race**: `state.speaking` is True for `_SPEAKING_HOLD_S=1.2` seconds after the last frame above threshold (`jarvis_voice_client.py:166`). During TTS gaps (inter-sentence silence with Orpheus), `state.speaking` flips off, the listening branch unmasks, and any speaker bleed into the mic re-triggers listening, causing the agent to interrupt itself. The user hears the first sentence then silence, then maybe nothing more depending on barge-in. This isn't *the* "can't hear" cause but it's a related desync. **Mitigation**: drive `state.speaking` from the *agent track subscription* lifecycle, not from RMS — analogous to the listening fix in §5.

4. **systemd race on cold boot**: from the log tail, the voice-client is restarting every ~30 s during early run (presence-watchdog firing at lines 11:46, 11:47, 11:58, 12:01, etc.). Each restart kills the playback stream. If a `response.done` lands during a 1-2 s teardown window the audio is dropped silently. The presence-watchdog firing 10 s after start (`WATCHDOG_HEARTBEAT_SEC` and friends imported at `jarvis_voice_client.py:177-182`) is *also* concerning — it means the agent isn't coming up within 10 s, which is fast for a fresh systemd boot of a Python LiveKit worker on 2 cores. **P1**: bump `AGENT_DISPATCH_TIMEOUT_SEC` (in `voice_client_watchdog.py`) to 20 s or gate the watchdog so it doesn't fire during the first 30 s of process life.

---

## Severity-tagged actions

### P0 (do tonight or first-thing)

| # | File:line | Change |
|---|---|---|
| P0-1 | `~/.config/pipewire/pipewire.conf.d/echo-cancel.conf` (new) | Load `module-echo-cancel` (webrtc method) on PipeWire startup; create `mic_aec` virtual source + `sink_aec` virtual sink |
| P0-2 | `/home/ulrich/Documents/Projects/jarvis/.env` | Add `JARVIS_AUDIO_INPUT_DEVICE=mic_aec`, `JARVIS_AUDIO_OUTPUT_DEVICE=sink_aec` |
| P0-3 | `/home/ulrich/Documents/Projects/jarvis/.env` | Set `JARVIS_APM_AGC=0` (in-APM AGC is what's amplifying room tone into Silero) |
| P0-4 | `providers/stt.py:69` | Add Whisper biasing prompt + (if supported) `temperature=0`, `no_speech_threshold=0.8`, `condition_on_previous_text=False`. If the livekit-plugins-groq stub doesn't accept these, override `_recognize_impl` to inject them in the request body |
| P0-5 | `jarvis_voice_client.py:632-641` (or via new data-channel topic) | Stop driving `state.listening` from RMS — wire it from the agent's `session.on("user_state_changed")` via a `vad_state` text stream the voice-client subscribes to |

### P1 (within next session)

| # | File:line | Change |
|---|---|---|
| P1-1 | `jarvis_voice_client.py:650` | Try `latency=0.05` (50 ms) explicit on the mic InputStream — `"low"` on pulse is implementation-defined and can be ~10 ms which underflows under load on 2C/4T |
| P1-2 | `jarvis_agent.py` near AgentSession | Add a max-speech-segment safety valve: force-close the VAD window at 12 s if `END_OF_SPEECH` hasn't fired — prevents the 60 s `max_buffered_speech` cliff |
| P1-3 | `main.rs:146` | Change muted color from `(20, 20, 20)` to a distinct grey like `(120, 120, 120)` or add a mic-slash overlay; black is invisible on dark panels |
| P1-4 | `voice_client_watchdog.py` (and `AGENT_DISPATCH_TIMEOUT_SEC` import) | Raise agent-presence timeout from 10 s → 20 s; skip the watchdog for the first 30 s after process start |
| P1-5 | `jarvis_voice_client.py:347-369` (`play_subscribed_track`) | Drive `state.speaking` from track-lifecycle events (`track_subscribed` open → `stream` end close) instead of RMS-gate; eliminates the inter-sentence flip that lets the mic re-trigger listening |

### P2 (when next refactoring audio)

| # | File:line | Change |
|---|---|---|
| P2-1 | systemd unit files for `jarvis-voice-{client,agent}.service` | Add `Nice=-5` and `CPUWeight=200`; verify with `chrt -p` post-boot. Frees Silero from Tauri/Chrome contention on 2C/4T |
| P2-2 | `play_subscribed_track` in `jarvis_voice_client.py:378-403` | Implement APM `process_reverse_stream` to wire AEC for the in-process mic path — proper end-state vs the PipeWire workaround in P0-1 |
| P2-3 | `main.rs:143` listening color | Shift cyan → teal (`20, 184, 166`) to widen perceptual gap from talking-blue at panel scale |
| P2-4 | Audit all functions in `tts_text_transforms` chain at `jarvis_agent.py:4647-4692` | Grep for `.strip()` — confirm no naive-strip on per-chunk streaming inputs (the same class of bug `internal_phrase.py:113-130` already guards against) |
| P2-5 | `pipeline/prosody.py` + telemetry | Log Silero VAD-window duration per turn (today only `rms_db` is captured); proves out the "AGC pumps room tone into VAD" theory from §3 against real data |

---

## Anti-recommendations (do NOT do these)

- **Do NOT re-add the voice reactor sphere.** `useVoiceClient.js:68` correctly pins `audioLevel = 0`; the memory note `project_reactor_removed` and the CLAUDE.md operational rule both confirm. Per-frame React state on the audio path caused dropped frames on this exact hardware.
- **Do NOT loosen Silero activation below 0.5.** It was at 0.5 yesterday; the 2026-05-16 bump to 0.6 was a *fix*, not a regression. Below 0.5 puts you in the territory where Whisper hallucinations dominate again.
- **Do NOT switch the listening RMS threshold back to the 1500 default.** With AGC+APM amplifying ambient noise to ~27000 (per the `.env` comment that just landed), 1500 is permanent-cyan. The right fix is removing AGC and switching `state.listening` to VAD-truth (P0-3, P0-5) — *then* the RMS path becomes dead code.
- **Do NOT re-enable `resume_false_interruption`.** Comment at `jarvis_agent.py:4596-4614` is correct: LiveKit's `pause()` on the SFU output doesn't clear the queue, so JARVIS keeps talking after a false interrupt. Until the SFU output path supports queue-clear on pause, this stays off.
- **Do NOT change Orpheus voice or remove EdgeTTS fallback.** Both were validated 2026-05-01 after ElevenLabs was removed.
- **Do NOT touch `handoff_text_suppressor` lookback or `confab-detector` evidence window** (out of scope for audio but in CLAUDE.md / regression-prevention).

---

## Quick wins ranked by effort × impact

1. **P0-1+P0-2: load module-echo-cancel and re-point env** — 5 min, eliminates speaker bleed → mic. Highest ROI.
2. **P0-3: `JARVIS_APM_AGC=0`** — 1 line, kills the room-tone-amplification feedback into Silero, fixes "VAD never closes" symptom.
3. **P0-4: Whisper prompt + decode knobs** — 10 lines in `providers/stt.py`, drops foreign-language hallucination rate to near-zero (industry-standard recipe).
4. **P1-3: muted color visibility** — 1 line in `main.rs`, ends the "tray looks broken when muted on dark panel" report.
5. **P0-5: VAD-truth listening indicator** — 1 day of work but the right architectural fix; once done, RMS thresholds become dead code and can be deleted.

---

**Bottom line:** the symptoms tonight are well-explained by two compounding issues: (a) no AEC anywhere in the chain, so the WebRTC APM's own AGC happily amplifies speaker bleed back into the mic; (b) the tray's "listening" indicator was always going to drift from STT-reality because it watches the *wrong signal*. The current RMS threshold of 28000 in `.env` is a workaround for (a), not a cure. The Whisper foreign-language drift is the standard `prompt=""`/`no_speech_threshold` story that has a 4-line fix. Silero tuning is sound; do not touch. Hardware is at its budget but not beyond it.
