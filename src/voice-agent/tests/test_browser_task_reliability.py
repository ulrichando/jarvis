"""Tests for ``browser_task`` reliability helpers (pure, no subprocess).

Covers the Phase-1 Task-3 helpers in ``tools/browser.py``:
  * ``_adaptive_max_steps`` — scale the step budget from the task string
    (~15 for a single lookup, ~50 for a multi-step flow), honoring an override.
  * ``_validate_task`` — reject a destination-less / goal-less task before the
    subprocess is ever spawned.

``tools/browser.py`` imports ONLY the stdlib + the registry, so loading it here
(in the voice venv that lacks ``browser_use``) is safe — no browser launch, no
subprocess.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_VA_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_VA_ROOT) not in sys.path:
    sys.path.insert(0, str(_VA_ROOT))


@pytest.fixture
def browser_tool():
    """Import ``tools.browser`` as a package module (stdlib + registry only).

    The module's ``from .registry import ...`` needs a package context, so it
    must be imported as ``tools.browser`` rather than loaded as a bare file.
    Importing it does NOT pull in ``browser_use`` (absent in the voice venv).
    """
    import tools.browser as mod  # noqa: PLC0415 — import inside fixture is intentional

    return mod


def test_adaptive_max_steps_lookup_vs_flow(browser_tool):
    assert browser_tool._adaptive_max_steps("find the price of X on nvidia.com") <= 20
    assert (
        browser_tool._adaptive_max_steps(
            "log into the site, add 3 items to the cart, fill checkout and pay"
        )
        >= 40
    )


def test_adaptive_max_steps_override_wins(browser_tool):
    # An explicit override is honored verbatim regardless of the task text.
    assert browser_tool._adaptive_max_steps("find the price of X on nvidia.com", 99) == 99


def test_task_validation_rejects_destinationless(browser_tool):
    ok, _ = browser_tool._validate_task("just look it up")
    assert ok is False
    ok, _ = browser_tool._validate_task("go to nvidia.com and find the RTX 6000 price")
    assert ok is True


def test_step_table_roundtrip(tmp_path):
    """``record_browser_step`` writes a row that reads back faithfully.

    Uses a throwaway DB so the live telemetry file is never touched. The table
    is additive (Web-Nav Phase 1, Task 4) — this asserts the insert + read-back
    contract for ``browser_task_steps`` independently of the runner/tool path.
    """
    import sqlite3

    from pipeline import turn_telemetry

    db = tmp_path / "telemetry.db"
    # init_db must create the new table without touching the turns schema.
    turn_telemetry.init_db(db)

    turn_telemetry.record_browser_step(
        db_path=db,
        task="find the price of the RTX 6000 on nvidia.com",
        step_index=0,
        action="go_to_url",
        ok=True,
        detail=None,
    )
    turn_telemetry.record_browser_step(
        db_path=db,
        task="find the price of the RTX 6000 on nvidia.com",
        step_index=1,
        action="click_element",
        ok=False,
        detail="element not found",
    )

    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT task, step_index, action, ok, detail "
            "FROM browser_task_steps ORDER BY step_index"
        ).fetchall()

    assert len(rows) == 2
    assert rows[0] == (
        "find the price of the RTX 6000 on nvidia.com",
        0,
        "go_to_url",
        1,
        None,
    )
    assert rows[1] == (
        "find the price of the RTX 6000 on nvidia.com",
        1,
        "click_element",
        0,
        "element not found",
    )


# ---------------------------------------------------------------------------
# CAPTCHA detection tests (pure pattern matching, no subprocess)
# ---------------------------------------------------------------------------


class _FakeHistory:
    """Minimal AgentHistoryList stand-in that exposes the methods
    ``_check_history_for_captcha`` calls."""

    def __init__(self, *, urls=None, content=None, action_results=None, action_names=None):
        self._urls = urls or []
        self._content = content or []
        self._action_results = action_results or []
        self._action_names = action_names or []

    def urls(self):
        return self._urls

    def extracted_content(self):
        return self._content

    def action_results(self):
        return self._action_results

    def action_names(self):
        return self._action_names


@pytest.fixture
def captcha_detector(browser_tool):
    """Direct access to the _check_history_for_captcha function from runner.py.

    Imported fresh each time from the browser_use_bridge package (which is
    stdlib-only — no browser_use dependency at module scope).
    """
    import importlib
    mod = importlib.import_module("browser_use_bridge.runner")
    return mod._check_history_for_captcha


def test_captcha_detector_url_match(captcha_detector):
    """A URL containing /captcha/ should be flagged."""
    hist = _FakeHistory(urls=["https://site.com/captcha/verify"])
    assert captcha_detector(hist) is not None


def test_captcha_detector_url_recaptcha(captcha_detector):
    """A URL containing recaptcha should be flagged."""
    hist = _FakeHistory(urls=["https://www.google.com/recaptcha/api2/demo"])
    assert captcha_detector(hist) is not None


def test_captcha_detector_clean_url(captcha_detector):
    """Normal URLs should return None."""
    hist = _FakeHistory(urls=[
        "https://example.com/page",
        "https://nvidia.com/products",
    ])
    assert captcha_detector(hist) is None


def test_captcha_detector_content_text(captcha_detector):
    """Page content containing 'unusual traffic' should be flagged."""
    hist = _FakeHistory(content=["We've detected unusual traffic from your network"])
    assert captcha_detector(hist) is not None


def test_captcha_detector_content_robot(captcha_detector):
    """Page content saying 'verify you are human' should be flagged."""
    hist = _FakeHistory(content=["Please verify you are human to continue"])
    assert captcha_detector(hist) is not None


def test_captcha_detector_clean_content(captcha_detector):
    """Normal page content should return None."""
    hist = _FakeHistory(content=["RTX 6000 price: $6,899", "Add to cart", "Search results"])
    assert captcha_detector(hist) is None


def test_captcha_detector_step_errors(captcha_detector):
    """Step errors containing CAPTCHA patterns should be flagged."""
    # action_results with an error attribute
    class FakeAR:
        def __init__(self, error=""):
            self.error = error

    results = [
        None,
        FakeAR(""),
        FakeAR("Too many requests. Please complete the captcha to continue."),
    ]
    hist = _FakeHistory(action_results=results)
    assert captcha_detector(hist) is not None


def test_captcha_format_result_with_hint(browser_tool):
    """When the payload carries a captcha_hint, the formatted result includes it."""
    payload = {
        "ok": True,
        "result": "Found the price list.",
        "steps_count": 10,
        "captcha_hint": "CAPTCHA in page content",
    }
    formatted = browser_tool._format_result(payload)
    assert "CAPTCHA" in formatted
    assert "computer_use" in formatted  # mentions fallback


def test_captcha_format_result_clean(browser_tool):
    """Without captcha_hint, the format is unchanged."""
    payload = {"ok": True, "result": "Found the price.", "steps_count": 5}
    formatted = browser_tool._format_result(payload)
    assert "CAPTCHA" not in formatted
    assert "computer_use" not in formatted
    assert "found the price" in formatted.lower()
