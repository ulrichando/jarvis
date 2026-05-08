"""Text-to-speech provider plugins + cached output.

Modules:
  - edge          : Microsoft Edge TTS plugin for livekit-agents
                    (was edge_tts_plugin.py)
  - canned_phrases : pre-rendered WAV cache for known short phrases
                    (e.g. "Yes, sir?", "At once, sir.") so circuit-
                    breaker-open + cold-start latency doesn't penalize
                    canonical replies

Stage B reorganization 2026-05-05 (RFC-001).
"""
