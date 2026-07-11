"""Local-mode context budget (2026-07-11).

The on-device supervisor (qwen3-4b class on the 6 GB GPU) has a real
context ceiling of 12288 tokens (measured: 12288 = 3.5 GB / 100% GPU with
q8 KV cache; 16384 spills), while the cloud prompt weighs ~38-40k tokens.
Three local-mode-gated mechanisms make local mode actually work:

  1. providers/llm.py: the speech LLM routes via a derived Ollama model
     variant `<tag>-jarvis-ctx:<N>` (Ollama's /v1 ignores request num_ctx;
     only a baked manifest PARAMETER works) — best-effort /api/create,
     base tag on any failure.
  2. tools/_adapter.py + jarvis_agent.py: the supervisor surface trims to
     a small-schema core tool set; memory snapshot hard-truncates; skill
     catalog drops; the compact soul_local/supervisor_local persona loads.
  3. providers/llm.py::local_ctx_prune_target + JarvisAgent.llm_node: the
     token-aware pruner engages with a num_ctx-derived budget.

EVERY mechanism is gated on providers.llm.is_local_voice_mode() — the
canonical ~/.jarvis/voice-mode == "local" predicate — so CLOUD MODE IS
BYTE-IDENTICAL. The cloud half of each test asserts exactly that.
"""
import pytest


@pytest.fixture
def local_mode(tmp_path, monkeypatch):
    """Point the canonical voice-mode file at a tmp 'local' file."""
    vm = tmp_path / "voice-mode"
    vm.write_text("local\n", encoding="utf-8")
    import providers.llm as llm
    monkeypatch.setattr(llm, "VOICE_MODE_FILE", vm)
    return vm


@pytest.fixture
def cloud_mode(tmp_path, monkeypatch):
    """Explicit cloud voice-mode file (the conftest default is a
    nonexistent path, which also reads as cloud)."""
    vm = tmp_path / "voice-mode"
    vm.write_text("cloud\n", encoding="utf-8")
    import providers.llm as llm
    monkeypatch.setattr(llm, "VOICE_MODE_FILE", vm)
    return vm


@pytest.fixture
def fresh_variant_cache(monkeypatch):
    import providers.llm as llm
    monkeypatch.setattr(llm, "_ENSURED_CTX_VARIANTS", {})


# ─────────────────────────────────────────────────────────────────────
# 1. The canonical predicate
# ─────────────────────────────────────────────────────────────────────

def test_predicate_false_by_default():
    """Hermetic default (conftest points voice-mode at a nonexistent
    path) → cloud."""
    from providers.llm import is_local_voice_mode
    assert is_local_voice_mode() is False


def test_predicate_true_in_local_mode(local_mode):
    from providers.llm import is_local_voice_mode
    assert is_local_voice_mode() is True


def test_predicate_false_on_cloud_file(cloud_mode):
    from providers.llm import is_local_voice_mode
    assert is_local_voice_mode() is False


# ─────────────────────────────────────────────────────────────────────
# 2. num_ctx knob (JARVIS_LOCAL_LLM_NUM_CTX) + VRAM-ceiling clamp
# ─────────────────────────────────────────────────────────────────────

def test_num_ctx_default(monkeypatch):
    monkeypatch.delenv("JARVIS_LOCAL_LLM_NUM_CTX", raising=False)
    from providers.llm import local_llm_num_ctx
    assert local_llm_num_ctx() == 12288


