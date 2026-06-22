# JARVIS Computer-Use Parity — Design Spec

**Date:** 2026-05-18
**Status:** ⚠️ ARCHITECTURE SUPERSEDED (2026-05-20 rebuild). The `HandoffSubagent` /
`transfer_to_computer_use` design + separate `subagents/`/`tools/computer_*` file map
+ native `computer_20251124` below no longer match the running system. Computer use is
now a **single direct registry tool** (`tools/computer_use.py`) with a custom **SOM
element-mode** schema, multi-provider via `pipeline/cu_adapters/`. This spec is retained
for its loop-shape + safety reasoning. **For how CU actually works today, read
[docs/runbook/computer-use.md](../../runbook/computer-use.md).**
**Status (original):** Approved (option B of the brainstorming session)
**Goal:** Match the canonical 2026 computer-use loop (Anthropic Computer Use / OpenAI Operator / Gemini 2.5 Computer Use) so JARVIS can drive arbitrary GUI tasks on Ulrich's Linux desktop end-to-end.
**Supersedes:** [2026-04-28-desktop-computer-use-design.md](./2026-04-28-desktop-computer-use-design.md). That earlier design used a hybrid pattern (Gemini Live continuous vision stream → Groq/DeepSeek orchestrator → xdotool). The 2026 industry consensus converged on a different shape — *the vision-capable model owns the loop* — and Anthropic, OpenAI, and Google all standardized on the same tool surface. This spec replaces the prior approach with the canonical pattern.

## 1. Motivation

JARVIS today can do almost anything that's reducible to one of: (a) a shell command via `bash`, (b) a known DOM action via the `browser` subagent, (c) a known X11 input sequence via `desktop`. It **cannot** drive an unfamiliar GUI app, complete a login flow with CAPTCHA/MFA, follow a system dialog with sudo prompts, or execute a "switch playlist + mute Discord + open yesterday's doc" multi-app sequence. The blocker is the missing vision-plan-act loop — JARVIS's `screenshot()` returns a text description from Gemini Flash Lite, which is useful for reading but not for grounding a click sequence.

The 2026 industry standard for this gap is the Anthropic `computer_20251124` tool surface: model receives a screenshot, emits a tool call (`screenshot`, `left_click`, `type`, `key`, `scroll`, `drag`, `wait`), the harness executes, captures a new screenshot, sends back, repeats until the model emits `task_done`. OpenAI's CUA and Google's Gemini 2.5 Computer Use use the same shape with cosmetic differences (coordinate space, safety hook placement).

For JARVIS specifically, Anthropic is the right pick because (a) Sonnet 4.6 is already in the supervisor cascade with a working `_strict_tool_schema=False` integration, (b) Anthropic publishes a reference Linux harness using the exact stack JARVIS already has (`xdotool`, `scrot`, `ImageMagick`), and (c) Gemini's 1000×1000 normalized grid would be a coordinate-system trap during the integration. Provider choice is documented further in §6.

## 2. Goals / Non-goals / Acceptance criteria

### Goals

- Demonstrable computer-use parity covering all four task classes from the brainstorming:
  - Driving an unfamiliar GUI app (e.g., Kdenlive).
  - Completing a login flow (sign-in with 2FA).
  - OS / system task (printer setup, USB mount).
  - Mixed casual desktop (playlist switch, app focus).
- Tool surface matches Anthropic's `computer_20251124` verbatim so future Anthropic SDK updates land cleanly.
- Audit trail of every action + screenshot to a SQLite table + a screenshot directory with 7-day rotation.
- Default OFF behind `JARVIS_SUBAGENT_COMPUTER_USE=1` until soak telemetry justifies enabling.
- Graceful failure: every bail produces a structured `LoopResult` and a final telemetry row.

### Non-goals

- Wayland support — Kali runs X11; document a `tools/computer_backend.py` swap point for later.
- Multi-provider routing (OpenAI CUA, Gemini Computer Use) — single Anthropic backend in v1; portability is a phase-2 concern.
- Xvfb / Xephyr sandbox mode (was option C in brainstorming) — high cost, low ROI for single-user desktop.
- Multi-monitor as first-class — pin to primary screen, expose `JARVIS_COMPUTER_USE_DISPLAY=N` for the rare case.
- Asynchronous task continuation across worker restarts — loop state is in-memory; restart = task lost.

