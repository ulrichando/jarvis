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
  VOICE_STT_STREAMING     — 1 (default): live type-as-you-speak interim
                            transcripts; 0 = proven finals-only path
  VOICE_STT_INTERIM_MODEL / _INTERVAL / _WINDOW — interim decode knobs
                            (see stt_local.build_stt)
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
# Web search proxies through jarvis-web → SearXNG, slower than a memory read;
# give it a longer budget so a normal search isn't cut off mid-turn.
_SEARCH_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=12)

# Strong refs to in-flight fire-and-forget memory writes (asyncio only keeps
# weak refs to tasks — without this a pending POST can be garbage-collected).
_pending: set[asyncio.Task] = set()

# The active room, set at entrypoint start so module-level tools (search_web)
# can push tool-status events to the phone. None until a job connects.
_active_room = None


def _publish_tool_event(tool: str, status: str, query: str = "", sources=None) -> None:
    """Fire-and-forget: tell the phone a tool started/finished (LiveKit data,
    topic `agent.tool`) so voice mode can show a "Searching the web" indicator
    like text chat, instead of a silent pause while the agent researches. On the
    "done" event `sources` carries the [{title, url}] hits so the phone can show
    the same source bubbles as a chat search."""
    room = _active_room
    if room is None:
        return
    try:
        payload = json.dumps(
            {"tool": tool, "status": status, "query": query, "sources": sources or []}
        ).encode("utf-8")
    except Exception:
        return
    task = asyncio.create_task(
        room.local_participant.publish_data(payload, reliable=True, topic="agent.tool")
    )
    _pending.add(task)
    task.add_done_callback(_pending.discard)

# Serializes cloud turn writes so a user/assistant pair can't commit inverted
# when their fire-and-forget tasks interleave (asyncio.Lock wakes waiters
# FIFO, so posts land in emission order).
# ponytail: global lock — serializes across ALL users; make it per-user if
# multi-user throughput ever matters.
_post_lock = asyncio.Lock()

# Separate lock for the semantic-recall (honcho) turn sync: keeps honcho
# ingestion in emission order without letting a slow sync delay the
# primary voice-memory writes behind _post_lock.
_sync_lock = asyncio.Lock()

# The sync runs fire-and-forget OFF the turn path, so it can afford more
# room than the 5s turn-adjacent _MEMORY_HTTP_TIMEOUT: on cold first contact
# the web route's worst case is 4 honcho ensure POSTs (5s server-side timeout
# each) + add_messages (10s) ≈ 30s even when the server ultimately succeeds.
# 35s covers that whole budget — a shorter client timeout (the original 15s)
# aborted while the server was still succeeding and logged a false
# "sync failed". A longer timeout costs nothing here: the task is off the
# turn path and serialized only against other syncs via _sync_lock.
_SYNC_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=35)

# First recall-sync failure per process logs at WARNING, repeats at DEBUG —
# quieter than the voice-memory path because sync is a best-effort
# enrichment layer (the web route fail-softs when honcho isn't deployed;
# a hard failure here usually means the web app predates /api/recall).
_recall_sync_failure_logged = False

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


def _read_history_file(user_id: str) -> list[dict]:
    """Every valid turn in the local JSONL store, in file order."""
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
    return turns


def _load_history_file(user_id: str) -> list[dict]:
    """Offline fallback: read the recent tail from the local JSONL store."""
    return _read_history_file(user_id)[-MAX_HISTORY_TURNS:]


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

    Upgrade path: a successful GET that returns ZERO turns while a legacy
    JSONL exists means this box just cut over from the JSONL store — serve
    the JSONL tail for this session and replay the whole file into the cloud
    store in the background (one-time: once the cloud store holds any turn,
    this branch never fires again). Without this, the first empty-200 from
    the new store would permanently orphan the pre-cutover history with no
    error anywhere.
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
        if turns:
            return turns[-MAX_HISTORY_TURNS:]
        legacy = _read_history_file(user_id)  # full file, not just the tail
        if legacy:
            logger.info(
                "[memory] cloud store empty but legacy JSONL has %d turn(s); "
                "serving the JSONL tail and backfilling the cloud store",
                len(legacy),
            )
            _start_history_backfill(user_id, legacy)
            return legacy[-MAX_HISTORY_TURNS:]
        return []
    except Exception as e:
        _log_cloud_failure("history load", e)
        return _load_history_file(user_id)


