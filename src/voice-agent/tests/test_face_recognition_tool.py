"""Tests for the ``face_recognition`` voice tool (tools/face_recognition.py)
and the vision/face_id.py store + matching logic.

The ONNX models are not present in CI: model-touching seams (_detect, _embed,
_match_score, grab_jpeg) are stubbed, while store I/O, thresholding, and the
spoken summaries are exercised for real against tmp paths.
"""
from __future__ import annotations

import json

import pytest

import tools.face_recognition as face_tool
import vision.face_id as face_id
from tools.registry import registry


@pytest.fixture()
def tmp_store(monkeypatch, tmp_path):
    store = tmp_path / "faces.json"
    monkeypatch.setenv("JARVIS_FACES_STORE_FILE", str(store))
    return store


# ---------------------------------------------------------------------------
# Registration + gating
# ---------------------------------------------------------------------------


def test_face_recognition_registered():
    entry = registry.get_entry("face_recognition")
    assert entry is not None
    assert entry.schema["parameters"]["required"] == ["action"]
    actions = entry.schema["parameters"]["properties"]["action"]["enum"]
    assert set(actions) == {"enroll", "identify", "list", "forget"}
    assert entry.check_fn is face_tool.check_face_recognition_requirements
    assert entry.is_async is False


def test_check_fn_gates_on_hardware_models_and_kill_switch(monkeypatch):
    monkeypatch.setattr(face_tool, "webcam_available", lambda: True)
    monkeypatch.setattr(face_id, "models_present", lambda: True)
    monkeypatch.delenv("JARVIS_FACE_RECOGNITION_DISABLED", raising=False)
    assert face_tool.check_face_recognition_requirements() is True

    monkeypatch.setenv("JARVIS_FACE_RECOGNITION_DISABLED", "1")
    assert face_tool.check_face_recognition_requirements() is False
    monkeypatch.delenv("JARVIS_FACE_RECOGNITION_DISABLED", raising=False)

    monkeypatch.setattr(face_id, "models_present", lambda: False)
    assert face_tool.check_face_recognition_requirements() is False

    monkeypatch.setattr(face_id, "models_present", lambda: True)
    monkeypatch.setattr(face_tool, "webcam_available", lambda: False)
    assert face_tool.check_face_recognition_requirements() is False


# ---------------------------------------------------------------------------
# face_id store logic (real I/O against tmp store)
# ---------------------------------------------------------------------------


def test_store_roundtrip_and_forget(tmp_store):
    assert face_id.list_people() == {}
    assert face_id._append_embedding("Alice", [0.1] * 4) == 1
    assert face_id._append_embedding("Alice", [0.2] * 4) == 2
    assert face_id._append_embedding("Bob", [0.3] * 4) == 1
    assert face_id.list_people() == {"Alice": 2, "Bob": 1}

    assert face_id.forget("alice") is True  # case-insensitive
    assert face_id.list_people() == {"Bob": 1}
    assert face_id.forget("Nobody") is False


def test_store_survives_corrupt_file(tmp_store):
    tmp_store.write_text("{not json")
    assert face_id.list_people() == {}
    assert face_id._append_embedding("Alice", [1.0]) == 1


def test_best_name_threshold(tmp_store, monkeypatch):
    face_id._append_embedding("Alice", [1.0, 0.0])
    face_id._append_embedding("Bob", [0.0, 1.0])

    # Dot product stand-in for SFace cosine matching.
    monkeypatch.setattr(
        face_id,
        "_match_score",
        lambda a, b: sum(x * y for x, y in zip(a, b)),
    )

    name, score = face_id._best_name([0.9, 0.1])
    assert name == "Alice" and score == pytest.approx(0.9)

    # Below the 0.363 default threshold → unknown, best score still reported.
    name, score = face_id._best_name([0.2, 0.1])
    assert name is None


