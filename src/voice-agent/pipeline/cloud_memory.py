"""Cloud memory client — flag-gated repoint of curated memory + recall at the
shared cloud store, so the local voice-agent, the phone, and the cloud voice
worker all read/write ONE memory (keyed by the single web user's id).

Everything is behind ``JARVIS_CLOUD_MEMORY=1`` (default OFF). With the flag
off, ``enabled()`` is False and every consumer takes its pre-existing local
path unchanged — zero new code paths execute.

Cloud endpoints (the web app, default ``https://0wlan.com``):

  GET  /api/memory?user_id=…   → frozen curated snapshot
                                 {user_block, memory_block, procedure_block,
                                  snapshot_text} (same ════-header format as
                                 ``file_memory.MemoryStore._render_block``)
  POST /api/memory             → the memory-tool contract; returns the tool
                                 JSON verbatim. Contract failures (budget /
                                 scan / validation) are HTTP 200
                                 ``success:false`` — tool results for the
                                 model, NOT transport errors.
  POST /api/recall             → {mode:"query"} → {text} (honcho dialectic);
                                 {mode:"sync"}  → {ok}   (turn ingestion)

Auth: an HS256 proxy JWT with ``sub="voice-agent"`` minted locally from
``JARVIS_PROXY_JWT_SECRET`` (``pipeline/proxy_token.py``; the secret is in
``~/.jarvis/keys.env``, auto-loaded by ``jarvis_agent._load_user_keys_env``).

Offline resilience (load-bearing — WiFi-off is a real feature):
  - every call is bounded (2 s connect + a per-call total budget);
  - after ANY failure the module enters a 60 s backoff (``_down_until``)
    during which every call short-circuits to its failure path instantly, so
    an offline session never pays a connect timeout per memory write;
  - the first failure per process logs at ERROR (persistent failure usually
    means config — JARVIS_PROXY_JWT_SECRET drift), repeats at WARNING;
  - all consumers fall back to the pre-existing LOCAL path (file_memory
    files / local honcho) on any error, so a session never breaks offline.

Frozen-snapshot rule: the snapshot is fetched at session start only —
``sync_at_session_start()`` (run OFF the event loop via ``asyncio.to_thread``
from jarvis_agent's async entrypoint, BEFORE ``_build_initial_prompt_state``)
calls ``load_frozen_snapshot()`` once, replays any journaled offline writes
(``cloud-memory-pending.jsonl`` — see the journal section), re-fetches once
after a clean replay, then mirrors. ``frozen_snapshot_text`` returns that
cached constant for the whole session, preserving the prompt-prefix-cache
stability of the local frozen-snapshot design. Mid-session memory writes
persist to the cloud (and mirror locally) but only appear in the prompt next
session.

Deliberately a SEPARATE base-URL var (``JARVIS_CLOUD_MEMORY_URL``) from
``JARVIS_WEB_URL`` — the latter means "the LOCAL web app on this box"
(default 127.0.0.1:3000) and is consumed by the ``web_*`` workbench tools;
repointing it would break them.
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

from pipeline import proxy_token
from pipeline.file_memory import (
    ENTRY_DELIMITER,
    VALID_TARGETS,
    MemoryStore,
    mirror_entries,
)
from tools.memory_providers import MemoryProvider
from tools.runtime import get_jarvis_home

logger = logging.getLogger("jarvis.cloud_memory")

_DEFAULT_BASE_URL = "https://0wlan.com"

# Per-call budgets (seconds). Connect is uniformly tight so a dead network
# fails fast; totals mirror the cloud worker's rationale
# (src/voice-agent-lk/agent.py): snapshot/writes are turn-adjacent → short;
# recall query is a multi-second server-side dialectic call; recall sync is
# off the turn path and the server's cold worst case is ~30 s.
_CONNECT_TIMEOUT_S = 2.0
_SNAPSHOT_TOTAL_S = 5.0
_MEMORY_POST_TOTAL_S = 10.0
_RECALL_QUERY_TOTAL_S = 30.0
_RECALL_SYNC_TOTAL_S = 35.0

# Offline backoff: after a failure, short-circuit every call for this long.
_DOWN_BACKOFF_S = 60.0

# ── Module state (reset via _reset_for_tests) ─────────────────────────
_down_until: float = 0.0                       # time.monotonic() deadline
_failure_logged: bool = False                  # first failure → ERROR, rest WARNING
_missing_cfg_logged: bool = False              # log-once for enabled() misconfig
_token_cache: tuple[str, float] = ("", 0.0)    # (token, epoch expiry)
_frozen_snapshot: Optional[Dict[str, Any]] = None
_cloud_provider: Optional["CloudMemoryProvider"] = None


# ---------------------------------------------------------------------------
# Config / gate
# ---------------------------------------------------------------------------


def base_url() -> str:
    """Cloud web-app base URL (NOT JARVIS_WEB_URL — see module docstring)."""
    return os.environ.get("JARVIS_CLOUD_MEMORY_URL", _DEFAULT_BASE_URL).rstrip("/")


def user_id() -> str:
    """The cloud web user id that keys the shared memory store."""
    return os.environ.get("JARVIS_CLOUD_USER_ID", "").strip()


def enabled() -> bool:
    """Master gate. True only when JARVIS_CLOUD_MEMORY=1 AND the required
    config (user id + JWT secret) is present. Reads env at call time so the
    flag works under pytest without importing jarvis_agent."""
    global _missing_cfg_logged
    if os.environ.get("JARVIS_CLOUD_MEMORY", "").strip() != "1":
        return False
    missing = []
    if not user_id():
        missing.append("JARVIS_CLOUD_USER_ID")
    if not os.environ.get("JARVIS_PROXY_JWT_SECRET", "").strip():
        missing.append("JARVIS_PROXY_JWT_SECRET")
    if missing:
        if not _missing_cfg_logged:
            _missing_cfg_logged = True
            logger.warning(
                "[cloud-memory] JARVIS_CLOUD_MEMORY=1 but %s unset — "
                "cloud memory stays OFF (local behavior)",
                " + ".join(missing),
            )
        return False
    return True


def _token() -> str:
    """Mint (and cache until 60 s before expiry) the sub=voice-agent JWT."""
    global _token_cache
    tok, exp = _token_cache
    now = time.time()
    if tok and now < exp - 60.0:
        return tok
    tok = proxy_token.mint_from_env(sub="voice-agent")
    _token_cache = (tok, now + proxy_token.DEFAULT_TTL_S)
    return tok


def _auth_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_token()}"}


# ---------------------------------------------------------------------------
# Offline backoff + once-per-process failure logging
# ---------------------------------------------------------------------------


def _is_down() -> bool:
    return time.monotonic() < _down_until


def _mark_down(what: str, err: BaseException | str, fallback: str) -> None:
    global _down_until, _failure_logged
    _down_until = time.monotonic() + _DOWN_BACKOFF_S
    if not _failure_logged:
        _failure_logged = True
        logger.error(
            "[cloud-memory] %s failed (%s); %s — check %s is reachable and "
            "JARVIS_PROXY_JWT_SECRET matches the web app (backoff %.0fs)",
            what, err, fallback, base_url(), _DOWN_BACKOFF_S,
        )
    else:
        logger.warning("[cloud-memory] %s failed (%s); %s", what, err, fallback)


def _mark_up() -> None:
    global _down_until
    _down_until = 0.0


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


def _timeout(total_s: float) -> httpx.Timeout:
    return httpx.Timeout(total_s, connect=_CONNECT_TIMEOUT_S)


def fetch_snapshot() -> Optional[Dict[str, Any]]:
    """GET the rendered curated snapshot. None on any error (fail to local)."""
    if _is_down():
        return None
    try:
        with httpx.Client(timeout=_timeout(_SNAPSHOT_TOTAL_S)) as client:
            resp = client.get(
                f"{base_url()}/api/memory",
                params={"user_id": user_id()},
                headers=_auth_headers(),
            )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        data = resp.json()
        if not isinstance(data, dict) or not isinstance(data.get("snapshot_text"), str):
            raise RuntimeError("malformed snapshot response")
        _mark_up()
        return data
    except Exception as e:  # noqa: BLE001 — any failure = local fallback
        _mark_down("snapshot fetch", e, "session boots from the local snapshot")
        return None


def post_memory_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """POST a memory-tool call to the cloud store. Returns the tool JSON dict
    (including HTTP-200 ``success:false`` contract failures — those are tool
    results for the model, do NOT fall back on them). RAISES on transport /
    HTTP errors so the caller can fall back to the local store."""
    if _is_down():
        raise RuntimeError("cloud memory in offline backoff")
    try:
        payload = {k: v for k, v in args.items() if v is not None}
        with httpx.Client(timeout=_timeout(_MEMORY_POST_TOTAL_S)) as client:
            resp = client.post(
                f"{base_url()}/api/memory", json=payload, headers=_auth_headers()
            )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError("malformed tool response")
        _mark_up()
        return data
    except Exception as e:  # noqa: BLE001 — re-raised; caller falls back local
        _mark_down("memory write", e, "falling back to the local store")
        raise


async def recall_query(query: str) -> Optional[str]:
    """POST /api/recall mode=query. Returns the recall text, or None on any
    error / empty result (caller falls back to the local provider)."""
    if _is_down():
        return None
    try:
        async with httpx.AsyncClient(timeout=_timeout(_RECALL_QUERY_TOTAL_S)) as client:
            resp = await client.post(
                f"{base_url()}/api/recall",
                json={"mode": "query", "user_id": user_id(), "query": query},
                headers=_auth_headers(),
            )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        data = resp.json()
        text = data.get("text") if isinstance(data, dict) else None
        _mark_up()
        # Server fail-soft ({text:"", error:…}) and genuinely-empty recall
        # both → None so the local fallback gets a chance.
        return text if isinstance(text, str) and text.strip() else None
    except Exception as e:  # noqa: BLE001
        _mark_down("recall query", e, "falling back to the local provider")
        return None


async def recall_sync(role: str, text: str, session_id: str) -> bool:
    """POST /api/recall mode=sync (turn ingestion). True only when the server
    confirmed the write ({ok:true}); False → caller syncs to local honcho."""
    if _is_down():
        return False
    try:
        async with httpx.AsyncClient(timeout=_timeout(_RECALL_SYNC_TOTAL_S)) as client:
            resp = await client.post(
                f"{base_url()}/api/recall",
                json={
                    "mode": "sync",
                    "user_id": user_id(),
                    "role": role,
                    "text": text,
                    "session_id": session_id,
                },
                headers=_auth_headers(),
            )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        data = resp.json()
        _mark_up()
        return bool(isinstance(data, dict) and data.get("ok"))
    except Exception as e:  # noqa: BLE001
        _mark_down("recall sync", e, "syncing the turn to the local provider")
        return False


# ---------------------------------------------------------------------------
# Frozen snapshot slot — fetched once per session, constant thereafter
# ---------------------------------------------------------------------------

_BLOCK_KEYS = {
    "user": "user_block",
    "memory": "memory_block",
    "procedure": "procedure_block",
}


def load_frozen_snapshot() -> bool:
    """Fetch the cloud snapshot and freeze it for the session. Resets the
    slot on every call. Called at session start only (``sync_at_session_
    start``): once as the freeze point, plus at most one RE-fetch right
    after a clean offline-journal replay so the frozen snapshot includes
    the replayed writes — the frozen text is constant for the rest of the
    session either way. False on any failure → the session runs on the
    local snapshot only."""
    global _frozen_snapshot
    _frozen_snapshot = None
    data = fetch_snapshot()
    if data is None:
        return False
    _frozen_snapshot = data
    return True


def frozen_snapshot_text() -> Optional[str]:
    """The frozen session snapshot text, verbatim from the GET ("" when the
    cloud stores are empty). None when no snapshot was loaded (fetch failed /
    never attempted) — callers then use the local snapshot."""
    if _frozen_snapshot is None:
        return None
    text = _frozen_snapshot.get("snapshot_text")
    return text if isinstance(text, str) else None


def frozen_block_entries(target: str) -> Optional[List[str]]:
    """Parse one store's entries out of the frozen snapshot block.

    Blocks render as 3 header lines (═-separator / header / ═-separator)
    followed by the entries joined with ENTRY_DELIMITER — identical to
    ``file_memory.MemoryStore._render_block``. Returns None when there is
    no snapshot OR the block is empty: an empty cloud store deliberately
    does NOT mirror, so first-enable against a fresh cloud store can never
    wipe the local files (they are the offline fallback)."""
    if _frozen_snapshot is None:
        return None
    key = _BLOCK_KEYS.get(target)
    if key is None:
        return None
    block = _frozen_snapshot.get(key)
    if not isinstance(block, str) or not block.strip():
        return None
    lines = block.split("\n")
    if len(lines) < 4:
        return None
    content = "\n".join(lines[3:])
    entries = [e.strip() for e in content.split(ENTRY_DELIMITER) if e.strip()]
    return entries or None


# ---------------------------------------------------------------------------
# Offline-write journal — journal-and-replay so curated writes made while
# offline are NOT clobbered by the next online session's snapshot mirror.
#
# Failure mode this closes (data loss): offline → cloud POST transport-fails
# → the memory tool's local file write succeeds ("Entry added") → next ONLINE
# session start mirrors the cloud snapshot (which never got that write) over
# the local files → the offline write is gone. Instead, the memory tool
# journals the exact tool args on a TRANSPORT failure (never on an HTTP-200
# ``success:false`` contract failure — those are authoritative server
# rejections, not lost writes), and ``sync_at_session_start`` replays the
# journal against the cloud BEFORE mirroring.
# ---------------------------------------------------------------------------

_JOURNAL_FILENAME = "cloud-memory-pending.jsonl"
_JOURNAL_KEYS = ("action", "target", "content", "old_text", "name")
_JOURNAL_ACTIONS = ("add", "replace", "remove")


def journal_path() -> Path:
    """The offline-write journal — one JSON tool-args object per line, under
    the same profile home as the memory files (``get_jarvis_home()``),
    lock-protected via the same sidecar ``.lock`` convention."""
    return get_jarvis_home() / _JOURNAL_FILENAME


def journal_pending_write(args: Dict[str, Any]) -> None:
    """Append a curated write whose cloud POST failed with a TRANSPORT/HTTP
    error, for replay at the next online session start.

    Callers must NOT journal HTTP-200 ``success:false`` contract failures —
    those are authoritative rejections (budget / scan / validation), not
    lost writes. ``tools/memory.py`` calls this only from its
    transport-error fallback, and only after the local fallback write
    actually succeeded (so the journal mirrors exactly the writes the model
    was told landed)."""
    line = {k: args[k] for k in _JOURNAL_KEYS if args.get(k) is not None}
    path = journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with MemoryStore._file_lock(path):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    logger.info(
        "[cloud-memory] journaled offline %s %s for replay next online session",
        line.get("action"), line.get("target"),
    )


def _read_journal(path: Path) -> List[Dict[str, Any]]:
    """Parse the journal. Corrupt / invalid lines are dropped with a warning
    (they could never replay successfully and would wedge the journal)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as e:  # pragma: no cover - unreadable journal → nothing to replay
        logger.warning("[cloud-memory] journal unreadable (%s); skipping replay", e)
        return []
    entries: List[Dict[str, Any]] = []
    for ln in raw.splitlines():
        if not ln.strip():
            continue
        try:
            obj = json.loads(ln)
        except ValueError:
            logger.warning("[cloud-memory] dropping corrupt journal line: %.120r", ln)
            continue
        if (
            not isinstance(obj, dict)
            or obj.get("action") not in _JOURNAL_ACTIONS
            or obj.get("target") not in VALID_TARGETS
        ):
            logger.warning("[cloud-memory] dropping invalid journal line: %.120r", ln)
            continue
        entries.append(obj)
    return entries


