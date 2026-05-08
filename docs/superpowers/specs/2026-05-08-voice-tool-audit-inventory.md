# Voice-Tool Audit Inventory — 2026-05-08

Every `@function_tool` attached to a JARVIS voice agent's `tools=[…]`, plus audit plan. No code changes.

---

## 1. Supervisor tools — [jarvis_agent.py:8848-8909](../../src/voice-agent/jarvis_agent.py)

| Tool [file:line] | Purpose / subsystem / failure |
|---|---|
| `bash` [tools/bash.py:118](../../src/voice-agent/tools/bash.py#L118) | claude-grade shell · subprocess · banned/timeout |
| `read` / `edit` / `write` [tools/file_read.py:73](../../src/voice-agent/tools/file_read.py#L73), [file_edit.py:28](../../src/voice-agent/tools/file_edit.py#L28), [file_write.py:26](../../src/voice-agent/tools/file_write.py#L26) | cat-n / exact-replace / full write · fs · read-first invariant |
| `enter_plan_mode` / `exit_plan_mode` / `read_plan` [tools/plan_mode.py:127, 194, 244](../../src/voice-agent/tools/plan_mode.py) | plan gating · global state · plans-dir missing |
| `web_search` / `web_fetch` [jarvis_agent.py:6168, 6315](../../src/voice-agent/jarvis_agent.py) | DDG-+IA / GET→text · HTTP · CAPTCHA, TLS, 403 |
| `current_time` / `date_math` / `calc` [jarvis_agent.py:6063, 5998, 5909](../../src/voice-agent/jarvis_agent.py) | tz / date arith / AST math · offline · bad input |
| `glob_files` / `grep_files` [jarvis_agent.py:6368, 6405](../../src/voice-agent/jarvis_agent.py) | rglob / rg-or-grep · fs · rg missing |
| `get_location` / `set_location` [jarvis_agent.py:5747, 5842](../../src/voice-agent/jarvis_agent.py) | override→cache→Google geo→IP / persist · nmcli + net + GOOGLE_API_KEY · API disabled |
| `recall_conversation` [jarvis_agent.py:5227](../../src/voice-agent/jarvis_agent.py#L5227) | substring `state.db.messages` · sqlite · DB missing |
| `remember_this` / `list_pending_proposals` / `accept_proposal` / `reject_proposal` [jarvis_agent.py:5294, 5337, 5370, 5409](../../src/voice-agent/jarvis_agent.py) | learned-rules + proposals · fs + log_analyzer · file missing |
| `tools.memory.remember` / `forget` / `list_memories` / `audit_memories` [tools/memory.py:151, 271, 298, 466](../../src/voice-agent/tools/memory.py) | hub-backed durable memory · hub WS + sqlite · hub down |
| `face_register` / `identify` / `list` / `delete` [tools/computer_use.py:1074, 1159, 1208, 1231](../../src/voice-agent/tools/computer_use.py) | InsightFace + IR liveness · webcam + V4L2 + CV · cam busy, no IR |
| `transfer_to_desktop` / `_browser` / `_planner` + `delegate` (auto, [specialists/agent.py:426](../../src/voice-agent/specialists/agent.py#L426)) | handoff / subagent dispatch · registry · spec disabled |

**Defined but only attached to specialists / weather subagent:** `run_jarvis_cli` ([:4512](../../src/voice-agent/jarvis_agent.py#L4512)), `type_in_terminal` ([:4672](../../src/voice-agent/jarvis_agent.py#L4672)), `media_control` ([:4835](../../src/voice-agent/jarvis_agent.py#L4835)), legacy `bash` ([:5463](../../src/voice-agent/jarvis_agent.py#L5463)), `launch_app` ([:5513](../../src/voice-agent/jarvis_agent.py#L5513)), legacy `read_file` ([:5875](../../src/voice-agent/jarvis_agent.py#L5875)).

---

## 2. Specialist tool factories

| Specialist (state) | File:line | Tools | Purpose |
|---|---|---|---|
| desktop (Spec, **on**) | [desktop.py:208](../../src/voice-agent/specialists/desktop.py#L208) | `bash`, `launch_app`, `computer_use`, `computer_stop`, `click`, `type_text`, `scroll`, `drag`, `key_press`, `wait`, `screenshot`, `live_screen`, `watch_screen`, `webcam_capture`, `run_jarvis_cli`, `type_in_terminal`, `media_control`, `browser_task` | desktop work |
| browser (Spec, **on**) | [browser.py:336](../../src/voice-agent/specialists/browser.py#L336) | All 38 in `browser_ext.ALL_TOOLS` | Chrome via ext bridge |
| browser_v2 (Spec, **off**) | [browser_v2.py:80](../../src/voice-agent/specialists/browser_v2.py#L80) | `browser_task_v2` | broken — see file:108-124 |
| planner (Spec, **on**) | [planner.py:219](../../src/voice-agent/specialists/planner.py#L219) | `run_jarvis_cli` | CLI plan engine |
| researcher (Sub, **on**) | [researcher.py:91](../../src/voice-agent/specialists/researcher.py#L91) | `run_jarvis_cli` | web research |
| summarize (Sub, **on**) | [summarize.py:64](../../src/voice-agent/specialists/summarize.py#L64) | `[]` | TL;DRs |
| weather (Sub, **on**) | [weather.py:96](../../src/voice-agent/specialists/weather.py#L96) | `bash`, `get_location` | wttr.in |
| github (Sub, gated) | [github.py:45](../../src/voice-agent/specialists/github.py#L45) | `github_list_prs`, `github_view_pr`, `github_list_issues`, `github_view_issue` | gh CLI read-only |
| memory_recall (Sub, gated) | [memory_recall.py:33](../../src/voice-agent/specialists/memory_recall.py#L33) | `recall` ([memory_recall.py:87](../../src/voice-agent/tools/memory_recall.py#L87)) | conversations.db |
| validator (Sub, gated GROQ) | [validator.py:44](../../src/voice-agent/specialists/validator.py#L44) | `validate_outcome` ([validator.py:68](../../src/voice-agent/tools/validator.py#L68)) | LLM verdict |
| code_reviewer (Sub, gated) | [code_reviewer.py:37](../../src/voice-agent/specialists/code_reviewer.py#L37) | `review_code` ([code_reviewer.py:75](../../src/voice-agent/tools/code_reviewer.py#L75)) | single-shot review |

`task_done` auto-attached by `RegistrySpecialist`; bailout-phrase allowlist enforced by tool gate.

---

## 3. `ext_*` family — [tools/browser_ext.py:722 (ALL_TOOLS)](../../src/voice-agent/tools/browser_ext.py#L722)

38 tools. POST `127.0.0.1:8765/ext/<action>` (hub → WS → Chrome ext). Names + line refs in `browser_ext.py`:

`web_search` (680), `ext_navigate` (116), `ext_new_tab` (127), `ext_back` (151), `ext_forward` (158), `ext_get_url` (165), `ext_close_tab` (173), `ext_list_tabs` (460), `ext_extract_text` (183), `ext_find_by_text` (197), `ext_dom_summary` (212), `ext_screenshot` (221), `ext_get_console` (470), `ext_observe` (600), `ext_click` (233), `ext_right_click` (247), `ext_hover` (254), `ext_drag` (261), `ext_select` (276), `ext_type` (290), `ext_fill_form` (305), `ext_keypress` (324), `ext_submit` (335), `ext_get_dropdown_options` (582), `ext_scroll` (349), `ext_wait_for` (361), `ext_wait_for_load` (620), `ext_accept_dialog` (375), `ext_switch_iframe` (387), `ext_save_pdf` (487), `ext_upload_file` (501), `ext_download_file` (636), `ext_get_cookies` (417), `ext_set_cookies` (429), `ext_local_storage` (520), `ext_storage_state_get` (547), `ext_storage_state_set` (562), `ext_exec_js` (402).

Failure: bridge down, ext disconnected, no active tab, selector miss, timeout, missing `confirmed=True` for destructive (`ext_exec_js`, `ext_set_cookies`).

---

## 4. Existing tests — skip in smoke pass

`test_launch_app`, `test_computer_use`, `test_direct_tools_and_plan_mode`, `test_get_location`, `test_browser_specialist`, `test_memory_recall`, `test_memory_extractor`, `test_recall_router`, `test_recall_consumer`, `test_validator`, `test_code_reviewer`, `test_github_subagent`, `test_specialists_health`, `test_specialist_registry`.

---

## Audit priority

**Tier 1 — likely-broken, voice-visible, cheap (smoke FIRST):**

1. `ext_*` family — needs `jarvis-bridge.service` AND ext connected. One `ext_get_url` proves all 38.
2. `launch_app` — needs `DISPLAY`/`XAUTHORITY` from systemd; env-fragile.
3. `computer_use` / `click` / `type_text` / `screenshot` / `live_screen` / `watch_screen` — xdotool + scrot -o + Gemini key.
4. `face_*` — webcam, IR cam, insightface model. Kali driver-update risk.
5. `web_search` — DDG CAPTCHA active 2026-05-08; verify IA fallback.
6. `recall_conversation` — was reading wrong DB until 2026-05-03; verify `state.db.messages` returns rows.

**Tier 2 — degraded-but-not-silent:** 7. `browser_task` ([tools/browser.py:108](../../src/voice-agent/tools/browser.py#L108)) — GROQ_API_KEY + Chromium. 8. `get_location` — Wi-Fi 403s without GOOGLE_API_KEY; verify IP fallback. 9. `tools.memory.*` — hub WS, recently rebuilt. 10. `run_jarvis_cli` — 24-s/call.

**Tier 3 — pure-function (skip unless 1/2 fingers them):** `calc`, `date_math`, `current_time`, `read`/`edit`/`write`, plan-mode, `remember_this`, proposals, summarize.

---

## Audit-execution order (≤200 words)

1. **Health** (60 s): `systemctl --user status jarvis-bridge.service` + `curl 127.0.0.1:8765/health`. Bridge down → all `ext_*` dead, fix first.
2. **Pure-function REPL** (5 min): `cd src/voice-agent && .venv/bin/python`; `await calc("17*23")`, `await current_time("Africa/Douala")`, `await date_math("today")`, `await read("/etc/hostname")`. Bin obvious-pass.
3. **FS side-effects** (10 min): `glob_files`, `grep_files`, `set_location("")`, `remember_this("audit ping")` → verify `~/.jarvis/learned_rules.md`.
4. **Network** (10 min): `web_fetch("https://example.com")`, `web_search("python")` — confirm CAPTCHA fallback fires; `get_location()` log which path won.
5. **X11 + media** (15 min): `launch_app("xterm")` + pgrep, `screenshot()`, `media_control("status","spotify")`. Match the systemd unit's env (`systemctl --user show-environment`) to catch DISPLAY/XAUTHORITY drift.
6. **Hub + ext_*** (15 min): `ext_get_url`, `ext_list_tabs`, `ext_screenshot`, `ext_navigate("https://example.com")` — proves the family.
7. **Specialist round-trip** (20 min): delegate→weather; transfer_to_browser ("what tab is open"); transfer_to_desktop ("open xterm"). Tests gate + ack + `task_done` end-to-end.

For each call, log (a) raw return, (b) side-effect file/process, (c) elapsed ms. One results table; flag every paraphrasable-error string. Budget <90 min.
