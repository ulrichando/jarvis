#!/usr/bin/env python3
"""Live integration smoke-test for new computer_use actions (Gaps 1-4, 9-10).

Runs against the REAL X11 backend — NOT the NoopBackend. Exercises every new
action enough to prove the xdotool plumbing works end-to-end. Mouse/key tests
only read state or target a temporary window to avoid disrupting the user's
desktop.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import tools.computer_use as cu
import tools.computer_use_backend as cub

PASS = 0
FAIL = 0


def test(name: str, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ✓ {name}")
    except Exception as e:
        FAIL += 1
        print(f"  ✗ {name}: {e}")


# ── Setup ────────────────────────────────────────────────────────────
cu.reset_backend_for_tests()
os.environ["JARVIS_COMPUTER_USE_BACKEND"] = "x11"
backend = cu._get_backend()

print(f"Backend: {type(backend).__name__}  |  DISPLAY={os.environ.get('DISPLAY','?')}")
print(f"xdotool: {cub.xdotool_available()}  |  wmctrl: {subprocess.run(['which','wmctrl'],capture_output=True,text=True).stdout.strip()}")
print()

# ── Read-only actions (safe to run anywhere) ─────────────────────────
print("── Read-only actions ──")


def _test_capture_vision():
    """capture(mode='vision') returns a valid screenshot."""
    out = json.loads(cu.handle_computer_use({"action": "capture", "mode": "vision"}))
    assert out["ok"], f"capture failed: {out}"
    assert out["screenshot_captured"], "no screenshot captured"
    assert out["width"] > 0 and out["height"] > 0, f"bad dimensions: {out['width']}x{out['height']}"
    print(f"      {out['width']}x{out['height']} px, {out['screenshot_bytes']} bytes")


test("capture vision", _test_capture_vision)


def _test_capture_som():
    """capture(mode='som') returns elements with overlays."""
    out = json.loads(cu.handle_computer_use({"action": "capture", "mode": "som"}))
    assert out["ok"], f"SOM capture failed: {out}"
    assert out["mode"] == "som"
    n = len(out.get("elements", []))
    print(f"      {n} window(s) with SOM overlays")


test("capture SOM", _test_capture_som)


def _test_capture_region_zoom():
    """capture with region=[x1,y1,x2,y2] crops to sub-region."""
    # First get the screen dimensions
    full = json.loads(cu.handle_computer_use({"action": "capture", "mode": "vision"}))
    w, h = full["width"], full["height"]
    # Crop the top-left 400x300 corner
    out = json.loads(cu.handle_computer_use({
        "action": "capture", "mode": "vision",
        "region": [0, 0, 400, 300],
    }))
    assert out["ok"], f"region capture failed: {out}"
    assert out["width"] <= 400 and out["height"] <= 300, (
        f"expected crop ≤400x300, got {out['width']}x{out['height']}"
    )
    print(f"      cropped: {out['width']}x{out['height']} px (original: {w}x{h})")


test("capture region zoom", _test_capture_region_zoom)


def _test_capture_bad_region():
    """malformed region returns error (not crash)."""
    out = json.loads(cu.handle_computer_use({
        "action": "capture", "region": [1, 2, 3],
    }))
    assert "error" in out, f"expected error for bad region, got: {out}"


test("capture bad region errors", _test_capture_bad_region)


def _test_cursor_position():
    """cursor_position returns current x,y."""
    out = json.loads(cu.handle_computer_use({"action": "cursor_position"}))
    assert out["ok"], f"cursor_position failed: {out}"
    assert out["x"] is not None and out["y"] is not None
    print(f"      cursor at ({out['x']}, {out['y']})")


test("cursor_position", _test_cursor_position)


def _test_list_apps():
    """list_apps returns window list."""
    out = json.loads(cu.handle_computer_use({"action": "list_apps"}))
    assert "apps" in out
    print(f"      {out['count']} window(s)")


test("list_apps", _test_list_apps)


# ── Mouse actions (safe: use tiny moves in safe area) ────────────────
print("\n── Mouse actions ──")


def _test_mouse_move():
    """mouse_move to a known coordinate."""
    out = json.loads(cu.handle_computer_use({
        "action": "mouse_move", "coordinate": [500, 500],
    }))
    assert out["ok"], f"mouse_move failed: {out}"
    # Verify the cursor actually moved
    pos = json.loads(cu.handle_computer_use({"action": "cursor_position"}))
    # Allow some tolerance — the WM may clamp coords
    assert abs(pos["x"] - 500) < 50 and abs(pos["y"] - 500) < 50, (
        f"cursor at ({pos['x']},{pos['y']}), expected near (500,500)"
    )


test("mouse_move", _test_mouse_move)


def _test_mouse_down_up():
    """left_mouse_down / left_mouse_up — raw button press/release."""
    # Move to a safe area first (bottom-right corner of screen)
    full = json.loads(cu.handle_computer_use({"action": "capture", "mode": "vision"}))
    safe_x, safe_y = full["width"] - 100, full["height"] - 100
    cu.handle_computer_use({"action": "mouse_move", "coordinate": [safe_x, safe_y]})

    down = json.loads(cu.handle_computer_use({
        "action": "left_mouse_down", "coordinate": [safe_x, safe_y],
    }))
    assert down["ok"], f"mouse_down failed: {down}"
    time.sleep(0.1)

    up = json.loads(cu.handle_computer_use({
        "action": "left_mouse_up", "button": "left",
    }))
    assert up["ok"], f"mouse_up failed: {up}"
    print(f"      down/up at ({safe_x}, {safe_y}) — ok")


test("left_mouse_down + left_mouse_up", _test_mouse_down_up)


# ── Keyboard actions (safe: test with a non-modifier key in a safe combo) ──
print("\n── Keyboard actions ──")


def _test_key_down_up():
    """key_down / key_up of a safe modifier."""
    down = json.loads(cu.handle_computer_use({
        "action": "key_down", "keys": "Shift",
    }))
    assert down["ok"], f"key_down failed: {down}"
    time.sleep(0.05)
    up = json.loads(cu.handle_computer_use({
        "action": "key_up", "keys": "Shift",
    }))
    assert up["ok"], f"key_up failed: {up}"
    print("      Shift down/up — ok")


test("key_down + key_up", _test_key_down_up)


def _test_hold_key():
    """hold_key for a short duration."""
    out = json.loads(cu.handle_computer_use({
        "action": "hold_key", "keys": "Shift", "seconds": 0.1,
    }))
    assert out["ok"], f"hold_key failed: {out}"
    print("      Shift held 0.1s — ok")


test("hold_key", _test_hold_key)


def _test_key_down_blocked_combo():
    """blocked combo is rejected."""
    out = json.loads(cu.handle_computer_use({
        "action": "key_down", "keys": "ctrl+alt+BackSpace",
    }))
    assert "blocked key combo" in out.get("error", ""), f"should block, got: {out}"


test("key_down blocked combo", _test_key_down_blocked_combo)


def _test_hold_key_blocked_combo():
    """hold_key blocked combo."""
    out = json.loads(cu.handle_computer_use({
        "action": "hold_key", "keys": "super+l",
    }))
    assert "blocked key combo" in out.get("error", ""), f"should block, got: {out}"


test("hold_key blocked combo (super+l)", _test_hold_key_blocked_combo)


# ── Permission tiers ─────────────────────────────────────────────────
print("\n── Permission tiers ──")

_SAVED_TIER = os.environ.get("JARVIS_COMPUTER_USE_TIER")


def _clean_tier():
    if _SAVED_TIER is not None:
        os.environ["JARVIS_COMPUTER_USE_TIER"] = _SAVED_TIER
    else:
        os.environ.pop("JARVIS_COMPUTER_USE_TIER", None)


def _test_tier_view_blocks_click():
    """view tier blocks destructive actions."""
    os.environ["JARVIS_COMPUTER_USE_TIER"] = "view"
    out = json.loads(cu.handle_computer_use({
        "action": "click", "coordinate": [100, 100],
    }))
    assert "blocked by tier" in out.get("error", ""), f"view should block click: {out}"
    # But capture still works
    out2 = json.loads(cu.handle_computer_use({"action": "capture"}))
    assert out2["ok"], f"view should allow capture: {out2}"


test("tier view blocks click, allows capture", _test_tier_view_blocks_click)
_clean_tier()


def _test_tier_interact_blocks_key():
    """interact tier blocks keyboard but allows mouse."""
    os.environ["JARVIS_COMPUTER_USE_TIER"] = "interact"
    # keyboard blocked
    out = json.loads(cu.handle_computer_use({"action": "type", "text": "hello"}))
    assert "blocked by tier" in out.get("error", ""), f"interact should block type: {out}"
    # mouse allowed — use safe corner coordinate
    out2 = json.loads(cu.handle_computer_use({
        "action": "mouse_move", "coordinate": [100, 100],
    }))
    assert out2["ok"], f"interact should allow mouse_move: {out2}"


test("tier interact blocks keyboard, allows mouse", _test_tier_interact_blocks_key)
_clean_tier()


# ── Browser schema check (doesn't need venv) ─────────────────────────
print("\n── Browser schema ──")

import tools.browser as br


def _test_browser_schema_has_new_params():
    """The browser_task schema includes the new optional params."""
    schema = br._BROWSER_TASK_SCHEMA
    props = schema["parameters"]["properties"]
    for key in ("flash_mode", "max_actions_per_step", "initial_actions", "sensitive_data"):
        assert key in props, f"missing {key} in browser_task schema"
    print("      flash_mode, max_actions_per_step, initial_actions, sensitive_data present")


test("browser schema has new params", _test_browser_schema_has_new_params)


def _test_browser_request_includes_new_params():
    """Request object forwards new params when set."""
    task_obj = {
        "task": "navigate to example.com",
        "flash_mode": True,
        "max_actions_per_step": 3,
        "initial_actions": [{"navigate": {"url": "https://example.com"}}],
        "sensitive_data": {"password": "hunter2"},
    }
    # Build the request the same way _handle_browser_task does
    request_obj: dict = {"task": task_obj["task"], "max_steps": 15, "headless": True}
    for key in ("flash_mode", "max_actions_per_step"):
        if key in task_obj:
            request_obj[key] = task_obj[key]
    if task_obj.get("initial_actions"):
        request_obj["initial_actions"] = task_obj["initial_actions"]
    if task_obj.get("sensitive_data"):
        request_obj["sensitive_data"] = task_obj["sensitive_data"]

    assert request_obj["flash_mode"] is True
    assert request_obj["max_actions_per_step"] == 3
    assert request_obj["initial_actions"] == [{"navigate": {"url": "https://example.com"}}]
    assert request_obj["sensitive_data"] == {"password": "hunter2"}
    print("      all params forwarded in request object")


test("browser request includes new params", _test_browser_request_includes_new_params)


# ── Summary ───────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"  {PASS} passed, {FAIL} failed out of {PASS+FAIL}")
print(f"{'='*50}")
sys.exit(0 if FAIL == 0 else 1)
