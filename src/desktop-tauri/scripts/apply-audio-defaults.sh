#!/usr/bin/env bash
# Apply the JARVIS-preferred PulseAudio/PipeWire defaults:
#   - Default source → mic_aec  (echo-cancelled mic)
#   - Default sink   → sink_aec (echo-cancelled speakers)
#   - mic_aec volume → 100 %    (>100 % clips peaks → Silero misreads)
#
# Invoked by jarvis-audio-defaults.service on every user-session start
# because PipeWire does NOT remember these per-user defaults across
# reboots. Without them the webview grabs the raw hardware mic +
# bypasses the AEC reference loop → Silero triggers on JARVIS's own
# voice → infinite self-hear loop.
#
# Retries ~15 s because the echo-cancel virtual devices can take a
# couple of seconds to register after PipeWire declares itself ready.
set -u

for _ in $(seq 1 15); do
  sources=$(pactl list short sources 2>/dev/null || true)
  sinks=$(pactl list short sinks 2>/dev/null || true)
  if echo "$sources" | awk '{print $2}' | grep -qx mic_aec \
  && echo "$sinks"   | awk '{print $2}' | grep -qx sink_aec; then
    pactl set-default-source mic_aec
    pactl set-default-sink   sink_aec
    pactl set-source-volume  mic_aec 100%
    echo "[jarvis-audio-defaults] defaults applied"
    exit 0
  fi
  sleep 1
done

echo "[jarvis-audio-defaults] mic_aec/sink_aec never appeared in 15 s" >&2
exit 1