def _write_journal(path: Path, entries: List[Dict[str, Any]]) -> None:
    """Rewrite the journal atomically (temp file + rename, same pattern as
    ``file_memory``). Empty → the file is removed."""
    if not entries:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    content = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix=".cmj_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def replay_pending_writes() -> Tuple[int, Set[str]]:
    """Replay journaled offline writes IN ORDER via ``post_memory_tool``.

    Per line:
      - POST success              → replayed; line dropped.
      - HTTP-200 ``success:false`` → authoritative contract rejection (the
        server ran the full contract and said no) — the line can never
        succeed; dropped with a warning.
      - transport/HTTP error       → still offline; replay ABORTS. This line
        and every later one stay journaled for the next session.

    Returns ``(replayed_count, blocked_targets)``:
      - ``replayed_count``  — writes that actually landed in the cloud.
        Callers re-fetch the snapshot when >0 and the journal drained, so
        the mirror includes the replayed writes.
      - ``blocked_targets`` — empty on a clean replay (journal drained).
        Otherwise the set of store targets that had pending lines BEFORE
        the attempt: callers must skip mirroring those (the held snapshot
        pre-dates the partial replay AND is missing the still-pending
        writes — mirroring would clobber the local copies). Retried next
        session.
    """
    path = journal_path()
    if not path.exists():
        return 0, set()
    with MemoryStore._file_lock(path):
        pending = _read_journal(path)
        if not pending:
            _write_journal(path, [])  # normalize away an empty/corrupt-only file
            return 0, set()
        affected = {e["target"] for e in pending}
        remaining: List[Dict[str, Any]] = []
        replayed = 0
        aborted = False
        for entry in pending:
            if aborted:
                remaining.append(entry)
                continue
            try:
                result = post_memory_tool({"user_id": user_id(), **entry})
            except Exception as e:  # noqa: BLE001 — still offline / cloud down
                aborted = True
                remaining.append(entry)
                logger.warning(
                    "[cloud-memory] journal replay aborted (%s); pending "
                    "write(s) kept for next session", e,
                )
                continue
            if isinstance(result, dict) and result.get("success"):
                replayed += 1
            else:
                logger.warning(
                    "[cloud-memory] journaled %s %s rejected by the cloud "
                    "store (%s) — dropped",
                    entry.get("action"), entry.get("target"),
                    (result or {}).get("error", "no error text"),
                )
        _write_journal(path, remaining)
    if remaining:
        return replayed, affected
    return replayed, set()


