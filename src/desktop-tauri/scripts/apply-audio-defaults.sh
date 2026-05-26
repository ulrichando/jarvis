#!/usr/bin/env bash
# Apply and HOLD the JARVIS-preferred PulseAudio/PipeWire defaults:
#   - Default source → echo-cancel-source  (echo-cancelled mic)
#   - Default sink   → echo-cancel-sink (echo-cancelled speakers)
#   - echo-cancel-source volume → 100 %    (>100 % clips peaks → Silero misreads)
#
# Why this exists:
#   PipeWire/WirePlumber pick defaults at session start without remembering
#   our preference for the echo-cancel virtual devices. Worse, applications
#   (notably Chromium/Tauri webviews and some media players) reset the
#   default sink mid-session when they grab focus or initialize their own
#   audio routing. Without holding the default at echo-cancel-sink, JARVIS's TTS
#   plays through the raw speaker → mic picks it up → infinite self-hear
#   loop confirmed in conversations.db at 2026-04-29 15:38:48.
#
# How it holds:
#   Two phases:
#     1. INITIAL — wait up to 15 s for echo-cancel-source/echo-cancel-sink to register, then
#        apply defaults.
#     2. WATCHDOG — `pactl subscribe` for server / sink / source events.
#        On every event, re-check the defaults and re-assert echo-cancel-sink /
#        echo-cancel-source if they've drifted.
#
# Lifecycle:
#   Designed for systemd Type=simple with Restart=always. If pactl
#   subscribe ever exits (PipeWire restart, etc.), systemd restarts us.
set -u

LOG_PREFIX="[jarvis-audio-defaults]"

# Returns 0 if echo-cancel-source and echo-cancel-sink exist (whether or not defaults
# needed to change); 1 if they don't yet exist.
apply_defaults() {
  local sources sinks changed=0
  sources=$(pactl list short sources 2>/dev/null || true)
  sinks=$(pactl list short sinks 2>/dev/null || true)

  echo "$sources" | awk '{print $2}' | grep -qx echo-cancel-source  || return 1
  echo "$sinks"   | awk '{print $2}' | grep -qx echo-cancel-sink || return 1

  local current_source current_sink
  current_source=$(pactl info | awk -F': ' '/^Default Source:/ {print $2}')
  current_sink=$(pactl info   | awk -F': ' '/^Default Sink:/ {print $2}')

  if [ "$current_source" != "echo-cancel-source" ]; then
    pactl set-default-source echo-cancel-source && changed=1
  fi
  if [ "$current_sink" != "echo-cancel-sink" ]; then
    pactl set-default-sink echo-cancel-sink && changed=1
  fi
  # NB: we no longer force echo-cancel-source volume to 100 % on every event —
  # that locked the volume slider so the user couldn't adjust it.
  # Initial 100 % is set ONCE in phase 1 (see set_initial_volume call
  # below). Mid-session, the user owns the slider.

  if [ "$changed" = "1" ]; then
    echo "$LOG_PREFIX defaults reasserted (source=echo-cancel-source, sink=echo-cancel-sink)"
  fi
  return 0
}

# One-shot initial volume normalisation. Called from phase 1 only.
# Rationale: cold-boot mic volume is unpredictable (alsamixer state
# from prior session). 100 % is the AEC sweet spot — anything higher
# clips peaks and Silero misreads them as voice. Once set, the user
# is free to adjust down (e.g. quiet rooms where 100 % picks up too
# much background).
set_initial_volume() {
  pactl set-source-volume echo-cancel-source 100% 2>/dev/null || true
  echo "$LOG_PREFIX initial echo-cancel-source volume set to 100 % (user can adjust freely)"
}

# Phase 1: wait for the echo-cancel virtuals to register, then set
# the initial sane volume. Subsequent watchdog passes do NOT touch
# volume so the user can adjust freely.
ready=0
for _ in $(seq 1 15); do
  if apply_defaults; then
    ready=1
    set_initial_volume
    echo "$LOG_PREFIX initial defaults applied"
    break
  fi
  sleep 1
done

if [ "$ready" != "1" ]; then
  echo "$LOG_PREFIX echo-cancel-source/echo-cancel-sink never appeared in 15 s — exiting" >&2
  exit 1
fi

# Phase 2: watchdog. Subscribe to pactl events and re-assert on every
# server / sink / source change. apply_defaults is idempotent so missed
# filtering is harmless. The grep is line-buffered so events flow
# through immediately.
echo "$LOG_PREFIX entering watchdog mode (pactl subscribe)"

pactl subscribe 2>/dev/null \
  | grep --line-buffered -E "(sink|source|server)" \
  | while read -r _evt; do
      apply_defaults || true
    done
