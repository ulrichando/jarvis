# Conversation Modes — Backend Implementation Plan (Phase 1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make conversation modes work end-to-end via the voice-client HTTP API — a named preset that, when selected, writes the existing `~/.jarvis` setting files together and applies a per-mode tool allowlist.

**Architecture:** A new `pipeline/conversation_modes.py` mode store owns `~/.jarvis/modes.json` (lock-protected atomic writes, same pattern as `pipeline/file_memory.py`). Selecting a mode *writes* the existing single-setting files (`voice-mode`, `voice-model`, `cli-model`, `tts-provider`, `voice-tts-voice`) plus a new `~/.jarvis/mode-allowed-tools` file — so every existing consumer is unchanged. A tiny filter in `tools/_adapter.py::load_all_livekit_tools` honors the allowlist on top of the existing `check_fn` skip. HTTP endpoints in `voice_client_http_api.py` expose list/select/create/update/delete, mirroring the existing `/voice-model` + `/cli-model` handlers.

**Tech Stack:** Python 3.13, the voice-agent `.venv`, pytest. Spec: `docs/superpowers/specs/2026-06-29-conversation-modes-design.md`.

**Scope:** Backend only. Web editor (Phase 2) and desktop tray (Phase 3) are separate plans that consume the API built here.

---

## File Structure

- **Create** `src/voice-agent/pipeline/conversation_modes.py` — the mode store: schema, load/seed/save (locked atomic), `resolve`, `apply`, `create/update/delete`, `active_allowed_tools`. One responsibility: own `modes.json` and translate a mode into the underlying setting files.
- **Modify** `src/voice-agent/tools/_adapter.py` — add the allowlist filter inside `load_all_livekit_tools` (one new guard + a small helper import).
- **Modify** `src/voice-agent/voice_client_http_api.py` — add `/modes`, `/mode`, `/mode/create`, `/mode/update`, `/mode/delete` route handlers.
- **Create** `src/voice-agent/tests/test_conversation_modes.py` — mode-store round-trip, resolve/apply, allowlist filter, endpoint behavior.

Before starting, READ these for the patterns to mirror:
- `src/voice-agent/pipeline/file_memory.py` — the file lock + atomic write (`tmp` then `os.replace`) pattern. Reuse it; do not invent a new locking scheme.
- `src/voice-agent/voice_client_tray_config.py` — the `~/.jarvis` path constants (`SPEECH_MODEL_FILE`, `CLI_MODEL_FILE`, `TTS_PROVIDER_FILE`, and the tts-voice / voice-mode files). Import/reuse these constants; do not hardcode paths twice.
- `src/voice-agent/voice_client_http_api.py` — the existing `POST /voice-model` and `POST /cli-model` handlers and the restart helper they call. Mirror their request parsing + restart trigger.
- `src/voice-agent/tools/registry.py` — `all_entries()` and `is_available()` (the `check_fn` gate) and `tools/_adapter.py::load_all_livekit_tools` (the existing skip at the `check_fn` line).

---

## Task 1: Mode store — schema, load, seed

**Files:**
- Create: `src/voice-agent/pipeline/conversation_modes.py`
- Test: `src/voice-agent/tests/test_conversation_modes.py`

- [ ] **Step 1: Write the failing test** (modes.json absent → built-ins seeded, DeepSeek active)

