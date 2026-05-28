"""Tests for confab_detector — write-time hallucination filter.

Two priorities:
  1. ZERO false positives that would drop legitimate success messages
  2. Catches the actual incidents we've observed live

The negative tests are the load-bearing ones — false positives would
silence JARVIS in normal use. False negatives just let a confab
slip through (the recall window safety-net catches it later)."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import confab_detector
from confab_detector import looks_like_completion_claim


# Today's six confab strings (2026-05-27 Instagram session). Each MUST
# return True from looks_like_completion_claim after Task 2 lands.
CONFAB_STRINGS_2026_05_27 = [
    "On it.",
    "Let me see your screen and navigate to Instagram.",
    "I can see your desktop. Let me focus Chrome and open a new tab to Instagram.",
    "Done — Instagram's loading in a new tab.",
    "It's already open in the tab I just created. Give it a moment to load if it's still spinning.",
    "Done — Instagram's loading.",
]

# Control set — legitimate replies that must NOT match.
LEGIT_CONTROLS = [
    "I'll see what I can do.",
    "I can't see your screen right now.",          # negated — existing negation guard
    "Let me think about that for a moment.",       # "let me" + non-action verb
    "I see what you mean.",                        # no screen-element anchor
    "The forecast is sunny.",
    "I haven't opened that.",                      # negated
    "Let me know if that helps.",                  # "let me" + non-action verb
]


@pytest.mark.parametrize("text", CONFAB_STRINGS_2026_05_27)
def test_confab_2026_05_27_strings_all_detected(text):
    looks, pattern = looks_like_completion_claim(text)
    assert looks is True, (
        f"Expected confab detection for: {text!r}. "
        f"None of _STRONG_CLAIMS matched."
    )


@pytest.mark.parametrize("text", LEGIT_CONTROLS)
def test_legit_controls_not_flagged(text):
    looks, _ = looks_like_completion_claim(text)
    assert looks is False, (
        f"False positive on legit reply: {text!r}. "
        f"A new pattern is too broad — narrow it."
    )


# ── Helpers — fake message shapes mimicking livekit ChatMessage ───


def _user_msg(text):
    return SimpleNamespace(role="user", content=[text])


def _assistant_text(text):
    return SimpleNamespace(role="assistant", content=[text], tool_calls=None)


def _assistant_tool_call(name="ext_new_tab"):
    """Assistant message with a tool_calls field — proves a tool was invoked."""
    return SimpleNamespace(
        role="assistant",
        content=[],
        tool_calls=[SimpleNamespace(name=name, arguments={})],
    )


def _tool_result(content="OK"):
    """tool-role message — represents a successful tool result."""
    return SimpleNamespace(role="tool", content=[content], tool_calls=None)


# ── POSITIVE: detector should fire on these confabulations ────────


def test_caught_tab_open_without_tool():
    """The exact incident from 2026-05-02 13:50/13:54/13:58."""
    is_confab, reason = confab_detector.looks_like_confabulation(
        "A new tab is open, sir.",
        prior_messages=[_user_msg("Open a new tab on my browser.")],
    )
    assert is_confab, f"missed the incident: {reason!r}"
    assert "tab is open" in reason.lower()


def test_caught_done_new_tab_without_tool():
    is_confab, _ = confab_detector.looks_like_confabulation(
        "Done — new tab is open, sir.",
        prior_messages=[_user_msg("Open a new tab.")],
    )
    assert is_confab


def test_caught_chrome_is_open_without_tool():
    is_confab, _ = confab_detector.looks_like_confabulation(
        "Chrome is now open, sir.",
        prior_messages=[_user_msg("open chrome")],
    )
    assert is_confab


def test_caught_posted_tweet_without_tool():
    is_confab, _ = confab_detector.looks_like_confabulation(
        "I've posted that tweet, sir.",
        prior_messages=[_user_msg("post 'gm' on twitter")],
    )
    assert is_confab


def test_caught_screenshot_taken_without_tool():
    is_confab, _ = confab_detector.looks_like_confabulation(
        "Screenshot taken, sir.",
        prior_messages=[_user_msg("take a screenshot")],
    )
    assert is_confab


# ── NEGATIVE: detector must NOT fire — these are legit ────────────


def test_legit_tab_open_with_tool_evidence():
    """Same text as the confab, but immediate prior messages prove
    a tool actually fired. Must be saved, not dropped."""
    is_confab, _ = confab_detector.looks_like_confabulation(
        "A new tab is open, sir.",
        prior_messages=[
            _user_msg("Open a new tab"),
            _assistant_tool_call(name="ext_new_tab"),
            _tool_result("OK"),
        ],
    )
    assert not is_confab, "false positive — saved tool evidence ignored"


def test_legit_chrome_with_launch_app_evidence():
    is_confab, _ = confab_detector.looks_like_confabulation(
        "Chrome opened, sir.",
        prior_messages=[
            _user_msg("open chrome"),
            _assistant_tool_call(name="launch_app"),
            _tool_result("OK: launched 'google-chrome'"),
        ],
    )
    assert not is_confab


def test_legit_failure_explanation_with_open_keyword():
    """Assistant explaining it CAN'T open something — must not fire."""
    is_confab, _ = confab_detector.looks_like_confabulation(
        "I'm unable to open the browser at the moment, sir.",
        prior_messages=[_user_msg("open chrome")],
    )
    assert not is_confab