def sync_at_session_start() -> bool:
    """The single cloud-memory phase at session start. BLOCKING (sync httpx
    GET/POSTs) — jarvis_agent's async entrypoint runs it via
    ``await asyncio.to_thread(...)`` BEFORE ``_build_initial_prompt_state``,
    which then reads the already-cached frozen snapshot (no second fetch, no
    HTTP on the event loop).

    Order (once per session):
      1. Fetch + freeze the cloud snapshot (``load_frozen_snapshot`` — also
         the reachability probe; offline → False → pure-local path, journal
         untouched).
      2. Replay journaled offline writes (``replay_pending_writes``). After
         a CLEAN replay that pushed writes up, RE-fetch the snapshot so the
         mirror below includes them (still session-start-only — the frozen
         text is constant for the rest of the session).
      3. Mirror the frozen entries into the local files so offline sessions
         boot from the last-seen cloud state — SKIPPING any target with
         still-pending journaled writes (never overwrite local state with a
         cloud state that's missing those writes).

    Returns True when a cloud snapshot is frozen (session runs cloud-primary),
    False when the flag is off / offline / misconfigured (pure-local path).
    """
    if not enabled():
        return False
    if not load_frozen_snapshot():
        return False
    replayed, blocked = replay_pending_writes()
    if replayed and not blocked:
        # Clean replay changed the cloud state — re-fetch so the mirror
        # includes the replayed writes. If THIS fetch fails (network died
        # mid-boot), the frozen slot is cleared and nothing mirrors — safe:
        # the local files already hold the replayed writes.
        load_frozen_snapshot()
    for target in ("user", "memory", "procedure"):
        if target in blocked:
            continue
        entries = frozen_block_entries(target)
        if entries is not None:
            mirror_entries(target, entries)
    return frozen_snapshot_text() is not None


