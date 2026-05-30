# Direct-mode idle auto-revert to Claude

**Date:** 2026-05-30
**Status:** approved (design)
**Scope:** `bin/jarvis-gemini-tools`, `bin/jarvis-gpt-tools` (+ one tiny shared helper)

## Problem

In the direct voice modes the backend streams to the provider **continuously,
regardless of whether the user is talking**, so API quota burns while idle:

- **Gemini** (`jarvis-gemini-tools`): `pump_mic` sends every mic chunk and
  `pump_screen` sends a JPEG **every 1.0 s (1 fps video)** in unconditional
  `while not stop` loops — ~3,600 billed input images/hour when idle. This is
  the likely cause of the 2026-05-29 Google AI Studio spend-cap hit.
- **OpenAI** (`jarvis-gpt-tools`): `pump_mic` streams mic audio continuously
  (`input_audio_buffer.append`). No continuous video (vision is on-demand),
  but continuous input-audio billing while idle.
- **Claude** (voice-agent): essentially clean when idle — the LLM is called
  per-turn (VAD/turn-router gated), not on a timer. (STT streams continuously
  but that is cheap STT pricing, not LLM tokens.) Claude is the free,
  always-on base mode.

There is no idle timeout on the direct modes today, so leaving JARVIS in
Gemini/OpenAI and walking away bleeds quota indefinitely.

## Goal

When a direct mode is idle (no activity) for a configurable window, **auto-revert
to JARVIS-Claude** (the free base mode) so no Gemini/OpenAI tokens burn while the
user isn't talking. During active use the timer continually resets, so it never
interrupts a live conversation.

## Design

### Activity tracking
A single `last_activity` monotonic timestamp per backend, reset on any of:

- **JARVIS replying** — model audio-out (Gemini: existing `last_audio_at`;
  OpenAI: `response.output_audio.delta`).
- **A tool running** — `set_tool_running(True)` paths in both backends.
- **OpenAI only, also:** `input_audio_buffer.speech_started` /
  `speech_stopped` (precise user-speech signals the API already sends).

Gemini has no explicit user-VAD event, but Gemini Live replies to every user
turn, so model-audio-out is a sound proxy for "the user is interacting."
(Deliberately **not** adding mic-RMS — unnecessary complexity for a 5-minute
timeout.)

### Idle watcher
One new async task per backend, added to the existing task group:

```
poll every ~20s:
  if IDLE_TIMEOUT_S > 0
     and (now - last_activity) > IDLE_TIMEOUT_S
     and not status._tool_running:        # never revert mid-task
       → revert (below); stop the loop
```

Config: `JARVIS_DIRECT_IDLE_TIMEOUT_S` (default **300** = 5 min; `0` disables).
Shared by both backends.

### Revert mechanism (the load-bearing part)
The backend runs under a transient systemd unit with `Restart=always`
(`KillMode=control-group`, confirmed). It therefore **cannot** simply exit (it
would be restarted) and **cannot** `Popen` a helper that calls `systemctl stop`
(the helper lives in the unit's cgroup and is killed by the stop before it can
unmute Claude). The revert is issued in a **separate cgroup**:

```
systemd-run --user --scope -- <script_dir>/jarvis-mode jarvis
```

`jarvis-mode jarvis` then: `systemctl stop <this unit>` (a *deliberate* stop, so
`Restart=always` does not restart it) → unmutes JARVIS-Claude → writes
`active-mode=jarvis`. The backend receives SIGTERM, exits cleanly; the tray's
3 s `systemctl` poll flips the indicator to Claude. `<script_dir>` is resolved
from the backend's own path (`jarvis-mode` sits in the same `bin/`).

The revert is **silent** (tray flip + a `log.warning` line only) — no spoken
announcement (avoids a wasted token / jarring audio).

### Testable core
Extract the decision to a pure, importable helper (the bin scripts have no test
coverage today):

```python
def should_revert(idle_s: float, timeout_s: float, tool_running: bool) -> bool:
    return timeout_s > 0 and not tool_running and idle_s > timeout_s
```

Unit-test it (`tests/test_idle_revert.py`): timeout=0 disabled; tool_running
blocks; boundary at exactly timeout_s; reverts past it. The revert *mechanism*
(systemd-run --scope) is verified by log + live observation.

## Interactions / non-goals

- **Complements** the `Restart=always` fix (2026-05-30): that auto-recovers
  *unexpected* Live drops (GoAway/keepalive); this handles *deliberate* idle
  (revert and stay reverted). They don't conflict — idle revert uses
  `systemctl stop`, which suppresses `Restart`.
- **Claude unchanged** — it's the revert target.
- **Not doing:** per-frame Gemini video gating (the revert already stops video),
  audio VAD-gating (breaks the providers' server-side turn detection),
  mic-RMS user-VAD (unneeded for a 5-min timeout).

## Risks

- **False revert during a long silent pause in an active session** (user
  listening/thinking >5 min with no replies or tools): low; user just
  re-selects the mode. Mitigated by the tool-running guard + 5-min default.
- **`jarvis-mode jarvis` slow if `:8767` is degraded** (mute retry up to 30 s):
  mitigated by the already-deployed voice-client loop-decoupling fix, which
  keeps `:8767` responsive.