```python
# tests/test_conversation_modes.py
import json
from pathlib import Path
import pytest


@pytest.fixture
def modes_path(tmp_path, monkeypatch):
    p = tmp_path / "modes.json"
    monkeypatch.setattr("pipeline.conversation_modes.MODES_FILE", p)
    return p


def test_seeds_builtins_when_missing(modes_path):
    from pipeline import conversation_modes as cm
    doc = cm.load()
    ids = [m["id"] for m in doc["modes"]]
    assert ids == ["deepseek", "claude", "local"]
    assert doc["active"] == "deepseek"
    # seeding persisted the file
    assert modes_path.exists()
    on_disk = json.loads(modes_path.read_text())
    assert on_disk["active"] == "deepseek"


def test_deepseek_builtin_is_internally_consistent(modes_path):
    from pipeline import conversation_modes as cm
    ds = next(m for m in cm.load()["modes"] if m["id"] == "deepseek")
    assert ds["voice_model"] == "deepseek-v4-flash"
    assert ds["cli_model"] == "deepseek-v4-pro"
    assert ds["voice_mode"] == "cloud"


def test_local_builtin_is_on_device(modes_path):
    from pipeline import conversation_modes as cm
    lo = next(m for m in cm.load()["modes"] if m["id"] == "local")
    assert lo["voice_mode"] == "local"
    assert lo["voice_model"] is None
    assert lo["cli_model"] == "ollama-qwen3-30b-a3b"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_conversation_modes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.conversation_modes'`

- [ ] **Step 3: Write minimal implementation**

```python
# pipeline/conversation_modes.py
"""Conversation modes — named presets that bundle the voice/CLI model, TTS
voice, on-device toggle, and a tool allowlist. Selecting a mode writes the
existing ~/.jarvis single-setting files as a set (see the
2026-06-29-conversation-modes-design.md spec). Lock-protected atomic writes,
mirroring pipeline/file_memory.py."""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("jarvis.modes")

MODES_FILE: Path = Path.home() / ".jarvis" / "modes.json"
_LOCK = threading.Lock()

# Built-in seeds. Each mode is internally consistent (one provider end to end).
_BUILTINS: list[dict[str, Any]] = [
    {
        "id": "deepseek", "label": "DeepSeek", "voice_mode": "cloud",
        "voice_model": "deepseek-v4-flash", "cli_model": "deepseek-v4-pro",
        "tts_provider": "kokoro:af_bella", "tts_voice": "af_bella",
        "allowed_tools": None,
    },
    {
        "id": "claude", "label": "Claude", "voice_mode": "cloud",
        "voice_model": "claude-haiku-4-5", "cli_model": "claude-sonnet-4-6",
        "tts_provider": "kokoro:af_bella", "tts_voice": "af_bella",
        "allowed_tools": None,
    },
    {
        "id": "local", "label": "Local (on-device)", "voice_mode": "local",
        "voice_model": None, "cli_model": "ollama-qwen3-30b-a3b",
        "tts_provider": "kokoro:af_heart", "tts_voice": "af_heart",
        "allowed_tools": None,
    },
]


def _default_doc() -> dict[str, Any]:
    return {"active": "deepseek", "modes": [dict(m) for m in _BUILTINS]}


def _write_atomic(doc: dict[str, Any]) -> None:
    MODES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = MODES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    os.replace(tmp, MODES_FILE)  # atomic on POSIX


def load() -> dict[str, Any]:
    """Return the modes doc, seeding built-ins if the file is missing/corrupt."""
    with _LOCK:
        if not MODES_FILE.exists():
            doc = _default_doc()
            _write_atomic(doc)
            return doc
        try:
            return json.loads(MODES_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[modes] modes.json unreadable (%s); reseeding", e)
            try:
                MODES_FILE.replace(MODES_FILE.with_suffix(".json.bak"))
            except OSError:
                pass
            doc = _default_doc()
            _write_atomic(doc)
            return doc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_conversation_modes.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/conversation_modes.py src/voice-agent/tests/test_conversation_modes.py
git commit -m "feat(voice): conversation_modes store — schema + seed + load"
```

---

## Task 2: resolve + apply (write the setting files)

**Files:**
- Modify: `src/voice-agent/pipeline/conversation_modes.py`
- Test: `src/voice-agent/tests/test_conversation_modes.py`

- [ ] **Step 1: Write the failing test** (apply writes every underlying file; local mode omits voice-model)

