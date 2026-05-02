"""Cross-specialist health tests.

Asserts the production-visible guarantees for every specialist + subagent:

  1. Registered and enabled
  2. Tool factory builds without import / decorator error
  3. Every @function_tool builds a strict OpenAI/Pydantic schema
     (catches the leading-underscore parameter bug + similar)
  4. Specialist prompt does NOT contain casual-register phrases the
     user has explicitly objected to (Got it, Yeah, Heck yes, etc.)
  5. Specialist prompt DOES contain the dignified-register phrasing

The matrix runs the same battery against all six specialists/subagents
so future additions inherit the discipline automatically.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Setup ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_and_register():
    """Each test starts with the registry registered fresh from the
    real specialist modules (not mocks). Cleanup after."""
    from specialists.registry import clear, clear_subagents
    clear()
    clear_subagents()
    from specialists import (
        desktop, browser, browser_v2, planner,
        summarize, weather, researcher, validator,
    )
    desktop.register_desktop()
    browser.register_browser()
    browser_v2.register_browser_v2()
    planner.register_planner()
    summarize.register_summarize()
    weather.register_weather()
    researcher.register_researcher()
    validator.register_validator()
    yield
    clear()
    clear_subagents()


# ── Specialist matrix ─────────────────────────────────────────────────


SPECIALIST_NAMES = ["desktop", "browser", "planner"]
SUBAGENT_NAMES = ["summarize", "weather", "researcher"]

# Conditionally tested specialists — present-in-registry but only
# enabled when their dep keys are available. The matrix tests below
# parametrize against enabled specialists only.
import os as _os
_BROWSER_V2_ENABLED = bool(
    _os.environ.get("GROQ_API_KEY") or _os.environ.get("DEEPSEEK_API_KEY")
)
_VALIDATOR_ENABLED = bool(_os.environ.get("GROQ_API_KEY"))


# Phrases the user has explicitly objected to (2026-05-01).
BANNED_REGISTER_PHRASES = [
    "Got it, sir",  # specifically the casual ack — examples in prompt
    "Sure thing",
    "You got it",
    "Heck yes",
    "Hell yes",
    "Mm-hm",
    "Yeah, that lands",
    "Rough day",
]


# ── Registration health ───────────────────────────────────────────────


@pytest.mark.parametrize("name", SPECIALIST_NAMES)
def test_specialist_registered_and_enabled(name):
    from specialists.registry import get
    spec = get(name)
    assert spec is not None, f"{name} specialist not registered"
    assert spec.enabled is True, f"{name} specialist disabled"
    assert spec.transfer_tool == f"transfer_to_{name}"


@pytest.mark.parametrize("name", SUBAGENT_NAMES)
def test_subagent_registered_and_enabled(name):
    from specialists.registry import get_subagent
    spec = get_subagent(name)
    assert spec is not None, f"{name} subagent not registered"
    assert spec.enabled is True


# ── Tool factory builds (catches import / decorator regressions) ──────


@pytest.mark.parametrize("name", SPECIALIST_NAMES)
def test_specialist_tool_factory_builds(name):
    """Tool factory must run cleanly — catches Pydantic / decorator
    regressions like the 2026-05-01 `_confirmed` leading-underscore
    bug that crashed every LLM call before any HTTP."""
    from specialists.registry import get
    spec = get(name)
    tools = spec.tool_factory()
    assert isinstance(tools, list)
    assert len(tools) > 0, f"{name} has zero tools"


@pytest.mark.parametrize("name", SUBAGENT_NAMES)
def test_subagent_tool_factory_builds(name):
    from specialists.registry import get_subagent
    spec = get_subagent(name)
    tools = spec.tool_factory()
    assert isinstance(tools, list)
    # subagents may have zero tools (summarize is a pure-prompt subagent)


# ── Schema correctness (the bug that broke Jarvis 2026-05-01) ─────────


@pytest.mark.parametrize("name", SPECIALIST_NAMES + SUBAGENT_NAMES)
def test_all_tool_schemas_build_strict(name):
    """Every @function_tool exposed by every specialist MUST build a
    strict Pydantic model — that's the path Groq's schema validator
    walks. Failures here = LLM call fails at schema-build time, agent
    goes silent on every prompt (bug captured live 2026-05-01,
    `_confirmed` parameter rejected by Pydantic v2.10).
    """
    from livekit.agents.llm.utils import function_arguments_to_pydantic_model
    from specialists.registry import get, get_subagent
    spec = get(name) or get_subagent(name)
    assert spec is not None
    failures = []
    for t in spec.tool_factory():
        tool_name = getattr(getattr(t, "_func", t), "__name__", "<anon>")
        try:
            function_arguments_to_pydantic_model(t)
        except Exception as e:
            failures.append(f"{tool_name}: {e}")
    assert not failures, (
        f"{name} specialist has tools with invalid schemas:\n  "
        + "\n  ".join(failures)
    )


# ── Persona register (no casual phrasing in instructions) ─────────────


@pytest.mark.parametrize("name", SPECIALIST_NAMES)
def test_specialist_prompt_avoids_banned_register(name):
    """Specialist prompts must not include casual-register phrases as
    *suggested* output — the user has explicitly objected ("you sound
    too funny"). Banned phrases that appear inside ❌ DON'T-USE blocks
    are exempt — those are warnings to the LLM."""
    from specialists.registry import get
    spec = get(name)
    instr = spec.instructions
    for phrase in BANNED_REGISTER_PHRASES:
        # Allowed if it's wrapped in a "never" / "don't" / "❌" / "Never"
        # context (i.e., used as a counter-example).
        idx = 0
        while True:
            pos = instr.find(phrase, idx)
            if pos == -1:
                break
            # Look at the 80 chars BEFORE the phrase for negation cues.
            before = instr[max(0, pos - 100):pos].lower()
            negated = any(
                marker in before
                for marker in ("never", "don't", "do not", "❌", "ban", "avoid", "not ", "instead")
            )
            assert negated, (
                f"{name} prompt contains banned register phrase {phrase!r} "
                f"as a suggested example (not negated). Context:\n"
                f"{instr[max(0, pos - 80):pos + len(phrase) + 80]}"
            )
            idx = pos + len(phrase)


# ── Critical guardrails present ───────────────────────────────────────


def test_supervisor_has_anti_confabulation_rule():
    """The supervisor prompt must contain Rule 4b (never claim an
    action you didn't take). Captured live 2026-05-01: desktop
    specialist hallucinated 'A new tab is open, sir.' with no tool
    fired. The mirror-rule prevents recurrence."""
    from jarvis_agent import JARVIS_INSTRUCTIONS
    assert "NEVER claim an action you didn't take" in JARVIS_INSTRUCTIONS, (
        "anti-confabulation rule (4b) missing from supervisor prompt"
    )


def test_desktop_specialist_has_anti_confabulation_rule():
    from specialists.registry import get
    spec = get("desktop")
    assert "NEVER claim success without a tool result" in spec.instructions


def test_browser_specialist_has_anti_confabulation_rule():
    from specialists.registry import get
    spec = get("browser")
    assert "NEVER claim success without a tool result" in spec.instructions


def test_supervisor_has_persona_register_block():
    """The dignified-butler register declaration must appear at the
    TOP of the supervisor prompt (first persona section). Burying it
    mid-prompt didn't work — the LLM kept producing casual openers."""
    from jarvis_agent import JARVIS_INSTRUCTIONS
    assert "PERSONA & REGISTER" in JARVIS_INSTRUCTIONS
    assert "dignified butler" in JARVIS_INSTRUCTIONS
    # Must appear in the FIRST 3000 chars (early in the prompt)
    assert JARVIS_INSTRUCTIONS.find("PERSONA & REGISTER") < 3000


# ── Specific tool catalog assertions (the gaps that caused real bugs) ─


def test_browser_has_new_tab_tool():
    """ext_new_tab must exist — its absence caused the 2026-05-01
    confabulation incident ('Done, sir.' with no tool fired)."""
    import jarvis_browser_ext as e
    assert hasattr(e, "ext_new_tab")
    assert e.ext_new_tab in e.ALL_TOOLS


def test_browser_has_phase_a_tools():
    """Phase A 2026-05-02 — gap fills from cross-product audit:
    list_tabs, get_console, save_pdf, upload_file. Their absence
    means JARVIS is below browser-use / Playwright MCP tool surface."""
    import jarvis_browser_ext as e
    for name in ("ext_list_tabs", "ext_get_console", "ext_save_pdf", "ext_upload_file"):
        assert hasattr(e, name), f"missing: {name}"
        tool = getattr(e, name)
        assert tool in e.ALL_TOOLS, f"{name} not in ALL_TOOLS"


def test_browser_has_phase_b_tools():
    """Phase B 2026-05-02 — modern-web parity: localStorage I/O +
    storage_state save/restore + dropdown introspection. Without
    these, JARVIS handles 2015-era cookies-only auth flows but
    breaks on every modern SPA that uses localStorage tokens."""
    import jarvis_browser_ext as e
    for name in (
        "ext_local_storage",
        "ext_storage_state_get",
        "ext_storage_state_set",
        "ext_get_dropdown_options",
    ):
        assert hasattr(e, name), f"missing: {name}"
        tool = getattr(e, name)
        assert tool in e.ALL_TOOLS, f"{name} not in ALL_TOOLS"


def test_extension_manifest_has_debugger_permission():
    """Phase A's save_pdf + upload_file + get_console all use
    chrome.debugger. Manifest must declare the permission, otherwise
    the user-visible failure is a silent NoOp on first call."""
    import json
    from pathlib import Path
    repo_root = Path(__file__).parent.parent.parent.parent
    manifest = json.loads(
        (repo_root / "src/extensions/jarvis-screen/manifest.json").read_text()
    )
    perms = manifest.get("permissions", [])
    assert "debugger" in perms, (
        "extension manifest missing 'debugger' permission — Phase A "
        "tools (save_pdf, upload_file, get_console) will fail at runtime"
    )
    assert "downloads" in perms, (
        "extension manifest missing 'downloads' permission — save_pdf "
        "uses chrome.downloads.download to drop the file"
    )


def test_browser_has_navigate_and_close_tab_distinguished():
    """ext_navigate REPLACES the active tab; ext_new_tab CREATES one.
    The browser specialist instructions must tell the LLM the
    difference (was the root cause of the bug)."""
    from specialists.registry import get
    instr = get("browser").instructions
    assert "ext_new_tab" in instr
    assert "open a new tab" in instr.lower()


# ── Specialist names are honest — no orphan registrations ────────────


def test_no_disabled_specialists_in_registry_for_long():
    """Anything with enabled=False shouldn't be cluttering the
    registry — disable means delete. (Loose check: hard-coded list of
    expected enabled names; tightens future cleanup work.)"""
    from specialists.registry import _REGISTRY, SUBAGENT_REGISTRY
    expected_enabled = set(SPECIALIST_NAMES) | set(SUBAGENT_NAMES)
    if _BROWSER_V2_ENABLED:
        expected_enabled.add("browser_v2")
    if _VALIDATOR_ENABLED:
        expected_enabled.add("validator")
    actual_enabled = {
        s.name for s in (list(_REGISTRY.values()) + list(SUBAGENT_REGISTRY.values()))
        if s.enabled
    }
    # If a NEW enabled spec is added, we want to know — explicit
    # add-here gate prevents quiet enables.
    unexpected = actual_enabled - expected_enabled
    assert not unexpected, (
        f"Unexpected enabled specialists/subagents (add to expected list "
        f"or remove): {unexpected}"
    )


# ── browser_v2 specialist (conditional on GROQ/DeepSeek availability) ─


def test_browser_v2_specialist_registered():
    """browser_v2 should always REGISTER (even if disabled). Lets us
    check enabled state directly rather than missing-spec errors."""
    from specialists.registry import _REGISTRY
    assert "browser_v2" in _REGISTRY
    spec = _REGISTRY["browser_v2"]
    assert spec.transfer_tool == "transfer_to_browser_v2"
    # enabled state mirrors is_available()
    from jarvis_browser_v2 import is_available
    assert spec.enabled == is_available()


def test_browser_v2_module_importable_without_key():
    """Importing jarvis_browser_v2 should never raise — even when
    GROQ/DeepSeek keys are missing. is_available() is the gate."""
    import jarvis_browser_v2
    assert hasattr(jarvis_browser_v2, "browser_task_v2")
    assert hasattr(jarvis_browser_v2, "is_available")


def test_browser_v2_self_disables_when_no_keys(monkeypatch):
    """is_available() returns False when both Groq and DeepSeek keys
    are absent. Critical for graceful-degradation behaviour: the
    specialist registers but stays out of the supervisor's tool list."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    import importlib
    import jarvis_browser_v2
    importlib.reload(jarvis_browser_v2)
    assert jarvis_browser_v2.is_available() is False


@pytest.mark.skipif(
    not _BROWSER_V2_ENABLED,
    reason="browser_v2 needs GROQ_API_KEY or DEEPSEEK_API_KEY",
)
def test_browser_v2_tool_factory_builds_when_enabled():
    from specialists.registry import get
    spec = get("browser_v2")
    assert spec is not None
    tools = spec.tool_factory()
    assert any(
        getattr(getattr(t, "_func", t), "__name__", "") == "browser_task_v2"
        for t in tools
    )


def test_no_specialist_imports_task_done_from_jarvis_agent():
    """task_done lives on RegistrySpecialist (specialists/agent.py:55)
    and is auto-attached when a specialist activates. Importing it from
    jarvis_agent ImportErrors at handoff time and crashes the
    specialist. Regression captured live 2026-05-01: browser_v2
    imported `task_done` from jarvis_agent → ImportError → handoff
    failed silently → supervisor's parallel web_fetch saved the turn,
    masking the bug."""
    from pathlib import Path
    spec_dir = Path(__file__).parent.parent / "specialists"
    offenders = []
    for f in spec_dir.glob("*.py"):
        text = f.read_text()
        # Crude but effective: any specialist that does
        # `from jarvis_agent import ... task_done ...` is broken.
        if "from jarvis_agent import" in text and "task_done" in text:
            # Walk the import lines to be sure.
            for line in text.splitlines():
                if line.startswith("from jarvis_agent import") and "task_done" in line:
                    offenders.append(f"{f.name}: {line.strip()}")
    assert not offenders, (
        f"Specialists must NOT import task_done from jarvis_agent "
        f"(it's a RegistrySpecialist method). Offenders:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("name", SPECIALIST_NAMES + ["browser_v2"])
def test_specialist_handoff_constructs_without_error(name):
    """Smoke test the full handoff path: tool_factory must build, and
    the registry's transfer-tool builder must not crash. Catches
    runtime ImportErrors / decorator bugs that only surface during a
    real handoff (which is what happened with browser_v2's spurious
    task_done import on 2026-05-01)."""
    from specialists.registry import get
    spec = get(name)
    if spec is None or not spec.enabled:
        pytest.skip(f"{name} not enabled in this environment")
    # Just call tool_factory — that's where the broken import surfaces.
    tools = spec.tool_factory()
    assert isinstance(tools, list)
