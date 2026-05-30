"""Pure animation math for the JARVIS talking face.

No bpy, no IO, no threads — every function here is deterministic and unit
tested. The orchestrator (blender_face.py) composes these with the loudness
monitor and the Blender socket.

ARKit shape-key NAME -> default index hint (FaceCap / standard 52-shape order).
The orchestrator resolves real indices by name against the live key_blocks;
these are only fallback hints.
"""

ARKIT_INDEX_HINTS = {
    "jawOpen": 17,
    "mouthClose": 18,
    "mouthFunnel": 19,
    "mouthPucker": 20,
}


def target_jaw(speaking: bool, level: float, gain: float = 4.0,
               max_jaw: float = 1.0) -> float:
    """Desired jaw openness for this frame.

    While speaking, jaw tracks loudness (level 0..1) scaled by gain and
    clamped to [0, max_jaw]. While not speaking, jaw target is 0.
    """
    if not speaking:
        return 0.0
    return max(0.0, min(max_jaw, gain * level))


def smooth_jaw(current: float, target: float,
               attack: float = 0.20, decay: float = 0.12) -> float:
    """One asymmetric smoothing step: opens (attack) faster than it closes
    (decay), which reads as natural speech."""
    smoothing = attack if target > current else decay
    return current + (target - current) * smoothing


def shape_values(jaw: float) -> dict:
    """Map a 0..1 jaw openness to ARKit shape-key values with light
    co-articulation so the mouth shuts cleanly at rest."""
    jaw = max(0.0, min(1.0, jaw))
    return {
        "jawOpen": jaw,
        "mouthClose": max(0.0, 1.0 - jaw * 1.5),
        "mouthFunnel": jaw * 0.25,
        "mouthPucker": jaw * 0.10,
    }