async def _backfill_history(user_id: str, turns: list[dict]) -> None:
    """One-time migration: replay a legacy JSONL history into the cloud store.

    Holds _post_lock for the WHOLE batch so the replayed history commits
    before any live turn from the session that triggered it (asyncio.Lock is
    FIFO; this task is created at session load, before any turn can fire).
    POSTs directly instead of via _post_turn — its JSONL-fallback-on-failure
    would re-append lines the file already holds. Aborts on the first
    failure: the common failure (first POST) leaves the cloud store still
    empty, so the untouched JSONL retriggers the backfill next session. The
    JSONL itself is never modified — it stays the offline fallback store.
    """
    posted = 0
    try:
        async with _post_lock:
            token = proxy_token.mint_from_env(sub="voice-agent")
            url = f"{WEB_URL}/api/voice-memory"
            async with aiohttp.ClientSession(timeout=_MEMORY_HTTP_TIMEOUT) as http:
                for turn in turns:
                    async with http.post(
                        url,
                        json={
                            "role": turn["role"],
                            "text": turn["text"],
                            "user_id": user_id,
                        },
                        headers={"Authorization": f"Bearer {token}"},
                    ) as resp:
                        if resp.status != 200:
                            raise RuntimeError(f"HTTP {resp.status}")
                    posted += 1
        logger.info(
            "[memory] backfilled %d legacy JSONL turn(s) into the cloud store",
            posted,
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(
            "[memory] legacy JSONL backfill stopped at %d/%d turn(s) (%s); "
            "JSONL kept — retries next session only if the cloud store is "
            "still empty",
            posted, len(turns), e,
        )


def _start_history_backfill(user_id: str, turns: list[dict]) -> None:
    """Fire the backfill off the load path, strong-ref'd like other writes."""
    task = asyncio.create_task(_backfill_history(user_id, turns))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


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
    _publish_tool_event("web_search", "searching", query)
    collected_sources = []
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
        lines = []
        for h in hits[:6]:
            title = (h.get("title") or "").strip()
            snippet = (h.get("snippet") or "").strip()
            src_url = (h.get("url") or "").strip()
            if not title:
                continue
            if src_url:
                collected_sources.append({"title": title, "url": src_url})
            lines.append(f"- {title}: {snippet}" if snippet else f"- {title}")
        logger.info("[search] %d result(s) for %r", len(lines), query)
        return f"Web results for '{query}':\n" + "\n".join(lines)
    except Exception as e:
        logger.warning("[search] failed for %r: %s", query, e)
        return "Web search failed; answer from what you know and say you couldn't search the web."
    finally:
        _publish_tool_event("web_search", "done", query, collected_sources)


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


async def _sync_recall_turn(
    user_id: str, role: str, text: str, session_id: str
) -> None:
    """Best-effort: mirror one finalized turn into the semantic-recall store
    (honcho, via the web app's POST /api/recall {mode:"sync"}).

    Deliberately independent of the /api/voice-memory write and its JSONL
    fallback — losing a sync only degrades deep recall, never the turn
    itself. Never blocks or fails a turn: it runs fire-and-forget off the
    turn path and swallows every error (except CancelledError on job
    shutdown, where there is nothing to fall back to)."""
    global _recall_sync_failure_logged
    try:
        async with _sync_lock:  # honcho ingests turns in emission order
            token = proxy_token.mint_from_env(sub="voice-agent")
            url = f"{WEB_URL}/api/recall"
            async with aiohttp.ClientSession(timeout=_SYNC_HTTP_TIMEOUT) as http:
                async with http.post(
                    url,
                    json={
                        "mode": "sync",
                        "user_id": user_id,
                        "role": role,
                        "text": text,
                        "session_id": session_id,
                    },
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status}")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        if not _recall_sync_failure_logged:
            _recall_sync_failure_logged = True
            logger.warning(
                "[recall] turn sync failed (%s); semantic recall will lag "
                "until /api/recall is reachable",
                e,
            )
        else:
            logger.debug("[recall] turn sync failed: %s", e)


def _append_turn(
    user_id: str, role: str, text: str, session_id: str | None = None
) -> None:
    """Synchronous shim for livekit event handlers (the EventEmitter rejects
    coroutine callbacks): fire the cloud write off-loop, keep a strong ref.
    When session_id is given, also mirror the turn into the semantic-recall
    store (honcho) as an independent fire-and-forget task."""
    try:
        task = asyncio.create_task(_post_turn(user_id, role, text))
        _pending.add(task)
        task.add_done_callback(_pending.discard)
        if session_id:
            sync = asyncio.create_task(
                _sync_recall_turn(user_id, role, text, session_id)
            )
            _pending.add(sync)
            sync.add_done_callback(_pending.discard)
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


# ── Semantic recall (honcho, via the web app's /api/recall) ────────────────
# Deep cross-session recall over EVERYTHING the user has ever said, beyond
# the MAX_HISTORY_TURNS tail replayed into chat_ctx and the curated snapshot.
# The web app is the only thing that talks to honcho (co-located on the VPS,
# internal network); this side just calls POST /api/recall. Two halves:
#   query — the 'recall' function tool below (LLM-CHOSEN, multi-second
#           dialectic call; NOT auto-fired per turn)
#   sync  — _sync_recall_turn above (fire-and-forget per finalized turn so
#           honcho's deriver ingests the conversation off the turn path)

# Honcho's dialectic endpoint is a multi-second LLM call — give it far more
# room than the 5s memory timeout, but still bound it so a wedged recall
# can't hold the tool call open forever.
_RECALL_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30)
_RECALL_MAX_CHARS = 8000  # web side caps too; belt and suspenders