### Acceptance criteria

The implementation is "done" when:

1. `bin/jarvis-cua-soak open-app` completes successfully — Sonnet opens the Files manager in ≤8 steps and ≤$0.15.
2. `bin/jarvis-cua-soak click-button` completes — clicks a labeled button on a local test page in ≤4 steps and ≤$0.08.
3. `bin/jarvis-cua-soak type-into-field` completes — types into an input on the test page in ≤5 steps and ≤$0.10.
4. All 6 unit-test files in `tests/test_computer_*.py` pass with ≥90 % coverage of the new modules.
5. A destructive action (test fixture: synthetic "Delete" button) triggers the voice-confirm gate; declining the confirmation skips the action and logs `notes="user declined"`.
6. A password-field-visible screenshot fixture triggers an immediate hard-stop with `reason="blocked"` and a clean bail message.
7. The audit table `computer_use_actions` populates one row per action across a full soak run; screenshots are dumped to `~/.local/share/jarvis/computer_use/screenshots/<handoff_id>/`.
8. With `JARVIS_SUBAGENT_COMPUTER_USE=0` (the default) the subagent is NOT registered and `transfer_to_computer_use` is not in the supervisor's tool list.

## 3. Architecture

```
                  ┌─────────────────────────────────────┐
                  │           SUPERVISOR (LLM)          │
                  │   gpt-5-mini / sonnet 4.6 / etc.    │
                  └───────────────┬─────────────────────┘
                                  │
                  transfer_to_computer_use(task)
                                  ▼
        ┌───────────────────────────────────────────────────┐
        │      computer_use SUBAGENT (HandoffSubagent)      │
        │                                                    │
        │   ┌────────────────────────────────────────────┐  │
        │   │ Anthropic client (beta header              │  │
        │   │ "computer-use-2025-11-24")                 │  │
        │   │                                            │  │
        │   │   Sonnet 4.6 ─── auto-escalate ──▶ Opus 4.7│  │
        │   │   (default)     (3 no-progress steps)      │  │
        │   └────────────────────────────────────────────┘  │
        │                       │                            │
        │     iterate (cap 30)  │                            │
        │                       ▼                            │
        │   ┌────────────────────────────────────────────┐  │
        │   │  SEE          → tools/computer_backend     │  │
        │   │  GROUND       → tools/computer_atspi       │  │
        │   │  GATE         → tools/computer_safety      │  │
        │   │  ACT          → xdotool (mouse/keyboard)   │  │
        │   │  LOG          → computer_use_actions table │  │
        │   └────────────────────────────────────────────┘  │
        │                                                    │
        │   task_done(summary) → back to supervisor          │
        └───────────────────────────────────────────────────┘
```

Key architectural decisions:

- **The subagent owns the loop, not LiveKit.** LiveKit's `LLM` adapter is one-turn-out; computer-use is many-turn-in. The subagent calls `anthropic.beta.messages.create(...)` directly inside its own `while` loop, NOT through `BreakeredGroqLLM`/dispatcher. Same "tool-less" shape as the existing `screen_share` subagent — `tools_required=False` on the spec.
- **Sonnet 4.6 default, Opus 4.7 on no-progress escalation.** Sonnet is faster and 72.5 % on OSWorld is plenty for routine GUI work; Opus is ~5 pp more accurate but ~3× cost. Auto-escalation captures the gain without paying always.
- **No FallbackAdapter cascade.** Groq / DeepSeek / Kimi don't speak `computer_20251124`. The subagent is Anthropic-exclusive; if Anthropic is down, the subagent bails to the supervisor with a clear message.
- **Tool surface = Anthropic's `computer_20251124` verbatim** + a `task_done` for the existing subagent gate. Schema-less — the model already knows the contract.
- **Gated `JARVIS_SUBAGENT_COMPUTER_USE=1`, default OFF.** Same pattern as the seven delegated subagents that are off by default.

## 4. Components

Six new units, plus migrations to two existing tables.

### New files

