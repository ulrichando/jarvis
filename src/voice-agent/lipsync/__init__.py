"""Local viseme lip-sync for the kiosk face.

Turns JARVIS's known TTS text + audio RMS into ARKit-morph weights that
drive the FaceCap GLB. Pure CPU/RAM — no GPU, no neural net. See
docs/superpowers/specs/2026-05-30-jarvis-face-viseme-lipsync-design.md.
"""
from .viseme_engine import VisemeEngine
from .expression import ExpressionEngine

__all__ = ["VisemeEngine", "ExpressionEngine"]