def test_recognition_ready(tmp_store, monkeypatch):
    monkeypatch.setattr(face_id, "models_present", lambda: True)
    assert face_id.recognition_ready() is False  # nobody enrolled
    face_id._append_embedding("Alice", [1.0])
    assert face_id.recognition_ready() is True
    monkeypatch.setattr(face_id, "models_present", lambda: False)
    assert face_id.recognition_ready() is False


def test_identify_detailed_dedupes_names(tmp_store, monkeypatch):
    face_id._append_embedding("Alice", [1.0])
    monkeypatch.setattr(face_id, "_decode_bgr", lambda jpeg: "FRAME")
    monkeypatch.setattr(face_id, "_detect", lambda frame: ["f1", "f2", "f3"])
    monkeypatch.setattr(face_id, "_embed", lambda frame, row: [1.0])
    # All three faces match Alice; only one claim allowed, rest unknown.
    monkeypatch.setattr(face_id, "_best_name", lambda emb: ("Alice", 0.9))
    detail = face_id.identify_detailed(b"jpeg")
    assert [r["name"] for r in detail["recognized"]] == ["Alice"]
    assert detail["unknown_count"] == 2
    assert detail["face_count"] == 3


def test_enroll_requires_exactly_one_face(tmp_store, monkeypatch):
    monkeypatch.setattr(face_id, "_decode_bgr", lambda jpeg: "FRAME")
    monkeypatch.setattr(face_id, "_embed", lambda frame, row: [1.0])

    monkeypatch.setattr(face_id, "_detect", lambda frame: [])
    with pytest.raises(face_id.FaceIdError, match="no face"):
        face_id.enroll(b"j", "Alice")

    monkeypatch.setattr(face_id, "_detect", lambda frame: ["f1", "f2"])
    with pytest.raises(face_id.FaceIdError, match="2 faces"):
        face_id.enroll(b"j", "Alice")

    monkeypatch.setattr(face_id, "_detect", lambda frame: ["f1"])
    assert face_id.enroll(b"j", "Alice") == {"name": "Alice", "samples": 1}

    with pytest.raises(face_id.FaceIdError, match="name is required"):
        face_id.enroll(b"j", "   ")


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------


@pytest.fixture()
def camera(monkeypatch):
    """Well-lit camera stub; records the allow_ir kwarg the handler passed."""
    calls = {}

    def fake_grab(allow_ir=True):
        calls["allow_ir"] = allow_ir
        return (b"JPEG", "device")

    monkeypatch.setattr(face_tool, "grab_jpeg", fake_grab)
    monkeypatch.setattr(face_tool, "mean_jpeg_luma", lambda jpeg: 120.0)
    monkeypatch.setattr(face_tool, "dark_luma_threshold", lambda: 32.0)
    return calls


@pytest.fixture()
def dark_camera(monkeypatch):
    """Camera stub returning a frame darker than the dark-luma threshold."""
    monkeypatch.setattr(
        face_tool, "grab_jpeg", lambda allow_ir=True: (b"DARKJPEG", "device")
    )
    monkeypatch.setattr(face_tool, "mean_jpeg_luma", lambda jpeg: 23.0)
    monkeypatch.setattr(face_tool, "dark_luma_threshold", lambda: 32.0)


def test_handler_rejects_unknown_action():
    out = json.loads(face_tool._handle_face_recognition({"action": "dance"}))
    assert "error" in out


def test_handler_enroll(camera, monkeypatch):
    monkeypatch.setattr(
        face_id, "enroll", lambda jpeg, name: {"name": name, "samples": 1}
    )
    out = json.loads(
        face_tool._handle_face_recognition({"action": "enroll", "name": "Alice"})
    )
    assert out["name"] == "Alice" and out["samples"] == 1
    assert "Alice" in out["result"]
    assert "angle" in out["result"]  # robustness tip on low sample count

    out = json.loads(face_tool._handle_face_recognition({"action": "enroll"}))
    assert "error" in out  # name required


def test_handler_enroll_surfaces_face_errors(camera, monkeypatch):
    def boom(jpeg, name):
        raise face_id.FaceIdError("no face visible in the camera frame")

    monkeypatch.setattr(face_id, "enroll", boom)
    out = json.loads(
        face_tool._handle_face_recognition({"action": "enroll", "name": "Al"})
    )
    assert out["error"] == "no face visible in the camera frame"