```python
def test_apply_writes_all_setting_files(modes_path, tmp_path, monkeypatch):
    from pipeline import conversation_modes as cm
    files = {}
    for name in ("voice-mode", "voice-model", "cli-model",
                 "tts-provider", "voice-tts-voice", "mode-allowed-tools"):
        p = tmp_path / name
        files[name] = p
        monkeypatch.setattr(cm, f"_F_{name.replace('-', '_').upper()}", p)

    cm.apply("claude")

    assert files["voice-mode"].read_text().strip() == "cloud"
    assert files["voice-model"].read_text().strip() == "claude-haiku-4-5"
    assert files["cli-model"].read_text().strip() == "claude-sonnet-4-6"
    assert files["tts-provider"].read_text().strip() == "kokoro:af_bella"
    assert files["voice-tts-voice"].read_text().strip() == "af_bella"
    # null allowlist → empty file (= "all tools")
    assert files["mode-allowed-tools"].read_text().strip() == ""
    assert cm.load()["active"] == "claude"


def test_apply_local_omits_voice_model(modes_path, tmp_path, monkeypatch):
    from pipeline import conversation_modes as cm
    vm = tmp_path / "voice-model"
    vm.write_text("stale-value\n")
    monkeypatch.setattr(cm, "_F_VOICE_MODEL", vm)
    monkeypatch.setattr(cm, "_F_VOICE_MODE", tmp_path / "voice-mode")
    monkeypatch.setattr(cm, "_F_CLI_MODEL", tmp_path / "cli-model")
    monkeypatch.setattr(cm, "_F_TTS_PROVIDER", tmp_path / "tts-provider")
    monkeypatch.setattr(cm, "_F_VOICE_TTS_VOICE", tmp_path / "voice-tts-voice")
    monkeypatch.setattr(cm, "_F_MODE_ALLOWED_TOOLS", tmp_path / "mode-allowed-tools")

    cm.apply("local")
    assert (tmp_path / "voice-mode").read_text().strip() == "local"
    # voice_model is None for local → leave the file untouched (local path owns model selection)
    assert vm.read_text().strip() == "stale-value"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_conversation_modes.py -k apply -v`
Expected: FAIL — `AttributeError: module 'pipeline.conversation_modes' has no attribute 'apply'`

- [ ] **Step 3: Write minimal implementation** (append to `conversation_modes.py`)

```python
# Underlying ~/.jarvis setting files a mode writes. Reuse the tray-config
# constants where they exist; define the rest here. (Import lazily so this
# module stays importable in tests that monkeypatch these.)
_JD = Path.home() / ".jarvis"
_F_VOICE_MODE       = _JD / "voice-mode"
_F_VOICE_MODEL      = _JD / "voice-model"
_F_CLI_MODEL        = _JD / "cli-model"
_F_TTS_PROVIDER     = _JD / "tts-provider"
_F_VOICE_TTS_VOICE  = _JD / "voice-tts-voice"
_F_MODE_ALLOWED_TOOLS = _JD / "mode-allowed-tools"


def _write_setting(path: Path, value: Optional[str]) -> None:
    """Write a single ~/.jarvis setting file atomically. None → leave untouched."""
    if value is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(str(value) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def get_mode(mode_id: str) -> Optional[dict[str, Any]]:
    return next((m for m in load()["modes"] if m["id"] == mode_id), None)


def resolve(mode_id: str) -> dict[str, Any]:
    """Return a mode dict; raise KeyError if unknown."""
    m = get_mode(mode_id)
    if m is None:
        raise KeyError(f"unknown mode: {mode_id!r}")
    return m


def apply(mode_id: str) -> dict[str, Any]:
    """Write all underlying setting files for `mode_id` + set it active.
    Caller is responsible for restarting the agent."""
    m = resolve(mode_id)
    _write_setting(_F_VOICE_MODE, m.get("voice_mode") or "cloud")
    _write_setting(_F_VOICE_MODEL, m.get("voice_model"))       # None (local) → untouched
    _write_setting(_F_CLI_MODEL, m.get("cli_model"))
    _write_setting(_F_TTS_PROVIDER, m.get("tts_provider"))
    _write_setting(_F_VOICE_TTS_VOICE, m.get("tts_voice"))
    allowed = m.get("allowed_tools")
    _write_setting(_F_MODE_ALLOWED_TOOLS, "\n".join(allowed) if allowed else "")
    with _LOCK:
        doc = load()
        doc["active"] = mode_id
        _write_atomic(doc)
    logger.info("[modes] applied %s", mode_id)
    return m
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_conversation_modes.py -k apply -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/conversation_modes.py src/voice-agent/tests/test_conversation_modes.py
git commit -m "feat(voice): conversation_modes resolve + apply (writes setting files)"
```

