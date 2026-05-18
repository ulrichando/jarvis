# Computer-Use Password-Check Fail-Open Hardening — Design Spec

**Date:** 2026-05-18
**Status:** Approved (option A from brainstorming session)
**Goal:** Eliminate the ~10-second-per-iteration overhead in JARVIS's computer-use loop caused by an unbounded Gemini Flash Lite call inside `is_password_field_visible`.

## 1. Motivation

The first live voice soak of `transfer_to_computer_use` (2026-05-18T14:17:36–14:19:07 UTC) ran to completion (`task_done` at step 9), but each loop iteration took ~10 seconds wall-clock vs the ~2 seconds the Anthropic Sonnet 4.6 call alone consumes. The user perceived "nothing happening" and barge-in-interrupted the loop mid-run.

Root cause: every iteration calls `is_password_field_visible(scaled, widgets)` in `tools/computer_safety.py`. On this Kali production box `python3-pyatspi` is not installed, so `enumerate_widgets()` returns `[]`, which triggers a Gemini Flash Lite fallback (`tools._vision_backend.describe_image`). Gemini is currently 503-throttled on high demand; each call falls back to Kimi after retries, eating 8–15 seconds. Multiplied across 9 iterations: ~90 seconds of safety-check overhead added to a task whose useful work is ~18 seconds.

Industry-standard reference impls (Anthropic `computer_use_demo/loop.py`, OpenAI Operator, Google Gemini 2.5 Computer Use) ship **zero** client-side per-iteration password detectors — they delegate to model training + server-side classifiers. JARVIS's client-side check is defense-in-depth, not the primary defense, so it must not be allowed to dominate latency.

## 2. Goals / Non-goals / Acceptance criteria

### Goals

- Per-iteration password-check wall-clock latency ≤ 1.5 seconds in the worst case (Gemini timeout → fail-open).
- Preserve the AT-SPI fast-path (microseconds) when widgets are available.
- Preserve defense-in-depth on the happy path: when Gemini is healthy, the check still fires and returns its real answer.
- Emit a structured WARN log on every fail-open with iteration + screenshot hash + cause, so the operator can audit ratios.
- Add per-action telemetry so fail-open rate is queryable from `~/.local/share/jarvis/turn_telemetry.db`.
- Provide `JARVIS_PASSWORD_CHECK_STRICT=1` opt-in for unattended environments where availability matters less than safety.

### Non-goals

- Caching the result by screenshot perceptual-hash (research called out as premature optimization; revisit if telemetry shows fail-open ratio > 5%).
- Replacing the check with the model's own self-reporting (Anthropic's training is the primary defense already; this layer stays as best-effort backup).
- Solving the parallel UX gaps (progress signal, cancel-event-on-barge-in, completion voicing). These are deferred to follow-up work; brainstorming explicitly scoped "speed first, UX after."
- Adding live-network tests for Gemini availability (covered by soak; CI mocks the check).

### Acceptance criteria

1. New function `check_password_visible(png, widgets) -> tuple[bool, str]` exists in `tools/computer_safety.py`. Returns `(visible, state)` where state ∈ `{fastpath_hit, fastpath_miss, slowpath, failopen}`.
2. The Gemini fallback is wrapped in `asyncio.wait_for(..., timeout=_GEMINI_TIMEOUT_S)` with `_GEMINI_TIMEOUT_S = 1.5`. On `asyncio.TimeoutError` or any exception from `_gemini_password_check`, the function returns `(strict_mode_bool, "failopen")` where `strict_mode_bool` is `True` when `JARVIS_PASSWORD_CHECK_STRICT=1` else `False`.
3. The legacy `is_password_field_visible` exists as a back-compat wrapper that calls `check_password_visible` and returns the bool. Existing callers continue to work without change.
4. `tools/computer_loop.py`'s call site uses `check_password_visible`, captures the state, and passes it to `_log_action` via a new `pwd_check_state` argument.
5. `pipeline/turn_telemetry.py` has an idempotent migration adding `pwd_check_state TEXT` to `computer_use_actions`. `log_computer_use_action` accepts a new optional `pwd_check_state` kwarg, defaults to `None`.
6. A structured WARN log fires on every fail-open with fields `handoff_id`, `iteration`, `screenshot_hash` (first 12 chars of md5), `widgets_count`, `elapsed_ms`, `cause`, `strict_mode`.
7. Five new tests in `tests/test_computer_safety.py` covering each state-tree branch + strict-mode behaviour. One new test in `tests/test_computer_use_telemetry.py` verifying the new column lands. Existing tests that called `is_password_field_visible` continue to pass via the back-compat wrapper.
8. With `pwd_check_state` populated, the operator can run `SELECT pwd_check_state, COUNT(*) FROM computer_use_actions GROUP BY pwd_check_state` and get a fast-path vs slow-path vs fail-open distribution.

