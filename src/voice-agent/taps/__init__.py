"""Passive observation taps.

Each tap subscribes to a stream (audio frames) and extracts
structured signals without participating in the turn pipeline.
Taps are read-only producers; consumers (jarvis_agent) read what
taps emit, never the other way around.

Modules:
  - acoustic : audio-frame analysis tap (was acoustic_tap.py)

Stage B reorganization 2026-05-05 (RFC-001).
"""