---

## Task 3: tool allowlist filter

**Files:**
- Modify: `src/voice-agent/pipeline/conversation_modes.py` (add `active_allowed_tools` + `CORE_TOOLS`)
- Modify: `src/voice-agent/tools/_adapter.py` (the `load_all_livekit_tools` loop)
- Test: `src/voice-agent/tests/test_conversation_modes.py`

- [ ] **Step 1: Write the failing test** (allowlist filter keeps allowed + CORE_TOOLS, drops the rest; empty = all)

```python
def test_active_allowed_tools_reads_file(tmp_path, monkeypatch):
    from pipeline import conversation_modes as cm
    f = tmp_path / "mode-allowed-tools"
    monkeypatch.setattr(cm, "_F_MODE_ALLOWED_TOOLS", f)
    f.write_text("computer_use\nbrowser_task\n")
    assert cm.active_allowed_tools() == {"computer_use", "browser_task"}
    f.write_text("")            # empty file = no restriction
    assert cm.active_allowed_tools() is None
    f.unlink()                  # absent = no restriction
    assert cm.active_allowed_tools() is None


def test_tool_is_mode_allowed(tmp_path, monkeypatch):
    from pipeline import conversation_modes as cm
    f = tmp_path / "mode-allowed-tools"
    monkeypatch.setattr(cm, "_F_MODE_ALLOWED_TOOLS", f)
    f.write_text("browser_task\n")
    assert cm.tool_is_mode_allowed("browser_task") is True
    assert cm.tool_is_mode_allowed("computer_use") is False
    assert cm.tool_is_mode_allowed("clarify") is True   # CORE_TOOLS floor
    f.write_text("")                                     # no restriction → all allowed
    assert cm.tool_is_mode_allowed("computer_use") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_conversation_modes.py -k allowed -v`
Expected: FAIL — `AttributeError: ... 'active_allowed_tools'`

- [ ] **Step 3: Write minimal implementation** (append to `conversation_modes.py`)

```python
# Tools that are ALWAYS available regardless of a mode's allowlist, so a mode
# can never brick the assistant (it still needs to talk + clarify + remember).
CORE_TOOLS: frozenset[str] = frozenset({"clarify", "memory"})


def active_allowed_tools() -> Optional[set[str]]:
    """The active mode's tool allowlist as a set, or None for 'no restriction'.
    Read from the file (not load()) so it's cheap + restart-fresh."""
    try:
        raw = _F_MODE_ALLOWED_TOOLS.read_text(encoding="utf-8")
    except OSError:
        return None
    names = {ln.strip() for ln in raw.splitlines() if ln.strip()}
    return names or None


def tool_is_mode_allowed(name: str) -> bool:
    allow = active_allowed_tools()
    if allow is None:
        return True
    return name in allow or name in CORE_TOOLS
```

