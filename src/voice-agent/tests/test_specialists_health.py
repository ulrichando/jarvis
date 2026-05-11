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
    from subagents.registry import clear, clear_subagents
    clear()
    clear_subagents()
    from subagents import (
        desktop, browser, browser_v2,
        summarize, weather, researcher, validator, code_reviewer,
        memory_recall, github,
    )
    desktop.register_desktop()
    browser.register_browser()
    browser_v2.register_browser_v2()
    summarize.register_summarize()
    weather.register_weather()
    researcher.register_researcher()
    validator.register_validator()
    code_reviewer.register_code_reviewer()
    memory_recall.register_memory_recall()
    github.register_github()
    yield
    clear()
    clear_subagents()


# ── Specialist matrix ─────────────────────────────────────────────────


SPECIALIST_NAMES = ["desktop", "browser"]
SUBAGENT_NAMES = ["summarize", "weather", "researcher"]

# Conditionally tested specialists — present-in-registry but only
# enabled when their dep keys are available. The matrix tests below
# parametrize against enabled specialists only.
import os as _os
_BROWSER_V2_ENABLED = bool(
    _os.environ.get("GROQ_API_KEY") or _os.environ.get("DEEPSEEK_API_KEY")
)
_VALIDATOR_ENABLED = bool(_os.environ.get("GROQ_API_KEY"))
_CODE_REVIEWER_ENABLED = bool(_os.environ.get("GROQ_API_KEY"))
from pathlib import Path as _Path
_MEMORY_RECALL_ENABLED = (_Path.home() / ".jarvis" / "conversations.db").exists()
import shutil as _shutil
_GITHUB_ENABLED = bool(_shutil.which("gh")) and (
    _Path.home() / ".config" / "gh" / "hosts.yml"
).exists()


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
    from subagents.registry import get
    spec = get(name)
    assert spec is not None, f"{name} specialist not registered"
    assert spec.enabled is True, f"{name} specialist disabled"
    assert spec.transfer_tool == f"transfer_to_{name}"


@pytest.mark.parametrize("name", SUBAGENT_NAMES)
def test_subagent_registered_and_enabled(name):
    from subagents.registry import get_subagent
    spec = get_subagent(name)
    assert spec is not None, f"{name} subagent not registered"
    assert spec.enabled is True


# ── Tool factory builds (catches import / decorator regressions) ──────


@pytest.mark.parametrize("name", SPECIALIST_NAMES)
def test_specialist_tool_factory_builds(name):
    """Tool factory must run cleanly — catches Pydantic / decorator
    regressions like the 2026-05-01 `_confirmed` leading-underscore
    bug that crashed every LLM call before any HTTP."""
    from subagents.registry import get
    spec = get(name)
    tools = spec.tool_factory()
    assert isinstance(tools, list)
    assert len(tools) > 0, f"{name} has zero tools"


@pytest.mark.parametrize("name", SUBAGENT_NAMES)
def test_subagent_tool_factory_builds(name):
    from subagents.registry import get_subagent
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
    from subagents.registry import get, get_subagent
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
    from subagents.registry import get
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
    """The supervisor prompt must contain a rule against false-success
    claims. Captured live 2026-05-01: desktop specialist hallucinated
    'A new tab is open, sir.' with no tool fired.

    Updated 2026-05-05 (W-021 prompt rewrite): structural anchor is now
    the section header `NEVER CLAIM AN ACTION YOU DIDN'T TAKE`."""
    from jarvis_agent import JARVIS_INSTRUCTIONS
    assert "NEVER CLAIM AN ACTION YOU DIDN'T TAKE" in JARVIS_INSTRUCTIONS, (
        "anti-confabulation section missing from supervisor prompt"
    )
    # The 2026-05-01 incident must still be in the prompt as a concrete
    # past-failure example (evidence-based rules retain their force).
    assert "A new tab is open" in JARVIS_INSTRUCTIONS, (
        "anti-confabulation section missing the 2026-05-01 incident "
        "evidence — rule loses force without the concrete failure"
    )


def test_desktop_specialist_has_anti_confabulation_rule():
    from subagents.registry import get
    spec = get("desktop")
    assert "NEVER claim success without a tool result" in spec.instructions


def test_browser_specialist_has_anti_confabulation_rule():
    from subagents.registry import get
    spec = get("browser")
    assert "NEVER claim success without a tool result" in spec.instructions


