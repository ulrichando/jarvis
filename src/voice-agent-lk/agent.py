"""JARVIS headless realtime voice agent (LiveKit worker, VPS/CPU).

P2 of the self-hosted LiveKit voice pipeline (see
docs/superpowers/specs/2026-07-12-livekit-realtime-voice-design.md in
jarvis-android). Per turn: Silero VAD → faster-whisper (CPU int8) →
Claude via the JARVIS gateway (Anthropic-compatible /v1/messages) →
Edge TTS. Barge-in is the framework's VAD interruption — the phone
client's WebRTC AEC keeps the agent's own TTS out of the mic path.

Dispatch: automatic (no agent_name) — this LiveKit deployment exists
only for JARVIS voice, so the worker joins every room the token
endpoint creates.

Env (see .env.example):
  LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET — LiveKit server
  JARVIS_PROXY_JWT_SECRET — mints per-job gateway bearer tokens
  JARVIS_GATEWAY_URL      — default http://localhost:4000
  JARVIS_WEB_URL          — web app base for cloud voice memory
                            (default http://127.0.0.1:80)
  VOICE_LLM_MODEL         — default claude-sonnet-4-6 (gateway registry id)
  VOICE_STT_MODEL         — default base.en (faster-whisper, cpu/int8)
  VOICE_TTS_VOICE         — default en-GB-RyanNeural (matches Android)
  VOICE_GREETING          — optional spoken line when a user joins
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

import aiohttp

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    function_tool,
    metrics,
)
from livekit.agents.llm import ChatContext
from livekit.plugins import anthropic, silero

import proxy_token
import stt_local
import tts_edge

logger = logging.getLogger("voice-agent")

DEFAULT_GATEWAY_URL = "http://localhost:4000"
DEFAULT_LLM_MODEL = "claude-sonnet-4-6"  # verified in the gateway model registry

INSTRUCTIONS = """\
You are JARVIS, a voice assistant. You are talking with the user over a
realtime voice call, so:
- Keep replies short and conversational — one to three sentences unless
  the user clearly asks for detail.
- Plain spoken prose only: no markdown, no bullet lists, no code blocks,
  no emojis. Spell out anything that must be read aloud.
- If a transcription seems garbled or cut off, ask a brief clarifying
  question instead of guessing.
- Earlier messages in this conversation may be transcripts replayed from
  the user's previous calls with you — that is your long-term memory of
  this user. Trust it and refer back to it naturally when relevant
  instead of asking for information already given.
- Never tell the user you cannot remember previous sessions or that each
  conversation starts fresh — you do carry memory across calls. If a
  detail is genuinely not in your memory, just say you don't have that
  particular detail and move on.