- [ ] **Step 4: Run test to verify the conversation_modes part passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_conversation_modes.py -k allowed -v`
Expected: PASS

- [ ] **Step 5: Wire the filter into `load_all_livekit_tools`**

In `tools/_adapter.py`, find the loop over `registry.all_entries()` that skips on `check_fn` (the `if entry.check_fn is not None and not registry.is_available(entry.name)` guard). Add a second guard right after it:

```python
        # Conversation-mode tool allowlist (on top of check_fn availability).
        # When the active mode restricts tools, skip any not in its allowlist
        # (CORE_TOOLS always pass). No active restriction → no-op.
        from pipeline.conversation_modes import tool_is_mode_allowed
        if not tool_is_mode_allowed(entry.name):
            logger.info("Skipping tool %s — not in the active conversation mode's allowlist", entry.name)
            continue
```

- [ ] **Step 6: Write the failing test for the integration** (a restricted mode drops a non-core, non-listed tool)

```python
def test_load_all_livekit_tools_honors_allowlist(tmp_path, monkeypatch):
    from pipeline import conversation_modes as cm
    f = tmp_path / "mode-allowed-tools"
    monkeypatch.setattr(cm, "_F_MODE_ALLOWED_TOOLS", f)
    f.write_text("clarify\n")  # restrict to clarify only (+ CORE)
    from tools._adapter import load_all_livekit_tools
    names = {t.info.name for t in load_all_livekit_tools()}
    assert "clarify" in names            # allowed
    assert "memory" in names             # CORE_TOOLS floor
    assert "computer_use" not in names   # restricted out
```

- [ ] **Step 7: Run the full conversation-modes test + a smoke of the adapter**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_conversation_modes.py tests/test_no_duplicate_tools.py -v`
Expected: PASS (no regression in tool loading; allowlist honored)

- [ ] **Step 8: Commit**

```bash
git add src/voice-agent/pipeline/conversation_modes.py src/voice-agent/tools/_adapter.py src/voice-agent/tests/test_conversation_modes.py
git commit -m "feat(voice): per-mode tool allowlist filter in load_all_livekit_tools"
```

---

## Task 4: HTTP endpoints (`/modes`, `/mode`, create/update/delete)

**Files:**
- Modify: `src/voice-agent/pipeline/conversation_modes.py` (add `create`/`update`/`delete`)
- Modify: `src/voice-agent/voice_client_http_api.py` (route handlers)
- Test: `src/voice-agent/tests/test_conversation_modes.py`

- [ ] **Step 1: Write the failing test** (create/update/delete persist; delete-active rejected)

```python
def test_create_update_delete(modes_path):
    from pipeline import conversation_modes as cm
    cm.create({"id": "focus", "label": "Focus", "voice_mode": "cloud",
               "voice_model": "claude-haiku-4-5", "cli_model": "claude-sonnet-4-6",
               "tts_provider": "kokoro:af_bella", "tts_voice": "af_bella",
               "allowed_tools": ["clarify"]})
    assert cm.get_mode("focus")["allowed_tools"] == ["clarify"]
    cm.update("focus", {"label": "Deep Focus"})
    assert cm.get_mode("focus")["label"] == "Deep Focus"
    cm.delete("focus")
    assert cm.get_mode("focus") is None


def test_delete_active_rejected(modes_path):
    from pipeline import conversation_modes as cm
    with pytest.raises(ValueError):
        cm.delete("deepseek")   # the seeded active mode
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_conversation_modes.py -k "create_update_delete or delete_active" -v`
Expected: FAIL — `AttributeError: ... 'create'`

- [ ] **Step 3: Write minimal implementation** (append to `conversation_modes.py`)

