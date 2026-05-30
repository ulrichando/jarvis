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