## 3. Architecture

One file (`tools/computer_safety.py`) does ~50 lines of net change; one migration adds one column to `computer_use_actions`; one call site in `tools/computer_loop.py` threads the state through to `_log_action`. The function signature changes (`bool` → `tuple[bool, str]`), but a back-compat wrapper preserves the existing public API.

```
         iteration top of loop
              │
              ▼
   ┌──────────────────────────────────────────┐
   │  check_password_visible(png, widgets)    │
   │                                          │
   │  ┌─ widgets has password_text role ──┐   │   μs
   │  └─→ return (True, "fastpath_hit")   │   │
   │                                          │
   │  ┌─ widgets non-empty, no pwd_text ──┐   │   μs
   │  └─→ return (False, "fastpath_miss") │   │
   │                                          │
   │  ┌─ widgets empty ──────────────────┐    │
   │  │ asyncio.wait_for(                │    │
   │  │   _gemini_password_check(png),    │    │   ≤1.5s
   │  │   timeout=_GEMINI_TIMEOUT_S       │    │
   │  │ )                                 │    │
   │  │ on success → (result, "slowpath") │    │
   │  │ on timeout / exception →          │    │
   │  │   strict = STRICT env == "1"      │    │
   │  │   log_warn(structured)            │    │
   │  │   return (strict, "failopen")     │    │
   │  └──────────────────────────────────┘    │
   └──────────────────────────────────────────┘
              │
              ▼
   loop continues OR bails (when visible=True)
   pwd_check_state recorded in audit row
```

## 4. Components

### `tools/computer_safety.py` (modified)

Module-level constant:

```python
# Hard timeout for the Gemini fallback. Research-validated (≤1.5s
# preserves the 30-iter loop's wall-clock budget). Tighter values
# (e.g. 0.8s) save more wall-clock but increase fail-open ratio on
# slow Gemini days. 1.5s is the soak-tested compromise.
_GEMINI_TIMEOUT_S: float = float(
    os.environ.get("JARVIS_PASSWORD_CHECK_TIMEOUT_S", "1.5")
)
```

New function:

```python
async def check_password_visible(
    png: bytes, widgets: list[Widget]
) -> tuple[bool, str]:
    """Two-layer password-field detection with bounded latency.

    Layer 1 — AT-SPI: scan `widgets` for `role == "password_text"`.
        Instant (microseconds). Returns (True, "fastpath_hit") on
        match, (False, "fastpath_miss") when widgets non-empty but
        no password_text role.

    Layer 2 — Gemini Flash Lite (fallback): called only when widgets
        is empty (AT-SPI unavailable or canvas app). Wrapped in
        asyncio.wait_for(timeout=_GEMINI_TIMEOUT_S). On success
        returns (result, "slowpath"). On TimeoutError or any
        exception, fails OPEN with (False, "failopen") by default,
        or fails CLOSED with (True, "failopen") when
        JARVIS_PASSWORD_CHECK_STRICT=1.

    Rationale: Anthropic's reference computer_use_demo/loop.py has
    NO client-side password check — they trust model training +
    server-side classifier. JARVIS's check is defense-in-depth that
    must not dominate latency. Research synthesis 2026-05-18 and
    OS-Harm benchmark (arxiv 2506.14866) inform the layering.
    """
    # Layer 1
    for w in widgets:
        if w.role == "password_text":
            return True, "fastpath_hit"
    if widgets:
        return False, "fastpath_miss"

    # Layer 2 — bounded
    import time as _time
    started = _time.monotonic()
    try:
        result = await asyncio.wait_for(
            _gemini_password_check(png),
            timeout=_GEMINI_TIMEOUT_S,
        )
        return result, "slowpath"
    except (asyncio.TimeoutError, Exception) as e:
        elapsed_ms = int((_time.monotonic() - started) * 1000)
        strict = os.environ.get("JARVIS_PASSWORD_CHECK_STRICT") == "1"
        import hashlib
        shot_hash = hashlib.md5(png).hexdigest()[:12] if png else "empty"
        logger.warning(
            "[computer_safety] password check failed open",
            extra={
                "screenshot_hash": shot_hash,
                "widgets_count": len(widgets),
                "elapsed_ms": elapsed_ms,
                "cause": type(e).__name__,
                "strict_mode": strict,
            },
        )
        return strict, "failopen"
```