RECALL_TOOL_SCHEMA = {
    "name": "recall",
    "description": (
        "Recall facts or context from your earlier conversations with this "
        "user by natural-language query. Searches the user's full "
        "cross-session history — far beyond the recent transcript replayed "
        "into this conversation.\n\n"
        "USE WHEN the user references something from a past conversation "
        "that is not in your current context ('what did I tell you about "
        "X', 'remember when we discussed Y', 'what was that restaurant I "
        "mentioned'), or when a durable detail about the user would help "
        "and you don't already have it.\n\n"
        "DON'T use it for things already visible in this conversation or "
        "in your memory snapshot, and don't fire it speculatively on every "
        "turn — it takes a few seconds. Ask a focused question, e.g. "
        "'What has the user said about their sister?'"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Natural-language question about the user's past "
                    "conversations, e.g. 'What projects has the user "
                    "mentioned working on?'"
                ),
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}


def _build_recall_tool(user_id: str):
    """Build the per-session 'recall' function tool, with the user's id bound
    in the closure (the LLM never supplies it — it can't query another
    user's history). Fail-soft: any error returns a spoken-prose-safe
    string so the turn never breaks when honcho/the web app is down."""

    @function_tool(raw_schema=RECALL_TOOL_SCHEMA)
    async def recall(raw_arguments: dict[str, object]) -> str:
        query = str(raw_arguments.get("query") or "").strip()
        if not query:
            return "recall needs a non-empty query."
        try:
            token = proxy_token.mint_from_env(sub="voice-agent")
            url = f"{WEB_URL}/api/recall"
            async with aiohttp.ClientSession(timeout=_RECALL_HTTP_TIMEOUT) as http:
                async with http.post(
                    url,
                    json={"mode": "query", "user_id": user_id, "query": query},
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status}")
                    data = await resp.json()
            text = str(data.get("text") or "").strip()
            if not text:
                return (
                    "No stored context matched that query. Answer from what "
                    "you already know, without claiming you can't remember."
                )
            return text[:_RECALL_MAX_CHARS]
        except Exception as e:
            logger.warning("[recall] query failed: %s", e)
            return (
                "Recall is unavailable right now. Answer from the current "
                "conversation and your memory snapshot."
            )

    return recall


def prewarm(proc: JobProcess) -> None:
    """Load Silero VAD + the whisper model before the first job."""
    proc.userdata["vad"] = silero.VAD.load(
        # Endpointing: wait ~0.8s of silence before finalizing so a natural
        # mid-sentence pause doesn't split one utterance into several chat
        # turns (0.6 chopped continuous speech into fragments). CPU whisper is
        # finals-only, so premature end-of-speech also costs a whole re-turn.
        min_silence_duration=0.5,
        # Require more confident speech to trigger at all — a hands-free mic in
        # a noisy room otherwise wakes Silero on background audio (default 0.5),
        # which then hands whisper non-speech to hallucinate on.
        activation_threshold=0.6,
        # Ignore sub-250ms blips (a click, a tap, a single cough).
        min_speech_duration=0.25,
    )
    # The streaming STT (VOICE_STT_STREAMING=1, default) opens its own
    # Silero stream off this same VAD instance for utterance boundaries —
    # silero VADStreams share one ONNX session, each with private state.
    stt_inst = stt_local.build_stt(vad=proc.userdata["vad"])
    # Load the ctranslate2 model(s) now so the first utterance doesn't pay
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


