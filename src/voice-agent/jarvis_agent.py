"""
JARVIS voice agent — LiveKit worker.

Connects to the local LiveKit SFU as a Python worker. When any client
joins a room, this worker spawns a job, sets up the voice pipeline
(Silero VAD → Groq Whisper STT → Groq Llama LLM → Groq Orpheus TTS),
and holds a conversation over WebRTC.

Architecture:
    Tauri webview / Android client  ──(WebRTC audio)──▶  LiveKit SFU
                                                            ▲
                                                            │ joins as
                                                            │ a peer
                                                            ▼
                                                       this process

All audio DSP (AEC, NS, jitter buffer) is handled by the WebRTC stack
at each end — we do not run Silero in the browser anymore. VAD below
runs server-side, on the decoded frames the SFU forwards us, which is
why reliability improves dramatically vs the previous pipeline.

Run modes:
    python jarvis_agent.py dev       # local, verbose, file-watch
    python jarvis_agent.py start     # production (systemd uses this)
    python jarvis_agent.py download-files  # pre-fetch Silero weights

Env (from .env alongside this file, loaded by systemd unit):
    LIVEKIT_URL         ws://127.0.0.1:7880  (or ws://<tailscale-ip>:7880)
    LIVEKIT_API_KEY     matches livekit.yaml keys block
    LIVEKIT_API_SECRET  matches livekit.yaml keys block
    GROQ_API_KEY        required for STT/LLM/TTS via Groq
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

from livekit import agents
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    function_tool,
)
from livekit.plugins import groq, silero

logger = logging.getLogger("jarvis-agent")


# Prompt cribbed from the existing speech.ts voice-channel prompt.
# Kept short on purpose — voice replies should sound conversational,
# not enumerate bullet points. The Tier 1 / Tier 3 rules and the
# "replies are spoken aloud" constraints are the load-bearing bits.
JARVIS_INSTRUCTIONS = """\
You are JARVIS, Ulrich's voice-first personal AI running locally on
his Linux (Kali) laptop.

This channel is VOICE. Your replies are spoken aloud by a TTS engine,
so:
  - No markdown, no code blocks, no URLs, no file paths, no UUIDs.
  - Prefer sentences under 15 words.
  - Pronounce numbers the way humans say them ("twenty gigabytes",
    not "20GB").
  - Skip filler openings like "Certainly!" or "As an AI…". Just
    answer.

Authority rules:
  - Power operations on THIS workstation (reboot, shutdown, suspend,
    hibernate, logout) are Tier 1 — fully reversible, the machine
    comes back. Do NOT demand "confirm irreversible" for these.
  - Tier 3 — which DOES need explicit confirmation — is: rm -rf
    against anything real, dd to a disk, dropping production
    databases, revoking production API keys.

You have ONE tool: `run_jarvis_cli`. Call it for anything that
requires real-world state or side effects — shell commands, file
reads/writes, git, opening apps, real-time info (time, weather,
news, prices), web fetches. Pass the user's request verbatim; the
CLI agent has its own tool set and will do the actual work. Do NOT
make up tool results — if you don't call the tool, don't pretend you
ran it.

For chit-chat, reasoning, opinions, and anything answerable from
general knowledge, answer directly without the tool.