Back-compat wrapper preserved:

```python
async def is_password_field_visible(
    png: bytes, widgets: list[Widget]
) -> bool:
    """Back-compat wrapper. New callers should use check_password_visible
    to get the audit state. This wrapper drops the state and returns
    only the bool."""
    visible, _state = await check_password_visible(png, widgets)
    return visible
```

### `pipeline/turn_telemetry.py` (migration)

One additional online migration block, mirroring the existing pattern:

```python
# 2026-05-18 — pwd_check_state per-action audit. Lets the operator
# query fast-path/slow-path/fail-open ratios over time and alert when
# fail-open exceeds the soak-acceptable threshold. Spec:
# docs/superpowers/specs/2026-05-18-cua-password-check-failopen-design.md
cua_cols = {r[1] for r in conn.execute(
    "PRAGMA table_info(computer_use_actions)")}
if "pwd_check_state" not in cua_cols:
    try:
        conn.execute(
            "ALTER TABLE computer_use_actions ADD COLUMN pwd_check_state TEXT"
        )
    except sqlite3.OperationalError:
        pass
```

`log_computer_use_action()` gains `pwd_check_state: Optional[str] = None` kwarg, persisted to the new column.

### `tools/computer_loop.py` (call site)

Replace:

```python
pw_visible = await _is_password_visible(scaled, widgets)
if pw_visible:
    ...
    return LoopResult(reason="blocked", ...)
```

with:

```python
pw_visible, pw_state = await _check_password_visible(scaled, widgets)
if pw_visible:
    _log_action(
        ...,
        pwd_check_state=pw_state,
    )
    return LoopResult(reason="blocked", ...)
# Otherwise pw_state is threaded into the next _log_action call
# (the action-execution one) so every audit row has it.
```

Add a new seam: `_check_password_visible` mirroring the existing `_is_password_visible` seam, bound by `_bind_production_seams()` to `computer_safety.check_password_visible`. Leave `_is_password_visible` seam for back-compat (still used by existing tests).

## 5. Error handling

| Failure | Behaviour | Default | STRICT=1 |
|---|---|---|---|
| `asyncio.TimeoutError` | log WARN + return `(strict, "failopen")` | `(False, "failopen")` — loop proceeds | `(True, "failopen")` — loop bails as `reason="blocked"` |
| Gemini provider error | same as TimeoutError | same | same |
| `_gemini_password_check` returns non-bool | logged as exception path | same | same |
| AT-SPI raises | caller-side; covered by `enumerate_widgets` returning `[]` | n/a | n/a |

Structured log fields (JSON):

```json
{
  "level": "WARNING",
  "name": "jarvis.computer_safety",
  "message": "[computer_safety] password check failed open",
  "screenshot_hash": "<md5[:12]>",
  "widgets_count": 0,
  "elapsed_ms": <int>,
  "cause": "TimeoutError" | "<exception class>",
  "strict_mode": false
}
```

`elapsed_ms` lets the operator distinguish "timed out at 1500 ms" from "errored at 4 ms" — different remediation (Gemini slow vs Gemini misconfigured).

## 6. Testing

### New tests in `tests/test_computer_safety.py` (5)

| Test | Validates |
|---|---|
| `test_check_password_visible_fastpath_hit` | AT-SPI `password_text` widget → `(True, "fastpath_hit")` instant |
| `test_check_password_visible_fastpath_miss` | AT-SPI non-empty but no password_text → `(False, "fastpath_miss")` instant |
| `test_check_password_visible_slowpath_success` | AT-SPI empty + fast Gemini stub → `(result, "slowpath")` |
| `test_check_password_visible_failopen_on_timeout` | AT-SPI empty + slow Gemini stub + `_GEMINI_TIMEOUT_S=0.05` → `(False, "failopen")` |
| `test_check_password_visible_failopen_strict_mode` | Same as above + `JARVIS_PASSWORD_CHECK_STRICT=1` → `(True, "failopen")` |

### Test seam pattern

Tests monkey-patch `tools.computer_safety._gemini_password_check` to control Gemini behaviour. Timeout fixture uses `monkeypatch.setattr(computer_safety, "_GEMINI_TIMEOUT_S", 0.05)` so the timeout fires within ~50 ms and CI doesn't sleep.

### Back-compat tests