def test_handler_identify(camera, monkeypatch):
    monkeypatch.setattr(
        face_id,
        "identify_detailed",
        lambda jpeg: {
            "recognized": [{"name": "Alice", "score": 0.8}],
            "unknown_count": 1,
            "face_count": 2,
        },
    )
    out = json.loads(face_tool._handle_face_recognition({"action": "identify"}))
    assert out["recognized"] == ["Alice"]
    assert out["unknown_count"] == 1
    assert "Alice" in out["result"] and "don't recognize" in out["result"]
    assert out["source"] == "device"
    assert "frame_too_dark" not in out  # well-lit → no darkness caveat


def test_handler_identify_empty_frame(camera, monkeypatch):
    monkeypatch.setattr(
        face_id,
        "identify_detailed",
        lambda jpeg: {"recognized": [], "unknown_count": 0, "face_count": 0},
    )
    out = json.loads(face_tool._handle_face_recognition({"action": "identify"}))
    assert "don't see anyone" in out["result"]


def test_handler_grabs_rgb_only(camera, monkeypatch):
    """Recognition frames must bypass the IR dark-assist (SFace is RGB-trained)."""
    monkeypatch.setattr(
        face_id,
        "identify_detailed",
        lambda jpeg: {"recognized": [], "unknown_count": 0, "face_count": 0},
    )
    face_tool._handle_face_recognition({"action": "identify"})
    assert camera["allow_ir"] is False


def test_handler_identify_dark_empty_frame_says_too_dark(dark_camera, monkeypatch):
    """Zero faces in a near-black frame ≠ empty room. Live failure 2026-06-11:
    user in frame at luma 23, JARVIS said "camera's empty" then confabulated
    a covered lens. The tool must hand the supervisor the darkness context."""
    monkeypatch.setattr(
        face_id,
        "identify_detailed",
        lambda jpeg: {"recognized": [], "unknown_count": 0, "face_count": 0},
    )
    out = json.loads(face_tool._handle_face_recognition({"action": "identify"}))
    assert "too dark" in out["result"]
    assert "don't see anyone" not in out["result"]
    assert out["frame_too_dark"] is True
    assert out["frame_luma"] == 23
    assert out["source"] == "device"


def test_handler_enroll_refuses_dark_frame(dark_camera, monkeypatch):
    """Dark-frame embeddings poison the store — enroll must refuse outright."""

    def never(jpeg, name):
        raise AssertionError("enroll must not run on a dark frame")

    monkeypatch.setattr(face_id, "enroll", never)
    out = json.loads(
        face_tool._handle_face_recognition({"action": "enroll", "name": "Alice"})
    )
    assert "too dark" in out["error"]
    assert out["frame_luma"] == 23


def test_handler_capture_failure(monkeypatch):
    def boom(allow_ir=True):
        raise RuntimeError("no camera hardware produced a frame")

    monkeypatch.setattr(face_tool, "grab_jpeg", boom)
    out = json.loads(face_tool._handle_face_recognition({"action": "identify"}))
    assert "error" in out and "no camera hardware" in out["error"]


def test_handler_list_and_forget(tmp_store, monkeypatch):
    out = json.loads(face_tool._handle_face_recognition({"action": "list"}))
    assert out["people"] == {}

    face_id._append_embedding("Alice", [1.0])
    out = json.loads(face_tool._handle_face_recognition({"action": "list"}))
    assert out["people"] == {"Alice": 1}
    assert "Alice" in out["result"]

    out = json.loads(
        face_tool._handle_face_recognition({"action": "forget", "name": "Alice"})
    )
    assert "Forgotten" in out["result"]
    out = json.loads(
        face_tool._handle_face_recognition({"action": "forget", "name": "Alice"})
    )
    assert "error" in out

    out = json.loads(face_tool._handle_face_recognition({"action": "forget"}))
    assert "error" in out  # name required