```
src/voice-agent/
├── subagents/
│   └── computer_use.py             ← HandoffSubagent + register
└── tools/
    ├── computer_backend.py         ← see + act primitives (xdotool/mss)
    ├── computer_atspi.py           ← ground primitive (AT-SPI widgets)
    ├── computer_safety.py          ← password block + destructive-verb gate
    └── computer_loop.py            ← iterate-until-done loop + escalation
```

### Module responsibilities and interfaces

#### `tools/computer_backend.py`

The see + act primitives. Backend-swappable for future Wayland support.

```python
async def take_screenshot() -> bytes
    # Returns PNG bytes. Uses mss (~10ms); falls back to scrot on import error.

def scale_for_model(png: bytes) -> tuple[bytes, float, float]
    # Returns (scaled_png, scale_x, scale_y). Targets one of:
    #   XGA   1024 x 768
    #   WXGA  1280 x 800
    #   FWXGA 1366 x 768
    # (Anthropic's MAX_SCALING_TARGETS — pick the one whose aspect
    # ratio is closest to the source.) scale_x/scale_y are the factors
    # to apply when converting model-emitted coordinates back to native.

async def click(x: int, y: int, button: str = "left", modifiers: list[str] = []) -> None
async def double_click(x: int, y: int) -> None
async def right_click(x: int, y: int) -> None
async def drag(start: tuple[int,int], end: tuple[int,int]) -> None
async def mouse_move(x: int, y: int) -> None
async def type_text(text: str, delay_ms: int = 12) -> None
async def key_combo(combo: str) -> None             # e.g. "ctrl+s"
async def scroll(x: int, y: int, direction: str, amount: int) -> None
```

Implementation: `mss` for screenshot, `xdotool` shell-out for input (matches Anthropic reference). All input ops raise `BackendError` on failure (non-zero exit, missing binary). Coordinate scaling logic is a verbatim port of Anthropic's `MAX_SCALING_TARGETS` from `computer_use_demo/tools/computer.py`.

#### `tools/computer_atspi.py`

The ground primitive. AT-SPI widget enumeration as side-channel.

```python
@dataclass
class Widget:
    role: str          # "push_button" | "text" | "menu_item" | "password_text" | ...
    bounds: tuple[int, int, int, int]   # (x, y, w, h) in native screen coords
    text: str          # label / value / name
    enabled: bool
    active: bool       # has focus

def enumerate_widgets(window_title_pattern: str | None = None) -> list[Widget]
    # Returns visible interactive widgets from the active window (or any
    # window matching the pattern). Returns [] when AT-SPI is sparse
    # (canvas apps, Electron without a11y, games). Cached for 100ms
    # within a single iteration step.
```

Dependencies: `gir1.2-atspi-2.0` (apt) + `python-atspi` (or system `python3-pyatspi`). Failure-mode: any pyatspi exception → return `[]` and log debug — caller falls back to bare vision.

#### `tools/computer_safety.py`

Defense layer.

```python
async def is_password_field_visible(
    screenshot: bytes,
    widgets: list[Widget],
) -> bool
    # Layer 1: AT-SPI — any widget with role == "password_text".
    # Layer 2 (fallback): Gemini Flash Lite — "is there a focused password
    # input on this screen?". Used only when AT-SPI returned no widgets.

def parse_destructive_intent(
    action: dict,
    widgets: list[Widget],
) -> str | None
    # Returns a confirmation phrase or None.
    # Match strategy:
    #   1. action is `left_click` AND a widget at that coordinate has
    #      text matching the destructive-verb vocabulary.
    #   2. action is `type` AND the typed text matches a destructive
    #      shell command (rm, dd, mkfs, ...).
    # Vocabulary: delete, send, submit, overwrite, format, remove,
    # erase, discard, publish, post, drop, wipe.
    # Phrase shape: "About to click 'Delete' on file 'budget.xlsx' — proceed?"
```

Both functions are pure — no I/O outside their inputs. Easy to unit-test with synthetic widgets and mock Gemini.

#### `tools/computer_loop.py`

The iterate-until-done driver.

