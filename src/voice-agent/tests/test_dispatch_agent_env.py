"""dispatch_agent model + env fix (2026-07-11).

The bug: `dispatch_agent` — the REACHABLE registry tool that spawns
bin/jarvis (`run_jarvis_cli` is NOT in the registry surface; the 16c68247
fix targeted a path the supervisor can't call) — spawned its subprocess with
NO model override and NO env cleaning:

  - voice-mode=local still ran the CLOUD cli-model pin (deepseek-v4-pro);
  - the subprocess inherited the agent's FULL environment — every provider
    key plus JARVIS_PG_DSN (real Postgres password).

The fix routes every dispatch through jarvis_agent.read_cli_model() +
_clean_env_for_cli() (the same machinery run_jarvis_cli uses) and, in local
mode, pins the argv: provider "ollama" (start-env.sh consumes argv[1] and
skips its cli-model→cloud re-mapping) + `--model ollama:<tag>` (beats the
built-in Explore agent's own 'haiku' model pin, which otherwise resolves to
a CLOUD fast-tier model). Also broadens the §P0-SEC-8 strip: _DSN /
_ACCESS_KEY / _SECRET_KEY / _PRIVATE_KEY suffixes, JARVIS_PG_DSN, and any
value shaped like a credential-bearing connection URL.

Hermetic: CLI_MODEL_FILE / VOICE_MODE_FILE are patched to tmp_path, model
discovery is stubbed, and the subprocess spawn is mocked — no real
~/.jarvis reads, no `ollama list`, no bin/jarvis run.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import jarvis_agent as ja
import providers.llm as llm_mod
from providers import local_model_picker
from tools import dispatch_agent as da


def _write(p, text):
    p.write_text(text + "\n", encoding="utf-8")


@pytest.fixture
def files(tmp_path, monkeypatch):
    """Redirect the two ~/.jarvis flat files + clear the model env."""
    cmodel = tmp_path / "cli-model"
    vmode = tmp_path / "voice-mode"
    monkeypatch.setattr(ja, "CLI_MODEL_FILE", cmodel)
    monkeypatch.setattr(llm_mod, "VOICE_MODE_FILE", vmode)
    monkeypatch.delenv("JARVIS_LOCAL_LLM_MODEL", raising=False)
    monkeypatch.delenv("JARVIS_LOCAL_LLM_URL", raising=False)
    monkeypatch.setattr(
        local_model_picker, "resolve_installed_model_tag", lambda preferred="": None
    )
    return cmodel, vmode


def _make_fake_proc(stdout: bytes = b"ok\n", returncode: int = 0):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)
    return proc


@pytest.fixture
def spawn_capture(monkeypatch):
    """Mock create_subprocess_exec, capturing (argv, kwargs) per call."""
    calls: list = []

    async def capture(*args, **kwargs):
        calls.append((list(args), kwargs))
        return _make_fake_proc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", capture)
    return calls


# ── Local mode: dispatch runs the LOCAL model, secrets stripped ──────

@pytest.mark.asyncio
async def test_local_mode_dispatch_pins_ollama_argv_and_env(
    files, spawn_capture, monkeypatch
):
    cmodel, vmode = files
    _write(cmodel, "deepseek-v4-pro")   # cloud pin stays on disk
    _write(vmode, "local")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_MODEL", "qwen2.5:7b")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-real-deepseek")
    monkeypatch.setenv("JARVIS_PG_DSN", "postgresql://jarvis:hunter2@127.0.0.1:5432/jarvis")

    result = await da.handle_dispatch_agent(
        {"subagent_type": "explore", "task": "find X", "description": "x"}
    )
    assert result == "ok"
    assert len(spawn_capture) == 1
    argv, kwargs = spawn_capture[0]

    # argv: provider pin + --model beat both start-env.sh's cli-model
    # re-mapping AND Explore's built-in 'haiku' model pin.
    assert argv[0].endswith("/bin/jarvis")
    assert argv[1] == "ollama"
    assert "--print" in argv and "--agent" in argv
    assert argv[argv.index("--agent") + 1] == "Explore"
    assert argv[argv.index("--model") + 1] == "ollama:qwen2.5:7b"
    assert argv[-1] == "find X"

    # env: sanitized + locally routed (same _clean_env_for_cli contract
    # the run_jarvis_cli path already proved against the live proxy).
    env = kwargs["env"]
    assert env is not None
    assert env["JARVIS_PROVIDER"] == "ollama"
    assert env["ANTHROPIC_MODEL"] == "ollama:qwen2.5:7b"
    assert env["ANTHROPIC_BASE_URL"] == "http://localhost:4000"
    assert env["ANTHROPIC_API_KEY"] == "jarvis-proxy"
    assert "DEEPSEEK_API_KEY" not in env
    assert "JARVIS_PG_DSN" not in env

    # The cli-model FILE is never rewritten.
    assert cmodel.read_text().strip() == "deepseek-v4-pro"


@pytest.mark.asyncio
async def test_local_mode_inherit_agents_get_env_model_only(
    files, spawn_capture, monkeypatch
):
    """Non-Explore agents still get the local argv pins (uniform shape)."""
    _write(files[0], "deepseek-v4-pro")
    _write(files[1], "local")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_MODEL", "qwen2.5:7b")
    await da.handle_dispatch_agent(
        {"subagent_type": "plan", "task": "design X", "description": "x"}
    )
    argv, kwargs = spawn_capture[0]
    assert argv[1] == "ollama"
    assert argv[argv.index("--agent") + 1] == "Plan"
    assert argv[argv.index("--model") + 1] == "ollama:qwen2.5:7b"
    assert kwargs["env"]["ANTHROPIC_MODEL"] == "ollama:qwen2.5:7b"


# ── Cloud mode: argv byte-identical to the pre-fix shape ─────────────

@pytest.mark.asyncio
async def test_cloud_mode_dispatch_keeps_argv_and_strips_secrets(
    files, spawn_capture, monkeypatch
):
    cmodel, vmode = files
    _write(cmodel, "deepseek-v4-pro")
    _write(vmode, "cloud")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-real-deepseek")
    monkeypatch.setenv("JARVIS_PG_DSN", "postgresql://jarvis:hunter2@127.0.0.1:5432/jarvis")

    result = await da.handle_dispatch_agent(
        {"subagent_type": "explore", "task": "find X", "description": "x"}
    )
    assert result == "ok"
    argv, kwargs = spawn_capture[0]
    # No provider argv, no --model — the cloud path resolves exactly as
    # before the fix (cli-model file via start-env.sh).
    assert argv == [str(da._BIN_JARVIS), "--print", "--agent", "Explore", "find X"]
    env = kwargs["env"]
    assert env["JARVIS_PROVIDER"] == "deepseek"
    assert env["JARVIS_MODEL"] == "deepseek-v4-pro"
    assert "ANTHROPIC_MODEL" not in env
    # Secrets stripped on the cloud path too.
    assert "DEEPSEEK_API_KEY" not in env
    assert "JARVIS_PG_DSN" not in env
    assert env["ANTHROPIC_API_KEY"] == "jarvis-proxy"
    assert cmodel.read_text().strip() == "deepseek-v4-pro"


# ── Background path: same env + argv contract ────────────────────────

@pytest.mark.asyncio
async def test_background_runner_uses_sanitized_env(
    files, spawn_capture, monkeypatch
):
    _write(files[0], "deepseek-v4-pro")
    _write(files[1], "local")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_MODEL", "qwen2.5:7b")
    monkeypatch.setenv("JARVIS_PG_DSN", "postgresql://jarvis:hunter2@127.0.0.1:5432/jarvis")
    completed: list = []
    monkeypatch.setattr(
        da.background_tasks, "complete",
        lambda task_id, ann, status="success": completed.append((task_id, ann, status)),
    )
    monkeypatch.setattr(da.background_tasks, "discard", lambda task_id: None)

    await da._run_background("explore", "find X", "label", "id123")

    assert len(spawn_capture) == 1
    argv, kwargs = spawn_capture[0]
    assert argv[1] == "ollama"
    assert argv[argv.index("--model") + 1] == "ollama:qwen2.5:7b"
    assert "JARVIS_PG_DSN" not in kwargs["env"]
    assert completed and completed[0][2] == "success"


# ── Fail closed: no sanitized env → no spawn ─────────────────────────

@pytest.mark.asyncio
async def test_env_sanitizer_failure_refuses_to_spawn(monkeypatch):
    spawn_mock = AsyncMock()
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn_mock)
    monkeypatch.setattr(da, "_cli_model_and_env", lambda: (None, None))

    result = await da.handle_dispatch_agent(
        {"subagent_type": "explore", "task": "x", "description": "x"}
    )
    parsed = json.loads(result)
    assert "sanitized environment" in parsed["error"]
    spawn_mock.assert_not_called()
    assert da._last_dispatch["status"] == "spawn_failed"


@pytest.mark.asyncio
async def test_background_env_sanitizer_failure_refuses_to_spawn(monkeypatch):
    spawn_mock = AsyncMock()
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn_mock)
    monkeypatch.setattr(da, "_cli_model_and_env", lambda: (None, None))
    completed: list = []
    monkeypatch.setattr(
        da.background_tasks, "complete",
        lambda task_id, ann, status="success": completed.append((task_id, ann, status)),
    )
    await da._run_background("explore", "x", "label", "id456")
    spawn_mock.assert_not_called()
    assert completed and completed[0][2] == "spawn_failed"


# ── _agent_module resolution ─────────────────────────────────────────

def test_agent_module_prefers_loaded_module():
    mod = da._agent_module()
    assert callable(mod.read_cli_model)
    assert callable(mod._clean_env_for_cli)


# ── Broadened §P0-SEC-8 strip (jarvis_agent._clean_env_for_cli) ──────

def test_strip_catches_dsn_and_credential_shapes(files, monkeypatch):
    monkeypatch.setenv("JARVIS_PG_DSN", "postgresql://jarvis:hunter2@127.0.0.1:5432/jarvis")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----")
    monkeypatch.setenv("SECRET_KEY", "django-secret")
    monkeypatch.setenv("MY_SERVICE_DSN", "mysql://u:p@db:3306/x")
    monkeypatch.setenv("SOME_QUEUE_URL", "amqp://guest:guest@rabbit:5672/")
    monkeypatch.setenv("PGPASSWORD", "hunter2")
    env = ja._clean_env_for_cli("deepseek-v4-pro")
    assert "JARVIS_PG_DSN" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "PRIVATE_KEY" not in env
    assert "SECRET_KEY" not in env
    assert "MY_SERVICE_DSN" not in env
    assert "SOME_QUEUE_URL" not in env       # credential-URL value shape
    assert "PGPASSWORD" not in env


def test_strip_keeps_vars_the_cli_needs(files, monkeypatch):
    ja._register_ollama_cli_model("ollama:qwen2.5:7b")
    monkeypatch.setenv("JARVIS_LOCAL_LLM_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("PLAIN_SERVICE_URL", "http://localhost:8080/api")
    monkeypatch.setenv("WS_ENDPOINT", "ws://127.0.0.1:7880")
    env = ja._clean_env_for_cli("ollama:qwen2.5:7b")
    # Local routing + plumbing vars survive.
    assert env["OLLAMA_BASE_URL"] == "http://127.0.0.1:11434"
    assert env["JARVIS_LOCAL_LLM_URL"] == "http://127.0.0.1:11434/v1"
    assert "PATH" in env and "HOME" in env
    assert env["JARVIS_PROVIDER"] == "ollama"
    # Plain URLs without user:pass@ userinfo are NOT credentials.
    assert env["PLAIN_SERVICE_URL"] == "http://localhost:8080/api"
    assert env["WS_ENDPOINT"] == "ws://127.0.0.1:7880"


def test_clean_env_defaults_require_login_optout(files, monkeypatch):
    """The CLI's --print login gate aborts unauthenticated boxes; the
    sanitized env carries the documented automation opt-out (live 2026-07-11:
    every dispatch exited 1 with 'Authentication required' without it)."""
    monkeypatch.delenv("JARVIS_REQUIRE_LOGIN", raising=False)
    env = ja._clean_env_for_cli("deepseek-v4-pro")
    assert env["JARVIS_REQUIRE_LOGIN"] == "0"


def test_clean_env_respects_parent_require_login(files, monkeypatch):
    monkeypatch.setenv("JARVIS_REQUIRE_LOGIN", "1")
    env = ja._clean_env_for_cli("deepseek-v4-pro")
    assert env["JARVIS_REQUIRE_LOGIN"] == "1"


def test_credential_url_regex_shapes():
    RE = ja._CREDENTIAL_URL_RE
    assert RE.match("postgresql://user:pw@host:5432/db")
    assert RE.match("redis://:pw@host:6379/0")          # empty user, pw set
    assert RE.match("amqps://a:b@mq.example.com")
    assert not RE.match("http://localhost:4000")          # no userinfo
    assert not RE.match("ws://127.0.0.1:7880")
    assert not RE.match("unix:path=/run/user/1000/bus")   # not scheme://
    assert not RE.match("http://user@host/")              # user but no password