async def _resolve_tts_attrs(
    room, participant, default_voice: str, default_rate: str = "+0%", timeout: float = 3.0,
):
    """Voice-mode voice AND speaking rate = the phone's selected Edge voice and
    pace, delivered together as the `voice` and `rate` participant attributes
    (Android LocalParticipant.updateAttributes) — no token/route change needed.
    Caught via the room's attribute-changed event (the participant object handed
    back by wait_for_participant does not reflect later runtime updates), with a
    check of whatever is already set. Falls back to server defaults after
    `timeout`. Returns (voice, rate)."""
    def _attrs_of(p):
        return dict(getattr(p, "attributes", None) or {})

    def _pick(attrs):
        return (attrs.get("voice") or "").strip(), (attrs.get("rate") or "").strip()

    logger.info("[job] initial attrs=%s", _attrs_of(participant))
    v, r = _pick(_attrs_of(participant))
    if v:
        return v, (r or default_rate)

    fut = asyncio.get_event_loop().create_future()

    def _on_changed(changed_attributes, changed_participant):
        try:
            # voice + rate are set in one updateAttributes call, so merge the
            # event payload over whatever is already on the participant.
            merged = {**_attrs_of(changed_participant), **dict(changed_attributes or {})}
            nv, nr = _pick(merged)
            logger.info(
                "[job] attrs changed (%s): voice=%s rate=%s",
                getattr(changed_participant, "identity", "?"), nv, nr,
            )
            if nv and not fut.done():
                fut.set_result((nv, nr or default_rate))
        except Exception as e:  # never let a bad event kill the session
            logger.warning("[job] attr handler error: %s", e)

    room.on("participant_attributes_changed", _on_changed)
    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        return default_voice, default_rate


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    # Expose the room to module-level tools (search_web) so they can push
    # tool-status events (e.g. web search) to the phone for a live indicator.
    global _active_room
    _active_room = ctx.room
    ctx.add_shutdown_callback(_drain_pending_writes)
    # Identity from the signed LiveKit token (route.ts sets identity=userId),
    # not the room-name string — so memory can't be keyed on another user even
    # if the room-naming convention ever changes. Room parse is only a fallback.
    participant = await ctx.wait_for_participant()
    user_id = participant.identity or _user_id_from_room(ctx.room.name)
    # Metadata from the token route: the chat voice was opened from (#15) and the
    # user's Settings voice-model pick (#19).
    meta = _participant_metadata(participant)
    conversation_id = _meta_str(meta, "conversationId")
    model_override = _meta_str(meta, "model")
    # Resumed from a chat → seed THAT conversation's history; else the continuous
    # voice thread. (#15)
    if user_id and conversation_id:
        history = await _load_conversation(user_id, conversation_id)
    elif user_id:
        history = await _load_history(user_id)
    else:
        history = []
    # Curated memory: fetched ONCE here and frozen into the system prompt for
    # the whole session (prompt-cache stability). Mid-session `memory` tool
    # writes persist to the cloud store immediately but only show up in the
    # NEXT session's snapshot — same design as the local file-backed stack.
    memory_snapshot = (await _load_memory_snapshot(user_id)) if user_id else ""
    logger.info(
        "[job] joined room %s (user=%s, %d prior turn(s)%s, %d memory chars)",
        ctx.room.name, user_id, len(history),
        f", conv={conversation_id}" if conversation_id else "",
        len(memory_snapshot),
    )
    instructions = INSTRUCTIONS
    if memory_snapshot:
        instructions = f"{INSTRUCTIONS}\n{memory_snapshot}\n"

    # Replay recent memory into the chat context so the agent recalls past calls.
    chat_ctx = ChatContext.empty()
    for turn in history:
        chat_ctx.add_message(role=turn["role"], content=turn["text"])

    # Voice-mode TTS voice = the phone's selected Edge voice (participant attr),
    # else the server default.
    default_voice = os.environ.get("VOICE_TTS_VOICE", tts_edge.DEFAULT_VOICE)
    default_rate = os.environ.get("VOICE_TTS_RATE", "+0%")
    tts_voice, tts_rate = await _resolve_tts_attrs(
        ctx.room, participant, default_voice, default_rate,
    )
    logger.info(
        "[job] tts voice=%s rate=%s%s", tts_voice, tts_rate,
        "" if tts_voice == default_voice else " [from phone]",
    )

    # ── Self-knowledge: tell JARVIS its own live runtime config so it can answer
    # truthfully when the user asks what model / voice / capabilities it's using,
    # instead of guessing. Built here where the model, STT, voice, and tool set
    # are all resolved. It reflects reality (env + the actually-loaded STT), so it
    # stays correct as config changes. Phrased to answer only when asked.
    llm_model = model_override or os.environ.get("VOICE_LLM_MODEL", DEFAULT_LLM_MODEL)
    stt_inst = ctx.proc.userdata["stt"]
    stt_main = getattr(stt_inst, "_model_size", "whisper")
    stt_streaming = bool(getattr(stt_inst.capabilities, "streaming", False))
    stt_interim = getattr(stt_inst, "_interim_model_size", None) if stt_streaming else None
    stt_desc = f"faster-whisper {stt_main} (on-device, CPU)"
    if stt_interim:
        stt_desc += f", with live word-by-word streaming transcription (interim model {stt_interim})"
    tool_lines = ["search the web for current, real-time information"]
    if user_id:
        tool_lines.append("remember facts about the user across calls (long-term memory)")
        tool_lines.append("recall details from your past conversations with this user")
    instructions = instructions + (
        "\n\n[Your current runtime configuration — answer accurately and briefly IF the user "
        "asks about your setup, model, voice, or capabilities; otherwise don't mention it.]\n"
        "- Mode: real-time voice call (self-hosted LiveKit).\n"
        f"- Language model: {llm_model}, served through the JARVIS gateway.\n"
        f"- Speech recognition: {stt_desc}.\n"
        f"- Voice / text-to-speech: Microsoft Edge Neural voice {tts_voice}.\n"
        "- Things you can do right now: " + "; ".join(tool_lines) + ".\n"
    )

    def _publish_speech(text: str, words: list[dict], dur: int = 0) -> None:
        # Push the reply text + per-word timings (from edge-tts) to the phone so
        # it can light each word white as JARVIS speaks it (karaoke read-along).
        # Fired from the TTS at synthesis time — near the start of playback,
        # unlike conversation_item_added which fires after the turn ends. `dur`
        # is this utterance's spoken span so the phone can chain utterances.
        try:
            payload = json.dumps({"text": text, "words": words, "dur": dur}).encode("utf-8")
        except Exception:
            return
        task = asyncio.create_task(
            ctx.room.local_participant.publish_data(
                payload, reliable=True, topic="agent.speech"
            )
        )
        _pending.add(task)
        task.add_done_callback(_pending.discard)

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        # Streaming STT (default): declares streaming=True, so AgentSession
        # uses its .stream() directly and emits live INTERIM_TRANSCRIPTs
        # (the phone renders them as the self-revising user partial).
        # With VOICE_STT_STREAMING=0 it's the old non-streaming STT and
        # AgentSession auto-wraps it in a StreamAdapter with the VAD above.
        stt=ctx.proc.userdata["stt"],
        llm=_build_llm(model_override),
        tts=tts_edge.EdgeTTS(voice=tts_voice, rate=tts_rate, on_speech=_publish_speech),
        turn_handling={
            "interruption": {
                "enabled": True,
                # Explicit VAD interruption: auto-detect would probe
                # LiveKit Cloud's adaptive-interruption gateway first,
                # which self-hosted LiveKit doesn't have.
                "mode": "vad",
                # Gate barge-in on VAD duration alone (min_words: 0):
                # streaming interims exist now, but word-gated interruption
                # would add interim-decode latency (~0.5-1s) to every
                # barge-in, and with VOICE_STT_STREAMING=0 there are no
                # partials at all. VAD-only keeps both modes identical.
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
                _append_turn(
                    user_id, "user", ev.transcript, session_id=ctx.room.name
                )

    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        item = ev.item
        if getattr(item, "role", None) == "assistant":
            text = item.text_content
            logger.info("[llm] agent replied: %r", text)
            interrupted = getattr(item, "interrupted", False)
            # (Karaoke full-text + word timings are published from the TTS via
            # _publish_speech at synthesis time, not here — this event fires too
            # late, after the turn has finished speaking.)
            # Skip barge-in fragments (interrupted=True): persisting cut-off
            # partials like "Your" would pollute the replayed memory; the
            # follow-up full reply is the turn worth remembering.
            if user_id and text and not interrupted:
                _append_turn(
                    user_id,
                    "assistant",
                    text,
                    session_id=ctx.room.name,
                )

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
            # The memory + recall tools are keyed on user_id in their
            # closures — without an identity there is nothing safe to bind
            # reads/writes to, so omit them.
            tools=(
                [_build_memory_tool(user_id), _build_recall_tool(user_id), search_web]
                if user_id
                else [search_web]  # research works even without an identity (#16)
            ),
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