```python
@dataclass
class LoopResult:
    ok: bool
    summary: str
    steps: int
    cost_usd: float
    reason: str       # "completed" | "budget" | "max_iters" | "blocked" | "bailed" | "interrupted"
    handoff_id: str

async def run(
    task: str,
    *,
    anthropic_client,
    safety_confirm_cb: Callable[[str], Awaitable[bool]],
    cancel_event: asyncio.Event,
    max_iters: int = 30,
    budget_usd: float = 0.50,
    wall_timeout_s: float = 180.0,
    model_primary: str = "claude-sonnet-4-6",
    model_escalation: str = "claude-opus-4-7",
    no_progress_escalation_after: int = 3,
) -> LoopResult
```

Owns: screenshot history (last 2 frames for no-progress perceptual-hash detection), cost accumulator, iteration count, model selection, audit-log writes. The `safety_confirm_cb` voices a confirmation phrase through the supervisor's voice channel and awaits a yes/no on the user's next utterance. The `cancel_event` is set when the user barges in.

#### `subagents/computer_use.py`

The supervisor-facing handoff.

```python
def register_computer_use() -> None
    # Registers HandoffSubagent("computer_use", "transfer_to_computer_use", ...)
    # with:
    #   instructions = short framing (see §5 for the LLM-side rules)
    #   tool_factory = lambda: [task_done]   # computer_20251124 is passed
    #                                          directly to Anthropic client,
    #                                          not through LiveKit's tools
    #   tools_required = False                # the subagent gate doesn't apply
    #   pre_transfer = _ensure_x11_session
    #   enabled = os.environ.get("JARVIS_SUBAGENT_COMPUTER_USE", "0") == "1"
```

`_ensure_x11_session` probes `WAYLAND_DISPLAY` env + runs `xdpyinfo`; aborts with a clear message on Wayland.

### Schema migrations

`pipeline/turn_telemetry.py` — two new columns on `turns`:

```sql
ALTER TABLE turns ADD COLUMN computer_use_steps INTEGER;
ALTER TABLE turns ADD COLUMN computer_use_cost_usd REAL;
```

Plus a new audit table:

```sql
CREATE TABLE IF NOT EXISTS computer_use_actions (
  id INTEGER PRIMARY KEY,
  ts_utc TEXT NOT NULL,
  handoff_id TEXT NOT NULL,
  step INTEGER NOT NULL,
  model_used TEXT,                  -- "sonnet-4-6" or "opus-4-7"
  action TEXT NOT NULL,             -- "left_click" | "type" | "screenshot" | "bail" | ...
  params_json TEXT,
  success INTEGER NOT NULL,
  screenshot_path TEXT,
  notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_cua_handoff ON computer_use_actions(handoff_id);
CREATE INDEX IF NOT EXISTS idx_cua_ts ON computer_use_actions(ts_utc);
```

Screenshots dumped to `~/.local/share/jarvis/computer_use/screenshots/<handoff_id>/<step>.png`. 7-day rotation extends the existing `jarvis-log-rotate.timer` script.

### Dependencies added

| Package | Purpose | Install |
|---|---|---|
| `anthropic>=0.40` | already in voice-agent venv | — |
| `mss>=10.0` | fast screenshot (~10 ms) | `pip install` |
| `gir1.2-atspi-2.0` | AT-SPI introspection bindings | `sudo apt install` |
| `python-atspi` (or system `python3-pyatspi`) | Python AT-SPI client | apt or pip |
| `xdotool`, `imagemagick`, `scrot` | already on system | — |
| `perceptual-hash` (any small impl, or stdlib `hashlib` on downsampled image) | no-progress detection | small inline helper, no new dep |

## 5. Data flow

### Outer flow — handoff lifecycle

```
USER VOICE: "open Kdenlive, import this video, add a fade"
   ▼
SUPERVISOR LLM → transfer_to_computer_use(request)
   ▼
PRE_TRANSFER HOOK
   ├─ probe X11 via xdpyinfo
   ├─ generate handoff_id = uuid4()
   └─ ack "Right away." (voiced by framework)
   ▼
SUBAGENT on_enter
   ├─ build first user-msg: task + screenshot + AT-SPI widget list
   └─ call computer_loop.run(task, ...)
   ▼
LOOP RETURNS LoopResult
   ├─ subagent voices task_done(summary)
   └─ supervisor takes over, voices summary to user
```