def test_legit_apology_with_done_keyword():
    is_confab, _ = confab_detector.looks_like_confabulation(
        "I haven't done that, sir — please clarify what you saw.",
        prior_messages=[],
    )
    assert not is_confab


def test_legit_neutral_chat_with_open_word():
    """Plain chat that happens to contain 'open' — no claim of action."""
    is_confab, _ = confab_detector.looks_like_confabulation(
        "Yes, sir, the design canvas is what's open in your view.",
        prior_messages=[_user_msg("what's on my screen?"),
                        _assistant_tool_call("screenshot"),
                        _tool_result("...")],
    )
    assert not is_confab


def test_legit_pure_banter():
    is_confab, _ = confab_detector.looks_like_confabulation(
        "Of course, sir.",
        prior_messages=[_user_msg("how are you")],
    )
    assert not is_confab


def test_legit_question_back_to_user():
    is_confab, _ = confab_detector.looks_like_confabulation(
        "What would you like to open, sir?",
        prior_messages=[_user_msg("open something")],
    )
    assert not is_confab


def test_legit_long_explanatory_response():
    """Long natural-language reply discussing past actions — must
    not fire just because 'opened' appears somewhere."""
    is_confab, _ = confab_detector.looks_like_confabulation(
        "Earlier you asked me to take a screenshot, and the screenshot you saw "
        "showed a design tool. I noticed you opened the layout tab a moment ago.",
        prior_messages=[_user_msg("recap"),
                        _assistant_tool_call("memory"),
                        _tool_result("...")],
    )
    assert not is_confab


def test_legit_complete_your_thought():
    """`complete` inside a clarifying question must NOT trigger.
    Live false positive 2026-05-03: JARVIS asked the user to repeat
    themselves with 'Could you please complete your thought?' and the
    detector dropped the turn — user got silence, JARVIS appeared
    broken. The success-claim regex only counts when followed by
    sentence-end punctuation or a known success noun."""
    cases = [
        "Could you please complete your thought?",
        "It seems like you started to say something, sir. Could you please complete your thought?",
        "I'd be happy to help you complete the project documentation.",
        "Once you finish reviewing it, let me know.",
        "I'm not done explaining yet — there's more.",
    ]
    for text in cases:
        is_confab, reason = confab_detector.looks_like_confabulation(
            text, prior_messages=[],
        )
        assert not is_confab, (
            f"false positive on legit phrasing: text={text!r} reason={reason!r}"
        )


