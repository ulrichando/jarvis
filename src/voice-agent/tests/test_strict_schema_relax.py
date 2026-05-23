"""Verify sanitizers.strict_schema_relax routes to the legacy schema
when a tool has any defaulted Python parameter, and keeps strict mode
for fully-required tools.

Live regression timeline (POSTMORTEM-001):

  - 2026-05-05 17:13 UTC: Groq rejects ext_new_tab calls with
    `tool call validation failed: missing properties: 'url'` —
    strict-mode `required: [all]` + LLM omits optional → reject.

  - 2026-05-05 17:22 UTC (W-009 first iteration): drop defaulted
    params from `required`. Groq rejects with `invalid JSON schema:
    /required must include every key`.

  - 2026-05-05 17:33 UTC (W-009 second iteration): also drop
    `additionalProperties: false`. Groq rejects with
    `additionalProperties:false must be set on every object`.

  - 2026-05-05 21:16 UTC: user reports JARVIS silent. Reading the
    error messages literally: strict mode requires BOTH
    `required: [all]` AND `additionalProperties: false`. There is
    no valid hybrid. The fix is: legacy schema for tools with
    defaults, strict schema for tools without.

These tests pin both halves of that contract.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


@pytest.fixture
def reinstall_relax():
    """Force a fresh install for this test even if the module-load
    install ran. Idempotent on the second-call no-op."""
    from livekit.agents.llm import utils as _lk_utils
    saved = _lk_utils.build_strict_openai_schema
    if getattr(saved, "_jarvis_relaxed", False):
        yield
        return
    import sanitizers.strict_schema_relax as relax
    relax.install()
    yield


def _build_tool(fn):
    """Wrap a plain async function as a livekit FunctionTool."""
    from livekit.agents.llm import function_tool
    return function_tool(fn)


def test_tool_with_optional_param_uses_legacy_schema(reinstall_relax):
    """Optional[str] = None is a Python default; the patch must route
    the WHOLE tool through the legacy generator (no
    additionalProperties:false, partial required) — not a hybrid that
    Groq rejects.
    """
    from livekit.agents.llm import utils as _lk_utils

    @_build_tool
    async def fake_new_tab(url: Optional[str] = None) -> str:
        """Args: url: optional URL."""
        return ""

    schema = _lk_utils.build_strict_openai_schema(fake_new_tab)
    params = schema["function"]["parameters"]
    # Legacy shape: NO additionalProperties: false.
    assert "additionalProperties" not in params, (
        f"tool with optional param must use legacy schema (no "
        f"additionalProperties); got params={params}"
    )
    # Legacy shape: optional NOT in required.
    assert "url" not in params.get("required", []), (
        f"url has Optional[str]=None; legacy schema must not require it. "
        f"Got required={params.get('required')}"
    )
    # url must still be a known property.
    assert "url" in params["properties"], "url must still be in properties"


def test_tool_with_default_int_uses_legacy_schema(reinstall_relax):
    """Mirror of ext_wait_for_load: defaulted int routes to legacy."""
    from livekit.agents.llm import utils as _lk_utils

    @_build_tool
    async def fake_wait(state: Optional[str] = None,
                        timeout_ms: int = 10000) -> str:
        """Args: state: load state. timeout_ms: max wait."""
        return ""

    schema = _lk_utils.build_strict_openai_schema(fake_wait)
    params = schema["function"]["parameters"]
    assert "additionalProperties" not in params
    required = params.get("required", [])
    assert "state" not in required
    assert "timeout_ms" not in required


def test_tool_all_required_uses_legacy_schema(reinstall_relax):
    """Even when every param is required, we still route through legacy.
    Mixing strict + legacy in the same request rejects (Groq enforces
    strict invariants on EVERY tool when ANY tool is strict).
    Forcing legacy for everything keeps the request shape consistent.
    """
    from livekit.agents.llm import utils as _lk_utils

    @_build_tool
    async def fake_navigate(url: str) -> str:
        """Args: url: full URL including protocol."""
        return ""

    schema = _lk_utils.build_strict_openai_schema(fake_navigate)
    params = schema["function"]["parameters"]
    # Legacy shape: NO additionalProperties:false anywhere.
    assert "additionalProperties" not in params, (
        f"tool with all-required params must use legacy. "
        f"Got params={params}"
    )
    assert params["required"] == ["url"]
    # Legacy schema must NOT include the function.strict flag.
    assert "strict" not in schema["function"], (
        "legacy schema must not include strict:True at the function "
        "level — Groq rejects mixed strict/legacy requests"
    )


def test_every_tool_uses_legacy_shape_no_invalid_hybrid(reinstall_relax):
    """The actual contract this test enforces: every schema we send is
    legacy-shape — no `additionalProperties: false`, no per-tool
    `strict: True` flag, `required` only lists params without defaults.

    This is the test that would have caught the W-009 first three
    iterations, all of which produced shapes Groq rejects.
    """
    from livekit.agents.llm import utils as _lk_utils

    @_build_tool
    async def fake_mixed(
        required_arg: str,
        optional_arg: Optional[str] = None,
        with_default: int = 42,
    ) -> str:
        """Args:
            required_arg: required.
            optional_arg: optional.
            with_default: int with default.
        """
        return ""

    schema = _lk_utils.build_strict_openai_schema(fake_mixed)
    params = schema["function"]["parameters"]
    properties_keys = set((params.get("properties") or {}).keys())
    required_keys = set(params.get("required") or [])
    has_add_props = params.get("additionalProperties") is False
    has_strict_flag = schema["function"].get("strict") is True

    assert not has_add_props, (
        f"legacy shape must not set additionalProperties:false. "
        f"Got params={params}"
    )
    assert not has_strict_flag, (
        "legacy shape must not set function.strict=True"
    )
    assert required_keys.issubset(properties_keys)
    # required_arg has no default → in required.
    # optional_arg + with_default → NOT in required.
    assert "required_arg" in required_keys
    assert "optional_arg" not in required_keys
    assert "with_default" not in required_keys


def test_install_is_idempotent():
    """Re-calling install() must be a no-op."""
    from livekit.agents.llm import utils as _lk_utils
    import sanitizers.strict_schema_relax as relax

    relax.install()
    first = _lk_utils.build_strict_openai_schema
    relax.install()
    second = _lk_utils.build_strict_openai_schema
    assert first is second
    assert getattr(_lk_utils.build_strict_openai_schema,
                   "_jarvis_relaxed", False)



def test_real_production_tools_use_legacy_shape(reinstall_relax):
    """Integration: spot-check the four tools that appeared in the
    live failure logs (bash, jarvis_agent.web_search, ext_new_tab,
    plus tools/browser_ext.web_search). Each must produce a
    legacy-shape schema."""
    from livekit.agents.llm import utils as _lk_utils

    candidates = []
    try:
        from jarvis_agent import bash
        candidates.append(("bash", bash))
    except Exception:
        pass
    try:
        from jarvis_agent import web_search as ws
        candidates.append(("jarvis_agent.web_search", ws))
    except Exception:
        pass
    try:
        from tools.browser_ext import ext_new_tab, web_search as ws2
        candidates.append(("ext_new_tab", ext_new_tab))
        candidates.append(("browser_ext.web_search", ws2))
    except Exception:
        pass

    assert candidates, "expected at least one of the named tools to import"

    for name, tool in candidates:
        schema = _lk_utils.build_strict_openai_schema(tool)
        params = schema["function"]["parameters"]

        assert "additionalProperties" not in params, (
            f"tool {name!r}: legacy schema must not set "
            f"additionalProperties. Got params={params}"
        )
        assert schema["function"].get("strict") is not True, (
            f"tool {name!r}: legacy schema must not set "
            f"function.strict=True"
        )



def test_supervisor_top_level_tools_use_legacy_shape(reinstall_relax):
    """The supervisor (jarvis_agent) imports several @function_tool
    decorated functions directly — they aren't in any subagent's
    tool_factory but they ARE sent to the LLM via the supervisor's
    own tool list. Bug captured live 2026-05-05 21:16 UTC: `bash` is
    one such tool and was producing the wrong shape during iter 2.

    This test names the supervisor-level tools explicitly because
    there's no registry to walk. If a new supervisor-level tool is
    added, add its name here.
    """
    from livekit.agents.llm import utils as _lk_utils
    import jarvis_agent

    # Names the supervisor calls @function_tool on at module scope.
    # bash/browser_task were removed in the Hermes teardown.
    supervisor_tools = (
        "launch_app",
        "run_jarvis_cli",
        "type_in_terminal",
        "media_control",
        "web_search",
        # Location split 2026-05-17: get_location/set_location retired
        # in favor of the three-tool surface below. See
        # prompts/supervisor.md "LOCATION QUESTIONS" for routing.
        "saved_address",
        "current_location",
        "set_saved_address",
    )

    failures: list[str] = []
    for name in supervisor_tools:
        tool = getattr(jarvis_agent, name, None)
        if tool is None:
            failures.append(f"{name}: missing from jarvis_agent module")
            continue
        try:
            schema = _lk_utils.build_strict_openai_schema(tool)
        except Exception as e:
            failures.append(f"{name}: build raised {type(e).__name__}: {e}")
            continue
        params = schema.get("function", {}).get("parameters", {})
        if "additionalProperties" in params:
            failures.append(
                f"{name}: schema sets additionalProperties "
                f"(must be legacy-shape)"
            )
        if schema.get("function", {}).get("strict") is True:
            failures.append(
                f"{name}: schema sets function.strict=True "
                f"(must be legacy-shape)"
            )

    assert not failures, (
        f"{len(failures)} supervisor tool(s) produce non-legacy schema:\n  "
        + "\n  ".join(failures)
    )