def test_num_ctx_env_override(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL_LLM_NUM_CTX", "8192")
    from providers.llm import local_llm_num_ctx
    assert local_llm_num_ctx() == 8192


def test_num_ctx_clamped_to_vram_ceiling(monkeypatch):
    """>12288 spills the KV cache to CPU on the 6 GB box (measured) — the
    knob clamps rather than honoring a spilling value."""
    monkeypatch.setenv("JARVIS_LOCAL_LLM_NUM_CTX", "16384")
    from providers.llm import local_llm_num_ctx, LOCAL_LLM_NUM_CTX_MAX
    assert local_llm_num_ctx() == LOCAL_LLM_NUM_CTX_MAX == 12288


@pytest.mark.parametrize("bad", ["banana", "1024", "-1", "0"])
def test_num_ctx_garbage_falls_back(monkeypatch, bad):
    monkeypatch.setenv("JARVIS_LOCAL_LLM_NUM_CTX", bad)
    from providers.llm import local_llm_num_ctx
    assert local_llm_num_ctx() == 12288


# ─────────────────────────────────────────────────────────────────────
# 3. Derived ctx-variant tag + /api/create ensure (mocked — no Ollama)
# ─────────────────────────────────────────────────────────────────────

def test_derived_tag_shape():
    """Must match the CLI's derivedOllamaModelName convention so both
    trees share variants (':' and '/' flatten to '-')."""
    from providers.llm import derived_ollama_ctx_tag
    assert (
        derived_ollama_ctx_tag("qwen3:4b-instruct-2507", 12288)
        == "qwen3-4b-instruct-2507-jarvis-ctx:12288"
    )
    assert (
        derived_ollama_ctx_tag("library/qwen3:4b", 8192)
        == "library-qwen3-4b-jarvis-ctx:8192"
    )


def test_ensure_variant_success_and_memoized(monkeypatch, fresh_variant_cache):
    import providers.llm as llm
    calls = []

    def fake_create(root, payload):
        calls.append((root, payload))

    monkeypatch.setattr(llm, "_ollama_api_create", fake_create)
    got = llm.ensure_ollama_ctx_variant("qwen3:4b", 12288,
                                        base_url="http://127.0.0.1:11434/v1")
    assert got == "qwen3-4b-jarvis-ctx:12288"
    assert len(calls) == 1
    root, payload = calls[0]
    assert root == "http://127.0.0.1:11434"           # /v1 stripped for /api
    assert payload["model"] == "qwen3-4b-jarvis-ctx:12288"
    assert payload["from"] == "qwen3:4b"
    assert payload["parameters"] == {"num_ctx": 12288}
    # Memoized: a second ensure makes no second HTTP hop.
    again = llm.ensure_ollama_ctx_variant("qwen3:4b", 12288,
                                          base_url="http://127.0.0.1:11434/v1")
    assert again == got and len(calls) == 1


def test_ensure_variant_failure_falls_back_to_base(monkeypatch, fresh_variant_cache):
    import providers.llm as llm

    def boom(root, payload):
        raise OSError("connection refused")

    monkeypatch.setattr(llm, "_ollama_api_create", boom)
    assert llm.ensure_ollama_ctx_variant("qwen3:4b", 12288) == "qwen3:4b"
    # Failure is NOT memoized — a later success recovers.
    monkeypatch.setattr(llm, "_ollama_api_create", lambda r, p: None)
    assert llm.ensure_ollama_ctx_variant("qwen3:4b", 12288) == "qwen3-4b-jarvis-ctx:12288"


def test_ensure_variant_passthrough_for_derived_tag(monkeypatch, fresh_variant_cache):
    import providers.llm as llm

    def never(root, payload):  # pragma: no cover — must not be reached
        raise AssertionError("should not POST for an already-derived tag")

    monkeypatch.setattr(llm, "_ollama_api_create", never)
    assert (
        llm.ensure_ollama_ctx_variant("qwen3-4b-jarvis-ctx:12288", 12288)
        == "qwen3-4b-jarvis-ctx:12288"
    )


def test_make_local_speech_llm_uses_variant_in_local_mode(
    local_mode, monkeypatch, fresh_variant_cache
):
    import providers.llm as llm
    monkeypatch.setattr(llm, "_ollama_api_create", lambda r, p: None)
    monkeypatch.delenv("JARVIS_LOCAL_LLM_NUM_CTX", raising=False)
    inst = llm._make_local_speech_llm("qwen3:4b-instruct-2507")
    assert inst._jarvis_label == "local:qwen3-4b-instruct-2507-jarvis-ctx:12288"


def test_make_local_speech_llm_base_tag_in_cloud_mode(cloud_mode, monkeypatch,
                                                      fresh_variant_cache):
    """CLOUD BYTE-IDENTITY: an explicit ollama pin outside local mode keeps
    the base tag and never touches the daemon."""
    import providers.llm as llm

    def never(root, payload):  # pragma: no cover
        raise AssertionError("cloud mode must not create variants")

    monkeypatch.setattr(llm, "_ollama_api_create", never)
    inst = llm._make_local_speech_llm("qwen3:4b-instruct-2507")
    assert inst._jarvis_label == "local:qwen3:4b-instruct-2507"


def test_make_local_speech_llm_survives_create_failure(local_mode, monkeypatch,
                                                       fresh_variant_cache):
    import providers.llm as llm

    def boom(root, payload):
        raise OSError("daemon down")

    monkeypatch.setattr(llm, "_ollama_api_create", boom)
    inst = llm._make_local_speech_llm("qwen3:4b")
    assert inst._jarvis_label == "local:qwen3:4b"  # degraded, not broken


# ─────────────────────────────────────────────────────────────────────
# 4. Ctx-aware prune budget
# ─────────────────────────────────────────────────────────────────────

def test_prune_target_none_in_cloud_mode(cloud_mode):
    from providers.llm import local_ctx_prune_target
    assert local_ctx_prune_target() is None


def test_prune_target_default_local(local_mode, monkeypatch):
    monkeypatch.delenv("JARVIS_LOCAL_LLM_NUM_CTX", raising=False)
    from providers.llm import local_ctx_prune_target
    assert local_ctx_prune_target() == 12288 - 3072  # 9216


def test_prune_target_follows_num_ctx(local_mode, monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL_LLM_NUM_CTX", "8192")
    from providers.llm import local_ctx_prune_target
    assert local_ctx_prune_target() == 8192 - 3072


def test_pruner_engages_at_local_budget(local_mode, monkeypatch):
    """End-to-end: an over-budget chat_ctx actually shrinks when pruned to
    the local target (the same helper llm_node calls)."""
    from livekit.agents.llm import ChatContext
    from providers.llm import local_ctx_prune_target, prune_chat_ctx_for_budget
    monkeypatch.delenv("JARVIS_LOCAL_LLM_NUM_CTX", raising=False)
    target = local_ctx_prune_target()
    ctx = ChatContext.empty()
    ctx.add_message(role="system", content="SYSTEM PROMPT — must survive")
    for i in range(80):
        ctx.add_message(role="user", content=f"turn {i}: " + ("word " * 800))
    pruned = prune_chat_ctx_for_budget(ctx, target)
    assert pruned is not ctx
    assert len(pruned.items) < len(ctx.items)
    kept_roles = [getattr(it, "role", None) for it in pruned.items]
    assert "system" in kept_roles  # system prompt always preserved


# ─────────────────────────────────────────────────────────────────────
# 5. Core tool surface (tools/_adapter.py)
# ─────────────────────────────────────────────────────────────────────

def test_tool_allowlist_none_in_cloud_mode(cloud_mode):
    from tools._adapter import local_voice_tool_allowlist
    assert local_voice_tool_allowlist() is None


def test_tool_allowlist_core_set_in_local_mode(local_mode, monkeypatch):
    monkeypatch.delenv("JARVIS_LOCAL_VOICE_TOOLS", raising=False)
    from tools._adapter import local_voice_tool_allowlist, LOCAL_VOICE_CORE_TOOLS
    assert local_voice_tool_allowlist() == frozenset(LOCAL_VOICE_CORE_TOOLS)
    assert set(LOCAL_VOICE_CORE_TOOLS) == {
        "terminal", "read_file", "write_file", "memory", "web_search", "clarify",
    }


def test_tool_allowlist_env_override(local_mode, monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL_VOICE_TOOLS", "terminal, web_fetch ,memory")
    from tools._adapter import local_voice_tool_allowlist
    assert local_voice_tool_allowlist() == frozenset({"terminal", "web_fetch", "memory"})


def test_loader_trims_surface_in_local_mode(local_mode, monkeypatch):
    """In local mode the loaded surface is a subset of the core set — the
    big-schema tools (computer_use / browser_task / dispatch_agent /
    skills / agent authoring / webcam / face_recognition) are gone."""
    monkeypatch.delenv("JARVIS_LOCAL_VOICE_TOOLS", raising=False)
    import pipeline.conversation_modes as modes
    monkeypatch.setattr(modes, "tool_is_mode_allowed", lambda n: True)
    from tools import _adapter
    names = {t.info.name for t in _adapter.load_all_livekit_tools()}
    assert names <= set(_adapter.LOCAL_VOICE_CORE_TOOLS)
    # The always-available core must actually be present.
    assert {"terminal", "memory", "clarify"} <= names
    for heavy in ("computer_use", "browser_task", "dispatch_agent",
                  "skills_list", "skill_manage", "agents_list", "webcam",
                  "face_recognition"):
        assert heavy not in names


def test_loader_full_surface_in_cloud_mode(cloud_mode, monkeypatch):
    """CLOUD BYTE-IDENTITY: cloud loads the full, UNFILTERED registry surface.
    Env-robust: headless CI gates out X11/hardware tools (computer_use, webcam,
    …), so we can't assert specific names or a fixed count. Assert instead that
    the local allowlist is not applied and the surface includes tools OUTSIDE
    the local core set — i.e. tools the local filter would have dropped."""
    import pipeline.conversation_modes as modes
    monkeypatch.setattr(modes, "tool_is_mode_allowed", lambda n: True)
    from tools import _adapter
    assert _adapter.local_voice_tool_allowlist() is None  # cloud: no filter
    names = {t.info.name for t in _adapter.load_all_livekit_tools()}
    # Cloud is unfiltered → it loads tools beyond the 6-tool local core.
    assert names - set(_adapter.LOCAL_VOICE_CORE_TOOLS)


# ─────────────────────────────────────────────────────────────────────
# 6. Prompt-side trims (jarvis_agent) + cloud byte-identity
# ─────────────────────────────────────────────────────────────────────

def test_memory_block_truncated_in_local_mode(local_mode, monkeypatch):
    import jarvis_agent as ja
    monkeypatch.setattr(ja, "_MEMORY_AVAILABLE", True)
    monkeypatch.setattr(ja.file_memory, "snapshot_for_prompt",
                        lambda: "M" * 5000)
    block = ja._build_memory_block()
    assert "truncated for local mode" in block
    # 2 (leading \n\n) + cap + marker line.
    assert len(block) < ja.LOCAL_MEMORY_BLOCK_MAX_CHARS + 200


def test_memory_block_byte_identical_in_cloud_mode(cloud_mode, monkeypatch):
    import jarvis_agent as ja
    monkeypatch.setattr(ja, "_MEMORY_AVAILABLE", True)
    monkeypatch.setattr(ja.file_memory, "snapshot_for_prompt",
                        lambda: "M" * 5000)
    assert ja._build_memory_block() == "\n\n" + "M" * 5000


def test_short_memory_block_untouched_in_local_mode(local_mode, monkeypatch):
    """Under the cap, local mode also passes the snapshot through whole."""
    import jarvis_agent as ja
    monkeypatch.setattr(ja, "_MEMORY_AVAILABLE", True)
    monkeypatch.setattr(ja.file_memory, "snapshot_for_prompt", lambda: "tiny")
    assert ja._build_memory_block() == "\n\ntiny"


def test_skill_catalog_dropped_in_local_mode(local_mode):
    import jarvis_agent as ja
    assert ja._build_skill_catalog_block() == ""


def test_skill_catalog_byte_identical_in_cloud_mode(cloud_mode):
    import jarvis_agent as ja
    from pipeline.prompt_builder import build_skill_catalog_block
    from pipeline.skills_loader import SKILLS
    assert ja._build_skill_catalog_block() == build_skill_catalog_block(SKILLS)


def test_local_prompt_pair_none_in_cloud_mode(cloud_mode):
    import jarvis_agent as ja
    assert ja._local_prompt_pair() is None


def test_local_prompt_pair_compact_and_in_character(local_mode):
    import jarvis_agent as ja
    pair = ja._local_prompt_pair()
    assert pair is not None
    soul, instr = pair
    # Identity survives the trim.
    assert "JARVIS" in soul and "sir" in soul  # the "sir" BAN is retained
    assert '"Yes?"' in instr                   # canonical bare-name reply
    assert "STAY-IN-SUPERVISOR" in instr
    # And it IS the compact pair, not the full persona.
    assert len(soul) < len(ja.SOUL)
    assert len(instr) < len(ja.JARVIS_INSTRUCTIONS)
    assert len(soul) + len(instr) < 12000  # ~3k tokens


def test_local_prompt_budget_fits_num_ctx(local_mode, monkeypatch):
    """The assembled local prompt (compact persona + core tool schemas +
    capped memory + runtime/breaker/session margins) must fit ≤ ~10k
    tokens, leaving conversation + output headroom under num_ctx=12288."""
    import jarvis_agent as ja
    from tools.token_estimation import estimate_tokens, estimate_tools
    from tools.registry import discover_builtin_tools, registry
    from tools._adapter import LOCAL_VOICE_CORE_TOOLS

    soul, instr = ja._local_prompt_pair()
    persona_tokens = estimate_tokens(soul + "\n\n" + instr)

    discover_builtin_tools()
    specs = []
    for e in registry.all_entries():
        if e.name in LOCAL_VOICE_CORE_TOOLS:
            specs.append({
                "name": e.name,
                "description": e.description or "",
                "parameters": (e.schema or {}).get("parameters", {}) or {},
            })
    assert specs, "core tools must exist in the registry"
    tool_tokens = estimate_tools(specs)

    # Margins for the volatile blocks: runtime-id (~300) + capped memory
    # (~330) + recent-sessions (~150) + breaker (usually 0) → 1000 is
    # generous.
    total = persona_tokens + tool_tokens + 1000
    assert total <= 10_000, (
        f"local prompt estimate {total} tokens exceeds the 10k target "
        f"(persona={persona_tokens}, tools={tool_tokens})"
    )
    assert total < 12_288


def test_cloud_prompt_state_uses_full_persona(cloud_mode, monkeypatch):
    """CLOUD BYTE-IDENTITY at the assembly level: in cloud mode the stable
    prefix is built from the FULL module-level SOUL + JARVIS_INSTRUCTIONS."""
    import jarvis_agent as ja
    monkeypatch.setattr(ja, "_MEMORY_AVAILABLE", False)
    monkeypatch.setattr(ja, "_build_recent_sessions_block", lambda: "")
    monkeypatch.setattr(ja, "_build_breaker_status_block", lambda *a, **k: "")
    monkeypatch.setattr(ja, "_build_runtime_id_block", lambda _id: "")
    ps = ja._build_initial_prompt_state("deepseek-v4-flash")
    assert ps["stable_prefix"].startswith(ja.SOUL)
    assert ja.JARVIS_INSTRUCTIONS in ps["stable_prefix"]


def test_local_prompt_state_uses_compact_persona(local_mode, monkeypatch):
    import jarvis_agent as ja
    monkeypatch.setattr(ja, "_MEMORY_AVAILABLE", False)
    monkeypatch.setattr(ja, "_build_recent_sessions_block", lambda: "")
    monkeypatch.setattr(ja, "_build_breaker_status_block", lambda *a, **k: "")
    monkeypatch.setattr(ja, "_build_runtime_id_block", lambda _id: "")
    ps = ja._build_initial_prompt_state("ollama/qwen3:4b")
    soul, instr = ja._local_prompt_pair()
    assert ps["stable_prefix"].startswith(soul)
    assert instr in ps["stable_prefix"]
    assert ja.SOUL not in ps["stable_prefix"]  # full persona NOT injected