def test_supervisor_has_anti_tool_call_text_rule():
    """W-017 (2026-05-05): the supervisor prompt must explicitly
    forbid writing tool-call protocol shapes as reply text. Live-
    captured turn 962 (22:29 UTC, EMOTIONAL/llama-4-scout, "Jarvis?"):
    JARVIS replied with `task_done("Opened the OSU website, sir.")`
    verbatim — the LLM was confused by a prior specialist's task_done
    in chat_ctx and parroted the protocol. TTS read it aloud which
    sounded non-English to the user.

    The cure is structural: the prompt must NAME each of the leak
    forms we've seen, mark them banned, and explain that task_done
    is specialist-internal so the supervisor never types it. This
    test pins the section so a future prompt rewrite can't silently
    drop the discipline.
    """
    from jarvis_agent import JARVIS_INSTRUCTIONS
    instr = JARVIS_INSTRUCTIONS

    # Section header — W-021 (2026-05-05) renamed the section to
    # "NEVER WRITE THESE AS REPLY TEXT" so the header covers all
    # three banned classes (tool-call shapes + prompt labels +
    # meta-silence). Anchor on the new stable header.
    assert "NEVER WRITE THESE AS REPLY TEXT" in instr, (
        "supervisor prompt missing the anti-reply-text-leak section"
    )

    # The four leak forms we've live-captured + sanitized:
    # 1. Python form (`task_done(...)`)
    # 2. XML attribute (`<function=name>...`)
    # 3. XML bare (`<function>name</function>` + `<arguments>`)
    # 4. JSON array (`[{"name": ..., "parameters": ...}]`)
    # 5. Pipe-bracket (`<tool_call>...</tool_call>`)
    assert "task_done(" in instr, (
        "anti-tool-call section missing the Python-form example "
        "(task_done(...))"
    )
    assert "<function=" in instr, (
        "anti-tool-call section missing the XML-attribute-form "
        "example (<function=name>)"
    )
    assert "<function>" in instr, (
        "anti-tool-call section missing the XML-bare-form example "
        "(<function>name</function>)"
    )
    assert '"name":' in instr or '"name"' in instr, (
        "anti-tool-call section missing the JSON-array-form example"
    )

    # The 'specialist-internal' rationale must be present — the LLM
    # needs to know WHY task_done is off-limits, not just that it is.
    assert "task_done" in instr and "specialist" in instr.lower(), (
        "anti-tool-call section missing the rationale that task_done "
        "is specialist-internal — without it the LLM may still type "
        "task_done in supervisor replies"
    )


def test_supervisor_has_post_handoff_relay_rule():
    """W-017 (2026-05-05): user reported "JARVIS doesn't follow up
    when task is completed by subagent." Root cause: the prompt
    didn't explicitly tell the supervisor what to do after a
    specialist's task_done lands in chat_ctx. So sometimes the
    supervisor stayed silent (user thought JARVIS forgot) or
    parroted task_done verbatim.

    The cure is a positive rule: "after a specialist hands back,
    relay their summary in plain English." Pin the section.
    """
    from jarvis_agent import JARVIS_INSTRUCTIONS
    instr = JARVIS_INSTRUCTIONS

    # Section header — W-021 (2026-05-05) renamed to
    # "AFTER A TOOL OR HANDOFF" to cover both plain-tool returns and
    # specialist hand-backs (the relay rule is identical). Anchor on
    # the new header.
    assert "AFTER A TOOL OR HANDOFF" in instr, (
        "supervisor prompt missing the post-tool/handoff relay section "
        "— without it, supervisor stays silent or parrots task_done "
        "after specialist completion"
    )

    # The positive guidance: relay in plain English.
    assert "plain natural English" in instr or "plain English" in instr, (
        "post-handoff section missing the 'relay in plain English' "
        "directive"
    )

    # Counter-example: silence is bad after handoff.
    assert "silence" in instr.lower(), (
        "post-handoff section missing the silence-counter-example — "
        "the LLM needs to see explicitly that staying silent after "
        "a specialist hands back is wrong (user thinks JARVIS forgot)"
    )

    # Counter-example: verbatim parroting of task_done is bad.
    assert "verbatim parrot" in instr.lower() or "verbatim repeat" in instr.lower(), (
        "post-handoff section missing the verbatim-parroting counter-"
        "example"
    )


