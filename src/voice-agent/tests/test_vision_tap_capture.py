"""Vision tap — screenshot capture layer (separate from the LLM call)."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_capture_screenshot_returns_path():
    """capture_screenshot uses scrot. Mock subprocess.run so the test
    doesn't actually take a screenshot."""
    from vision_tap import capture_screenshot

    with patch("vision_tap.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"fake-png-bytes")
            tmp_path = Path(f.name)

        try:
            with patch("vision_tap._screenshot_path", return_value=tmp_path):
                path = capture_screenshot()
            assert path == tmp_path
            assert mock_run.called
            args = mock_run.call_args.args[0]
            assert "scrot" in args[0]
        finally:
            tmp_path.unlink(missing_ok=True)


def test_capture_screenshot_returns_none_on_scrot_failure():
    from vision_tap import capture_screenshot
    with patch("vision_tap.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        path = capture_screenshot()
        assert path is None


def test_active_app_via_xdotool():
    """get_active_app uses xdotool. Mock the subprocess call."""
    from vision_tap import get_active_app
    with patch("vision_tap.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "google-chrome\n"
        app = get_active_app()
        assert app == "google-chrome"


def test_active_app_returns_none_on_xdotool_failure():
    from vision_tap import get_active_app
    with patch("vision_tap.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        assert get_active_app() is None