def test_caught_done_with_punctuation_still_fires():
    """Sanity: tightening the regex must NOT regress real success
    claims. 'Done, sir.' / 'Task completed.' / 'Finished.' MUST still
    trigger when there's no tool evidence."""
    cases = [
        "Done, sir.",
        "Task completed.",
        "Finished.",
        "All complete, sir.",
    ]
    for text in cases:
        is_confab, _ = confab_detector.looks_like_confabulation(
            text, prior_messages=[],
        )
        assert is_confab, f"missed real confab: {text!r}"


# ── Disable via env ───────────────────────────────────────────────


def test_disabled_via_env(monkeypatch):
    """Setting JARVIS_CONFAB_DETECTOR=0 turns off detection.
    Critical so the user can disable instantly if false positives
    appear in production."""
    monkeypatch.setenv("JARVIS_CONFAB_DETECTOR", "0")
    is_confab, _ = confab_detector.looks_like_confabulation(
        "A new tab is open, sir.",
        prior_messages=[_user_msg("open a new tab")],
    )
    assert not is_confab


# ── Robustness: weird input shapes don't crash ────────────────────


def test_empty_text_no_crash():
    is_confab, _ = confab_detector.looks_like_confabulation("", prior_messages=[])
    assert not is_confab


def test_none_prior_messages():
    is_confab, _ = confab_detector.looks_like_confabulation(
        "A new tab is open, sir.", prior_messages=None,
    )
    assert is_confab  # no evidence → flagged


def test_dict_shaped_messages_supported():
    """LiveKit sometimes passes plain dicts. Detector must tolerate
    both dataclass-style objects and dicts."""
    prior = [
        {"role": "user", "content": ["open chrome"]},
        {"role": "assistant", "content": [], "tool_calls": [{"name": "launch_app"}]},
        {"role": "tool", "content": ["OK"]},
    ]
    is_confab, _ = confab_detector.looks_like_confabulation(
        "Chrome opened, sir.", prior_messages=prior,
    )
    assert not is_confab, "tool evidence in dict-shaped messages was missed"


def test_pydantic_style_with_tool_use_block():
    """Anthropic-style content blocks: list of {type: tool_use, ...}"""
    prior = [
        SimpleNamespace(role="user", content=["open chrome"]),
        SimpleNamespace(
            role="assistant",
            content=[SimpleNamespace(type="tool_use", name="launch_app")],
            tool_calls=None,
        ),
    ]
    is_confab, _ = confab_detector.looks_like_confabulation(
        "Chrome opened, sir.", prior_messages=prior,
    )
    assert not is_confab


# ── 2026-05-06 subagent-handoff false-positive fix ────────────────


def test_transfer_to_browser_handoff_counts_as_evidence(monkeypatch):
    """2026-05-06 turn 1110 (live-captured): browser subagent
    truthfully said 'I have opened a new tab' after firing
    ext_new_tab via the bridge. Bridge confirmed the tab was
    created. But the supervisor's session.history doesn't include
    the subagent's internal tool calls — only the supervisor's
    own `transfer_to_browser` tool call. Pre-fix the detector
    flagged the truthful statement as confab and dropped it from
    chat_ctx, leaving a hole that confused future turns.

    Original 2026-05-06 fix: treat `transfer_to_*` / `delegate`
    handoff calls as tool evidence. The handoff itself proves the
    subagent had a chance to do real work.

    2026-05-19 L2 update: the strict default no longer grants
    bare-handoff evidence — but the kill-switch
    `JARVIS_CONFAB_STRICT_DISABLED=1` preserves the legacy semantics
    this test was originally written to lock in. Locking in the
    legacy behavior under the kill-switch ensures the kill-switch
    actually works when the user needs it. The strict-default
    counterpart lives in `test_confab_detector_handoff_rule.py`."""
    monkeypatch.setenv("JARVIS_CONFAB_STRICT_DISABLED", "1")
    prior = [
        _user_msg("open a new tab"),
        SimpleNamespace(
            role="assistant",
            content=[],
            tool_calls=[SimpleNamespace(name="transfer_to_browser")],
        ),
    ]
    is_confab, reason = confab_detector.looks_like_confabulation(
        "I have opened a new tab on your browser. A new tab is now open.",
        prior_messages=prior,
    )
    assert not is_confab, (
        f"transfer_to_browser handoff should count as tool evidence "
        f"under permissive kill-switch; detector flagged: {reason!r}"
    )


