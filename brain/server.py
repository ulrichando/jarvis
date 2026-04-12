"""
FastAPI brain server. Single endpoint /message accepts requests from all 3 channels.
Channels are intentionally dumb — they only send text and receive text.

Entry-point guards (applied in order before any model call):
  1. System command intercept — ping/heartbeat returns immediately, zero AI cost
  2. Voice gate             — rejects noise, short filler, and wake-word-less phrases
  3. Deduplicator           — blocks identical messages within a per-channel window
"""

import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from brain.pipeline     import handle_request
from brain.telemetry    import telemetry
from brain.voice_gate   import is_valid_voice_input, strip_wake_word
from brain.deduplicator import deduplicator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

VALID_CHANNELS: frozenset[str] = frozenset({"cli", "voice", "chrome"})

# Messages that are answered without touching any AI at all
_SYSTEM_COMMANDS: frozenset[str] = frozenset({
    "ping", "heartbeat", "status", "alive",
    "/ping", "/status", "/heartbeat", "/alive",
})

app = FastAPI(title="JARVIS Brain Server", version="1.0.0")


class ChannelRequest(BaseModel):
    channel_id: str
    message:    str


class ChannelResponse(BaseModel):
    channel_id: str
    response:   str


@app.post("/message", response_model=ChannelResponse)
async def receive_message(req: ChannelRequest) -> ChannelResponse:
    """
    Main brain endpoint. All 3 channels POST here.
    Returns a plain-text response tailored to the requesting channel.
    Never crashes — returns a safe fallback on any unhandled error.
    """
    if req.channel_id not in VALID_CHANNELS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown channel '{req.channel_id}'. Valid: {sorted(VALID_CHANNELS)}",
        )

    msg = req.message.strip()

    if not msg:
        raise HTTPException(status_code=400, detail="Message must not be empty.")

    # ── Guard 1: System commands — zero AI cost ───────────────────────────────
    if msg.lower() in _SYSTEM_COMMANDS:
        logger.debug(f"[server] system_command channel={req.channel_id} msg='{msg}'")
        return ChannelResponse(channel_id=req.channel_id, response="ok")

    # ── Guard 2: Voice gate — reject noise before it hits any model ───────────
    if req.channel_id == "voice":
        valid, reason = is_valid_voice_input(msg)
        if not valid:
            logger.debug(f"[voice_gate] rejected: {reason} — '{msg[:60]}'")
            return ChannelResponse(channel_id=req.channel_id, response="")
        msg = strip_wake_word(msg)

    # ── Guard 3: Deduplication — block repeated phrases within the window ─────
    if deduplicator.is_duplicate(req.channel_id, msg):
        return ChannelResponse(channel_id=req.channel_id, response="")

    # Periodic cleanup — cheap, runs on every request
    deduplicator.cleanup()

    try:
        response = await handle_request(channel_id=req.channel_id, message=msg)
        return ChannelResponse(channel_id=req.channel_id, response=response)

    except Exception as e:
        logger.error(f"[server] unhandled error channel={req.channel_id}: {e}", exc_info=True)
        fallback = (
            f"Error: {e}"
            if req.channel_id == "cli"
            else "I encountered an error. Please try again."
        )
        return ChannelResponse(channel_id=req.channel_id, response=fallback)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/")
async def index() -> dict:
    """Available endpoints."""
    return {
        "endpoints": {
            "POST /message":          "Send a message — body: {channel_id, message}",
            "GET  /health":           "Health check",
            "GET  /telemetry":        "Token usage breakdown",
            "GET  /telemetry/top":    "Top 10 most expensive calls",
            "POST /telemetry/clear":  "Reset telemetry counters",
            "GET  /budget":           "Daily token spend vs budget",
            "POST /budget/reset":     "Reset daily budget counter",
        }
    }


@app.get("/health")
async def health() -> dict:
    """Health check — zero AI cost."""
    return {"status": "ok", "channels": sorted(VALID_CHANNELS)}


# ── Telemetry ─────────────────────────────────────────────────────────────────

@app.get("/telemetry")
async def get_telemetry() -> dict:
    """Full token breakdown — channel / route / tool. Use to find leaks."""
    return telemetry.report()


@app.get("/telemetry/top")
async def get_top_offenders() -> list[dict]:
    """Top 10 most expensive individual API calls."""
    calls = telemetry.top_offenders(10)
    return [
        {
            "channel":   c.channel_id,
            "route":     c.route,
            "tool":      c.tool_name,
            "tokens":    c.total_tokens,
            "cost":      f"${c.estimated_cost_usd:.6f}",
            "message":   c.message[:80],
            "timestamp": c.timestamp,
        }
        for c in calls
    ]


@app.post("/telemetry/clear")
async def clear_telemetry() -> dict:
    """Reset the in-memory telemetry counters."""
    telemetry.clear()
    return {"status": "cleared"}


# ── Budget ────────────────────────────────────────────────────────────────────

@app.get("/budget")
async def get_budget() -> dict:
    """Current daily token spend vs. budget."""
    from brain.budget import budget_guard, DAILY_TOKEN_BUDGET
    return {
        "tokens_used":   budget_guard.total_tokens,
        "daily_budget":  DAILY_TOKEN_BUDGET,
        "percent_used":  f"{budget_guard.percent_used:.1f}%",
        "over_budget":   budget_guard.is_over_budget(),
    }


@app.post("/budget/reset")
async def reset_budget() -> dict:
    """Reset the daily budget counter (call at midnight via cron)."""
    from brain.budget import budget_guard
    budget_guard.reset()
    return {"status": "reset"}