Existing tests `test_password_visible_via_atspi`, `test_password_not_visible_without_password_widget`, `test_password_visible_via_gemini_fallback` continue to call `is_password_field_visible` and pass via the back-compat wrapper. No edits needed.

### Loop test update

`tests/test_computer_loop.py::test_loop_blocks_on_password_field` currently mocks `_is_password_visible` to return `True`. Update to:
- New mock target: `_check_password_visible` returning `(True, "slowpath")`.
- Assertion stays the same (loop bails with `reason="blocked"`).
- Preserves the back-compat `_is_password_visible` seam for future tests.

### Telemetry test (`tests/test_computer_use_telemetry.py`)

```python
def test_migration_adds_pwd_check_state_column(tmp_path):
    db = tmp_path / "tele.db"
    init_db(db)
    cols = {r[1] for r in sqlite3.connect(db).execute(
        "PRAGMA table_info(computer_use_actions)")}
    assert "pwd_check_state" in cols
```

Plus extend `test_log_computer_use_action_persists_kwargs` (if it exists, else add one) to verify the new column persists.

### Coverage target

`tools/computer_safety.py` reaches ≥ 95 % line coverage after this change. Loop-test coverage unchanged.

## 7. Rollout

1. Land the spec → land the implementation behind no env flag (the change is purely additive on the safety-check return shape).
2. Voice-agent restart picks up new code AND the migration (`init_db` runs on every worker spawn; idempotent).
3. Soak: re-run `bin/jarvis-cua-soak all` — expect `open-app` to drop from ~50s to ~25s, individual steps to drop from ~10s to ~2.5s.
4. Operator monitors fail-open ratio via `sqlite3 ... GROUP BY pwd_check_state`. If sustained > 5 % on the prod box, escalate (likely means Gemini is down hard OR pyatspi never got installed).
5. Voice retest the original failed phrase ("Jarvis, look at my screen and find the open Chrome window") — expect normal-feeling latency.

Rollback: revert the commit; existing back-compat wrapper means old call sites keep working. The new column on `computer_use_actions` stays (additive, no harm).

## 8. Open questions

1. **`_GEMINI_TIMEOUT_S` value**: 1.5s is the research-recommended ceiling; 1.0s is the research-recommended target. Pick 1.5 for first soak; tighten to 1.0 once telemetry confirms it doesn't blow up fail-open ratio.
2. **Cache by pHash**: deferred per research. Revisit if `failopen > 5 %` sustained.
3. **Voice-UX gaps (progress / cancel / completion)**: explicitly out of scope. Separate brainstorm + spec if this work doesn't materially improve perceived "nothing's happening" UX.

## 9. References

- [Anthropic computer_use_demo/loop.py](https://github.com/anthropics/claude-quickstarts/blob/main/computer-use-demo/computer_use_demo/loop.py) — reference impl ships ZERO client-side preflight checks.
- [Anthropic Computer Use docs — server-side classifier](https://platform.claude.com/docs/en/docs/agents-and-tools/computer-use) — describes the prompt-injection classifier that runs on screenshots automatically.
- [Anthropic Constitutional Classifiers](https://www.anthropic.com/research/next-generation-constitutional-classifiers) — Sonnet 4.6 safety score ≈100 % vs 88 % (Opus 4) baseline.
- [OS-Harm benchmark (arxiv 2506.14866)](https://arxiv.org/html/2506.14866v1) — agents refuse "send password by email" 100 %, but leak via URL 40 % (the gap our defense-in-depth fills).
- [OpenAI Operator — server-side safety monitor](https://help.openai.com/en/articles/10421097) — pauses at login/payment surfaces via server-side classifier.
- [Google Gemini 2.5 Computer Use docs](https://ai.google.dev/gemini-api/docs/computer-use) — `safety_decision: require_confirmation` field in API response.
- [LiteLLM PR #17785](https://github.com/BerriAI/litellm/pull/17785) — fail-open made explicit opt-in default for one-of-several-layers guardrails.
- [NeMo Guardrails — Cisco AI Defense integration](https://docs.nvidia.com/nemo/guardrails/latest/configure-rails/guardrail-catalog/community/ai-defense.html) — documents both fail-open and fail-closed modes for layered checks.
- Local: `tools/computer_safety.py:128` — current `is_password_field_visible`.
- Local: `tools/computer_loop.py:95` — current per-iteration call site.
- Local: prior CUA spec [`2026-05-18-jarvis-computer-use-parity-design.md`](./2026-05-18-jarvis-computer-use-parity-design.md) — establishes the broader loop architecture this hardens.