You know Ulrich personally — informal tone, no honorifics.
"""


# ── Tool bridge: delegate tool-using turns to the full JARVIS CLI ────
#
# The LiveKit agent by itself is a pure STT→LLM→TTS pipeline — no
# access to bash, files, web, MCP, or any of the tool surface the
# jarvis-cli process exposes. The old sidecar (speech.ts's `runAgent`)
# bridged this by spawning the CLI as a subprocess when the user's
# text matched a "needs tools" regex. We replicate the same pattern
# here, but exposed as a LiveKit `function_tool` so the LLM decides
# when to invoke it rather than a server-side regex. Avoids the
# regex's false positives ("what TIME is best to deploy" ≠ needs
# tools) and gives the LLM context to phrase the reply naturally.

JARVIS_CLI_SCRIPT = os.environ.get(
    "JARVIS_CLI_SCRIPT",
    str(Path.home() / "Documents/Projects/jarvis/src/cli/scripts/start.sh"),
)
JARVIS_CLI_PROVIDER  = os.environ.get("JARVIS_CLI_PROVIDER",  "groq")
JARVIS_CLI_TIMEOUT_S = int(os.environ.get("JARVIS_CLI_TIMEOUT_S", "60"))

# ANSI escape sequences leak through from the CLI's coloured output
# and read as noise when TTS tries to voice them. Stripped before
# returning the tool result to the LLM.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _clean_env_for_cli() -> dict[str, str]:
    """
    Strip Claude-Code env vars that would make the nested CLI bypass
    the local proxy (port 4000) or enable features we don't want
    (analytics, nested-session detection). Matches the `cleanEnv`
    block from the old speech.ts runAgent.
    """
    env: dict[str, str] = {}
    for k, v in os.environ.items():
        if v is None:
            continue
        if k.startswith("CLAUDE_CODE_") or k.startswith("CLAUDE_DESKTOP_"):
            continue
        if k == "CLAUDECODE":
            continue
        env[k] = v
    env.setdefault("ANTHROPIC_BASE_URL", "http://localhost:4000")
    env.setdefault("ANTHROPIC_API_KEY",  "jarvis-proxy")
    # Bash, not zsh — zsh's NOMATCH would fail on URL-with-`?` args
    # the CLI passes to xdg-open / curl.
    env["SHELL"] = "/bin/bash"
    for k in (
        "DISABLE_TELEMETRY",
        "DISABLE_ERROR_REPORTING",
        "DISABLE_BUG_COMMAND",
        "DISABLE_NON_ESSENTIAL_MODEL_CALLS",
        "DISABLE_AUTOUPDATER",
        "DISABLE_COST_WARNINGS",
    ):
        env[k] = "1"
    return env


@function_tool
async def run_jarvis_cli(request: str) -> str:
    """Execute any request that needs real tools — shell, files, web, system state.

    Call this tool whenever the user asks for something you cannot
    answer from conversation alone. Examples:
      - shell / running processes / system state / what's installed
      - real-time data: current time, weather, date, news, prices
      - file reads / writes / searches / git / code inspection
      - opening, launching, controlling apps (spotify, firefox, terminal)
      - browsing the web / fetching URLs / looking things up

    Pass the user's natural-language request verbatim — the CLI agent
    has its own system prompt and tool set and will interpret the
    request itself. Return the CLI agent's reply; you can summarise
    or rephrase it for voice if it's long, but do NOT invent tool
    results.

    Args:
        request: The user's request in their own words.

    Returns:
        The CLI agent's reply as plain text (ANSI stripped).
    """
    logger.info(f"run_jarvis_cli → {request[:80]}")
    # Invoke the CLI script via its own shebang (`#!/usr/bin/env bash`).
    # Running through `sh` here breaks — start.sh uses bash-only
    # features (BASH_SOURCE, arrays, `[[`). The executable bit is
    # already set, so exec'ing the path directly picks up the right
    # interpreter.
    try:
        proc = await asyncio.create_subprocess_exec(
            JARVIS_CLI_SCRIPT,
            JARVIS_CLI_PROVIDER,
            "-p",
            "--bare",
            "--",
            request,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            cwd="/tmp",
            env=_clean_env_for_cli(),
        )
    except (FileNotFoundError, PermissionError) as e:
        return f"(CLI script unavailable: {e})"

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=JARVIS_CLI_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        # Two-stage kill: SIGTERM → wait 2 s → SIGKILL if still alive.
        # Matches the speech.ts pattern; Claude-Code can trap SIGTERM
        # and hang on shutdown, pinning agentBusy forever in the old
        # design.
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        return "(tool ran past its 60 s deadline and was cancelled)"

    text = _ANSI_RE.sub("", stdout_b.decode("utf-8", errors="replace")).strip()
    err  = stderr_b.decode("utf-8", errors="replace").strip()
    logger.info(
        f"run_jarvis_cli done exit={proc.returncode} "
        f"out_len={len(text)} err_len={len(err)}"
    )
    if not text:
        if err:
            return f"(no output; stderr tail: {err[-200:]})"
        return "(no output)"
    return text


def prewarm(proc: JobProcess) -> None:
    """
    Runs once per worker process BEFORE any job. Loads the Silero VAD
    ONNX weights into RAM so they're shared across all future job
    invocations — loading is ~100 ms and the model is ~2 MB, not
    worth repeating on every connection.
    """
    proc.userdata["vad"] = silero.VAD.load()
    logger.info("Silero VAD loaded in prewarm")


async def entrypoint(ctx: JobContext) -> None:
    """
    Runs once per client that joins a room. This is the actual
    conversation loop — AgentSession handles the VAD → STT → LLM →
    TTS plumbing internally; we just wire the pieces and let it
    drive.

    Also listens on the LiveKit data channel for {"type": "speak",
    "text": "..."} messages. This lets the Tauri UI (or any other
    client) ask the agent to voice arbitrary text through the same
    TTS pipeline the conversation uses, rather than maintaining a
    separate TTS path. Triggered today when the typed-text chat
    path emits a `chat_response` over the bridge WS.
    """
    await ctx.connect()
    logger.info(f"joined room: {ctx.room.name}")

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        # Groq Whisper Turbo — same model as the old sidecar, but
        # streaming. First partial transcripts arrive while the user
        # is still talking, so turn latency drops from ~500 ms
        # (whole-clip upload) to ~100 ms (just the tail decoder).
        stt=groq.STT(
            model="whisper-large-v3-turbo",
            language="en",
        ),
        # Llama 3.3 70B on Groq — ~200 ms first-token, the fastest
        # real LLM available right now.
        llm=groq.LLM(
            model="llama-3.3-70b-versatile",
            temperature=0.6,
        ),
        # Orpheus with the "troy" voice — same warm male voice we
        # landed on in the old stack. Override with JARVIS_TTS_VOICE
        # env var to pick a different one (austin/daniel/hannah/
        # diana/autumn).
        tts=groq.TTS(
            model="canopylabs/orpheus-v1-english",
            voice=os.getenv("JARVIS_TTS_VOICE", "troy"),
        ),
    )

    await session.start(
        room=ctx.room,
        agent=Agent(
            instructions=JARVIS_INSTRUCTIONS,
            # Give the LLM access to the CLI-agent bridge so it can
            # run shell / files / web / real-time queries when the
            # user asks for them. See run_jarvis_cli's docstring for
            # when the LLM should invoke it.
            tools=[run_jarvis_cli],
        ),
    )

    # Handle one-shot "speak this text" requests from any client in
    # the room. session.say() voices the text directly without an
    # LLM round-trip — used by the Tauri UI to voice typed-chat
    # replies that come in over the bridge WS. Payload format:
    #   {"type": "speak", "text": "Rebooting now."}
    # Any other topic / type is ignored silently.
    import json as _json
    import asyncio as _asyncio

    async def _speak_when_ready(text: str) -> None:
        """
        session.say() requires AgentSession._activity to be set —
        which it is mid-turn but may NOT be while the session is
        idle between turns. Poll briefly (up to 3 s) for readiness
        before giving up. If still unavailable, fall back to calling
        the TTS plugin directly via session.tts and publishing to
        the room's audio output manually.
        """
        for _ in range(30):  # 30 × 100 ms = 3 s
            if session._activity is not None:
                try:
                    session.say(text)
                    return
                except RuntimeError as e:
                    if "isn't running" not in str(e):
                        raise
                    # fall through and retry
            await _asyncio.sleep(0.1)
        # Fallback path — the session hasn't produced an activity in
        # 3 s, which shouldn't happen in practice but covers edge
        # cases (agent still booting, reconfiguring). We warn and
        # drop the utterance rather than crashing.
        logger.warning(
            f"session.say unavailable after 3s wait — dropping: {text[:60]}"
        )

    @ctx.room.on("data_received")
    def _on_data(packet) -> None:
        try:
            msg = _json.loads(packet.data.decode("utf-8"))
        except Exception:
            return
        if not isinstance(msg, dict):
            return
        t = msg.get("type")
        if t == "speak":
            text = (msg.get("text") or "").strip()
            if text:
                logger.info(f"data-speak: {text[:60]}…")
                _asyncio.create_task(_speak_when_ready(text))
        elif t == "stop":
            # interrupt() has the same activity guard. Swallow its
            # RuntimeError if the session is idle — there's nothing
            # to interrupt anyway.
            logger.info("data-stop: interrupting current utterance")
            try:
                session.interrupt()
            except RuntimeError:
                pass

    # Greet the user as soon as the connection is up, so they know
    # the agent is alive without having to speak first. The "don't
    # mention you were just connected" guard keeps the greeting from
    # sounding robotic ("I just joined the room…").
    await session.generate_reply(
        instructions="Greet the user by name in one short sentence. "
                     "Do not mention that you just connected.",
    )


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        ),
    )
