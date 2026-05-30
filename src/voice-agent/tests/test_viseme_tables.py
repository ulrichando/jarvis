from lipsync import viseme_tables as vt


def test_jaw_open_maps_to_target_24():
    # Confirmed twice in the live kiosk code (FaceWebGL).
    assert vt.ARKIT_TO_TARGET["jawOpen"] == "target_24"
    assert vt.ARKIT_TO_TARGET["eyeWideLeft"] == "target_17"
    assert vt.ARKIT_TO_TARGET["eyeBlinkLeft"] == "target_13"


def test_every_arpabet_phoneme_maps_to_a_viseme():
    arpabet = {
        "AA","AE","AH","AO","AW","AY","B","CH","D","DH","EH","ER","EY",
        "F","G","HH","IH","IY","JH","K","L","M","N","NG","OW","OY","P",
        "R","S","SH","T","TH","UH","UW","V","W","Y","Z","ZH",
    }
    for p in arpabet:
        assert p in vt.ARPABET_TO_VISEME, f"{p} unmapped"
        assert vt.ARPABET_TO_VISEME[p] in vt.VISEMES


def test_every_viseme_pose_uses_known_arkit_names():
    for viseme, pose in vt.VISEME_TO_ARKIT.items():
        assert viseme in vt.VISEMES, f"pose for unknown viseme {viseme}"
        for name, weight in pose.items():
            assert name in vt.ARKIT_TO_TARGET, f"{name} not in ARKIT_TO_TARGET"
            assert 0.0 <= weight <= 1.0


def test_resolve_pose_returns_target_indexed_weights():
    weights = vt.resolve_pose("aa", openness=1.0)
    assert weights["target_24"] > 0.5
    assert all(k.startswith("target_") for k in weights)


def test_resolve_pose_scales_by_openness():
    full = vt.resolve_pose("aa", openness=1.0)["target_24"]
    half = vt.resolve_pose("aa", openness=0.5)["target_24"]
    assert abs(half - full * 0.5) < 1e-6