### Middle flow — one loop iteration

```
For iteration in 1 .. max_iters:
    1. SEE
       png      = take_screenshot()
       scaled,  = scale_for_model(png)
       widgets  = enumerate_widgets()

    2. SAFETY PRE-CHECK
       if is_password_field_visible(scaled, widgets):
           return LoopResult(blocked, "password field — needs you")

    3. PLAN
       msg = build_user_message(scaled, widgets, history)
       response = anthropic.beta.messages.create(
           model=active_model,
           tools=[computer_20251124],
           extra_headers={"anthropic-beta": "computer-use-2025-11-24"},
       )
       cost_usd += compute_cost(response.usage, active_model)
       if cost_usd > budget_usd:
           return LoopResult(budget, ...)

    4. PARSE + GATE
       action = response.content[-1]
       if action.name == "task_done":
           return LoopResult(completed, summary=action.input.summary)
       confirm = parse_destructive_intent(action, widgets)
       if confirm is not None:
           ok = await safety_confirm_cb(confirm)
           if not ok:
               log(action, success=0, notes="user declined")
               continue          # replan on next iter

    5. ACT
       native_x = action.coordinate[0] * scale_x
       native_y = action.coordinate[1] * scale_y
       try:
           await backend.<action>(native_x, native_y, ...)
       except BackendError as e:
           log(action, success=0, notes=str(e))
           continue

    6. LOG
       screenshot_path = dump_to_disk(take_screenshot(), handoff_id, step)
       insert_audit_row(action, success=1, screenshot_path)

    7. NO-PROGRESS CHECK
       if last_3_hashes_match and last_3_actions_same:
           if active_model == model_primary:
               active_model = model_escalation
               logger.info("[computer_use] escalating Sonnet → Opus 4.7")
           else:
               return LoopResult(blocked, "stuck on same action")
```

### Data shapes at the boundaries

Anthropic API call payload:
```python
client.beta.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    tools=[{
        "type": "computer_20251124",
        "name": "computer",
        "display_width_px": 1280,
        "display_height_px": 800,
        "display_number": 1,
    }],
    messages=[...prior turns..., new_user_msg_with_screenshot],
    extra_headers={"anthropic-beta": "computer-use-2025-11-24"},
)
```

Model emits a `tool_use` block:
```python
{"type": "tool_use", "id": "toolu_xyz", "name": "computer",
 "input": {"action": "left_click", "coordinate": [340, 220]}}
```

We return a `tool_result`:
```python
{"role": "user", "content": [{
    "type": "tool_result",
    "tool_use_id": "toolu_xyz",
    "content": [
        {"type": "text", "text": "OK"},
        {"type": "image", "source": {...new screenshot...}},
    ],
}]}
```

### Safety-confirm side-channel

When `parse_destructive_intent` returns a phrase, the subagent calls `safety_confirm_cb(phrase)`. Wiring:

1. The callback is bound at subagent construction to a function on the supervisor's session.
2. The callback pushes the phrase through TTS via the supervisor's voice channel.
3. It sets `session._jarvis_waiting_for_cua_confirm = True` and an `asyncio.Future`.
4. The next user turn that arrives, if `_waiting_for_cua_confirm` is set, the supervisor's `on_user_turn_completed` resolves the Future with yes/no parsed from the transcript.
5. Returns to the subagent loop within 30 s (timeout → False = treat as "no").

The subagent never directly drives TTS. The supervisor's voice channel remains canonical.

## 6. Error handling

Six failure layers. Default posture: **fail safe + fail loud** — bail to supervisor with a structured summary rather than silently give up.

### A. Backend errors (xdotool / mss / AT-SPI)

| Failure | Detection | Recovery |
|---|---|---|
| `mss` import fails | `ImportError` on module load | Fall back to `scrot -p` (slower but ships with most distros) |
| `xdotool` returns non-zero | `proc.returncode != 0` | Wrap as `BackendError`; loop logs `success=0`, replans on next iter (one auto-retry, then escalate) |
| AT-SPI D-Bus unreachable | `pyatspi` raises | Return `[]` silently; loop falls back to bare vision |
| Screenshot saved but empty | byte-length check | `BackendError`; retry once with scrot, then bail |