```python
def create(mode: dict[str, Any]) -> None:
    with _LOCK:
        doc = load()
        if any(m["id"] == mode["id"] for m in doc["modes"]):
            raise ValueError(f"mode already exists: {mode['id']!r}")
        doc["modes"].append(mode)
        _write_atomic(doc)


def update(mode_id: str, patch: dict[str, Any]) -> None:
    with _LOCK:
        doc = load()
        m = next((m for m in doc["modes"] if m["id"] == mode_id), None)
        if m is None:
            raise KeyError(mode_id)
        m.update({k: v for k, v in patch.items() if k != "id"})
        _write_atomic(doc)


def delete(mode_id: str) -> None:
    with _LOCK:
        doc = load()
        if doc["active"] == mode_id:
            raise ValueError("cannot delete the active mode; switch first")
        doc["modes"] = [m for m in doc["modes"] if m["id"] != mode_id]
        _write_atomic(doc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_conversation_modes.py -k "create_update_delete or delete_active" -v`
Expected: PASS

- [ ] **Step 5: Add the HTTP handlers**

In `voice_client_http_api.py`, locate the existing `POST /voice-model` handler (it parses `{"model": …}`, writes the file, and triggers the agent restart). Add these routes following the SAME structure (request parsing, JSON response, the existing restart helper for `/mode`):

```python
# GET /modes -> {"active": id, "modes": [...]}
#   from pipeline import conversation_modes as cm
#   doc = cm.load(); respond_json(doc)
#
# POST /mode {"id": "claude"} -> apply + restart
#   cm.apply(body["id"]); <call the same restart helper /voice-model uses>; respond_json({"ok": True})
#
# POST /mode/create  {mode dict}   -> cm.create(body); respond_json({"ok": True})
# POST /mode/update  {"id":..,"patch":{..}} -> cm.update(body["id"], body["patch"])
# POST /mode/delete  {"id":..}     -> cm.delete(body["id"]) (409 on ValueError)
```

Write them as real handlers matching the file's routing style (do not invent a framework — copy the dispatch shape of the existing `/voice-model` + `/cli-model` cases). Map `KeyError` → 404, `ValueError` → 409.

- [ ] **Step 6: Test the handlers** (import the api module, drive the route functions directly with a fake request, assert the store changed). Mirror any existing endpoint test in the suite (`grep -l "voice-model\|cli-model" tests/`). Run:

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_conversation_modes.py -v`
Expected: PASS (all conversation-modes tests)

- [ ] **Step 7: Full-suite regression + live smoke**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/ -q`
Expected: full suite green (3608+ pass).

Live smoke (optional, needs the voice-client API running): `curl -s 127.0.0.1:8767/modes | jq .active` → `"deepseek"`; `curl -s -XPOST 127.0.0.1:8767/mode -d '{"id":"claude"}'` → agent restarts on Claude.

- [ ] **Step 8: Commit**

```bash
git add src/voice-agent/pipeline/conversation_modes.py src/voice-agent/voice_client_http_api.py src/voice-agent/tests/test_conversation_modes.py
git commit -m "feat(voice): /modes + /mode HTTP endpoints (select/create/update/delete)"
```

---

## Self-Review

- **Spec coverage:** mode store + modes.json + seeds (Task 1) ✓; resolve/apply writing all setting files incl. `voice-mode` for Local (Task 2) ✓; allowlist filter + CORE_TOOLS floor on `load_all_livekit_tools` (Task 3) ✓; `/modes` + `/mode` + create/update/delete (Task 4) ✓; error handling — KeyError→404, ValueError→409, delete-active rejected, corrupt-file reseed (Tasks 1+4) ✓. The web editor + tray are explicitly out of this plan (Phases 2–3).
- **Placeholders:** Step 5 of Task 4 references the existing `/voice-model` handler rather than fabricating the file's exact routing internals — the implementer must read it. This is intentional (accuracy over invented signatures), not a TODO; the route bodies + store calls are fully specified.
- **Type consistency:** `apply`/`resolve`/`get_mode`/`create`/`update`/`delete`/`active_allowed_tools`/`tool_is_mode_allowed` names + signatures are consistent across tasks; mode dict keys (`id,label,voice_mode,voice_model,cli_model,tts_provider,tts_voice,allowed_tools`) match the spec schema throughout.
