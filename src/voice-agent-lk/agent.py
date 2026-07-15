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
    metrics,
)
from livekit.agents.llm import ChatContext, function_tool
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
- You can look things up on the web with the search tool. Use it for current
  events, prices, weather, or any fact you're unsure of — never claim you have
  no internet access or can't check. After searching, answer in one or two
  spoken sentences; don't read out links or lists.
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
# Web search proxies through jarvis-web → SearXNG, which is slower than a memory
# read; give it a longer budget so a normal search isn't cut off mid-turn.
_SEARCH_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=12)

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


def _log_cloud_failure(what: str, err: BaseException) -> None:
    global _cloud_failure_logged
    if not _cloud_failure_logged:
        _cloud_failure_logged = True
        logger.error(
            "[memory] cloud %s failed (%s); using local file — "
            "check JARVIS_PROXY_JWT_SECRET matches the web app",
            what, err,
        )
    else:
        logger.warning("[memory] cloud %s failed (%s); using local file", what, err)


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


def _participant_metadata(participant) -> dict:
    """Parse the participant metadata JSON the token route (POST
    /api/livekit/token) sets: {conversationId?, model?}. conversationId = the
    chat voice was opened from, so the agent seeds THAT conversation (#15);
    model = the user's Settings voice-model pick (#19). Empty dict when absent
    (standalone voice / old client). The phone's conversation UUID IS the cloud
    web.conversations.id."""
    raw = getattr(participant, "metadata", "") or ""
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _meta_str(meta: dict, key: str) -> str | None:
    """A non-empty string field from parsed metadata, else None."""
    v = meta.get(key)
    return v.strip() if isinstance(v, str) and v.strip() else None


