"""Static lookup tables for viseme lip-sync.

Three maps, all hand-authored and pure data:
  ARPABET_TO_VISEME  — CMU ARPAbet phoneme -> Oculus 15-viseme code
  VISEME_TO_ARKIT    — Oculus viseme -> {ARKit-morph-name: weight 0..1}
  ARKIT_TO_TARGET    — ARKit-morph-name -> FaceCap GLB morph key 'target_N'

The Oculus viseme vocabulary and the phoneme->viseme grouping follow the
met4citizen/TalkingHead (MIT) and Oculus LipSync conventions. The
ARKit-52 canonical ordering matches the FaceCap GLB (jawOpen=24,
eyeWideL/R=17/18 are both confirmed in the kiosk code).
"""
from __future__ import annotations

# Oculus 15-viseme set.
VISEMES = (
    "sil", "PP", "FF", "TH", "DD", "kk", "CH", "SS",
    "nn", "RR", "aa", "E", "ih", "oh", "ou",
)

# CMU ARPAbet phoneme (stress digits already stripped by the caller) -> viseme.
ARPABET_TO_VISEME = {
    # vowels
    "AA": "aa", "AE": "aa", "AH": "aa", "AY": "aa", "AW": "aa",
    "AO": "oh", "OW": "oh", "OY": "oh",
    "EH": "E",  "EY": "E",
    "ER": "RR",
    "IH": "ih", "IY": "ih",
    "UH": "ou", "UW": "ou",
    # consonants
    "B": "PP", "P": "PP", "M": "PP",
    "F": "FF", "V": "FF",
    "TH": "TH", "DH": "TH",
    "D": "DD", "T": "DD",
    "K": "kk", "G": "kk", "NG": "kk",
    "CH": "CH", "JH": "CH", "SH": "CH", "ZH": "CH",
    "S": "SS", "Z": "SS",
    "N": "nn", "L": "nn",
    "R": "RR",
    "HH": "aa",
    "W": "ou",
    "Y": "ih",
}

# ARKit-52 canonical name -> FaceCap GLB morph key. Only the channels the
# viseme + idle-life layers actually use are listed (the GLB has all 52).
ARKIT_TO_TARGET = {
    "eyeBlinkLeft":      "target_13",
    "eyeBlinkRight":     "target_14",
    "eyeWideLeft":       "target_17",
    "eyeWideRight":      "target_18",
    "jawOpen":           "target_24",
    "mouthFunnel":       "target_28",
    "mouthPucker":       "target_29",
    "mouthClose":        "target_36",
    "mouthSmileLeft":    "target_37",
    "mouthSmileRight":   "target_38",
    "mouthUpperUpLeft":  "target_43",
    "mouthUpperUpRight": "target_44",
    "mouthLowerDownLeft":  "target_45",
    "mouthLowerDownRight": "target_46",
    "mouthPressLeft":    "target_47",
    "mouthPressRight":   "target_48",
    "mouthStretchLeft":  "target_49",
    "mouthStretchRight": "target_50",
    "tongueOut":         "target_51",
}

# Each viseme -> the mouth pose it holds at full openness (weights 0..1).
# 'sil' is the closed rest pose. Openness (the RMS gate) scales the whole
# pose at resolve time.
VISEME_TO_ARKIT = {
    "sil": {},
    "PP":  {"mouthClose": 0.9, "mouthPressLeft": 0.4, "mouthPressRight": 0.4},
    "FF":  {"jawOpen": 0.12, "mouthFunnel": 0.2, "mouthLowerDownLeft": 0.2, "mouthLowerDownRight": 0.2},
    "TH":  {"jawOpen": 0.2, "tongueOut": 0.3},
    "DD":  {"jawOpen": 0.2, "mouthStretchLeft": 0.1, "mouthStretchRight": 0.1},
    "kk":  {"jawOpen": 0.25},
    "CH":  {"jawOpen": 0.2, "mouthFunnel": 0.4, "mouthPucker": 0.3},
    "SS":  {"jawOpen": 0.1, "mouthStretchLeft": 0.3, "mouthStretchRight": 0.3},
    "nn":  {"jawOpen": 0.15, "mouthUpperUpLeft": 0.1, "mouthUpperUpRight": 0.1},
    "RR":  {"jawOpen": 0.2, "mouthPucker": 0.3},
    "aa":  {"jawOpen": 0.7, "mouthLowerDownLeft": 0.2, "mouthLowerDownRight": 0.2},
    "E":   {"jawOpen": 0.4, "mouthStretchLeft": 0.3, "mouthStretchRight": 0.3},
    "ih":  {"jawOpen": 0.25, "mouthStretchLeft": 0.2, "mouthStretchRight": 0.2},
    "oh":  {"jawOpen": 0.45, "mouthFunnel": 0.5, "mouthPucker": 0.3},
    "ou":  {"jawOpen": 0.3, "mouthFunnel": 0.4, "mouthPucker": 0.7},
}


def resolve_pose(viseme: str, openness: float) -> dict[str, float]:
    """Return {target_N: weight} for `viseme`, every weight scaled by
    `openness` (0..1, the RMS gate). Unknown viseme -> closed mouth ({})."""
    o = max(0.0, min(1.0, openness))
    pose = VISEME_TO_ARKIT.get(viseme, {})
    out: dict[str, float] = {}
    for name, w in pose.items():
        target_key = ARKIT_TO_TARGET.get(name)
        if target_key is not None:
            out[target_key] = round(w * o, 4)
    return out