### B. Anthropic API errors

| Failure | Detection | Recovery |
|---|---|---|
| 429 rate limit | exception class | Exponential backoff up to 30 s, max 3 retries, then bail |
| 401 auth | exception class | Immediate bail; surface `"Anthropic key invalid — check ANTHROPIC_API_KEY"` |
| 400 beta header rejected | message content | Bail with `"computer-use beta not available on this model"` |
| Network timeout | `httpx.ReadTimeout` | One retry, then bail |
| `usage` field missing | response.usage is None | Cost cap can't fire — log warning, conservative cap remaining iterations |

The Anthropic client does NOT go through `BreakeredGroqLLM` / FallbackAdapter. Direct `anthropic.AsyncAnthropic` with its own retry config.

### C. Model behavior

| Failure | Detection | Recovery |
|---|---|---|
| No progress 3 iterations | last 3 screenshots match (perceptual hash) AND last 3 actions same coord ±5 px | Sonnet → Opus 4.7 escalation; if Opus also stuck for 3 more iters → `LoopResult(blocked, "stuck on same action")` |
| Hallucinated coordinates | x or y outside display | Skip + synthesize `tool_result: "ERROR: coordinate out of bounds"` so model retries |
| `max_iters` (30) | counter | Bail with reason `"max_iters"` |
| Budget cap ($0.50) | post-call check | Bail with reason `"budget"` |
| Empty `task_done` summary | content check | Force-bail with reason `"empty_summary"` |

### D. Safety layer

| Trigger | Action |
|---|---|
| Password field visible | Hard-stop; bail with `"needs password — handing back to supervisor"`. Never proceed even if previously confirmed. |
| Destructive verb detected | `safety_confirm_cb` voices the phrase, waits ≤ 30 s for yes/no. Default-deny on timeout or ambiguous. |
| 2FA/banking heuristic (window title matches `*bank*` / `*verify*` / `*2fa*` / `*OTP*` / `*authenticator*`) | Same as password — bail; never proceed. |
| Action targets a window outside inferred task scope | Voice-confirm before continuing; default-deny |

### E. System layer

| Failure | Detection | Recovery |
|---|---|---|
| Wayland session | `WAYLAND_DISPLAY` set OR `xdpyinfo` fails | Pre_transfer aborts before handoff |
| Multi-monitor mismatch | `xrandr` shows ≥ 2 outputs, model clicked beyond primary | Pin to primary; env override `JARVIS_COMPUTER_USE_DISPLAY=N` |
| App crashes mid-task | `wmctrl -l` shows target window gone | Bail with summary of what was done |
| Screenshot takes > 5 s | wall-time | Backend error; one retry then bail |

### F. Voice / session layer

| Failure | Detection | Recovery |
|---|---|---|
| User barge-in mid-loop | LiveKit interrupt → `cancel_event.set()` | Loop checks event between iterations; bail with `reason="interrupted"`, summary of progress so far |
| Worker restart mid-task | process dies | Task lost; handoff_id audit row has last screenshot, so user can query "what were you doing" from telemetry. No automatic resume. |
| Loop hangs in network code | wall-clock watchdog | `JARVIS_COMPUTER_USE_WALL_TIMEOUT_S=180`; force-bail on expiry |

### Audit on every bail

Whatever the reason, every bail writes a final row to `computer_use_actions` with `action="bail"`, `params_json` containing the LoopResult, and a final screenshot. Lets you reconstruct what went wrong after the fact.

## 7. LLM-side rules (subagent instructions)

The subagent's system prompt is short and rule-anchored. The LLM is told what tools it has, what to call when done, and the safety stops. The actual contract for the `computer` tool is already in the model's training — we don't repeat it.