# ---------------------------------------------------------------------------
# CloudMemoryProvider — wraps /api/recall with the local provider as fallback
# ---------------------------------------------------------------------------


class CloudMemoryProvider(MemoryProvider):
    """Cloud recall/sync via /api/recall, with the resolved LOCAL provider
    (honcho) as the always-available fallback so offline sessions keep the
    exact pre-flag behavior.

    ``recall_context`` deliberately delegates STRAIGHT to the local fallback:
    ``maybe_recall_for_turn`` runs it under a 1.5 s hard budget
    (pipeline/memory_provider.py) and a WAN round-trip to the cloud web app
    plus honcho cannot fit that; /api/recall also has no cheap context mode.
    Deep recall (the ``recall`` tool) and turn sync go cloud-first.
    """

    name = "cloud"

    def __init__(self, fallback: Optional[MemoryProvider] = None) -> None:
        self._fallback = fallback
        self._session_id: Optional[str] = None

    # -- availability / lifecycle --------------------------------------

    def is_available(self) -> bool:
        return enabled()

    def initialize(self, session_id: str) -> None:
        """Store the id only — no network (same lazy contract as honcho).
        Also initializes the fallback so it is ready the moment a cloud
        call fails."""
        self._session_id = session_id
        if self._fallback is not None:
            try:
                self._fallback.initialize(session_id)
            except Exception as exc:  # noqa: BLE001 — fallback init must not break startup
                logger.warning("[cloud-memory] fallback initialize failed: %s", exc)

    def end_session(self) -> None:
        self._session_id = None
        if self._fallback is not None:
            try:
                self._fallback.end_session()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[cloud-memory] fallback end_session failed: %s", exc)

    # -- recall / sync ---------------------------------------------------

    async def _delegate(self, method: str, *call_args: Any) -> Any:
        """Await-aware delegation to the fallback provider (honcho methods
        are async; a test double may be sync)."""
        if self._fallback is None:
            return None
        fn = getattr(self._fallback, method, None)
        if fn is None:
            return None
        result = fn(*call_args)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def recall(self, query: str) -> str:
        """Cloud dialectic recall; local provider on any miss/error."""
        text = await recall_query(query)
        if isinstance(text, str) and text.strip():
            return text
        try:
            result = await self._delegate("recall", query)
            return result if isinstance(result, str) else ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("[cloud-memory] fallback recall failed: %s", exc)
            return ""

    async def recall_context(self, hint: str = "") -> str:
        """LOCAL-ONLY on purpose — see class docstring (1.5 s turn budget)."""
        try:
            result = await self._delegate("recall_context", hint)
            return result if isinstance(result, str) else ""
        except Exception as exc:  # noqa: BLE001
            logger.debug("[cloud-memory] fallback recall_context failed: %s", exc)
            return ""

    async def sync_message(self, role: str, text: str) -> None:
        """Cloud-first turn sync; on failure the turn lands in local honcho
        so offline conversations still feed local semantic recall."""
        ok = False
        try:
            ok = await recall_sync(role, text, self._session_id or "")
        except Exception:  # noqa: BLE001 — recall_sync shouldn't raise, but be safe
            ok = False
        if ok:
            return
        try:
            await self._delegate("sync_message", role, text)
        except Exception as exc:  # noqa: BLE001 — background; never surface
            logger.debug("[cloud-memory] fallback sync_message failed: %s", exc)


def get_cloud_provider(fallback: Optional[MemoryProvider] = None) -> CloudMemoryProvider:
    """Memoized module singleton (one per process, like the honcho plugin
    instance). The fallback is refreshed on every call because the local
    provider resolution in ``memory_provider.active_provider`` can change
    (e.g. honcho coming up/down mid-process)."""
    global _cloud_provider
    if _cloud_provider is None:
        _cloud_provider = CloudMemoryProvider(fallback=fallback)
    else:
        _cloud_provider._fallback = fallback
    return _cloud_provider


# ---------------------------------------------------------------------------
# Test helper
# ---------------------------------------------------------------------------


def _reset_for_tests() -> None:
    """Clear all module state (backoff, log-once flags, token cache, frozen
    snapshot, memoized provider). Tests only."""
    global _down_until, _failure_logged, _missing_cfg_logged
    global _token_cache, _frozen_snapshot, _cloud_provider
    _down_until = 0.0
    _failure_logged = False
    _missing_cfg_logged = False
    _token_cache = ("", 0.0)
    _frozen_snapshot = None
    _cloud_provider = None
