import math

from animators import face_anim_core as fac


def test_target_jaw_zero_when_not_speaking():
    assert fac.target_jaw(False, 1.0, gain=4.0) == 0.0


def test_target_jaw_tracks_level_with_gain_and_clamp():
    # level 0.1 * gain 4 = 0.4
    assert fac.target_jaw(True, 0.1, gain=4.0) == 0.4
    # clamps to max_jaw
    assert fac.target_jaw(True, 1.0, gain=4.0, max_jaw=1.0) == 1.0
    # never negative
    assert fac.target_jaw(True, 0.0, gain=4.0) == 0.0


def test_smooth_jaw_opens_faster_than_it_closes():
    # opening: current 0 -> target 1 with attack 0.5
    assert fac.smooth_jaw(0.0, 1.0, attack=0.5, decay=0.1) == 0.5
    # closing: current 1 -> target 0 with decay 0.1
    assert math.isclose(fac.smooth_jaw(1.0, 0.0, attack=0.5, decay=0.1), 0.9)


def test_shape_values_co_articulation():
    v = fac.shape_values(0.0)
    assert v["jawOpen"] == 0.0
    assert v["mouthClose"] == 1.0          # fully closed at rest
    v = fac.shape_values(1.0)
    assert v["jawOpen"] == 1.0
    assert v["mouthClose"] == 0.0          # 1 - 1*1.5 clamped to 0
    assert math.isclose(v["mouthFunnel"], 0.25)
    assert math.isclose(v["mouthPucker"], 0.10)


def test_shape_values_clamps_input():
    assert fac.shape_values(5.0)["jawOpen"] == 1.0
    assert fac.shape_values(-5.0)["jawOpen"] == 0.0
