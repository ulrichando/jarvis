"""Tests for the extended Route label set (2026-05-24).

8 routes: BANTER, TASK_{DESKTOP,BROWSER,CODE,FILES,OTHER}, REASONING,
EMOTIONAL. The pre-existing BANTER/REASONING/EMOTIONAL labels are
unchanged; the prior single TASK label has been split into 5 sub-routes.
"""
from __future__ import annotations

import pytest

from pipeline.turn_router import (
    Route,
    _VALID_ROUTES,
    _ROUTE_BASE,
    route_from_classifier_output,
)


def test_all_8_routes_in_valid_set():
    expected = {
        "BANTER",
        "TASK_DESKTOP", "TASK_BROWSER", "TASK_CODE", "TASK_FILES", "TASK_OTHER",
        "REASONING", "EMOTIONAL",
    }
    assert _VALID_ROUTES == expected


def test_route_base_covers_all_routes():
    for r in _VALID_ROUTES:
        assert r in _ROUTE_BASE, f"{r} missing from _ROUTE_BASE"


def test_route_from_output_recognizes_sub_routes():
    assert route_from_classifier_output("TASK_DESKTOP") == "TASK_DESKTOP"
    assert route_from_classifier_output("TASK_CODE") == "TASK_CODE"
    # Case-insensitive normalization
    assert route_from_classifier_output("task_browser") == "TASK_BROWSER"


def test_route_from_output_unknown_falls_back_to_task_other():
    """Pre-2026-05-24 the fallback was 'TASK'; now it's TASK_OTHER."""
    assert route_from_classifier_output("BOGUS") == "TASK_OTHER"
    assert route_from_classifier_output("") == "TASK_OTHER"


def test_legacy_task_label_normalizes_to_task_other():
    """A classifier emitting bare 'TASK' (old label) gets normalized."""
    assert route_from_classifier_output("TASK") == "TASK_OTHER"


def test_router_prompt_distinguishes_browser_from_other():
    """The router prompt must cleanly separate INTERACTIVE browser work from web
    lookups. Current routing (2026-06): web_search / web_fetch are DIRECT tools,
    so web lookups route to TASK_OTHER; TASK_BROWSER is reserved for page
    interaction (navigate, login, forms, clicks). Guards against misrouting a
    web lookup into a headless browser task."""
    from pipeline.turn_router import ROUTER_PROMPT_TEMPLATE

    # TASK_BROWSER is for interactive/visible browser work.
    assert "TASK_BROWSER" in ROUTER_PROMPT_TEMPLATE
    assert "page interaction" in ROUTER_PROMPT_TEMPLATE

    # The web path (web_search / web_fetch) lives under TASK_OTHER.
    assert "web_search" in ROUTER_PROMPT_TEMPLATE
    assert "web_fetch" in ROUTER_PROMPT_TEMPLATE

    # The IMPORTANT note must steer web lookups to TASK_OTHER, not the browser.
    important = ROUTER_PROMPT_TEMPLATE.split("IMPORTANT:")[1]
    assert "TASK_BROWSER" in important
    assert "TASK_OTHER" in important

    # "search the web" must be in TASK_OTHER, NOT TASK_BROWSER.
    browser_section_start = ROUTER_PROMPT_TEMPLATE.index("TASK_BROWSER")
    other_section_start = ROUTER_PROMPT_TEMPLATE.index("TASK_OTHER")
    reasoning_section_start = ROUTER_PROMPT_TEMPLATE.index("REASONING")
    browser_section = ROUTER_PROMPT_TEMPLATE[browser_section_start:other_section_start]
    other_section = ROUTER_PROMPT_TEMPLATE[other_section_start:reasoning_section_start]
    assert "search the web" in other_section
    assert "search the web" not in browser_section