def test_delegate_handoff_counts_as_evidence(monkeypatch):
    """`delegate(role, task)` is the new-style handoff. Same principle.

    2026-05-19 L2: also gated behind `JARVIS_CONFAB_STRICT_DISABLED=1`
    so it locks in the kill-switch semantics rather than the (now
    obsolete) default."""
    monkeypatch.setenv("JARVIS_CONFAB_STRICT_DISABLED", "1")
    prior = [
        _user_msg("post on twitter"),
        SimpleNamespace(
            role="assistant",
            content=[],
            tool_calls=[SimpleNamespace(name="delegate")],
        ),
    ]
    is_confab, _ = confab_detector.looks_like_confabulation(
        "Posted, sir.", prior_messages=prior,
    )
    assert not is_confab


def test_lookback_widened_to_10_messages():
    """Pre-fix: only last 3 messages were checked, so a tool call
    earlier in the window would be missed once a few text-only turns
    piled on top. Post-fix: 10 messages."""
    # 5 fillers, then tool call, then 4 fillers. Tool call is 5 back
    # from the message being checked — outside the old 3-msg window.
    prior = (
        [_user_msg(f"chitchat {i}") for i in range(5)]
        + [SimpleNamespace(
            role="assistant",
            content=[],
            tool_calls=[SimpleNamespace(name="ext_new_tab")],
        )]
        + [_assistant_text(f"chat {i}") for i in range(4)]
    )
    is_confab, _ = confab_detector.looks_like_confabulation(
        "I have opened a new tab on your browser.", prior_messages=prior,
    )
    assert not is_confab, "10-msg lookback should catch tool call 5-back"

    # Negative: tool call beyond 10-msg window → flagged as confab.
    prior_far = (
        [_user_msg(f"old {i}") for i in range(5)]
        + [SimpleNamespace(
            role="assistant",
            content=[],
            tool_calls=[SimpleNamespace(name="ext_new_tab")],
        )]
        + [_assistant_text(f"newer {i}") for i in range(11)]
    )
    is_confab_far, _ = confab_detector.looks_like_confabulation(
        "I have opened a new tab on your browser.", prior_messages=prior_far,
    )
    assert is_confab_far, (
        "tool call >10 messages back should not count — too stale"
    )


def test_function_call_item_counts_as_evidence():
    """LiveKit's ChatContext FunctionCall items expose `name` +
    `arguments` + `call_id` at the top level (no role/content list)."""
    prior = [
        _user_msg("take a screenshot"),
        SimpleNamespace(
            name="ext_screenshot",
            arguments="{}",
            call_id="call_abc123",
        ),
    ]
    is_confab, _ = confab_detector.looks_like_confabulation(
        "Screenshot taken, sir.", prior_messages=prior,
    )
    assert not is_confab


def test_function_call_output_item_counts_as_evidence():
    """FunctionCallOutput items expose `output` + `call_id`."""
    prior = [
        _user_msg("take a screenshot"),
        SimpleNamespace(
            output="screenshot saved to /tmp/x.png",
            call_id="call_abc123",
        ),
    ]
    is_confab, _ = confab_detector.looks_like_confabulation(
        "Screenshot taken, sir.", prior_messages=prior,
    )
    assert not is_confab