async def _load_conversation(user_id: str, conversation_id: str) -> list[dict]:
    """Seed from a SPECIFIC chat (the one the phone opened voice from) so the
    agent continues that conversation with its real history, instead of the
    standalone voice thread. GET /api/voice-memory?conversation_id=… (same
    service auth + ownership scoping as _load_history). Falls back to the
    continuous voice thread on any error so resumed voice is never worse off."""
    try:
        token = proxy_token.mint_from_env(sub="voice-agent")
        url = f"{WEB_URL}/api/voice-memory"
        async with aiohttp.ClientSession(timeout=_MEMORY_HTTP_TIMEOUT) as http:
            async with http.get(
                url,
                params={
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                    "limit": str(MAX_HISTORY_TURNS),
                },
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
        _log_cloud_failure("conversation load", e)
        return await _load_history(user_id)


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


def _build_llm(model_override: str | None = None) -> anthropic.LLM:
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
    # Per-job model: the user's Settings voice pick (forwarded via participant
    # metadata, #19) wins; else the server default. The gateway serves the
    # DeepSeek/Claude/Gemini/OpenAI registry, so any of those ids works here.
    model = model_override or os.environ.get("VOICE_LLM_MODEL", DEFAULT_LLM_MODEL)
    logger.info(
        "[llm] gateway=%s model=%s%s (per-job proxy token minted)",
        base_url, model, " [from phone]" if model_override else "",
    )
    return anthropic.LLM(model=model, api_key=token, base_url=base_url)


async def _drain_pending_writes() -> None:
    """Job-shutdown callback: let in-flight memory posts finish instead of
    being cancelled with the loop (a cancelled post still lands in the JSONL
    fallback via _post_turn's CancelledError handler, but finishing the cloud
    write is strictly better)."""
    if _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


async def _resolve_tts_voice(
    participant, default_voice: str, attempts: int = 15, interval: float = 0.1
) -> str:
    """Voice-mode voice = the phone's selected Edge voice, delivered as the
    `voice` participant attribute (Android LocalParticipant.updateAttributes) —
    no token/route change needed. The client sets it just after connect, so
    poll briefly for it (exits the instant it's present) then fall back to the
    server default. Worst case (old client that never sets it) waits ~1.5 s."""
    for _ in range(attempts):
        v = (participant.attributes or {}).get("voice", "").strip()
        if v:
            return v
        await asyncio.sleep(interval)
    return default_voice


@function_tool
async def search_web(query: str) -> str:
    """Search the web for current, real-time, or factual information — news,
    prices, weather, recent events, or any fact you're unsure of or that may be
    newer than your training. Call this whenever the user asks something that
    needs up-to-date or external information, then answer conversationally from
    the results in one or two spoken sentences (never read out URLs or lists).

    Args:
        query: A concise web search query capturing what to look up.
    """
    try:
        token = proxy_token.mint_from_env(sub="voice-agent")
        url = f"{WEB_URL}/api/voice-search"
        async with aiohttp.ClientSession(timeout=_SEARCH_HTTP_TIMEOUT) as http:
            async with http.get(
                url,
                params={"q": query},
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                if resp.status != 200:
                    logger.warning("[search] HTTP %s for %r", resp.status, query)
                    return "Web search is unavailable right now; answer from what you already know and mention you couldn't check the web."
                data = await resp.json()
        hits = data.get("hits", []) if isinstance(data, dict) else []
        if not hits:
            return f"No web results for '{query}'."
        # Title + snippet only — voice-friendly context for the model to
        # summarize; URLs are omitted so they're never spoken aloud.
        lines = []
        for h in hits[:6]:
            title = (h.get("title") or "").strip()
            snippet = (h.get("snippet") or "").strip()
            if not title:
                continue
            lines.append(f"- {title}: {snippet}" if snippet else f"- {title}")
        logger.info("[search] %d result(s) for %r", len(lines), query)
        return f"Web results for '{query}':\n" + "\n".join(lines)
    except Exception as e:
        logger.warning("[search] failed for %r: %s", query, e)
        return "Web search failed; answer from what you know and say you couldn't search the web."


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    ctx.add_shutdown_callback(_drain_pending_writes)
    # Identity from the signed LiveKit token (route.ts sets identity=userId),
    # not the room-name string — so memory can't be keyed on another user even
    # if the room-naming convention ever changes. Room parse is only a fallback.
    participant = await ctx.wait_for_participant()
    user_id = participant.identity or _user_id_from_room(ctx.room.name)
    # Metadata from the token route: the chat voice was opened from (#15) and
    # the user's Settings voice-model pick (#19).
    meta = _participant_metadata(participant)
    conversation_id = _meta_str(meta, "conversationId")
    model_override = _meta_str(meta, "model")
    # If voice was opened from an existing chat, seed THAT conversation's history
    # so the agent has its context; otherwise the continuous voice thread. (#15)
    if user_id and conversation_id:
        history = await _load_conversation(user_id, conversation_id)
    elif user_id:
        history = await _load_history(user_id)
    else:
        history = []
    # The phone selects the voice-mode voice (an Edge voice id) and sends it as
    # the `voice` participant attribute (LocalParticipant.updateAttributes on the
    # Android client) — no token/route change needed. It's set right after the
    # client connects, so it may land a beat after wait_for_participant; poll
    # briefly (the history fetch above already gave it time) then fall back to
    # the server default.
    default_voice = os.environ.get("VOICE_TTS_VOICE", tts_edge.DEFAULT_VOICE)
    tts_voice = await _resolve_tts_voice(participant, default_voice)
    logger.info(
        "[job] joined room %s (user=%s, %d prior turn(s)%s, voice=%s%s)",
        ctx.room.name, user_id, len(history),
        f", conv={conversation_id}" if conversation_id else "",
        tts_voice,
        "" if tts_voice == default_voice else " [from phone]",
    )

    # Replay recent memory into the chat context so the agent recalls past calls.
    chat_ctx = ChatContext.empty()
    for turn in history:
        chat_ctx.add_message(role=turn["role"], content=turn["text"])

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        # Non-streaming STT: AgentSession auto-wraps it in a
        # StreamAdapter with the VAD above.
        stt=ctx.proc.userdata["stt"],
        llm=_build_llm(model_override),
        tts=tts_edge.EdgeTTS(voice=tts_voice),
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
            instructions=INSTRUCTIONS,
            chat_ctx=chat_ctx,
            tools=[search_web],  # research in voice mode (#16)
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
