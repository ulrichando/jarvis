"""Text-to-speech provider plugins + cached output.

Modules:
  - edge          : Microsoft Edge TTS plugin for livekit-agents
                    (was edge_tts_plugin.py)
                    (e.g. "Yes?", "Right away.") so circuit-
                    breaker-open + cold-start latency doesn't penalize
                    canonical replies

Stage B reorganization 2026-05-05 (RFC-001).
"""
