"""Pure animation math for the JARVIS talking face.

No bpy, no IO, no threads — every function here is deterministic and unit
tested. The orchestrator (blender_face.py) composes these with the loudness
monitor and the Blender socket.

ARKit shape-key NAME -> default index hint (FaceCap / standard 52-shape order).
The orchestrator resolves real indices by name against the live key_blocks;
these are only fallback hints.
"""

# FaceCap glTF heads name their 52 ARKit morphs `target_0..target_51` in the
# canonical ARKit index order (the imported head's key_blocks are Basis +
# target_0..51, NOT literal names like "jawOpen"). These indices were CONFIRMED
# empirically against the live head (target_24 produced the only real jaw drop;
# the prototype's target_17 was an eye shape, which is why its face never
# talked). Heads that DO use literal ARKit names resolve directly (the literal
# name is preferred over the alias in resolve_key_names).
FACECAP_ALIASES = {
    "jawOpen": "target_24",     # confirmed: dominant downward jaw deformation
    "mouthClose": "target_28",  # canonical ARKit order (co-articulation, unused in MVP)
    "mouthFunnel": "target_29",
    "mouthPucker": "target_30",
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
    """Map a 0..1 jaw openness to the ARKit shape-key values we drive.

    MVP is jaw-only ("jaw-open from loudness"): the Basis already has closed
    lips, so a jaw drop alone reads clearly as talking and carries zero risk of
    deforming the idle face. Co-articulation (mouthClose/funnel/pucker) is a
    fast-follow that requires per-shape verification before it's applied at
    rest — see FACECAP_ALIASES."""
    jaw = max(0.0, min(1.0, jaw))
    return {"jawOpen": jaw}
