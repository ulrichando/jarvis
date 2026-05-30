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

# FaceCap glTF heads name their 52 ARKit morphs `target_0..target_51` in ARKit
# index order (the imported head's key_blocks are Basis + target_0..51, NOT
# literal names like "jawOpen"). Map the ARKit names we drive to those aliases
# so we can resolve the right shape key. Heads that DO use literal ARKit names
# resolve directly and skip the alias.
FACECAP_ALIASES = {
    "jawOpen": "target_17",
    "mouthClose": "target_18",
    "mouthFunnel": "target_19",
    "mouthPucker": "target_20",
}


def resolve_key_names(arkit_names, available) -> dict:
    """Map each ARKit name to the actual shape-key name present on the mesh.

    `available` is the set/list of key_blocks names. Prefer the literal ARKit
    name; fall back to the FaceCap `target_N` alias. ARKit names with no match
    are omitted (the caller simply won't drive them).
    """
    avail = set(available)
    out = {}
    for name in arkit_names:
        if name in avail:
            out[name] = name
        elif FACECAP_ALIASES.get(name) in avail:
            out[name] = FACECAP_ALIASES[name]
    return out


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