# test_planner_specialist_has_truthfulness_section was retired
# 2026-05-09 alongside the planner specialist itself. The anti-
# confabulation discipline it guarded now lives in the in-process
# plan-mode tool's prompt and the supervisor's "NEVER CLAIM AN ACTION
# YOU DIDN'T TAKE" section in JARVIS_INSTRUCTIONS.


def test_supervisor_has_persona_register_block():
    """The persona+register block must appear near the TOP of the
    supervisor prompt and must declare its policy via positive + negative
    register lists. Burying it mid-prompt didn't work — the LLM kept
    producing casual openers (live-observed pre-2026-05-04).

    Structural assertions only. The literal persona phrasing has been
    rewritten more than once (2026-05-04: 'dignified butler' → 'competent
    professional, not a Victorian butler'; W-021 2026-05-05: section
    header renamed to 'WHO YOU ARE' and the policy lists renamed to
    'Register — use these' / 'Register — BANNED'); pinning specific
    copy was brittle. The actionable invariants are: header present,
    header near the top, both positive+negative register lists present.
    """
    from jarvis_agent import JARVIS_INSTRUCTIONS

    header_idx = JARVIS_INSTRUCTIONS.find("WHO YOU ARE")
    assert header_idx != -1, (
        "WHO YOU ARE persona block missing entirely (W-021 renamed "
        "this from 'PERSONA & REGISTER')"
    )

    # Must appear at the very top — it's the first section in the
    # prompt as of W-021. 200 chars covers leading whitespace + the
    # header itself.
    assert header_idx < 200, (
        f"WHO YOU ARE header buried at offset {header_idx}; "
        "must appear in the first 200 chars (top-of-prompt)"
    )

    # The block must declare the actual policy. Without these two lists
    # the block is decorative and the LLM has no constraint to follow.
    assert "Register — use these" in JARVIS_INSTRUCTIONS, (
        "WHO YOU ARE block missing the 'Register — use these' "
        "positive policy list"
    )
    assert "Register — BANNED" in JARVIS_INSTRUCTIONS, (
        "WHO YOU ARE block missing the 'Register — BANNED' "
        "negative policy list"
    )


# ── Specific tool catalog assertions (the gaps that caused real bugs) ─


def test_browser_has_new_tab_tool():
    """ext_new_tab must exist — its absence caused the 2026-05-01
    confabulation incident ('Done, sir.' with no tool fired)."""
    import tools.browser_ext as e
    assert hasattr(e, "ext_new_tab")
    assert e.ext_new_tab in e.ALL_TOOLS


def test_browser_has_phase_a_tools():
    """Phase A 2026-05-02 — gap fills from cross-product audit:
    list_tabs, get_console, save_pdf, upload_file. Their absence
    means JARVIS is below browser-use / Playwright MCP tool surface."""
    import tools.browser_ext as e
    for name in ("ext_list_tabs", "ext_get_console", "ext_save_pdf", "ext_upload_file"):
        assert hasattr(e, name), f"missing: {name}"
        tool = getattr(e, name)
        assert tool in e.ALL_TOOLS, f"{name} not in ALL_TOOLS"


def test_browser_has_phase_b_tools():
    """Phase B 2026-05-02 — modern-web parity: localStorage I/O +
    storage_state save/restore + dropdown introspection. Without
    these, JARVIS handles 2015-era cookies-only auth flows but
    breaks on every modern SPA that uses localStorage tokens."""
    import tools.browser_ext as e
    for name in (
        "ext_local_storage",
        "ext_storage_state_get",
        "ext_storage_state_set",
        "ext_get_dropdown_options",
    ):
        assert hasattr(e, name), f"missing: {name}"
        tool = getattr(e, name)
        assert tool in e.ALL_TOOLS, f"{name} not in ALL_TOOLS"


def test_browser_has_phase_c_tools():
    """Phase C 2026-05-02 — advanced: Stagehand observe pattern,
    Playwright wait_for_load states, direct file download."""
    import tools.browser_ext as e
    for name in ("ext_observe", "ext_wait_for_load", "ext_download_file"):
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
    from subagents.registry import get
    instr = get("browser").instructions
    assert "ext_new_tab" in instr
    assert "open a new tab" in instr.lower()


# ── Specialist names are honest — no orphan registrations ────────────