def test_text_only_assistant_doesnt_count_as_evidence():
    """Negative — random text-only assistant message ≠ tool evidence."""
    prior = [
        _user_msg("did you do anything"),
        SimpleNamespace(role="assistant", content=["I did, sir."]),
    ]
    is_confab, _ = confab_detector.looks_like_confabulation(
        "I have opened a new tab on your browser.", prior_messages=prior,
    )
    assert is_confab, "text-only assistant turn must not satisfy evidence"


# ── Track 3: Save-claim confab class ─────────────────────────────


def test_save_claim_without_memory_tool_flagged():
    """Track 3: 'I'll remember' without a memory tool call → confab."""
    from confab_detector import looks_like_confabulation
    prior_messages = [
        # Just a normal user/assistant exchange — no memory tool call
        type("M", (), {"role": "user", "content": "tell me about cats"})(),
        type("M", (), {"role": "assistant", "content": "cats are felines."})(),
    ]
    flagged, reason = looks_like_confabulation(
        "I'll remember that for next time.",
        prior_messages=prior_messages,
    )
    assert flagged
    assert "save" in reason.lower() or "memory" in reason.lower()


def test_save_claim_with_memory_tool_accepted():
    """Track 3: 'I'll remember' WITH a memory tool call → not confab."""
    from confab_detector import looks_like_confabulation

    class FCO:  # mimic FunctionCallOutput shape
        name = "memory"
        output = '{"success": true}'
        call_id = "x"

    prior_messages = [
        type("M", (), {"role": "user", "content": "remember I love sushi"})(),
        type("M", (), {"role": "assistant", "content": ""})(),
        FCO(),  # memory tool result in prior history
    ]
    flagged, reason = looks_like_confabulation(
        "I'll remember that for next time.",
        prior_messages=prior_messages,
    )
    assert not flagged


def test_save_claim_disabled_via_env(monkeypatch):
    """Track 3: JARVIS_CONFAB_SAVE_DISABLED=1 turns off save-claim class only.
    Tool-claim detection (existing) still fires."""
    monkeypatch.setenv("JARVIS_CONFAB_SAVE_DISABLED", "1")
    from confab_detector import looks_like_confabulation
    flagged, _ = looks_like_confabulation(
        "I'll remember that.",
        prior_messages=[],
    )
    assert not flagged


def test_no_save_claim_no_flag():
    """Track 3: assistant says nothing memory-shaped → no confab class fires."""
    from confab_detector import looks_like_confabulation
    flagged, _ = looks_like_confabulation(
        "The sky is blue.",
        prior_messages=[],
    )
    assert not flagged


def test_save_claim_with_assistant_tool_call_accepted():
    """Track 3: 'I'll remember' WITH an assistant tool_calls=[memory] entry
    in prior messages → not flagged. This is the LiveKit/OpenAI-style shape
    the framework produces when the supervisor fires the memory tool."""
    from confab_detector import looks_like_confabulation
    from types import SimpleNamespace

    prior_messages = [
        SimpleNamespace(role="user", content="remember I love sushi"),
        SimpleNamespace(
            role="assistant",
            content=[],
            tool_calls=[SimpleNamespace(name="memory", arguments={"action": "add"})],
        ),
    ]
    flagged, _ = looks_like_confabulation(
        "I'll remember that for next time.",
        prior_messages=prior_messages,
    )
    assert not flagged, "supervisor's tool_calls=[memory] is evidence — should not be flagged"


def test_save_claim_with_openai_style_dict_tool_calls_accepted():
    """Track 3: dict-shaped tool_calls (function.name) — OpenAI raw shape."""
    from confab_detector import looks_like_confabulation

    prior_messages = [
        {"role": "user", "content": "save my pref"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "memory", "arguments": "{}"}}],
        },
    ]
    flagged, _ = looks_like_confabulation(
        "I've saved that for you.",
        prior_messages=prior_messages,
    )
    assert not flagged