```
You are JARVIS's computer-use subagent. The supervisor has handed you a
task that requires direct GUI interaction on Ulrich's Linux desktop.

Your tools:
- `computer` — Anthropic computer-use tool (you know the contract).
- `task_done(summary)` — call after the work is complete, voicing one
  short English sentence describing what you accomplished.

Rules:
1. **Observe first.** Take a screenshot before your first action; don't
   guess what's on screen.
2. **Iterate.** After each action, screenshot to verify the action
   produced the change you expected.
3. **Stop on sensitive screens.** Password fields, 2FA prompts, banking
   sites, system password dialogs → call `task_done` with summary
   "needs password / 2FA / sensitive screen — handing back to supervisor".
   Do NOT type credentials.
4. **Ask before destruction.** For Delete, Send, Submit, Format,
   Overwrite, Remove, Erase, Discard, Publish, Post, Drop, Wipe —
   the harness will voice a confirmation prompt. If declined you must
   skip the action; do not retry it without re-asking.
5. **Be efficient.** Max 30 iterations and $0.50 budget per task. If
   you repeat the same action 3 times without progress, the harness
   will escalate the model; if escalation also fails it will bail.
6. **Voice is the user's mic.** Don't narrate. The supervisor speaks;
   you only emit `task_done` when finished.
```

The harness appends the per-handoff context (current screen description, AT-SPI widget list) to the user-side message of each iteration. The subagent's `instructions_factory` returns this prompt (no dynamic content per handoff; static for now).

## 8. Testing

Strategy: mock every I/O boundary at the test layer; real APIs + real desktop only in manual soak.

### Test files

```
src/voice-agent/tests/
├── test_computer_backend.py
├── test_computer_atspi.py
├── test_computer_safety.py
├── test_computer_loop.py
├── test_computer_use_subagent.py
└── fixtures/computer_use/
    ├── screenshot_kdenlive_start.png
    ├── screenshot_password_visible.png
    ├── screenshot_unchanged_A.png
    └── screenshot_unchanged_B.png
```

| File | Coverage | Mocks |
|---|---|---|
| `test_computer_backend.py` | scaling math, coordinate round-trip, xdotool argv construction, `BackendError` on non-zero exit, scrot fallback when mss raises | `asyncio.create_subprocess_exec`, `mss.mss()` |
| `test_computer_atspi.py` | flat enumeration → tree walk, role filtering, `[]` on D-Bus failure, 100 ms cache invalidation | `pyatspi.Registry` |
| `test_computer_safety.py` | every banned verb detected; non-destructive verbs ignored ("read", "preview"); `is_password_field_visible` with synthetic AT-SPI widgets + Gemini fallback; 2FA window-title heuristic | Gemini Flash Lite stub |
| `test_computer_loop.py` | happy path completes; budget breach bails; max_iters bails; no-progress escalates Sonnet → Opus exactly once; off-screen coords are skipped + retried; password-visible mid-loop hard-stops; destructive path calls `safety_confirm_cb`; wall-clock timeout fires; cancel event bails cleanly | scripted `anthropic.AsyncAnthropic` fixture (returns list of tool_use responses), backend primitives, `safety_confirm_cb` callable |
| `test_computer_use_subagent.py` | `register_computer_use` honors `JARVIS_SUBAGENT_COMPUTER_USE` env gate, default OFF; `pre_transfer` aborts on Wayland; spec has `tools_required=False`; `safety_confirm_cb` round-trips through a fake session | session shim, `xdpyinfo` subprocess |

### The Anthropic-mock pattern (test_computer_loop.py)

```python
@pytest.fixture
def scripted_anthropic(monkeypatch):
    """Replay a list of (action_dict, usage_dict) tuples as Anthropic
    responses. Tests assert against backend calls, not the real API."""
    script, calls = [], []
    async def fake_create(**kw):
        calls.append(kw)
        action, usage = script[len(calls) - 1]
        return AnthropicResponse(content=[ToolUse(action)], usage=usage)
    monkeypatch.setattr("tools.computer_loop._anthropic_call", fake_create)
    return script, calls
```

### Coverage targets

- Unit coverage ≥ 90 % on the five new modules.
- At least one test per `LoopResult.reason` value (`completed | budget | max_iters | blocked | bailed | interrupted`).
- One safety-regression test per past-failure pattern (mirrors the dated-incident style of `prompts/supervisor.md`).

### Manual soak (NOT in CI) — `bin/jarvis-cua-soak`

Three scenarios, each writing result + cost + step count to `~/.local/share/jarvis/cua-soak-runs.jsonl`:

1. `open-app` — task "open the Files app." Expect ≤ 8 steps, ≤ $0.15.
2. `click-button` — preconditions: test page at `file:///tmp/cua-soak.html` with one button labeled "Confirm." Task "click Confirm." Expect ≤ 4 steps, ≤ $0.08.
3. `type-into-field` — same test page with an input. Task "type 'hello world' into the input." Expect ≤ 5 steps, ≤ $0.10.

### Not tested (intentionally)

- Real Anthropic API calls in CI (flaky, costs money — replaced by scripted mock).
- Real xdotool against real X server in CI (needs a display — covered by soak).
- Multi-monitor + Wayland edge cases (manual verification before any release touching them).

## 9. Rollout

1. Land the subagent + backend + safety + loop + tests behind `JARVIS_SUBAGENT_COMPUTER_USE=0` (default).
2. Voice-agent restart picks up the new code.
3. Manually flip `JARVIS_SUBAGENT_COMPUTER_USE=1` in the systemd unit's Environment line.
4. Run `bin/jarvis-cua-soak all` and confirm three scenarios complete inside their cost/step budgets.
5. After ~50 live turns without incident, flip the default to `enabled=True` and stop requiring the env var.
6. Add `transfer_to_computer_use` to the supervisor prompt's routing table.

Rollback: set `JARVIS_SUBAGENT_COMPUTER_USE=0` and restart. Subagent unregisters; the supervisor loses the `transfer_to_computer_use` tool; existing telemetry stays.

## 10. Open questions

These are not blockers — flagged here for the implementation plan to resolve.

1. **Cost calibration.** The $0.50/handoff budget is a guess based on the research's "30 steps × ~$0.015 = $0.45" math. Real-world costs may be 2-3× higher when factoring image input tokens (a screenshot at WXGA is ~600 tokens). First soak run will set the real number.
2. **AT-SPI on Kali.** `gir1.2-atspi-2.0` is installable but actual D-Bus session presence depends on the active window manager (XFCE / KDE / GNOME differ). Verify on this box before relying on the AT-SPI grounding rung; gracefully no-op if the bus is absent.
3. **Voice-confirm interaction with the supervisor turn router.** The `safety_confirm_cb` flow assumes the supervisor's `on_user_turn_completed` can route a yes/no into the subagent's awaiting Future. This requires a small hook in `jarvis_agent.py::on_user_turn_completed` — the implementation plan should specify where exactly.
4. **Subagent's instructions_factory or static instructions?** The current design uses static instructions (no per-handoff dynamism). If we later want to disclose escalation state ("currently running on Opus 4.7"), revisit and convert to `instructions_factory` like the browser subagent.

## 11. References

External:
- [Anthropic Computer Use tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool) — `computer_20251124` action vocabulary, beta header, model matrix
- [Anthropic computer-use-demo](https://github.com/anthropics/anthropic-quickstarts/tree/main/computer-use-demo) — reference Dockerfile, `tools/computer.py`, MAX_SCALING_TARGETS
- [OpenAI Computer Use guide](https://developers.openai.com/api/docs/guides/tools-computer-use) — CUA shape for comparison
- [Gemini 2.5 Computer Use docs](https://ai.google.dev/gemini-api/docs/computer-use) — 1000×1000 normalized grid, `safety_decision: require_confirmation`
- [Anthropic prompt-injection defenses](https://www.anthropic.com/research/prompt-injection-defenses) — 88 % block rate, classifier behavior
- [OSWorld-Human latency paper (arXiv:2506.16042)](https://arxiv.org/html/2506.16042v1) — 75-94 % of latency in LLM step
- [AT-SPI2 protocol reference](https://www.freedesktop.org/wiki/Accessibility/AT-SPI2/)
- [LangGraph Computer Use template](https://github.com/langchain-ai/langgraph) — for cross-check on the loop shape

Local:
- `docs/superpowers/specs/2026-04-28-desktop-computer-use-design.md` — the superseded earlier design
- `src/voice-agent/subagents/screen_share.py` — reference for the "tool-less" subagent pattern (`tools_required=False`)
- `src/voice-agent/subagents/browser.py` — reference for `pre_transfer` hook + dynamic instructions
- `src/voice-agent/providers/llm.py` — existing Anthropic integration to mirror