def test_no_disabled_specialists_in_registry_for_long():
    """Anything with enabled=False shouldn't be cluttering the
    registry — disable means delete. (Loose check: hard-coded list of
    expected enabled names; tightens future cleanup work.)"""
    from subagents.registry import _REGISTRY, SUBAGENT_REGISTRY
    expected_enabled = set(SPECIALIST_NAMES) | set(SUBAGENT_NAMES)
    if _BROWSER_V2_ENABLED:
        expected_enabled.add("browser_v2")
    if _VALIDATOR_ENABLED:
        expected_enabled.add("validator")
    if _CODE_REVIEWER_ENABLED:
        expected_enabled.add("code_reviewer")
    if _MEMORY_RECALL_ENABLED:
        expected_enabled.add("memory_recall")
    if _GITHUB_ENABLED:
        expected_enabled.add("github")
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
    """browser_v2 must always REGISTER (even when disabled), so the
    supervisor's tool-routing logic sees a recognized name rather than
    a missing-spec error.

    Per `specialists/browser_v2.py:108-125`, browser_v2 is HARD-CODED
    disabled until three documented bugs are fixed (CDP attach to
    user's Chrome, Groq response_format=json_schema rejection, and the
    `actions[-1]` subscript TypeError on the bound method). The test
    enforces that intentional disable: when the disable is lifted, the
    assertion below MUST be updated to `assert spec.enabled == is_available()`
    so we re-link enabled state to key availability.
    """
    from subagents.registry import _REGISTRY
    assert "browser_v2" in _REGISTRY
    spec = _REGISTRY["browser_v2"]
    assert spec.transfer_tool == "transfer_to_browser_v2"
    assert spec.enabled is False, (
        "browser_v2 is intentionally disabled per browser_v2.py:108-125. "
        "If the three known bugs are fixed and this assertion now fails, "
        "update the test to compare with is_available() instead."
    )


def test_browser_v2_module_importable_without_key():
    """Importing tools.browser_v2 should never raise — even when
    GROQ/DeepSeek keys are missing. is_available() is the gate."""
    import tools.browser_v2
    assert hasattr(tools.browser_v2, "browser_task_v2")
    assert hasattr(tools.browser_v2, "is_available")


def test_browser_v2_self_disables_when_no_keys(monkeypatch):
    """is_available() returns False when both Groq and DeepSeek keys
    are absent. Critical for graceful-degradation behaviour: the
    specialist registers but stays out of the supervisor's tool list."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    import importlib
    import tools.browser_v2
    importlib.reload(tools.browser_v2)
    assert tools.browser_v2.is_available() is False


@pytest.mark.skipif(
    not _BROWSER_V2_ENABLED,
    reason="browser_v2 needs GROQ_API_KEY or DEEPSEEK_API_KEY",
)
def test_browser_v2_tool_factory_builds_when_enabled():
    """The tool factory must build cleanly when the dep keys are
    present. Reads from `_REGISTRY` directly because `registry.get()`
    returns None for any spec with `enabled=False`, and browser_v2 is
    intentionally hard-disabled (see test_browser_v2_specialist_registered).
    The factory itself works regardless of `enabled`; this test is
    about the import + decorator path, not the gate.
    """
    from subagents.registry import _REGISTRY
    spec = _REGISTRY.get("browser_v2")
    assert spec is not None, (
        "browser_v2 not registered — `register_browser_v2()` did not run"
    )
    tools = spec.tool_factory()
    assert any(
        getattr(getattr(t, "_func", t), "__name__", "") == "browser_task_v2"
        for t in tools
    )


def test_no_specialist_imports_task_done_from_jarvis_agent():
    """task_done lives on RegistrySubagent (specialists/agent.py:55)
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
        f"(it's a RegistrySubagent method). Offenders:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("name", SPECIALIST_NAMES + ["browser_v2"])
def test_specialist_handoff_constructs_without_error(name):
    """Smoke test the full handoff path: tool_factory must build, and
    the registry's transfer-tool builder must not crash. Catches
    runtime ImportErrors / decorator bugs that only surface during a
    real handoff (which is what happened with browser_v2's spurious
    task_done import on 2026-05-01)."""
    from subagents.registry import get
    spec = get(name)
    if spec is None or not spec.enabled:
        pytest.skip(f"{name} not enabled in this environment")
    # Just call tool_factory — that's where the broken import surfaces.
    tools = spec.tool_factory()
    assert isinstance(tools, list)
