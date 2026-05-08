"""Passive observation taps.

Each tap subscribes to a stream (audio frames, screenshots) and
extracts structured signals without participating in the turn
pipeline. Taps are read-only producers; consumers (jarvis_agent,
supervisor_graph) read what taps emit, never the other way around.

Modules:
  - acoustic : audio-frame analysis tap (was acoustic_tap.py)
  - vision   : periodic-screenshot Kimi-vision tap, runnable as a
               standalone systemd unit (was vision_tap.py)

Stage B reorganization 2026-05-05 (RFC-001).
"""
