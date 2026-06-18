"""Tests for the ``webcam`` voice tool (tools/webcam.py) + capture helper
(vision/webcam.py).

External effects are stubbed at the seams: frame capture, the Anthropic
vision call, and the audit-frame write. The tracker status/frame paths are
exercised against real tmp files because freshness (mtime) logic is the
load-bearing part.
"""
from __future__ import annotations

import json
import os
import time

import pytest

import tools.webcam as webcam_tool
import vision.webcam as vision_webcam
from tools.registry import registry


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_webcam_tool_registered():
    entry = registry.get_entry("webcam")
    assert entry is not None
    assert entry.schema["name"] == "webcam"
    assert entry.schema["parameters"]["required"] == ["question"]
    assert "question" in entry.schema["parameters"]["properties"]
    assert entry.check_fn is webcam_tool.check_webcam_requirements
    assert entry.is_async is False
    assert "ANTHROPIC_API_KEY" in entry.requires_env


def test_description_routes_camera_not_screen():
    desc = registry.get_entry("webcam").schema["description"]
    assert "computer_use" in desc  # steers screen questions away
    assert "webcam" in desc.lower() or "camera" in desc.lower()


# ---------------------------------------------------------------------------
# check_fn gating
# ---------------------------------------------------------------------------


def test_check_fn_kill_switch(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(webcam_tool, "webcam_available", lambda: True)
    monkeypatch.setenv("JARVIS_WEBCAM_DISABLED", "1")
    assert webcam_tool.check_webcam_requirements() is False


def test_check_fn_requires_anthropic_key(monkeypatch):
    monkeypatch.delenv("JARVIS_WEBCAM_DISABLED", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(webcam_tool, "webcam_available", lambda: True)
    assert webcam_tool.check_webcam_requirements() is False


def test_check_fn_requires_frame_source(monkeypatch):
    monkeypatch.delenv("JARVIS_WEBCAM_DISABLED", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(webcam_tool, "webcam_available", lambda: False)
    assert webcam_tool.check_webcam_requirements() is False
    monkeypatch.setattr(webcam_tool, "webcam_available", lambda: True)
    assert webcam_tool.check_webcam_requirements() is True


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


@pytest.fixture()
def quiet_side_effects(monkeypatch):
    """Silence the audit write + tracker/face enrichments for handler tests."""
    monkeypatch.setattr(webcam_tool, "_save_last_frame", lambda jpeg: None)
    monkeypatch.setattr(webcam_tool, "_live_tracker_status", lambda: {})
    monkeypatch.setattr(webcam_tool, "_recognized_faces", lambda jpeg: {})


def test_handler_success(monkeypatch, quiet_side_effects):
    seen = {}

    def fake_analyze(jpeg, question):
        seen["jpeg"], seen["question"] = jpeg, question
        return "You're holding a red mug.", "claude-haiku-4-5"

    monkeypatch.setattr(webcam_tool, "grab_jpeg", lambda: (b"JPEGBYTES", "device"))
    monkeypatch.setattr(webcam_tool, "_analyze_jpeg", fake_analyze)

    out = json.loads(webcam_tool._handle_webcam({"question": "What am I holding?"}))
    assert out["result"] == "You're holding a red mug."
    assert out["source"] == "device"
    assert out["model"]
    assert seen == {"jpeg": b"JPEGBYTES", "question": "What am I holding?"}


def test_handler_defaults_blank_question(monkeypatch, quiet_side_effects):
    seen = {}
    monkeypatch.setattr(webcam_tool, "grab_jpeg", lambda: (b"x", "tracker"))
    monkeypatch.setattr(
        webcam_tool, "_analyze_jpeg", lambda jpeg, q: (seen.setdefault("q", q) or "ok", "claude-haiku-4-5")
    )
    out = json.loads(webcam_tool._handle_webcam({"question": "   "}))
    assert seen["q"] == webcam_tool.DEFAULT_QUESTION
    assert out["source"] == "tracker"


def test_handler_capture_failure_is_tool_error(monkeypatch, quiet_side_effects):
    def boom():
        raise vision_webcam.WebcamError("cannot open webcam index 0")

    monkeypatch.setattr(webcam_tool, "grab_jpeg", boom)
    out = json.loads(webcam_tool._handle_webcam({"question": "look"}))
    assert "error" in out
    assert "cannot open webcam" in out["error"]


def test_handler_analysis_failure_is_tool_error(monkeypatch, quiet_side_effects):
    monkeypatch.setattr(webcam_tool, "grab_jpeg", lambda: (b"x", "device"))

    def boom(jpeg, q):
        raise RuntimeError("api down")

    monkeypatch.setattr(webcam_tool, "_analyze_jpeg", boom)
    out = json.loads(webcam_tool._handle_webcam({"question": "look"}))
    assert "error" in out and "api down" in out["error"]


def test_handler_merges_tracker_and_face_enrichment(monkeypatch):
    monkeypatch.setattr(webcam_tool, "_save_last_frame", lambda jpeg: None)
    monkeypatch.setattr(webcam_tool, "grab_jpeg", lambda: (b"x", "device"))
    monkeypatch.setattr(webcam_tool, "_analyze_jpeg", lambda jpeg, q: ("two people", "claude-haiku-4-5"))
    monkeypatch.setattr(
        webcam_tool,
        "_live_tracker_status",
        lambda: {"person_detected": True, "face_count": 2},
    )
    monkeypatch.setattr(
        webcam_tool, "_recognized_faces", lambda jpeg: {"recognized": ["Alice"]}
    )
    out = json.loads(webcam_tool._handle_webcam({"question": "who's here?"}))
    assert out["person_detected"] is True
    assert out["face_count"] == 2
    assert out["recognized"] == ["Alice"]


# ---------------------------------------------------------------------------
# Tracker status freshness
# ---------------------------------------------------------------------------


def _write_status(tmp_path, payload, age_s=0.0):
    p = tmp_path / "person_tracker.json"
    p.write_text(json.dumps(payload))
    if age_s:
        old = time.time() - age_s
        os.utime(p, (old, old))
    return p


def test_live_tracker_status_fresh(monkeypatch, tmp_path):
    p = _write_status(
        tmp_path,
        {"person_detected": True, "face_count": 2, "fps": 5.0, "error": None},
    )
    monkeypatch.setenv("JARVIS_TRACKER_STATUS_FILE", str(p))
    assert webcam_tool._live_tracker_status() == {
        "person_detected": True,
        "face_count": 2,
    }


def test_live_tracker_status_stale_is_empty(monkeypatch, tmp_path):
    p = _write_status(tmp_path, {"person_detected": True, "face_count": 1}, age_s=60)
    monkeypatch.setenv("JARVIS_TRACKER_STATUS_FILE", str(p))
    assert webcam_tool._live_tracker_status() == {}


def test_live_tracker_status_error_or_missing_is_empty(monkeypatch, tmp_path):
    p = _write_status(tmp_path, {"person_detected": False, "error": "no cam"})
    monkeypatch.setenv("JARVIS_TRACKER_STATUS_FILE", str(p))
    assert webcam_tool._live_tracker_status() == {}
    monkeypatch.setenv("JARVIS_TRACKER_STATUS_FILE", str(tmp_path / "absent.json"))
    assert webcam_tool._live_tracker_status() == {}


# ---------------------------------------------------------------------------
# Face-ID enrichment seam
# ---------------------------------------------------------------------------


def test_recognized_faces_when_ready(monkeypatch):
    from vision import face_id

    monkeypatch.setattr(face_id, "recognition_ready", lambda: True)
    monkeypatch.setattr(face_id, "identify_all", lambda jpeg: ["Alice", "Bob"])
    assert webcam_tool._recognized_faces(b"x") == {"recognized": ["Alice", "Bob"]}


def test_recognized_faces_not_ready_or_failing(monkeypatch):
    from vision import face_id

    monkeypatch.setattr(face_id, "recognition_ready", lambda: False)
    assert webcam_tool._recognized_faces(b"x") == {}

    monkeypatch.setattr(face_id, "recognition_ready", lambda: True)

    def boom(jpeg):
        raise RuntimeError("model corrupt")

    monkeypatch.setattr(face_id, "identify_all", boom)
    assert webcam_tool._recognized_faces(b"x") == {}


# ---------------------------------------------------------------------------
# vision/webcam.py capture helper
# ---------------------------------------------------------------------------


def test_grab_jpeg_prefers_fresh_tracker_frame(monkeypatch, tmp_path):
    frame = tmp_path / "person_tracker.jpg"
    frame.write_bytes(b"TRACKERJPEG")
    monkeypatch.setenv("JARVIS_TRACKER_FRAME_FILE", str(frame))
    data, source = vision_webcam.grab_jpeg()
    assert (data, source) == (b"TRACKERJPEG", "tracker")


def test_grab_jpeg_falls_back_to_device(monkeypatch, tmp_path):
    stale = tmp_path / "person_tracker.jpg"
    stale.write_bytes(b"OLD")
    old = time.time() - 60
    os.utime(stale, (old, old))
    monkeypatch.setenv("JARVIS_TRACKER_FRAME_FILE", str(stale))
    monkeypatch.setattr(vision_webcam, "_capture_device_jpeg", lambda: b"DEVICEJPEG")
    data, source = vision_webcam.grab_jpeg()
    assert (data, source) == (b"DEVICEJPEG", "device")


def test_webcam_available_pinned_device(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_WEBCAM_DEVICE", str(tmp_path / "no-such-device"))
    monkeypatch.setenv("JARVIS_TRACKER_FRAME_FILE", str(tmp_path / "absent.jpg"))
    assert vision_webcam.webcam_available() is False

    frame = tmp_path / "fresh.jpg"
    frame.write_bytes(b"J")
    monkeypatch.setenv("JARVIS_TRACKER_FRAME_FILE", str(frame))
    assert vision_webcam.webcam_available() is True


def test_webcam_available_hardware_autodetect(monkeypatch, tmp_path):
    monkeypatch.delenv("JARVIS_WEBCAM_DEVICE", raising=False)
    monkeypatch.setenv("JARVIS_TRACKER_FRAME_FILE", str(tmp_path / "absent.jpg"))

    monkeypatch.setattr(vision_webcam, "detect_webcam_devices", lambda: [])
    assert vision_webcam.webcam_available() is False

    monkeypatch.setattr(
        vision_webcam, "detect_webcam_devices", lambda: ["/dev/video3"]
    )
    assert vision_webcam.webcam_available() is True


def test_candidate_indexes_pin_beats_detection(monkeypatch):
    monkeypatch.setattr(vision_webcam, "_ir_index", lambda: None)  # isolate from IR exclusion
    monkeypatch.setenv("JARVIS_WEBCAM_INDEX", "5")
    assert vision_webcam._candidate_indexes() == [5]

    monkeypatch.delenv("JARVIS_WEBCAM_INDEX", raising=False)
    monkeypatch.setattr(
        vision_webcam, "detect_webcam_devices", lambda: ["/dev/video0", "/dev/video2"]
    )
    assert vision_webcam._candidate_indexes() == [0, 2]

    monkeypatch.setattr(vision_webcam, "detect_webcam_devices", lambda: [])
    assert vision_webcam._candidate_indexes() == [0]


def test_capture_falls_back_across_hardware_nodes(monkeypatch):
    monkeypatch.setattr(vision_webcam, "_ir_index", lambda: None)  # isolate from IR exclusion
    monkeypatch.delenv("JARVIS_WEBCAM_INDEX", raising=False)
    monkeypatch.setattr(
        vision_webcam, "detect_webcam_devices", lambda: ["/dev/video0", "/dev/video2"]
    )

    def fake_open_and_grab(index):
        if index == 0:  # UVC metadata node: opens, yields nothing
            raise vision_webcam.WebcamError("opened but returned no frames")
        return b"FROMVIDEO2"

    monkeypatch.setattr(vision_webcam, "_open_and_grab", fake_open_and_grab)
    assert vision_webcam._capture_device_jpeg() == b"FROMVIDEO2"


def test_capture_reports_hardware_when_all_fail(monkeypatch):
    monkeypatch.delenv("JARVIS_WEBCAM_INDEX", raising=False)
    monkeypatch.setattr(
        vision_webcam, "detect_webcam_devices", lambda: ["/dev/video0"]
    )

    def always_fail(index):
        raise vision_webcam.WebcamError("cannot open")

    monkeypatch.setattr(vision_webcam, "_open_and_grab", always_fail)
    with pytest.raises(vision_webcam.WebcamError) as exc_info:
        vision_webcam._capture_device_jpeg()
    assert "/dev/video0" in str(exc_info.value)


def test_resize_and_encode_caps_longest_edge():
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    big = np.zeros((1000, 2000, 3), dtype=np.uint8)
    jpeg = vision_webcam.resize_and_encode_jpeg(big, max_dim=1024)
    decoded = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert max(decoded.shape[:2]) <= 1024
    # Aspect ratio preserved: 2000x1000 → 1024x512.
    assert decoded.shape[:2] == (512, 1024)


# ---------------------------------------------------------------------------
# IR (Windows-Hello sensor) dark-room assist
# ---------------------------------------------------------------------------


def _jpeg_of_luma(value: int) -> bytes:
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    frame = np.full((48, 64, 3), value, dtype=np.uint8)
    ok, jpeg = cv2.imencode(".jpg", frame)
    assert ok
    return jpeg.tobytes()


def test_mean_jpeg_luma():
    assert vision_webcam.mean_jpeg_luma(_jpeg_of_luma(10)) < 20
    assert vision_webcam.mean_jpeg_luma(_jpeg_of_luma(200)) > 180
    assert vision_webcam.mean_jpeg_luma(b"not a jpeg") == 255.0  # fail-bright


def test_ir_index_resolution(monkeypatch, tmp_path):
    fake_node = tmp_path  # exists; we monkeypatch the existence check path
    monkeypatch.setenv("JARVIS_IR_INDEX", "7")
    monkeypatch.setattr(
        vision_webcam.os.path, "exists", lambda p: p == "/dev/video7"
    )
    assert vision_webcam._ir_index() == 7

    monkeypatch.setattr(vision_webcam.os.path, "exists", lambda p: False)
    assert vision_webcam._ir_index() is None  # node absent → disabled

    monkeypatch.delenv("JARVIS_IR_INDEX", raising=False)
    monkeypatch.setenv("JARVIS_IR_DEVICE", "/dev/video2")
    monkeypatch.setattr(
        vision_webcam.os.path, "exists", lambda p: p == "/dev/video2"
    )
    assert vision_webcam._ir_index() == 2


def test_candidate_indexes_exclude_ir(monkeypatch):
    monkeypatch.delenv("JARVIS_WEBCAM_INDEX", raising=False)
    monkeypatch.setattr(
        vision_webcam,
        "detect_webcam_devices",
        lambda: ["/dev/video0", "/dev/video1", "/dev/video2"],
    )
    monkeypatch.setattr(vision_webcam, "_ir_index", lambda: 2)
    assert vision_webcam._candidate_indexes() == [0, 1]


def test_dark_frame_triggers_ir_assist(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_TRACKER_FRAME_FILE", str(tmp_path / "absent.jpg"))
    monkeypatch.setenv("JARVIS_WEBCAM_IR_ASSIST", "1")
    dark, bright = _jpeg_of_luma(5), _jpeg_of_luma(120)
    seen = {}

    def fake_grab(idx, pick_brightest=False):
        seen["pick_brightest"] = pick_brightest
        return bright

    monkeypatch.setattr(vision_webcam, "_capture_device_jpeg", lambda: dark)
    monkeypatch.setattr(vision_webcam, "_ir_index", lambda: 2)
    monkeypatch.setattr(vision_webcam, "_open_and_grab", fake_grab)

    data, source = vision_webcam.grab_jpeg()
    assert (data, source) == (bright, "ir")
    # Strobing emitters light alternating frames — the IR grab must take the
    # brightest of the burst, or it can return an unlit frame and the assist
    # wrongly concludes IR is "no better than RGB".
    assert seen["pick_brightest"] is True


def test_bright_frame_skips_ir(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_TRACKER_FRAME_FILE", str(tmp_path / "absent.jpg"))
    monkeypatch.setenv("JARVIS_WEBCAM_IR_ASSIST", "1")
    bright = _jpeg_of_luma(120)

    def never(idx):
        raise AssertionError("IR must not be touched for bright frames")

    monkeypatch.setattr(vision_webcam, "_capture_device_jpeg", lambda: bright)
    monkeypatch.setattr(vision_webcam, "_ir_index", lambda: 2)
    monkeypatch.setattr(vision_webcam, "_open_and_grab", never)

    assert vision_webcam.grab_jpeg() == (bright, "device")


def test_ir_assist_respects_kill_switch_and_no_gain(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_TRACKER_FRAME_FILE", str(tmp_path / "absent.jpg"))
    dark = _jpeg_of_luma(5)
    monkeypatch.setattr(vision_webcam, "_capture_device_jpeg", lambda: dark)
    monkeypatch.setattr(vision_webcam, "_ir_index", lambda: 2)

    # Kill switch off → stays on the RGB frame.
    monkeypatch.setenv("JARVIS_WEBCAM_IR_ASSIST", "0")
    assert vision_webcam.grab_jpeg() == (dark, "device")

    # IR no brighter than RGB (emitter dead / sensor covered) → stays RGB.
    monkeypatch.setenv("JARVIS_WEBCAM_IR_ASSIST", "1")
    monkeypatch.setattr(vision_webcam, "_open_and_grab", lambda idx, **kw: dark)
    assert vision_webcam.grab_jpeg() == (dark, "device")


def test_open_and_grab_picks_brightest_of_burst(monkeypatch):
    """Alternating lit/unlit frames (strobing IR emitter): default keeps the
    last frame (unlit), pick_brightest must keep the lit one."""
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")

    class _FakeCap:
        def __init__(self, *_a):
            # 5 reads = WARMUP_FRAMES + 1; lit frames on odd indexes, like
            # the real sensor (measured luma 16/50/16/44/16).
            self._frames = [
                np.full((8, 8, 3), v, dtype=np.uint8) for v in (10, 200, 10, 180, 10)
            ]

        def isOpened(self):
            return True

        def set(self, *_a):
            pass

        def read(self):
            if self._frames:
                return True, self._frames.pop(0)
            return False, None

        def release(self):
            pass

    monkeypatch.setattr(cv2, "VideoCapture", _FakeCap)
    last = vision_webcam._open_and_grab(0)
    assert vision_webcam.mean_jpeg_luma(last) < 30  # unlit tail frame

    monkeypatch.setattr(cv2, "VideoCapture", _FakeCap)
    brightest = vision_webcam._open_and_grab(0, pick_brightest=True)
    assert vision_webcam.mean_jpeg_luma(brightest) > 150  # the lit frame


def test_grab_jpeg_allow_ir_false_skips_ir_assist(monkeypatch, tmp_path):
    """Face recognition's RGB-only capture: dark frame, IR available — the IR
    sensor must not even be opened (SFace embeddings are RGB-trained)."""
    monkeypatch.setenv("JARVIS_TRACKER_FRAME_FILE", str(tmp_path / "absent.jpg"))
    monkeypatch.setenv("JARVIS_WEBCAM_IR_ASSIST", "1")
    dark = _jpeg_of_luma(5)

    def never(idx):
        raise AssertionError("IR must not be touched when allow_ir=False")

    monkeypatch.setattr(vision_webcam, "_capture_device_jpeg", lambda: dark)
    monkeypatch.setattr(vision_webcam, "_ir_index", lambda: 2)
    monkeypatch.setattr(vision_webcam, "_open_and_grab", never)

    assert vision_webcam.grab_jpeg(allow_ir=False) == (dark, "device")


def test_handler_adds_ir_hint(monkeypatch):
    monkeypatch.setattr(webcam_tool, "_save_last_frame", lambda jpeg: None)
    monkeypatch.setattr(webcam_tool, "_live_tracker_status", lambda: {})
    monkeypatch.setattr(webcam_tool, "_recognized_faces", lambda jpeg: {})
    monkeypatch.setattr(webcam_tool, "grab_jpeg", lambda: (b"x", "ir"))
    seen = {}
    monkeypatch.setattr(
        webcam_tool, "_analyze_jpeg", lambda jpeg, q: (seen.setdefault("q", q) or "dark room", "claude-haiku-4-5")
    )
    out = json.loads(webcam_tool._handle_webcam({"question": "Can you see me?"}))
    assert out["source"] == "ir"
    assert "infrared" in seen["q"]
    assert seen["q"].startswith("Can you see me?")


def test_handler_never_runs_face_recognition_on_ir_frames(monkeypatch):
    """SFace is RGB-trained: an IR night frame must not reach the recognizer
    even when enrolled people exist (cross-domain embeddings mis-match)."""
    monkeypatch.setattr(webcam_tool, "_save_last_frame", lambda jpeg: None)
    monkeypatch.setattr(webcam_tool, "_live_tracker_status", lambda: {})
    monkeypatch.setattr(webcam_tool, "grab_jpeg", lambda: (b"x", "ir"))
    monkeypatch.setattr(webcam_tool, "_analyze_jpeg", lambda jpeg, q: ("a dark room", "claude-haiku-4-5"))

    def never(jpeg):
        raise AssertionError("face recognition must not run on IR frames")

    monkeypatch.setattr(webcam_tool, "_recognized_faces", never)
    out = json.loads(webcam_tool._handle_webcam({"question": "who's here?"}))
    assert out["source"] == "ir"
    assert "recognized" not in out