"""

# ── Cross-session memory ────────────────────────────────────────────────────
# Each voice session is a fresh AgentSession, so without this the agent forgets
# everything between calls. We persist finalized turns per user (the userId is
# embedded in the room name) and replay the recent tail into the chat context on
# join. Primary store is the web app's Postgres via /api/voice-memory (turns
# land in the user's single continuous 'voice' conversation, so they also
# render in the web chat UI). The append-only JSONL under a host-mounted
# volume is kept as the offline fallback when the web API is unreachable.
MEMORY_DIR = Path(os.environ.get("VOICE_MEMORY_DIR", "/data/memory"))
MAX_HISTORY_TURNS = int(os.environ.get("VOICE_MEMORY_TURNS", "40"))
WEB_URL = os.environ.get("JARVIS_WEB_URL", "http://127.0.0.1:80").rstrip("/")
_MEMORY_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=5)

# Strong refs to in-flight fire-and-forget memory writes (asyncio only keeps
# weak refs to tasks — without this a pending POST can be garbage-collected).
_pending: set[asyncio.Task] = set()

# Serializes cloud turn writes so a user/assistant pair can't commit inverted
# when their fire-and-forget tasks interleave (asyncio.Lock wakes waiters
# FIFO, so posts land in emission order).
# ponytail: global lock — serializes across ALL users; make it per-user if
# multi-user throughput ever matters.
_post_lock = asyncio.Lock()

# First cloud-memory failure per process logs at ERROR (a persistent failure
# is usually config, e.g. JARVIS_PROXY_JWT_SECRET drift); repeats at WARNING.
_cloud_failure_logged = False


def _log_cloud_failure(
    what: str, err: BaseException, fallback: str = "using local file"
) -> None:
    global _cloud_failure_logged
    if not _cloud_failure_logged:
        _cloud_failure_logged = True
        logger.error(
            "[memory] cloud %s failed (%s); %s — "
            "check JARVIS_PROXY_JWT_SECRET matches the web app",
            what, err, fallback,
        )
    else:
        logger.warning("[memory] cloud %s failed (%s); %s", what, err, fallback)


def _user_id_from_room(room_name: str) -> str | None:
    """Rooms are `voice-<userId>-<uuid8>`; the userId is itself a hyphenated
    UUID, so strip the `voice-` prefix and the trailing `-<uuid8>` suffix."""
    if not room_name.startswith("voice-"):
        return None
    rest = room_name[len("voice-") :]
    uid, _, _suffix = rest.rpartition("-")
    return uid or None


def _memory_file(user_id: str) -> Path:
    safe = "".join(c for c in user_id if c.isalnum() or c in "-_")
    return MEMORY_DIR / f"{safe}.jsonl"


def _load_history_file(user_id: str) -> list[dict]:
    """Offline fallback: read the recent tail from the local JSONL store."""
    f = _memory_file(user_id)
    if not f.exists():
        return []
    turns: list[dict] = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("role") in ("user", "assistant") and obj.get("text"):
            turns.append(obj)
    return turns[-MAX_HISTORY_TURNS:]


def _append_turn_file(user_id: str, role: str, text: str) -> None:
    """Offline fallback: append one turn to the local JSONL store."""
    try:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        with _memory_file(user_id).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"role": role, "text": text}, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("[memory] append failed: %s", e)


async def _load_history(user_id: str) -> list[dict]:
    """Load the user's recent voice turns from the web app's cloud store.

    GET {JARVIS_WEB_URL}/api/voice-memory, authed with a service proxy JWT
    (sub="voice-agent"). On any error/timeout, fall back to the local JSONL.
    """
    try:
        token = proxy_token.mint_from_env(sub="voice-agent")
        url = f"{WEB_URL}/api/voice-memory"
        async with aiohttp.ClientSession(timeout=_MEMORY_HTTP_TIMEOUT) as http:
            async with http.get(
                url,
                params={"user_id": user_id, "limit": str(MAX_HISTORY_TURNS)},
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                data = await resp.json()
        turns = [
            {"role": t["role"], "text": t["text"]}
            for t in data.get("turns", [])
            if t.get("role") in ("user", "assistant") and t.get("text")
        ]
        return turns[-MAX_HISTORY_TURNS:]
    except Exception as e:
        _log_cloud_failure("history load", e)
        return _load_history_file(user_id)


async def _post_turn(user_id: str, role: str, text: str) -> None:
    """Write one finalized turn to the cloud store; JSONL fallback on failure."""
    try:
        async with _post_lock:  # commit turns in emission order
            token = proxy_token.mint_from_env(sub="voice-agent")
            url = f"{WEB_URL}/api/voice-memory"
            async with aiohttp.ClientSession(timeout=_MEMORY_HTTP_TIMEOUT) as http:
                async with http.post(
                    url,
                    json={"role": role, "text": text, "user_id": user_id},
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status}")
    except asyncio.CancelledError:
        # Cancelled mid-write (job shutdown) — land the turn in the JSONL
        # fallback rather than dropping it, then let cancellation propagate.
        _append_turn_file(user_id, role, text)
        raise
    except Exception as e:
        _log_cloud_failure("append", e)
        _append_turn_file(user_id, role, text)


def _append_turn(user_id: str, role: str, text: str) -> None:
    """Synchronous shim for livekit event handlers (the EventEmitter rejects
    coroutine callbacks): fire the cloud write off-loop, keep a strong ref."""
    try:
        task = asyncio.create_task(_post_turn(user_id, role, text))
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    except RuntimeError:
        # No running loop (shouldn't happen inside session callbacks) —
        # persist locally rather than dropping the turn.
        _append_turn_file(user_id, role, text)


# ── Curated memory (USER / MEMORY / PROCEDURE stores) ──────────────────────
# Cloud port of the local file-backed curated-memory stack
# (src/voice-agent/pipeline/file_memory.py + tools/memory.py). The stores live
# in the web app's Postgres behind /api/memory; the full contract (char
# budgets, overflow rules, injection scan, dedup, rendering) is enforced
# server-side. This side only (a) fetches the rendered snapshot ONCE at
# session start and freezes it into the system prompt (prompt-cache
# stability — a mid-session write persists immediately but only appears
# next session, matching the local FROZEN-snapshot design), and (b) exposes
# the 'memory' function tool that POSTs deliberate writes to the same API.

MEMORY_TOOL_SCHEMA = {
    "name": "memory",
    "description": (
        "Save or update durable information that survives across sessions. "
        "Memory is injected into your system prompt at the start of every "
        "session, so keep entries compact and focused on facts that will "
        "still matter later.\n\n"
        "WHEN TO SAVE (proactively — don't wait to be asked):\n"
        "- The user corrects you or says 'remember this' / 'don't do that again'\n"
        "- They share a preference, habit, or personal detail (name, role, "
        "timezone, how they like replies)\n"
        "- You learn a stable fact about their work or environment that will be "
        "useful again\n"
        "- They ask you to 'save this process' or 'remember how to X' — "
        "store as target='procedure' with a kebab-case name and numbered steps\n\n"
        "THREE STORES (the 'target'):\n"
        "- 'user': who the user is — role, background, preferences, "
        "communication style, pet peeves.\n"
        "- 'memory': your own notes — environment facts, project "
        "conventions, tool quirks, lessons learned.\n"
        "- 'procedure': named multi-step processes the user "
        "wants to invoke later. Requires 'name' (kebab-case, e.g. "
        "'deploy-app') and 'content' as a numbered step list.\n\n"
        "ACTIONS:\n"
        "- add     — store a new entry (needs 'content'; procedure also needs 'name').\n"
        "- replace — update an existing entry; 'old_text' is a short unique "
        "substring identifying it, 'content' is the new text.\n"
        "- remove  — delete an entry; 'old_text' identifies it.\n"
        "- read    — list the live entries in a store (use to audit before "
        "editing).\n\n"
        "DO save before replying when the user states something durable about "
        "their life or work — silent, no need to announce it.\n"
        "DON'T save: code patterns, file paths, git history, debug recipes, "
        "anything already in your instructions, ephemeral state ('I'm hungry', "
        "'working on X right now'), or credentials. Write plain assertions, "
        "never narration ('The user is asking about…')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove", "read"],
                "description": "What to do.",
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user", "procedure"],
                "description": "Which store: 'user' for the user's profile, 'memory' for your own notes, 'procedure' for named multi-step processes.",
            },
            "content": {
                "type": "string",
                "description": "The entry text. Required for 'add' and 'replace'. For target='procedure', supply a numbered step list (e.g. '1. step one\\n2. step two').",
            },
            "old_text": {
                "type": "string",
                "description": "Short unique substring identifying the entry to replace or remove.",
            },
            "name": {
                "type": "string",
                "description": "Kebab-case identifier (e.g. 'deploy-app'). Required when target='procedure' and action='add'.",
            },
        },
        "required": ["action", "target"],
        "additionalProperties": False,
    },
}


async def _load_memory_snapshot(user_id: str) -> str:
    """Fetch the rendered curated-memory snapshot (the ════-header USER /
    MEMORY / PROCEDURE blocks) from the web app's cloud store.

    GET {JARVIS_WEB_URL}/api/memory, authed with a service proxy JWT
    (sub="voice-agent"). Fail-open: on any error/timeout return "" — the
    session just runs without curated memory (there is no local fallback
    for this store; the contract lives server-side)."""
    try:
        token = proxy_token.mint_from_env(sub="voice-agent")
        url = f"{WEB_URL}/api/memory"
        async with aiohttp.ClientSession(timeout=_MEMORY_HTTP_TIMEOUT) as http:
            async with http.get(
                url,
                params={"user_id": user_id},
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                data = await resp.json()
        return str(data.get("snapshot_text") or "")
    except Exception as e:
        _log_cloud_failure(
            "memory snapshot load", e, fallback="session runs without curated memory"
        )
        return ""


def _build_memory_tool(user_id: str):
    """Build the per-session 'memory' function tool, with the user's id bound
    in the closure (the LLM never supplies it — it can't write to another
    user's stores). Returns the /api/memory tool JSON verbatim so the model
    sees {success,target,entries,entry_count,usage,message|error} and can
    self-correct on a char-budget rejection."""

    @function_tool(raw_schema=MEMORY_TOOL_SCHEMA)
    async def memory(raw_arguments: dict[str, object]) -> str:
        payload: dict[str, object] = {"user_id": user_id}
        for key in ("action", "target", "content", "old_text", "name"):
            value = raw_arguments.get(key)
            if value is not None:
                payload[key] = value
        try:
            token = proxy_token.mint_from_env(sub="voice-agent")
            url = f"{WEB_URL}/api/memory"
            async with aiohttp.ClientSession(timeout=_MEMORY_HTTP_TIMEOUT) as http:
                async with http.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    body = await resp.text()
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status}: {body[:200]}")
                    return body
        except Exception as e:
            _log_cloud_failure("memory write", e, fallback="write NOT persisted")
            return json.dumps(
                {"success": False, "error": f"memory store unreachable: {e}"},
                ensure_ascii=False,
            )

    return memory


def prewarm(proc: JobProcess) -> None:
    """Load Silero VAD + the whisper model before the first job."""
    proc.userdata["vad"] = silero.VAD.load(
        # Slightly longer than the default 0.55s — CPU whisper is
        # finals-only, so premature end-of-speech costs a whole re-turn.
        min_silence_duration=0.6,
    )
    stt_inst = stt_local.build_stt()
    # Load the ctranslate2 model now so the first utterance doesn't pay
    # the load cost (the weights are baked into the docker image).
    stt_inst.preload()
    proc.userdata["stt"] = stt_inst
    logger.info("[prewarm] VAD + STT ready")


def _build_llm() -> anthropic.LLM:
    """Claude through the JARVIS gateway, authed with a freshly minted
    proxy JWT.

    The gateway routes claude* models via its Anthropic-compatible
    /v1/messages endpoint (it rejects them on /v1/chat/completions), so
    the anthropic plugin is the right client. The anthropic SDK sends the
    credential as `x-api-key`; the hub accepts JWT-shaped x-api-key
    values as bearer credentials (src/cli/src/proxy/server.ts).
    """
    token = proxy_token.mint_from_env(sub="voice-agent")
    base_url = os.environ.get("JARVIS_GATEWAY_URL", DEFAULT_GATEWAY_URL).rstrip("/")
    model = os.environ.get("VOICE_LLM_MODEL", DEFAULT_LLM_MODEL)
    logger.info("[llm] gateway=%s model=%s (per-job proxy token minted)", base_url, model)
    return anthropic.LLM(model=model, api_key=token, base_url=base_url)


async def _drain_pending_writes() -> None:
    """Job-shutdown callback: let in-flight memory posts finish instead of
    being cancelled with the loop (a cancelled post still lands in the JSONL
    fallback via _post_turn's CancelledError handler, but finishing the cloud
    write is strictly better)."""
    if _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    ctx.add_shutdown_callback(_drain_pending_writes)
    # Identity from the signed LiveKit token (route.ts sets identity=userId),
    # not the room-name string — so memory can't be keyed on another user even
    # if the room-naming convention ever changes. Room parse is only a fallback.
    participant = await ctx.wait_for_participant()
    user_id = participant.identity or _user_id_from_room(ctx.room.name)
    history = (await _load_history(user_id)) if user_id else []
    # Curated memory: fetched ONCE here and frozen into the system prompt for
    # the whole session (prompt-cache stability). Mid-session `memory` tool
    # writes persist to the cloud store immediately but only show up in the
    # NEXT session's snapshot — same design as the local file-backed stack.
    memory_snapshot = (await _load_memory_snapshot(user_id)) if user_id else ""
    logger.info(
        "[job] joined room %s (user=%s, %d prior turn(s), %d memory chars)",
        ctx.room.name, user_id, len(history), len(memory_snapshot),
    )
    instructions = INSTRUCTIONS
    if memory_snapshot:
        instructions = f"{INSTRUCTIONS}\n{memory_snapshot}\n"

    # Replay recent memory into the chat context so the agent recalls past calls.
    chat_ctx = ChatContext.empty()
    for turn in history:
        chat_ctx.add_message(role=turn["role"], content=turn["text"])

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        # Non-streaming STT: AgentSession auto-wraps it in a
        # StreamAdapter with the VAD above.
        stt=ctx.proc.userdata["stt"],
        llm=_build_llm(),
        tts=tts_edge.EdgeTTS(
            voice=os.environ.get("VOICE_TTS_VOICE", tts_edge.DEFAULT_VOICE)
        ),
        turn_handling={
            "interruption": {
                "enabled": True,
                # Explicit VAD interruption: auto-detect would probe
                # LiveKit Cloud's adaptive-interruption gateway first,
                # which self-hosted LiveKit doesn't have.
                "mode": "vad",
                # min_words > 0 needs STT partials to fire; our whisper
                # rung is finals-only, so gate on VAD duration alone.
                # The phone's WebRTC AEC keeps agent echo out of the mic.
                "min_duration": 0.55,
                "min_words": 0,
            },
        },
    )

    # ── observability: make each pipeline stage visible in docker logs ──
    @session.on("user_input_transcribed")
    def _on_transcript(ev) -> None:
        if ev.is_final:
            logger.info("[stt] user said: %r", ev.transcript)
            if user_id and ev.transcript:
                _append_turn(user_id, "user", ev.transcript)

    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        item = ev.item
        if getattr(item, "role", None) == "assistant":
            logger.info("[llm] agent replied: %r", item.text_content)
            # Skip barge-in fragments (interrupted=True): persisting cut-off
            # partials like "Your" would pollute the replayed memory; the
            # follow-up full reply is the turn worth remembering.
            if (
                user_id
                and item.text_content
                and not getattr(item, "interrupted", False)
            ):
                _append_turn(user_id, "assistant", item.text_content)

    @session.on("agent_state_changed")
    def _on_state(ev) -> None:
        logger.info("[state] %s -> %s", ev.old_state, ev.new_state)

    @session.on("metrics_collected")
    def _on_metrics(ev) -> None:
        metrics.log_metrics(ev.metrics)

    await session.start(
        agent=Agent(
            instructions=instructions,
            chat_ctx=chat_ctx,
            # The memory tool is keyed on user_id in its closure — without an
            # identity there is nothing safe to bind writes to, so omit it.
            tools=[_build_memory_tool(user_id)] if user_id else [],
        ),
        room=ctx.room,
    )

    greeting = os.environ.get("VOICE_GREETING", "").strip()
    if greeting:
        session.say(greeting)


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            # 4 vCPU / 8 GB box shared with the web stack — keep one warm
            # process, don't fan out.
            num_idle_processes=1,
        )
    )
