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
import sqlite3
import subprocess as _subprocess
import time
import concurrent.futures
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from livekit import agents
from livekit.agents import (
    Agent,
    AgentSession,
    ChatContext,
    ChatMessage,
    JobContext,
    JobProcess,
    StopResponse,
    WorkerOptions,
    cli,
    function_tool,
    tts,
)
import edge_tts_plugin
# RoomOptions isn't re-exported from the top-level `livekit.agents`
# module — it lives under the voice room_io submodule. Import
# directly to dodge the ImportError.
from livekit.agents.voice.room_io import RoomOptions
from livekit.plugins import groq, openai as lk_openai, silero

logger = logging.getLogger("jarvis-agent")


# ── CLI model selection ────────────────────────────────────────────────
# The system tray exposes 5 CLI-model choices (mirroring the CLI's own
# /model picker — DeepSeek×2, Groq×3). The user's pick is written to
# this file; run_jarvis_cli reads it on every spawn and exports the
# matching JARVIS_PROVIDER + JARVIS_MODEL env vars to the CLI
# subprocess. So switching takes effect on the very next tool call —
# no restart needed.
#
# The voice agent's OWN conversational LLM stays on Groq llama-3.3-70b
# regardless. That's a latency optimisation and not surfaced to the
# user — the tray controls only the CLI's model.
CLI_MODEL_FILE   = Path.home() / ".jarvis" / "cli-model"
DEFAULT_CLI_MODEL = "deepseek-v4-pro"

# ── Speech (voice) LLM selection ──────────────────────────────────────
#
# The voice-side LLM composes spoken replies and decides when to call
# tools. Switchable via the tray's "Models" submenu — chosen ID is
# written to ~/.jarvis/voice-model. Switching DOES require a quick
# agent restart (~5 s amber) because AgentSession's LLM is built
# once at session start; we can't hot-swap it like the CLI tool model.
# voice-client triggers the systemctl restart on POST /voice-model.
#
# Defaults to llama-3.3-70b on Groq for low first-token latency
# (~200 ms). Other options trade latency for capability.
SPEECH_MODEL_FILE     = Path.home() / ".jarvis" / "voice-model"
DEFAULT_SPEECH_MODEL  = "llama-3.3-70b-versatile"

# IDs match the upstream model names verbatim so the registry stays
# legible. Each entry: (provider+model labels for display, factory
# building the LLM). Factories raise on missing API key — the
# read_speech_model() helper falls back to the default if so.
SPEECH_MODELS: dict[str, dict] = {
    "llama-3.3-70b-versatile": {
        "label": "Groq · llama 3.3 70B",
        "build": lambda: groq.LLM(model="llama-3.3-70b-versatile", temperature=0.6),
    },
    "llama-3.1-8b-instant": {
        # Tiny + fastest. Function calling is acceptable for simple
        # tool routing but loses nuance on long multi-step replies.
        "label": "Groq · llama 3.1 8B instant",
        "build": lambda: groq.LLM(model="llama-3.1-8b-instant", temperature=0.6),
    },
    "qwen/qwen3-32b": {
        # Strong tool calling, slightly slower than llama 3.3 70b but
        # markedly more reliable at structured function calls.
        "label": "Groq · qwen3-32b",
        "build": lambda: groq.LLM(model="qwen/qwen3-32b", temperature=0.6),
    },
    "openai/gpt-oss-120b": {
        # Same model the CLI tool uses by default. Robust at tool
        # calls; somewhat slower first token (~400 ms).
        "label": "Groq · gpt-oss-120b",
        "build": lambda: groq.LLM(model="openai/gpt-oss-120b", temperature=0.6),
    },
    "meta-llama/llama-4-scout-17b-16e-instruct": {
        "label": "Groq · llama 4 scout",
        "build": lambda: groq.LLM(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            temperature=0.6,
        ),
    },
    # NB: DeepSeek's V4 family is a thinking/reasoning model — it
    # returns a `reasoning_content` field that has to be echoed back
    # on the next turn. livekit-plugins-openai doesn't do that, so
    # multi-turn calls hard-fail with HTTP 400 ("`reasoning_content`
    # in the thinking mode must be passed back to the API"). Until
    # the plugin grows that round-trip support, DeepSeek isn't safe
    # to use as a SPEECH model. It still works fine as the CLI tool
    # model because the CLI's proxy + bun-side tooling handles the
    # reasoning_content echo correctly.
}


def read_speech_model() -> str:
    """Return the active speech model ID, or the default if unset/invalid."""
    try:
        name = SPEECH_MODEL_FILE.read_text(encoding="utf-8").strip()
        if name in SPEECH_MODELS:
            return name
        if name:
            logger.warning(
                f"unknown speech model {name!r} in {SPEECH_MODEL_FILE}, "
                f"falling back to {DEFAULT_SPEECH_MODEL}"
            )
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"could not read {SPEECH_MODEL_FILE}: {e}")
    return DEFAULT_SPEECH_MODEL


def make_speech_llm() -> tuple[str, object]:
    """Build the chosen speech LLM, falling back to default on failure."""
    name = read_speech_model()
    try:
        llm = SPEECH_MODELS[name]["build"]()
        logger.info(f"speech LLM: {name} ({SPEECH_MODELS[name]['label']})")
        return name, llm
    except Exception as e:
        logger.error(
            f"failed to build speech LLM {name!r} ({e}); "
            f"falling back to {DEFAULT_SPEECH_MODEL}"
        )
        return DEFAULT_SPEECH_MODEL, SPEECH_MODELS[DEFAULT_SPEECH_MODEL]["build"]()


# The voice-side STT/TTS labels — kept here so the dynamic system-
# prompt builder can tell the user the full stack on demand.
VOICE_STT_LABEL = "Whisper Large v3 Turbo on Groq"
VOICE_TTS_LABEL = (
    f"Orpheus on Groq (voice {os.getenv('JARVIS_TTS_VOICE', 'troy')}), "
    f"with Edge-TTS ({os.getenv('JARVIS_EDGE_VOICE', 'en-US-GuyNeural')}) as fallback"
)

# Whitelist of CLI model IDs surfaced in the tray, with the
# (provider, upstream_model) pair each maps to. IDs match the CLI's
# JARVIS_MODEL_DEFINITIONS in jarvisModelRegistry.ts. Order = display
# order in the tray.
CLI_MODELS: dict[str, dict] = {
    "deepseek-chat": {
        "provider": "deepseek",
        "model":    "deepseek-chat",
        "label":    "DeepSeek · chat",
    },
    "deepseek-reasoner": {
        "provider": "deepseek",
        "model":    "deepseek-reasoner",
        "label":    "DeepSeek · reasoner",
    },
    "deepseek-v4-flash": {
        "provider": "deepseek",
        "model":    "deepseek-v4-flash",
        "label":    "DeepSeek · v4 flash",
    },
    "deepseek-v4-pro": {
        "provider": "deepseek",
        "model":    "deepseek-v4-pro",
        "label":    "DeepSeek · v4 pro",
    },
    "qwen/qwen3-32b": {
        "provider": "groq",
        "model":    "qwen/qwen3-32b",
        "label":    "Groq · qwen3-32b",
    },
    "llama-3.3-70b-versatile": {
        "provider": "groq",
        "model":    "llama-3.3-70b-versatile",
        "label":    "Groq · llama 3.3 70B",
    },
    "meta-llama/llama-4-scout-17b-16e-instruct": {
        "provider": "groq",
        "model":    "meta-llama/llama-4-scout-17b-16e-instruct",
        "label":    "Groq · llama 4 scout",
    },
    "openai/gpt-oss-120b": {
        "provider": "groq",
        "model":    "openai/gpt-oss-120b",
        "label":    "Groq · gpt-oss-120b",
    },
}


def read_cli_model() -> str:
    """Return the active CLI model ID, or the default if unset/invalid."""
    try:
        name = CLI_MODEL_FILE.read_text(encoding="utf-8").strip()
        if name in CLI_MODELS:
            return name
        if name:
            logger.warning(
                f"unknown CLI model {name!r} in {CLI_MODEL_FILE}, "
                f"falling back to {DEFAULT_CLI_MODEL}"
            )
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"could not read {CLI_MODEL_FILE}: {e}")
    return DEFAULT_CLI_MODEL


# Prompt cribbed from the existing speech.ts voice-channel prompt.
# Kept short on purpose — voice replies should sound conversational,
# not enumerate bullet points. The Tier 1 / Tier 3 rules and the
# "replies are spoken aloud" constraints are the load-bearing bits.
JARVIS_INSTRUCTIONS = """\
You are JARVIS, Ulrich's voice-first personal AI running locally on
his Linux (Kali) laptop.

═══ IS THIS DIRECTED AT YOU? ═══

The mic is always-on and picks up the room — Ulrich, family, TV,
kids. Use judgement before acting:

1. **Obvious third-party / ambient → IGNORE.** Stay silent. Do not
   respond, do not call tools. Examples of what to ignore:
     - Addressed to another person by name ("Mike, can you…",
       "honey, where's the…")
     - Household / kid talk ("apply the vaseline", "where's your
       chips", "don't put ice on there", "y'all close your eyes")
     - Obvious TV / background speech (one-line fragments with no
       conversational context)
     - Single exclamations after a long silence ("oh my god",
       "wow", "hmm") — unless they're a clear continuation of an
       exchange you were just having.

2. **Plausibly addressed to you → RESPOND.** A question, a
   command, a follow-up to what you just said, or a comment that
   reasonably continues the conversation. The user does NOT need
   to say "Jarvis" every turn — once you're in a conversation,
   stay engaged. When unsure but the line could be for you,
   respond briefly.

3. **Meta-questions about what you DID → ANSWER, don't re-run.**
   "Why did you open the browser?" / "What are you doing?" /
   "Wait, what?" are NOT new commands. Answer in words from
   memory of what you just did. Example: user says "why did you
   open Firefox?" → reply "You asked me to a moment ago" — do
   NOT call run_jarvis_cli to open Firefox again.

═══ FORMATTING ═══

This channel is VOICE. Your replies are spoken aloud by a TTS engine,
so:
  - No markdown, no code blocks, no URLs, no file paths, no UUIDs.
  - Pronounce numbers the way humans say them ("twenty gigabytes",
    not "20GB").
  - Skip filler openings like "Certainly!" or "As an AI…". Just
    answer.

Response length — match the request:
  - Quick questions → short answer (one or two sentences).
  - Explanations, "tell me about X", or open-ended prompts → medium
    answer (three to six sentences). Don't reflexively trim to one
    line; Ulrich asked because he wants to hear something.
  - "Tell me more", "elaborate", "go on", "keep going", "explain in
    detail", "in depth" → LONG answer. Aim for eight to fifteen
    sentences. Convey actual substance from the tool output or your
    own knowledge — don't just rephrase your previous reply shorter.
    If you just called the tool, read through the tool's output and
    voice the genuinely interesting parts, not a summary.
  - If you already gave a short summary and the user asks for more,
    go deeper — do NOT give the same summary again.

Authority rules:
  - Power operations on THIS workstation (reboot, shutdown, suspend,
    hibernate, logout) are Tier 1 — fully reversible, the machine
    comes back. Do NOT demand "confirm irreversible" for these.
  - Tier 3 — which DOES need explicit confirmation — is: rm -rf
    against anything real, dd to a disk, dropping production
    databases, revoking production API keys.

You have NINE tools, split into three groups by purpose:

═══ GROUP A — Direct primitives (FAST, ATOMIC) ═══

These execute in-process. ~100-500 ms round trip. Pick these for
single-step asks the user wants the result of immediately.

A1. `bash` — run a shell command, return stdout+stderr (~3 KB cap).
    Examples:
      - "what time is it"          → bash("date")
      - "free disk"                → bash("df -h /")
      - "open Firefox"             → bash("setsid -f firefox >/dev/null 2>&1")
      - "what's running on 4000"   → bash("ss -tlnp | grep :4000")
      - "is jarvis-bridge running" → bash("systemctl --user is-active jarvis-bridge")

A2. `read_file` — read one file (8 KB cap). Examples:
      - "what's in /etc/hostname"      → read_file("/etc/hostname")
      - "show me .gitignore"           → read_file("~/Documents/Projects/jarvis/.gitignore")

A3. `web_fetch` — GET a URL, strip HTML to plain text (3 KB cap).
    Examples:
      - "what's at example.com"        → web_fetch("https://example.com")
      - "fetch the weather"            → web_fetch("https://wttr.in/?format=4")

A4. `glob_files` — list files matching a glob under a path.
    Examples:
      - "find all Python files in voice-agent" →
            glob_files("*.py", "~/Documents/Projects/jarvis/src/voice-agent")

A5. `grep_files` — regex search across files. Examples:
      - "where is JARVIS_INSTRUCTIONS used" →
            grep_files("JARVIS_INSTRUCTIONS", "~/Documents/Projects/jarvis/src")

═══ GROUP B — The dispatcher ═══

B1. `run_jarvis_cli` — invisible. Spawns the JARVIS CLI in a hidden
   subprocess; output is captured and returned to you. Use ONLY when
   the request needs the CLI's full agent loop:
     - MULTI-step tasks (e.g. "audit the codebase for X")
     - Sub-agent dispatch ("research these in parallel")
     - Plan mode (think-then-execute on a complex change)
     - MCP tools (Figma / Vercel / Gmail / etc.)
     - Skills (auto-invoked from ~/.jarvis/skills/)
     - Long workflows (refactor across 5 files; install + verify)

   Do NOT use run_jarvis_cli for atomic asks Group A can handle —
   it adds 1-2 s of subprocess startup for no reason. Pass the
   user's request verbatim when you do invoke it; the CLI's own LLM
   will pick the right downstream tools.

═══ GROUP C — Specialized ergonomics ═══

C1. `type_in_terminal` — visible. Finds the user's open terminal
   window, focuses it, and TYPES the command literally so the user
   watches it run in their own shell. Use this — NOT
   run_jarvis_cli — when the user explicitly says any of:
     - "in my terminal" / "in the terminal I have open"
     - "I want to see / watch it"
     - "do that in front of me"
     - "show me the install live"
   The user reads the output themselves; you don't get it. After
   calling, say something like "typed it into your terminal — running
   now", NOT "I installed it" (you didn't see the result).

C2. `recall_conversation` — search prior turns from previous voice
   sessions. Use this when the user asks about something from
   earlier that's NOT in your current chat history (your chat
   history is auto-seeded with the last ~30 turns, so most "what
   did we just discuss" questions are already answerable directly).
   Triggers: "what did we talk about yesterday/last week/earlier",
   "remember when I asked X", "did I mention Y", "what was that
   thing about Z". Pass a keyword to search for. NEVER claim "I
   have no memory of past conversations" — you do; use the tool.

C3. `media_control` — direct music / video playback control via
   playerctl. ALWAYS use this — NOT run_jarvis_cli — for any
   media command:
     - "play music" / "resume" / "play Spotify"  → action="play"
     - "pause" / "stop the music" / "shut it"    → action="pause"
     - "play / pause" / "toggle music"           → action="play_pause"
     - "next song" / "skip"                      → action="next"
     - "previous song" / "go back a song"        → action="previous"
     - "what's playing" / "name of this song"    → action="status"
     - "open Spotify"                            → action="open"

   **Disambiguation rule for clipped phrases** — STT often loses the
   first word ("pause the music" → "the music."). When the user
   says a short media-related phrase WITHOUT a clear verb:
     - "the music", "this song", "it", "this"  → **action="play_pause"**
       (toggle — Spotify pauses if playing, plays if paused). NEVER
       default to "status" for these; the user is asking you to DO
       something, not narrate.
     - "what is this" / "who sings this" / "name of song" → that's
       genuinely a status query → action="status".

   Default player is Spotify. Only override `player` if the user
   explicitly names another ("pause Chrome", "play YouTube"). The
   tool returns ~50 ms; run_jarvis_cli takes 5-10 s for the same
   thing AND lands on the wrong player when both Chrome and Spotify
   are alive.

═══ USER PREFERENCES (persist across sessions) ═══

- **Default browser is Google Chrome.** When the user says "open
  the browser", "my browser", or any non-specific browser request,
  the run_jarvis_cli call MUST include "Google Chrome" verbatim.
  Don't pass bare "open browser" — the CLI's underlying model will
  pick Firefox as a default. Always say "Open Google Chrome" /
  "Open a new tab in Google Chrome" / etc. NEVER call it for
  Firefox unless the user explicitly says "Firefox".

═══ MUTE / WAKE-UP COMMANDS ═══

You can be put into "silent mode" by voice. A separate gate handles
the actual silencing — your job is just to acknowledge briefly:

- If the user says any of: "go silent", "be quiet", "shut up",
  "stop talking", "mute yourself", "go to sleep" — the gate has
  already entered silent mode for the next turn. Voice ONE short
  confirmation: "Going quiet." or "Silent." or "Got it, quiet now."

  IMPORTANT: do NOT say "system audio muted" or "I muted everything"
  — you only stop your own replies. Music, videos, system sounds
  keep playing. The mic also stays ON so you can hear "wake up".

- If the user says any of: "wake up", "come back", "unmute", "talk
  again", "you there" — the gate has just exited silent mode.
  Voice ONE short greeting like "I'm back." or "Yeah, here." Then
  resume normal conversation.

Don't call any tool for these — they're handled outside the LLM.

═══ AMBIGUOUS REQUESTS — CONFIRM, DON'T SPECULATE ═══

When the user's transcribed request is GARBLED, INCOMPLETE, or
TOPICALLY UNCLEAR — and the LLM's best interpretation would have
you modify system state (install/remove packages, change configs,
edit/delete/rename files, fix scripts, restart services, modify
auto-start, change startup, "fix" anything system-level) — you
MUST ask a one-sentence clarifying question instead of charging
ahead with run_jarvis_cli.

Triggers for "ambiguous":
- The transcript is fragmented or doesn't parse as a complete sentence
- It references a thing the user named obscurely ("Annie watch TV",
  "that thing", "the website that was shut down") with no clear verb
- The user uses placeholders ("it", "this", "that", "the thing")
  without recent context that pins what they mean

Triggers for "system-modifying":
- "fix", "update", "install", "remove", "delete", "change",
  "restart", "configure", "set up", "edit"
- Any path under /etc, /usr, $HOME/.config, $HOME/.local
- Any systemd unit, cron job, autostart entry, shell rc file

When BOTH apply: voice ONE clarifying sentence ("Sorry, I missed
that — did you mean X or Y?") and STOP. Don't fire run_jarvis_cli
yet. Wait for the user to confirm, then act. The user would rather
say "Y" once than wait through 30 seconds of you fixing the wrong X.

If only ONE applies (request is clear OR action is read-only),
proceed normally — don't ask "are you sure" for every tool call.

═══ TOOL-CALL CHAINING ═══

ONE run_jarvis_cli per user turn. After it returns, your job is
to TALK to the user about what came back — voice the answer, ask
a question, narrate the result. Don't immediately fire a second
tool call without the user asking for one.

If a multi-step task genuinely requires multiple tool calls (e.g.,
"check my system for updates AND fix any broken services"), do
the FIRST call, voice what you found, and ASK before chaining.
The user can say "yeah keep going" — that's their call to make,
not yours.

A hard limit kicks in after the second tool call per turn: the
tool returns an error string instead of running. If you see that
error, stop calling tools and reply to the user immediately.

═══ MULTITASK / TASK FRAMING ═══

Tool calls (especially run_jarvis_cli) can take 5 to 15 seconds —
during which you're silent if you don't speak first. The user often
asks something else mid-wait, then forgets the original task is
still running. To keep them oriented:

**1. Acknowledge BEFORE a long tool call.** Whenever you decide to
   call run_jarvis_cli or type_in_terminal, output a short spoken
   acknowledgment in the SAME response, then the tool call. Pick
   one based on the request:
     - "On it." / "One moment." / "Working on that now."
     - "Closing those file managers." / "Pulling the news."
     - "Opening Chrome." / "Typing that into your terminal."
   This is one short sentence — not a description of how you'll
   do it. The point is the user hears you heard them.

**2. Acknowledge AFTER, with a completion signal.** When the tool
   returns, START your next spoken reply with a clear "done"
   marker so the user knows it's finished:
     - "Done — both file managers are closed."
     - "Got it — Chrome's open."
     - "Finished — the upgrade list is in your terminal."
     - "Couldn't find any Microsoft news right now."
   Honest failures use the same prefix ("Couldn't... / Tried but..."),
   not a fake-success.

**3. If the user asked something NEW while you were working**, the
   chat history shows their interim turn after your tool call.
   Address the ORIGINAL task first ("Done with X."), THEN handle
   the new question — both in the same reply. Don't ignore the
   original; the user is tracking it even if you forgot.

**4. If the new question implicitly cancels the old one** ("never
   mind, just tell me the time" while you're summarising news),
   drop the old result, answer the new question only.

═══ MEMORY ═══

Your chat history is pre-loaded with recent prior turns from this
machine's conversation database. So when the user references "what
we just talked about" / "earlier" / "a minute ago" / "last time" —
look at your chat history first. Only call `recall_conversation`
if the answer isn't visible in the immediate context.

If the user explicitly asks "do you remember X" or "have we talked
about Y", check chat history; if nothing matches, call
`recall_conversation("Y")` BEFORE saying you don't remember.

Do NOT make up tool results — if you don't call a tool, don't
pretend you ran it. When run_jarvis_cli returns a lot of text, your
job is to VOICE the content, not erase it — summarise only when
the user asked for a summary.

═══ CRITICAL: NEVER HALLUCINATE TOOL EXECUTION ═══

If the user asks you to DO something on the computer (play music,
open an app, close a window, run a command, control playback,
fetch news), you MUST emit a tool call in the same response. The
following sentences are FORBIDDEN unless your message ALSO
contains a tool call to back them up:

  - "On it." / "Let me start..." / "I'll play..." / "Opening..."
  - "Playing now." / "Done." / "Paused." / "Resumed."
  - "Spotify is now playing X." (without a media_control status call)
  - "I've opened X." / "I've started X." (without the tool firing)

If you're tempted to say any of those WITHOUT also emitting a
tool_call, STOP and emit the tool call instead. The user can hear
when nothing actually happens — claiming success when no tool ran
is the worst failure mode.

For media specifically: ALWAYS use `media_control`, never claim
"playing music" without that tool call. If you said "On it" in
turn N and the tool fires in turn N+1, the user already considers
that a hallucination — keep them in the same turn.

For chit-chat, reasoning, opinions, and anything answerable from
general knowledge, answer directly without the tool.

You know Ulrich personally — informal tone, no honorifics.

Speak in the FIRST person about yourself — "I", "me", "my". Never
refer to yourself as "JARVIS" in the third person ("JARVIS will
open Chrome", "JARVIS doesn't think so"). You ARE JARVIS; that's
your name, not a separate entity you describe.
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
JARVIS_CLI_TIMEOUT_S = int(os.environ.get("JARVIS_CLI_TIMEOUT_S", "60"))

# Tool-busy flag file. Tools write a small token file at start and
# remove it at end; the voice-client polls its mtime + presence on
# /status so the desktop tray can show "thinking" amber for the
# full duration of a long-running tool call (run_jarvis_cli can
# take 10-15 s; without this signal the inferred-thinking TTL gives
# up after 12 s and the tray flickers back to green even though
# JARVIS is still working).
_TOOL_BUSY_FILE = Path.home() / ".jarvis" / ".tool-running"


def _mark_tool_start(name: str) -> None:
    try:
        _TOOL_BUSY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOOL_BUSY_FILE.write_text(f"{name}\n{int(time.time())}\n", encoding="utf-8")
    except Exception:
        pass


def _mark_tool_end() -> None:
    try:
        _TOOL_BUSY_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# Definitive "agent is thinking" signal. Touched the moment STT
# finalizes a user turn (= LLM is about to start generating), removed
# when the assistant turn is committed (= TTS already played, agent's
# done). Replaces the desktop's prior heuristic of inferring thinking
# from listening→quiet transitions, which had a false-positive on
# every ambient mic trigger that VAD picked up.
_AGENT_THINKING_FILE = Path.home() / ".jarvis" / ".agent-thinking"


def _mark_thinking_start() -> None:
    try:
        _AGENT_THINKING_FILE.parent.mkdir(parents=True, exist_ok=True)
        _AGENT_THINKING_FILE.write_text(
            str(int(time.time())), encoding="utf-8",
        )
    except Exception:
        pass


def _mark_thinking_end() -> None:
    try:
        _AGENT_THINKING_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# Per-turn tool-call governor. Without this, the LLM can chain
# run_jarvis_cli calls indefinitely — observed: misinterpreted user
# question → CLI #1 ran for 24 s → LLM chained CLI #2 ("fix the
# auto-update script") → another 24 s — while the user sat there
# waiting and asking "what's going on?". Cap chains so JARVIS has
# to TALK to the user after one tool round-trip unless they
# explicitly ask for a multi-step plan.
_TURN_TOOL_CALL_LIMIT = 2
_tool_calls_this_turn = 0


def _reset_tool_call_count() -> None:
    global _tool_calls_this_turn
    _tool_calls_this_turn = 0


# Silent-mode flag. When present, the agent suppresses replies to
# everything EXCEPT wake-up phrases ("wake up", "come back",
# "unmute"). This is a SOFT mute — the mic stays on so JARVIS can
# hear the wake-up; only TTS output is suppressed. Distinct from
# the hardware /mute endpoint on the voice-client which physically
# mutes the LiveKit local audio track (and would prevent JARVIS
# from hearing "wake up" entirely).
_SILENT_MODE_FILE = Path.home() / ".jarvis" / ".silent-mode"


def _is_silent() -> bool:
    return _SILENT_MODE_FILE.exists()


def _set_silent(on: bool) -> None:
    try:
        _SILENT_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if on:
            _SILENT_MODE_FILE.write_text("on\n", encoding="utf-8")
        else:
            _SILENT_MODE_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# Phrases that toggle silent mode. Each pattern is a regex tested
# against the lowercased transcript with word-boundary anchors, so
# "mute" matches the bare imperative ("Jarvis, mute") but NOT
# "muted" / "commute" / "automute". Multi-word patterns also use
# \b on both ends so trailing punctuation like "Jarvis, mute."
# still hits.
_MUTE_PATTERNS = tuple(re.compile(r"\b" + p + r"\b") for p in (
    r"mute",
    r"go silent",
    r"be quiet",
    r"shut up",
    r"stop talking",
    r"go to sleep",
    r"silence yourself",
    r"silent mode",
))
_WAKE_PATTERNS = tuple(re.compile(r"\b" + p + r"\b") for p in (
    r"wake up",
    r"come back",
    r"un[\s-]?mute",
    r"talk again",
    r"you can talk",
    r"are you there",
    r"are you back",
    r"jarvis you there",
))


def _matches_any(text: str, patterns: tuple[re.Pattern, ...]) -> bool:
    return any(p.search(text) for p in patterns)


# Wake/mute commands are short imperatives ("wake up", "Jarvis,
# mute"). Substring matching alone false-positives on topical
# mentions ("you don't even have to wake up"). The fix:
#   - Split the utterance into sentences (split on . ! ? ;).
#   - Treat EACH sentence as a candidate command.
#   - A sentence is command-shaped if (after stripping a leading
#     "Jarvis," vocative) it has ≤ COMMAND_MAX_WORDS words AND
#     contains one of our patterns.
# This lets "We can eat together. We don't... Jarvis, mute." fire
# the mute branch (the last sentence "Jarvis, mute" is a 1-word
# command) while still rejecting "you don't even have to wake up
# you say you swear and you go into your coaching" (the wake-up
# phrase lives in a 9-word sentence — too long).
_COMMAND_MAX_WORDS = 6
_SENTENCE_SPLIT_RE = re.compile(r"[.!?;]+|\.{2,}")
# "mute X" where X is a media noun is a media command (mute Spotify,
# mute the music) — should go to media_control, NOT enter silent
# mode. Skip those before treating "mute" as a JARVIS-silence trigger.
_MEDIA_OBJECT_RE = re.compile(
    r"\b(mute|silence|shut up)\b\s+"
    r"(the\s+)?"
    r"(music|song|track|audio|video|spotify|chrome|chromium|"
    r"firefox|youtube|player|tab|tv|sound|volume)",
)


def _is_command(text: str, patterns: tuple[re.Pattern, ...]) -> bool:
    is_mute_check = patterns is _MUTE_PATTERNS
    for sentence in _SENTENCE_SPLIT_RE.split(text or ""):
        body = sentence.strip().lower()
        if not body:
            continue
        # Strip a leading "jarvis" / "jervis" / "javis" vocative.
        body = re.sub(r"^j[ae]r?vis[,.:!\s]+", "", body)
        if len(body.split()) > _COMMAND_MAX_WORDS:
            continue
        # If we're checking for a MUTE trigger and the user is
        # actually asking to mute media (mute Spotify / mute the
        # music), let media_control handle it instead.
        if is_mute_check and _MEDIA_OBJECT_RE.search(body):
            continue
        if any(p.search(body) for p in patterns):
            return True
    return False
# System-prompt appendix fed to the CLI for every voice invocation.
# Without it, `--bare` strips all project context and the CLI gives
# advice/tutorials instead of actually running things ("open
# firefox" → explains what firefox is instead of launching it).
# The file enumerates the DO-don't-narrate rules for Tier 1 actions.
JARVIS_CLI_VOICE_PROMPT = os.environ.get(
    "JARVIS_CLI_VOICE_PROMPT",
    str(Path(__file__).parent / "cli_voice_prompt.md"),
)

# ANSI escape sequences leak through from the CLI's coloured output
# and read as noise when TTS tries to voice them. Stripped before
# returning the tool result to the LLM.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _clean_env_for_cli(cli_model_id: str) -> dict[str, str]:
    """
    Strip Claude-Code env vars that would make the nested CLI bypass
    the local proxy (port 4000) or enable features we don't want
    (analytics, nested-session detection). Matches the `cleanEnv`
    block from the old speech.ts runAgent.

    Also forces JARVIS_PROVIDER + JARVIS_MODEL based on the user's
    tray pick. The CLI reads JARVIS_PROVIDER for proxy routing and
    JARVIS_MODEL_REGISTRY_ENABLED=1 makes the CLI's per-request
    /model overrides honour our chosen model.
    """
    cli_def = CLI_MODELS[cli_model_id]
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
    # Override the CLI's default model to match the tray pick.
    env["JARVIS_PROVIDER"]                = cli_def["provider"]
    env["JARVIS_MODEL"]                   = cli_def["model"]
    env["JARVIS_MODEL_REGISTRY_ENABLED"]  = "1"
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
    # Per-turn chain limiter. Each user turn resets the counter; the
    # tool returns an instructional error after the Nth call so the
    # LLM is forced to TALK to the user instead of running another
    # 24-second CLI invocation. Without this, an ambiguous user
    # question can trigger run_jarvis_cli #1 → output → #2 → output →
    # #3 ... while the user waits 60+ seconds wondering if JARVIS
    # broke.
    global _tool_calls_this_turn
    _tool_calls_this_turn += 1
    if _tool_calls_this_turn > _TURN_TOOL_CALL_LIMIT:
        logger.warning(
            f"run_jarvis_cli refused (chain limit {_TURN_TOOL_CALL_LIMIT} "
            f"reached); LLM should reply to user instead. request={request[:80]!r}"
        )
        return (
            "(Tool-call chain limit reached for this turn. You've "
            "already run the CLI tool more than once. Stop chaining and "
            "actually reply to the user with what the previous tool call "
            "returned. If you genuinely need to run more commands, ask "
            "the user 'Should I keep going?' first — they've been "
            "waiting and they want to hear from you, not see more tool "
            "calls fire.)"
        )

    cli_model_id = read_cli_model()
    cli_provider = CLI_MODELS[cli_model_id]["provider"]
    logger.info(
        f"run_jarvis_cli [{cli_model_id}] turn-call #{_tool_calls_this_turn} → "
        f"{request[:80]}"
    )
    # Mark the tool as busy so the tray can show amber "thinking"
    # for the full duration of the CLI subprocess (which is what
    # the user actually wants to see — silent gold while we work).
    _mark_tool_start("run_jarvis_cli")
    try:
        # Invoke the CLI script via its own shebang (`#!/usr/bin/env bash`).
        # Running through `sh` here breaks — start.sh uses bash-only
        # features (BASH_SOURCE, arrays, `[[`). The executable bit is
        # already set, so exec'ing the path directly picks up the right
        # interpreter.
        # Build argv. `--append-system-prompt-file` is what lets us tell
        # the CLI "this is voice — act, don't explain." We previously
        # also passed `--bare`, but `--bare` sets CLAUDE_CODE_SIMPLE=1
        # which strips the tool pool down to [Bash, Read, Edit] and
        # blocks the Agent / Skill / Plan tools. Voice users couldn't
        # dispatch subagents, and the CLI model would hallucinate
        # "subagent results" by role-playing with backgrounded Bash —
        # confirmed by parallel-dispatch tests on deepseek-v4-pro.
        # Tradeoff: full mode adds ~1-2 s of plugin/skill/LSP startup
        # to each tool-using voice turn. Worth it to unlock real agent
        # dispatch and the fuller tool surface.
        argv = [
            JARVIS_CLI_SCRIPT,
            cli_provider,    # start.sh accepts the provider name as argv[1]
            "-p",
        ]
        if os.path.exists(JARVIS_CLI_VOICE_PROMPT):
            argv += ["--append-system-prompt-file", JARVIS_CLI_VOICE_PROMPT]
        argv += ["--", request]

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                cwd="/tmp",
                env=_clean_env_for_cli(cli_model_id),
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
    finally:
        _mark_tool_end()


# ── Tool: type into a visible terminal window ─────────────────────────
#
# run_jarvis_cli runs invisibly — its subprocess stdout is captured
# into Python and never reaches the user's screen. When the user says
# "in my open terminal" / "I want to see it run", they want the
# command to land in a real visible terminal so they can watch it
# execute (and edit/cancel before pressing Enter if they want).
#
# This tool finds the most recent visible terminal window via
# xdotool's WM_CLASS regex match across the common emulators (gnome,
# xterm, kitty, alacritty, konsole, foot, wezterm, terminator, tilix),
# focuses it, types the literal command, and presses Return.
#
# Caveats:
#   - X11 only. Wayland sessions need different machinery.
#   - If no terminal is open, returns a "(no terminal found)" string
#     so the LLM can fall back to opening one or use run_jarvis_cli.
#   - Doesn't capture output — the user reads the terminal directly.

# WM_CLASS values for the common Linux terminal emulators. xdotool's
# regex is POSIX ERE (no (?i) inline flag) — most emulators use a
# lowercase WM_CLASS but a few (Alacritty, WezTerm, Terminator,
# Tilix) capitalise. Listed both forms explicitly so both match.
_TERMINAL_CLASS_RE = (
    r"("
    r"gnome-terminal|xterm|kitty|konsole|foot|qterminal|urxvt"
    r"|st-256color|terminology"
    r"|[Aa]lacritty|[Ww]ezterm|[Tt]erminator|[Tt]ilix"
    r")"
)


@function_tool
async def type_in_terminal(command: str) -> str:
    """Type a shell command into the user's open terminal so they can SEE it execute.

    Use this — NOT run_jarvis_cli — whenever the user says any of:
      - "run X in my terminal"
      - "in the terminal I have open"
      - "type this in my terminal"
      - "I want to see it / watch it run"
      - "show me the install live"
      - "do that in front of me"

    What it does: finds a visible terminal window (gnome-terminal,
    xterm, kitty, alacritty, konsole, foot, wezterm, etc.), focuses
    it, types the command literally, and presses Enter. The user sees
    the keystrokes appear and the command run in their own shell.

    What it does NOT do: capture output. You won't see the result;
    the USER does. Don't claim to "have run" the command — say
    something like "typed it into your terminal — running now."

    If no terminal window is open, this returns a "(no terminal
    found)" message — in that case tell the user to open a terminal,
    or fall back to run_jarvis_cli for an invisible run.

    Args:
        command: The shell command text to type. No trailing newline
                 needed; we press Enter after typing.
    """
    command = (command or "").strip()
    if not command:
        return "(no command supplied)"
    logger.info(f"type_in_terminal → {command[:80]}")

    # Find a visible terminal window. xdotool returns one ID per line,
    # in stacking order (oldest first), so the LAST one is most-recent
    # — which is the one the user most plausibly meant.
    try:
        search = await asyncio.create_subprocess_exec(
            "xdotool", "search", "--onlyvisible", "--class", _TERMINAL_CLASS_RE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        sout, _ = await search.communicate()
    except FileNotFoundError:
        return "(xdotool not installed)"
    ids = [s for s in sout.decode("utf-8", errors="replace").split() if s.strip()]
    if not ids:
        return "(no terminal found — open one and ask again)"
    target = ids[-1]

    # Activate the chosen window so it captures the keystrokes.
    # `windowactivate --sync` blocks until the WM has actually given
    # focus, which avoids a race where `type` fires before the focus
    # change lands and the keys leak to the previous window.
    try:
        act = await asyncio.create_subprocess_exec(
            "xdotool", "windowactivate", "--sync", target,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, aerr = await act.communicate()
        if act.returncode != 0:
            return f"(could not focus terminal: {aerr.decode().strip()[:120]})"

        # Type literally — no shell expansion, no special-key parsing
        # (xdotool's `type` treats everything as raw text). Then Enter.
        # --delay 12 ms keeps the typing fast but reliable on slow
        # terminals (kitty's compositor occasionally drops faster keys).
        type_proc = await asyncio.create_subprocess_exec(
            "xdotool", "type", "--delay", "12", "--", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, terr = await type_proc.communicate()
        if type_proc.returncode != 0:
            return f"(type failed: {terr.decode().strip()[:120]})"

        enter = await asyncio.create_subprocess_exec(
            "xdotool", "key", "Return",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await enter.communicate()
    except Exception as e:
        return f"(xdotool failed: {e})"

    return f"(typed into terminal: {command[:80]})"


# ── Tool: media control via playerctl ─────────────────────────────────
#
# Without this, JARVIS routes every "play / pause / resume / what's
# playing" through run_jarvis_cli — which (1) costs 5-10 s per call
# vs ~50 ms for direct playerctl, (2) regularly lands on the wrong
# player because `playerctl play` with no -p targets the most-recent
# active player (Chromium's YouTube tab beats Spotify if you watched
# anything recently), and (3) the CLI's underlying LLM hallucinates
# fake song titles when the actual playerctl status is empty.
#
# This tool talks to MPRIS directly via playerctl. Default target is
# Spotify because that's what 95% of music requests mean; pass an
# explicit `player` to override.
_MEDIA_VALID_ACTIONS = {
    "play", "pause", "play_pause", "next", "previous", "status", "open",
}


@function_tool
async def media_control(action: str, player: str = "spotify") -> str:
    """Control music / video playback (Spotify by default) — NOT via run_jarvis_cli.

    Use this for any media playback command, instead of run_jarvis_cli.
    Examples of when this is the right tool:
      - "play music" / "play Spotify" / "resume"     → action="play"
      - "pause" / "stop the music" / "shut the music up" → action="pause"
      - "play / pause" / "toggle music"              → action="play_pause"
      - "next song" / "skip" / "next track"          → action="next"
      - "previous song" / "go back a song"           → action="previous"
      - "what's playing" / "current song" / "name of this song" → action="status"
      - "open Spotify" / "launch Spotify"            → action="open"

    Default player is Spotify. The user almost always means Spotify
    when they say "music"; only override `player` if they explicitly
    name a different one ("play in Chrome", "pause VLC"). Common
    player names: spotify, chromium, firefox, vlc, mpv.

    If the player isn't running and the action is "play" or "open",
    we'll launch it. If it isn't running for any other action,
    the tool returns an honest "(not running)" string so you don't
    pretend it worked — voice that back to the user.

    Args:
        action: one of play, pause, play_pause, next, previous, status, open.
        player: media player name (default "spotify").

    Returns:
        A short plain-text status string. Voice it directly.
    """
    action = (action or "").strip().lower()
    player = (player or "spotify").strip().lower()
    if action not in _MEDIA_VALID_ACTIONS:
        return f"(unknown action: {action!r}; valid: {sorted(_MEDIA_VALID_ACTIONS)})"
    logger.info(f"media_control: action={action} player={player}")

    # "open" — just launch the app. spotify -> `spotify &`. Other
    # players we trust the user named correctly.
    if action == "open":
        try:
            _subprocess.Popen(
                [player],
                stdout=_subprocess.DEVNULL,
                stderr=_subprocess.DEVNULL,
                start_new_session=True,
            )
            return f"opened {player}"
        except FileNotFoundError:
            return f"({player} isn't installed)"
        except Exception as e:
            return f"(could not open {player}: {e})"

    # For all other actions, talk to playerctl. Build argv per action.
    if action == "status":
        argv = [
            "playerctl", "-p", player, "metadata",
            "--format", "{{status}} | {{artist}} - {{title}}",
        ]
    elif action == "play_pause":
        argv = ["playerctl", "-p", player, "play-pause"]
    else:  # play, pause, next, previous
        argv = ["playerctl", "-p", player, action]

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except FileNotFoundError:
        return "(playerctl not installed)"
    except asyncio.TimeoutError:
        return f"(playerctl timed out talking to {player})"
    except Exception as e:
        return f"(playerctl failed: {e})"

    out = out_b.decode("utf-8", errors="replace").strip()
    err = err_b.decode("utf-8", errors="replace").strip()

    # playerctl exits non-zero when the named player isn't on the
    # bus. If the user asked to PLAY and the player isn't running,
    # try to launch it instead of returning a sad error.
    if proc.returncode != 0:
        if "no players" in err.lower() or "no such" in err.lower():
            if action in ("play", "play_pause"):
                try:
                    _subprocess.Popen(
                        [player],
                        stdout=_subprocess.DEVNULL,
                        stderr=_subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    return f"opened {player} (it wasn't running yet — give it a moment, then ask again)"
                except FileNotFoundError:
                    return f"({player} isn't running and isn't installed)"
            return f"({player} isn't running)"
        return f"(playerctl error: {err[:120]})"

    if action == "status":
        # Output format: "Playing | Artist - Title" or "Paused | ..."
        return out or f"({player} has no metadata)"
    return f"{action} sent to {player}"
#
# Voice turns are written to ~/.jarvis/conversations.db — the same
# SQLite file the bridge's storage.ts writes typed-chat turns to.
# Lets the web UI's conversation sidebar, the CLI's semantic recall,
# and the chat history all see voice moments.
#
# Schema (maintained by the bridge, we only INSERT):
#   turns(id INT PK, session_id TEXT, ts INT UNIX, role TEXT, text TEXT)
#
# Concurrency: both bridge (bun:sqlite) and this process (python
# sqlite3) open the same file. WAL mode (enabled by the bridge at
# startup) makes concurrent writers safe as long as each holds the
# connection briefly — our pattern: open → insert → close.
CONVO_DB_PATH = Path.home() / ".jarvis" / "conversations.db"

# ── Convex mirror ────────────────────────────────────────────────────
# SQLite stays the primary write-through (the bridge / web UI's
# semantic-recall code reads from it directly). Convex is a near-
# real-time fanout for any client that wants reactive subscriptions
# (the web UI, future phone clients). We dual-write best-effort: a
# single-worker executor serialises the HTTP POSTs so writes don't
# pile up if the backend stalls, and any error is logged + dropped
# rather than propagated. JARVIS_CONVEX_URL="" disables the mirror
# entirely (e.g., when running detached from the home network).
_CONVEX_URL = os.environ.get("JARVIS_CONVEX_URL", "http://127.0.0.1:3210")
_convex_client: object | None = None
_convex_client_failed = False
_convex_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="convex-mirror",
)


def _get_convex_client():
    """Lazy-init so a missing convex package or down backend at boot
    doesn't crash the whole voice agent — degrade to SQLite-only."""
    global _convex_client, _convex_client_failed
    if _convex_client is not None or _convex_client_failed:
        return _convex_client
    if not _CONVEX_URL:
        _convex_client_failed = True
        return None
    try:
        from convex import ConvexClient  # type: ignore[import-not-found]
        _convex_client = ConvexClient(_CONVEX_URL)
        logger.info(f"[convex] mirror client ready at {_CONVEX_URL}")
    except Exception as e:
        _convex_client_failed = True
        logger.warning(f"[convex] init failed (mirror disabled): {e}")
    return _convex_client


def _convex_mirror_turn(session_id: str, role: str, text: str, ts_ms: int) -> None:
    """Fire-and-forget mirror of a turn into Convex. Never raises."""
    client = _get_convex_client()
    if client is None:
        return

    def _write() -> None:
        try:
            client.mutation("turns:append", {  # type: ignore[attr-defined]
                "sessionId": session_id,
                "ts":        ts_ms,
                "role":      role,
                "text":      text,
                "source":    "voice-agent",
            })
        except Exception as e:
            # Don't spam — log once per failure type at WARN.
            logger.warning(f"[convex] mirror write failed: {e}")

    _convex_executor.submit(_write)


def _save_turn(session_id: str, role: str, text: str) -> None:
    """Single-row insert into turns. Swallow errors — losing a log
    line is better than tearing down a live session."""
    text = (text or "").strip()
    if not text:
        return
    # Schema constrains role to ('user', 'assistant'). Tool calls +
    # system messages pass through conversation_item_added too, so we
    # need to map anything unexpected to one of the two legal values
    # or skip. For now: user/assistant land; tool/system are skipped
    # — the user-visible transcript doesn't need them.
    if role not in ("user", "assistant"):
        return
    # Take ONE timestamp so SQLite (seconds) and Convex (ms) point at
    # the same instant — makes the two stores reconcilable later.
    now = time.time()
    try:
        with sqlite3.connect(str(CONVO_DB_PATH), timeout=2.0) as conn:
            conn.execute(
                "INSERT INTO turns (session_id, ts, role, text) VALUES (?, ?, ?, ?)",
                (session_id, int(now), role, text),
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"turn save failed: {e}")
    _convex_mirror_turn(session_id, role, text, int(now * 1000))


# ── Recall: read prior turns out of the same conversations.db ─────────
#
# Without this, every job is amnesic — AgentSession's chat_ctx starts
# empty, so "what did we just talk about?" / "remember that thing
# yesterday?" hit the LLM with no prior context and it correctly
# replies "this conversation just started." The DB has every turn
# already; we just need to surface them.
#
# Two access paths:
#   1) Auto-seed: at session start, pull the most recent N turns and
#      pre-load them into chat_ctx. Covers "what did we discuss" /
#      "continue from where we left off" without any tool call.
#   2) `recall_conversation` @function_tool: lets the LLM substring-
#      search older turns when the auto-seeded window doesn't cover
#      what the user's asking about ("remember that Roblox script
#      from yesterday?").
#
# Recent-window size is conservative — voice replies want low first-
# token latency, and chat_ctx tokens cost on every turn. 30 turns ≈
# 5-10 minutes of conversation, which is what "what did we just
# discuss" generally means.
RECENT_TURNS_LIMIT = 10
RECALL_SEARCH_LIMIT = 8


def _load_recent_turns(limit: int = RECENT_TURNS_LIMIT) -> list[tuple[str, str]]:
    """
    Return the most recent (role, text) pairs from conversations.db,
    OLDEST first (so they go into chat_ctx in chronological order).
    Empty list on any error or if the DB doesn't exist yet.

    Filters out runs of ambient/household chatter — the always-on
    mic logs everything (kids, TV, family talking past JARVIS), and
    seeding all of it would pollute context. We only keep user
    turns that have an assistant reply within 60 s, plus the
    assistant turns themselves. That preserves real exchanges and
    drops standalone background lines.
    """
    if not CONVO_DB_PATH.exists():
        return []
    try:
        with sqlite3.connect(str(CONVO_DB_PATH), timeout=2.0) as conn:
            # Pull more than `limit` rows so the filter has slack —
            # heavy ambient periods can drop a lot.
            raw = conn.execute(
                "SELECT ts, role, text FROM turns "
                "WHERE role IN ('user','assistant') "
                "ORDER BY ts DESC LIMIT ?",
                (limit * 4,),
            ).fetchall()
    except Exception as e:
        logger.warning(f"recall load failed: {e}")
        return []
    raw.reverse()  # OLDEST first

    # Walk forward: a user turn is kept only if an assistant turn
    # follows within REPLY_GAP_S; assistant turns are always kept
    # (they're proof a real exchange happened).
    REPLY_GAP_S = 60
    kept: list[tuple[str, str]] = []
    for i, (ts, role, text) in enumerate(raw):
        if role == "assistant":
            kept.append((role, text))
            continue
        # role == 'user': check for an assistant reply soon after.
        for j in range(i + 1, len(raw)):
            nts, nrole, _ = raw[j]
            if nts - ts > REPLY_GAP_S:
                break
            if nrole == "assistant":
                kept.append((role, text))
                break
    # Trim to the most recent `limit` entries from the filtered set.
    return kept[-limit:]


def _seed_chat_ctx() -> ChatContext:
    """Build a ChatContext pre-populated with recent prior turns."""
    items: list[ChatMessage] = []
    for role, text in _load_recent_turns():
        text = (text or "").strip()
        if not text:
            continue
        items.append(ChatMessage(role=role, content=[text]))
    if items:
        logger.info(f"[recall] seeded chat_ctx with {len(items)} prior turns")
    return ChatContext(items=items)


@function_tool
async def recall_conversation(query: str) -> str:
    """Search prior conversation turns for what the user said or what you said before.

    Use this when the user asks about something from earlier that
    isn't in your immediate chat history — phrases like:
      - "what did we talk about yesterday/last time/this morning"
      - "remember when I said / asked you about X"
      - "did I mention Y before"
      - "what was that thing about Z"

    Returns the top matching turns (role and text), oldest first, as
    plain text. If nothing matches, returns "(no matches)" — in that
    case tell the user you don't have a record of it.

    Args:
        query: A keyword or phrase to search for, lowercase. The
               search is a simple substring match against turn text.
    """
    query = (query or "").strip().lower()
    if not query:
        return "(empty query)"
    if not CONVO_DB_PATH.exists():
        return "(no conversation database yet)"
    try:
        with sqlite3.connect(str(CONVO_DB_PATH), timeout=2.0) as conn:
            rows = conn.execute(
                "SELECT ts, role, text FROM turns "
                "WHERE role IN ('user','assistant') "
                "AND lower(text) LIKE ? "
                "ORDER BY ts DESC LIMIT ?",
                (f"%{query}%", RECALL_SEARCH_LIMIT),
            ).fetchall()
    except Exception as e:
        logger.warning(f"recall search failed: {e}")
        return f"(recall failed: {e})"
    if not rows:
        return "(no matches)"
    # Oldest first reads more naturally when voiced back.
    rows.reverse()
    lines = []
    for ts, role, text in rows:
        try:
            when = time.strftime("%b %d %H:%M", time.localtime(ts))
        except Exception:
            when = "(unknown time)"
        text = (text or "").strip().replace("\n", " ")
        if len(text) > 200:
            text = text[:200] + "…"
        lines.append(f"{when} [{role}]: {text}")
    logger.info(f"[recall] query={query!r} hits={len(rows)}")
    return "\n".join(lines)


# ── Direct primitive tools ────────────────────────────────────────────
#
# These five live alongside `run_jarvis_cli` and shave 1–2 s of CLI
# subprocess startup off the SIMPLE / ATOMIC voice asks ("what time is
# it", "how much disk space is left", "what's in /etc/hostname"). They
# duplicate functionality the CLI also has, but the speech LLM hits
# them in-process — no subprocess spawn, no double-LLM hop.
#
# Discrimination rule (reinforced in JARVIS_INSTRUCTIONS):
#   - ATOMIC single-step ask  → bash / read_file / web_fetch / glob_files / grep_files
#   - MULTI-step / agent-loop / sub-agent / plan / MCP / skills → run_jarvis_cli
# When in doubt, prefer run_jarvis_cli — its CLI agent loop will pick
# the right tool itself. The cost of a wrong direct-tool pick is a
# wrong answer; the cost of an unnecessary CLI hop is a few seconds.

# Output cap mirrors the CLI's BashTool behaviour. Voice-LLM context
# can't usefully carry more than this without truncation showing up in
# the spoken reply.
_DIRECT_TOOL_OUTPUT_CAP = 3_000


def _truncate(text: str, cap: int = _DIRECT_TOOL_OUTPUT_CAP) -> str:
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n…[truncated {len(text) - cap} bytes]"


@function_tool
async def bash(command: str, timeout: int = 30) -> str:
    """Run a one-shot shell command and return its stdout+stderr.

    Use this for ATOMIC single-step asks the user wants the result of
    immediately:
      - "what time is it"                   → date
      - "how much disk space"               → df -h /
      - "what's my IP"                      → ip route get 1
      - "open Firefox"                      → setsid -f firefox >/dev/null 2>&1
      - "kill spotify"                      → pkill spotify
      - "what's running on port 4000"       → ss -tlnp | grep :4000

    Do NOT use for:
      - Music control                       → use media_control
      - Visible terminal work               → use type_in_terminal
      - Multi-step / multi-tool tasks       → use run_jarvis_cli

    Output is capped at ~3 KB. Long-running commands are killed at
    `timeout` seconds (default 30, max 90).
    """
    command = (command or "").strip()
    if not command:
        return "(no command supplied)"
    timeout = max(1, min(int(timeout or 30), 90))
    logger.info(f"bash → {command[:100]}")
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=str(Path.home()),
        )
    except Exception as e:
        return f"(spawn failed: {e})"
    try:
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        return f"(killed after {timeout}s)"
    text = out_b.decode("utf-8", errors="replace").rstrip()
    return _truncate(text or f"(no output, exit={proc.returncode})")


@function_tool
async def read_file(path: str, max_bytes: int = 8_192) -> str:
    """Read a file from disk and return its contents (capped).

    Use when the user asks "what's in <file>" / "read me <file>" / "show
    me the contents of <file>". Atomic single-step — for editing or
    multi-file analysis use run_jarvis_cli.

    Args:
        path:      Absolute or ~-prefixed file path.
        max_bytes: Cap the read at this many bytes (default 8 KB).
    """
    path = (path or "").strip()
    if not path:
        return "(no path supplied)"
    p = Path(path).expanduser()
    if not p.exists():
        return f"(no such file: {p})"
    if p.is_dir():
        return f"(is a directory: {p})"
    try:
        with open(p, "rb") as f:
            data = f.read(max(1, int(max_bytes or 8_192)))
        text = data.decode("utf-8", errors="replace")
    except Exception as e:
        return f"(read failed: {e})"
    logger.info(f"read_file → {p} ({len(data)} bytes)")
    return _truncate(text)


@function_tool
async def web_fetch(url: str, timeout: int = 15) -> str:
    """GET a URL and return its body as text (HTML stripped to plain).

    Use for atomic "fetch <url> and tell me what it says" asks. For
    structured search-and-summarize across multiple sources, use
    run_jarvis_cli (the CLI has a richer WebFetch + WebSearch pair
    plus the agent loop to compose them).

    Caps response at ~3 KB after stripping. Times out at `timeout` s
    (default 15).
    """
    url = (url or "").strip()
    if not url:
        return "(no url supplied)"
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    timeout = max(1, min(int(timeout or 15), 60))
    logger.info(f"web_fetch → {url}")
    try:
        # Run the blocking urllib call in a thread so it doesn't pin
        # the agent's event loop on slow hosts.
        def _fetch() -> str:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "JARVIS-voice/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                ct = resp.headers.get("Content-Type", "")
                raw = resp.read(64 * 1024)  # cap network read at 64 KB
                if "text" not in ct and "json" not in ct and "html" not in ct:
                    return f"(non-text content-type: {ct or 'unknown'})"
                return raw.decode("utf-8", errors="replace")
        body = await asyncio.to_thread(_fetch)
    except urllib.error.HTTPError as e:
        return f"(HTTP {e.code}: {e.reason})"
    except urllib.error.URLError as e:
        return f"(network error: {e.reason})"
    except Exception as e:
        return f"(fetch failed: {e})"
    # Strip HTML to plain-ish text. Not perfect, but good enough for
    # voice-side summarisation.
    body = re.sub(r"<script\b.*?</script>", "", body, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"<style\b.*?</style>", "", body, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", body).strip()
    return _truncate(body)


@function_tool
async def glob_files(pattern: str, path: str = "~") -> str:
    """List files matching a glob pattern under `path`, recursively.

    Use for atomic "find all <kind> files in <dir>" asks. Returns one
    path per line, capped at 100 entries.

    Args:
        pattern: e.g. "*.py", "**/*.ts", "src/**/test_*.py".
        path:    Root to search under (default = home).
    """
    pattern = (pattern or "").strip()
    if not pattern:
        return "(no pattern supplied)"
    root = Path(path or "~").expanduser()
    if not root.exists():
        return f"(no such root: {root})"
    try:
        # `**` in pattern means recursive — pathlib handles it.
        # If user gave a non-recursive pattern, glob it as-is.
        matches = list(root.rglob(pattern) if "**" not in pattern else root.glob(pattern))
    except Exception as e:
        return f"(glob failed: {e})"
    matches = [str(m) for m in matches if m.is_file()]
    total = len(matches)
    matches = matches[:100]
    logger.info(f"glob_files → pattern={pattern!r} root={root} matched={total}")
    head = "\n".join(matches)
    if total > 100:
        head += f"\n…[+{total - 100} more]"
    return head or f"(no matches under {root})"


@function_tool
async def grep_files(pattern: str, path: str = ".", glob: str = "") -> str:
    """Search for a regex `pattern` across files under `path`.

    Use for atomic "where is X used" / "find every TODO" asks. Wraps
    ripgrep if installed (fast), else falls back to grep -R. Returns
    `file:line:match` lines, capped at 50.

    Args:
        pattern: Regex (POSIX ERE / PCRE2 depending on rg vs grep).
        path:    Root to search under (default = cwd).
        glob:    Optional file glob filter, e.g. "*.py".
    """
    pattern = (pattern or "").strip()
    if not pattern:
        return "(no pattern supplied)"
    root = Path(path or ".").expanduser()
    if not root.exists():
        return f"(no such root: {root})"
    # Prefer ripgrep — bundled into many distros and into bun's embedded
    # tools. Fast and handles binary-skipping by default.
    has_rg = shutil_which("rg")
    if has_rg:
        argv = ["rg", "--no-heading", "--line-number", "--max-count", "5", "--max-columns", "300"]
        if glob:
            argv += ["-g", glob]
        argv += ["--", pattern, str(root)]
    else:
        argv = ["grep", "-RHn", "--max-count=5"]
        if glob:
            argv += [f"--include={glob}"]
        argv += ["--", pattern, str(root)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        proc.terminate()
        return "(grep timed out after 30s)"
    except Exception as e:
        return f"(grep failed: {e})"
    text = out_b.decode("utf-8", errors="replace").strip().splitlines()
    total = len(text)
    text = text[:50]
    logger.info(f"grep_files → pattern={pattern!r} hits={total}")
    head = "\n".join(text)
    if total > 50:
        head += f"\n…[+{total - 50} more matches]"
    return head or "(no matches)"


def shutil_which(name: str) -> str | None:
    """Cheap stdlib `which` (avoids importing shutil at module top to
    keep the import block stable)."""
    import shutil
    return shutil.which(name)


# ── TTS guard: strip function-call leakage ────────────────────────────
#
# llama-3.3-70b on Groq sometimes emits a tool call as raw TEXT in the
# completion stream instead of through the structured tool_call API.
# When that happens, the TTS voices "function run_jarvis_cli request
# Show a 3D view of a human being" which sounds completely broken to
# the user and the actual tool never runs. This filter spots common
# leakage patterns and removes them from the TTS-bound stream while
# leaving normal speech intact.
_LEAK_PATTERNS = [
    re.compile(r"<\s*function[^>]*>.*?</\s*function\s*>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<\s*function_calls\s*>.*?</\s*function_calls\s*>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<\s*invoke[^>]*>.*?</\s*invoke\s*>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<\s*parameter[^>]*>.*?</\s*parameter\s*>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<\|tool_call_(?:start|end|begin|finish)\|>", re.IGNORECASE),
    re.compile(r"\{\s*\"request\"\s*:\s*\"[^\"]*\"\s*\}"),
    re.compile(r"\{\s*\"name\"\s*:\s*\"(?:run_jarvis_cli|type_in_terminal|recall_conversation)\"[^}]*\}", re.DOTALL),
]


async def strip_function_call_leakage(text):
    """Drop raw function-call markup from the TTS-bound text stream.

    Buffers ~250 chars at a time so multi-token leakage spanning chunk
    boundaries still gets caught. When the stream ends, any remaining
    buffer is flushed (after one final regex pass).
    """
    buffer = ""
    BUF_KEEP = 250
    async for chunk in text:
        buffer += chunk
        for p in _LEAK_PATTERNS:
            buffer = p.sub("", buffer)
        if len(buffer) > BUF_KEEP:
            yield buffer[:-BUF_KEEP]
            buffer = buffer[-BUF_KEEP:]
    if buffer:
        for p in _LEAK_PATTERNS:
            buffer = p.sub("", buffer)
        yield buffer


def _flatten_chat_content(content: object) -> str:
    """ChatMessage.content can be a string, a list of mixed parts
    (strings + ImageContent + etc), or None. Flatten to a plain
    string — the DB only stores text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            else:
                # Non-string content (images, tool calls). Skip —
                # don't pollute the transcript.
                continue
        return " ".join(parts).strip()
    return str(content)


# ── Agent subclass: silent-mode gating ─────────────────────────────────
#
# The framework's base `Agent` always forwards the user's transcript
# to the LLM. We override `on_user_turn_completed` to:
#   - Drop the turn entirely (raise StopResponse) if silent mode is
#     active and the user didn't say a wake-up phrase. JARVIS stays
#     quiet, no LLM call, no TTS.
#   - Toggle silent mode on/off based on detected mute/wake phrases.
#     Wake phrases pass through to the LLM so it can voice a brief
#     "I'm back" acknowledgment; mute phrases also pass through so
#     it can voice "going silent" once before suppressing.
class JarvisAgent(Agent):
    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage,
    ) -> None:
        # Pull the transcript however we can — different livekit-agents
        # versions stash it in slightly different places. Try the
        # canonical text_content() first; fall back to digging through
        # content list element by element.
        raw = ""
        try:
            tc = new_message.text_content()
            if tc:
                raw = tc
        except Exception:
            pass
        if not raw:
            try:
                content = getattr(new_message, "content", None)
                if isinstance(content, str):
                    raw = content
                elif isinstance(content, list):
                    parts = []
                    for c in content:
                        if isinstance(c, str):
                            parts.append(c)
                        else:
                            # Some plugins wrap text in objects with a .text
                            # or .content attribute. Try both before giving
                            # up.
                            t = getattr(c, "text", None) or getattr(c, "content", None)
                            if isinstance(t, str):
                                parts.append(t)
                    raw = " ".join(parts)
            except Exception:
                pass
        text = (raw or "").lower().strip()
        if not text:
            return

        if _is_silent():
            # Silent mode: only the wake-up family unblocks JARVIS.
            # Use _is_command (length-bounded) instead of bare substring
            # matching so "you don't have to wake up" — a topical
            # mention in a long sentence — doesn't count as a wake.
            if _is_command(text, _WAKE_PATTERNS):
                _set_silent(False)
                logger.info("[silent-mode] wake phrase detected → exiting silent mode")
                # Fall through so the LLM voices a quick "I'm back".
                return
            # Anything else while silent → drop turn, no reply.
            logger.info(f"[silent-mode] suppressed turn: {text[:60]!r}")
            raise StopResponse()

        # Not silent. Check for mute trigger.
        if _is_command(text, _MUTE_PATTERNS):
            _set_silent(True)
            logger.info("[silent-mode] mute phrase detected → entering silent mode")
            # Don't drop — let the LLM voice a brief "going silent"
            # so the user gets confirmation. Future turns will be
            # suppressed by the silent-mode branch above.
            return

        # Not silent, not a mute trigger → normal LLM path.
        return


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

    # Clear any stale thinking/tool flags from a prior crashed agent.
    # If we leave them, the new fresh agent reports "thinking" forever
    # until the next user turn fires user_input_transcribed.
    _mark_thinking_end()
    _mark_tool_end()
    # Don't auto-clear silent mode on agent restart — it's a user
    # preference that should persist across speech-model switches and
    # incidental restarts. The user toggles it explicitly via voice
    # ("wake up") when they want JARVIS back.

    # Build the speech LLM from the user's tray pick (or default).
    # Done HERE rather than at module load so a /voice-model POST +
    # systemctl restart picks up the new file on the very next job.
    active_speech_id, _active_speech_llm = make_speech_llm()

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
        # Speech LLM — switchable via the tray's "Models" submenu.
        # Default is llama-3.3-70b on Groq for ~200 ms first-token
        # latency. Switching writes ~/.jarvis/voice-model and bounces
        # the agent unit, so the new LLM is built on next startup
        # (read_speech_model() fires below as we exit entrypoint and
        # re-enter on the fresh job dispatch).
        llm=_active_speech_llm,
        # ── TTS chain ───────────────────────────────────────────────
        # Primary: Groq Orpheus (warm voice, Ulrich's preference).
        # Fallback: Microsoft Edge-TTS (no auth, no quota — kicks in
        # if Groq TTS hiccups, so JARVIS doesn't go silent during a
        # Groq incident like the one earlier today).
        # FallbackAdapter auto-routes: if primary fails (timeout, 5xx,
        # whatever), framework retries the next entry. If both fail,
        # the existing _on_error notification fires.
        #
        # Voices:
        #   JARVIS_TTS_VOICE  — Groq Orpheus voice. Defaults to "troy"
        #     (warm male). Other options: austin/daniel/hannah/diana/autumn.
        #   JARVIS_EDGE_VOICE — Microsoft fallback voice. Defaults to
        #     en-US-GuyNeural. `python -m edge_tts --list-voices` for more.
        tts=tts.FallbackAdapter([
            groq.TTS(
                model="canopylabs/orpheus-v1-english",
                voice=os.getenv("JARVIS_TTS_VOICE", "troy"),
            ),
            edge_tts_plugin.EdgeTTS(
                voice=os.getenv("JARVIS_EDGE_VOICE", "en-US-GuyNeural"),
            ),
        ]),
        # ── Barge-in / multitask tuning ─────────────────────────────
        # Defaults make JARVIS feel "deaf while speaking": the agent
        # keeps talking through the user's next request, then queues
        # a stale reply. These knobs make the interrupt fast and
        # graceful so the user can start a new turn mid-sentence.
        # Shape: TurnHandlingOptions TypedDict with three sections.
        turn_handling={
            "interruption": {
                "enabled": True,
                # min_words and min_duration are AND-gated in the
                # framework: interrupt fires only after VAD has crossed
                # min_duration AND STT has produced ≥ min_words words.
                # Setting min_words: 1 was making barge-in wait for
                # Groq Whisper to land a partial transcript on top of
                # the VAD window — total ~550–800 ms before the agent
                # would stop talking. Use VAD-only (min_words: 0) so
                # interrupt fires the instant min_duration of speech
                # is seen.
                "min_duration": 0.4,
                "min_words": 0,
                # resume_false_interruption / false_interruption_timeout
                # OFF on purpose. Why: the framework's "false interrupt"
                # path replaces the real interrupt() with audio_output
                # .pause() (agent_activity.py:1628). For the LiveKit
                # ParticipantAudioOutput, pause() only gates new frames
                # — it does NOT clear the SFU-side AudioSource queue
                # (room_io/_output.py:129-132 has the clear_queue line
                # commented out). With Groq Orpheus pushing the whole
                # utterance to the SFU in well under a second, by the
                # time pause fires the audio is already buffered at the
                # SFU and plays to the end. That was the "JARVIS keeps
                # talking until he's done" symptom. Disabling pause
                # routes every barge-in straight to interrupt() →
                # clear_buffer() → clear_queue(), which actually drops
                # the in-flight audio. Cost: a cough silences JARVIS
                # without auto-resume; user re-asks. Mild vs. the prior
                # "can't interrupt" UX.
                "resume_false_interruption": False,
                "false_interruption_timeout": None,
            },
            "endpointing": {
                # How long after the user stops talking before we
                # treat the turn as complete and fire the LLM.
                # Slightly tighter than default reduces dead-air
                # without cutting off mid-thought pauses.
                "min_delay": 0.4,
                "max_delay": 4.0,
            },
            "preemptive_generation": {
                # Disabled because llama-3.3-70b on Groq emits
                # malformed function calls under preemptive generation
                # with our 3-tool setup — the LLM tries to commit to
                # a tool call before the user finishes speaking, the
                # call is malformed, Groq returns "Failed to call a
                # function", retries exhaust, and the user gets total
                # silence + a permanently-amber tray. Cleaner to wait
                # for the full user turn and pay the ~200 ms.
                "enabled": False,
            },
        },
        # Note: use_tts_aligned_transcript was removed — the Groq
        # Orpheus TTS plugin doesn't return aligned transcripts, so
        # turning it on just spammed warnings. The DB still gets the
        # whole intended utterance, which is fine for recall.
        #
        # tts_text_transforms — keep the framework defaults
        # (filter_markdown, filter_emoji) AND prepend our own filter
        # that strips raw function-call markup that llama-3.3 sometimes
        # emits as text instead of structured tool_calls. Without this
        # the TTS voices "function run_jarvis_cli request open Chrome"
        # which sounds completely broken.
        tts_text_transforms=[
            strip_function_call_leakage,
            "filter_markdown",
            "filter_emoji",
        ],
    )

    # Persist every user/agent turn to ~/.jarvis/conversations.db —
    # same SQLite file the bridge writes typed-chat turns to, so the
    # web UI's history sidebar surfaces voice moments too. A new
    # session_id per job keeps voice conversations grouped correctly
    # in the UI. Handler is cheap (one INSERT per turn, write → close)
    # so no need to offload to a thread.
    convo_session_id = str(uuid.uuid4())
    logger.info(f"[convo-db] session {convo_session_id}  → {CONVO_DB_PATH}")

    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        try:
            item = ev.item
            role = getattr(item, "role", None)
            text = _flatten_chat_content(getattr(item, "content", None))
            _save_turn(convo_session_id, role, text)
            # Assistant turn just landed → LLM phase is over (TTS has
            # been streaming). Clear the thinking flag. The desktop
            # tray drops gold the next /status poll.
            if role == "assistant":
                _mark_thinking_end()
        except Exception as e:
            logger.warning(f"[convo-db] save failed: {e}")

    # STT finalised a user turn — LLM is about to start generating
    # (or the agent will decide to stay silent if the directed-at-me
    # filter rejects it). Touch the thinking flag so the tray goes
    # gold immediately. Without this, gold doesn't show until the
    # tool actually starts running for tool-using turns.
    @session.on("user_input_transcribed")
    def _on_user_input(ev) -> None:
        # Only flip on FINAL transcripts — partial chunks fire too.
        if getattr(ev, "is_final", True):
            _mark_thinking_start()
            # Reset the per-turn tool-call counter so each new user
            # turn gets a fresh budget. Otherwise long sessions slowly
            # accumulate tool calls and trip the limit prematurely.
            _reset_tool_call_count()

    # ── TTS-error surfacing ────────────────────────────────────────
    # Groq Orpheus has tight free-tier limits; on rate-limit the
    # framework logs warnings and silently drops the utterance, which
    # leaves the user wondering if JARVIS broke. Hook the session
    # error event, recognise TTS failures specifically, and:
    #   1. Append the unspoken text to a log file the user can tail
    #      (~/.jarvis/tts-failures.log) so nothing is lost
    #   2. Pop a desktop notification once per minute so the cause
    #      is obvious without being spammy
    _tts_fail_marker = Path.home() / ".jarvis" / "tts-failures.log"
    _last_notify_ts = [0.0]   # boxed so the closure can mutate it

    @session.on("error")
    def _on_error(ev) -> None:
        try:
            from livekit.agents import tts as _lk_tts  # local to avoid top-level slow path
            err = getattr(ev, "error", None)
            if not isinstance(err, _lk_tts.TTSError):
                return
            # Best-effort grab of the in-flight text — if we can't,
            # at least log the timestamp and error message.
            failed_text = getattr(err, "input_text", "") or getattr(err, "text", "")
            now = time.time()
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
            try:
                _tts_fail_marker.parent.mkdir(parents=True, exist_ok=True)
                with _tts_fail_marker.open("a", encoding="utf-8") as f:
                    f.write(f"[{stamp}] {err}\n")
                    if failed_text:
                        f.write(f"  text: {failed_text[:500]}\n")
            except Exception:
                pass
            # Classify the error so the desktop notification tells the
            # user what's actually wrong instead of always saying
            # "rate-limited" (the prior wording was misleading for
            # network timeouts, which are most of what we see).
            err_type_name = type(err).__name__
            err_msg = str(err)
            status_code = getattr(err, "status_code", None)
            if "Timeout" in err_type_name or "timed out" in err_msg.lower():
                title = "JARVIS — TTS slow / timing out"
                body = (
                    "Groq TTS isn't responding fast enough. JARVIS heard "
                    "you but the speech synthesis call timed out. Often "
                    "this is just transient Groq-side load — try again "
                    "in a few seconds."
                )
            elif status_code == 429 or (
                status_code == 400 and "quota" in err_msg.lower()
            ):
                title = "JARVIS — TTS rate-limited"
                body = (
                    "Groq TTS quota hit. Wait a minute or switch the "
                    "speech model in the tray (anything but Orpheus uses "
                    "a different quota bucket)."
                )
            elif status_code == 400:
                title = "JARVIS — TTS bad request"
                body = (
                    "Groq TTS rejected the request payload. Usually "
                    "transient on Groq's side; the framework will retry."
                )
            else:
                title = "JARVIS — TTS error"
                body = f"{err_type_name}: {err_msg[:160]}"

            # Throttle notifications to one per 60 s so a flood of
            # retries doesn't spam the desktop.
            if now - _last_notify_ts[0] > 60:
                _last_notify_ts[0] = now
                try:
                    _subprocess.Popen(
                        ["notify-send", "-u", "normal", "-t", "6000",
                         title, body],
                        stdout=_subprocess.DEVNULL,
                        stderr=_subprocess.DEVNULL,
                    )
                except FileNotFoundError:
                    pass  # notify-send not installed; the log file is enough
            logger.warning(f"TTS error logged to {_tts_fail_marker}: {err}")
        except Exception as e:
            logger.debug(f"_on_error handler hiccup: {e}")

    # Build the system prompt with current model info appended, so the
    # LLM can answer "what model are you?" correctly. Without this it
    # gives a vague "I'm a conversational AI" answer because LLMs
    # don't know their own underlying model unless told. Reads the
    # CLI model live from the file so a tray switch is reflected on
    # the next session start (or in-place chat-ctx update).
    cli_model_id = read_cli_model()
    cli_def = CLI_MODELS.get(cli_model_id, {})
    cli_label = cli_def.get("label", cli_model_id)
    speech_label = SPEECH_MODELS.get(active_speech_id, {}).get(
        "label", active_speech_id,
    )
    runtime_id_block = (
        "\n\n═══ WHO YOU ARE ═══\n\n"
        "When the user asks what model you're using, what's powering\n"
        "you, what stack you're on, or similar identity questions,\n"
        "answer plainly with the active configuration:\n"
        f"  - Speech LLM (the one composing this reply): {speech_label}.\n"
        f"  - Tool model (the one that runs run_jarvis_cli): {cli_label}.\n"
        f"  - Speech-to-text: {VOICE_STT_LABEL}.\n"
        f"  - Text-to-speech: {VOICE_TTS_LABEL}.\n"
        "If the user asks a vaguer 'what model' question, lead with\n"
        "the speech LLM and offer the tool model as 'and for tool work'.\n"
        "Don't say you don't know — you do, it's right here."
    )

    await session.start(
        room=ctx.room,
        agent=JarvisAgent(
            instructions=JARVIS_INSTRUCTIONS + runtime_id_block,
            # Pre-load recent prior turns from conversations.db so the
            # LLM sees what was discussed before this job started.
            # Without this, every voice-client reconnect = amnesia.
            chat_ctx=_seed_chat_ctx(),
            # Tool surface explanation:
            #   bash / read_file / web_fetch / glob_files / grep_files
            #     — direct primitives. Atomic single-step asks. ~3 KB
            #     output cap. No CLI subprocess hop, ~1-2 s faster
            #     than going via run_jarvis_cli.
            #   run_jarvis_cli — the dispatcher. Multi-step / agent-
            #     loop / sub-agent / plan / MCP / skills work goes
            #     here. The CLI's own LLM picks the right downstream
            #     tools.
            #   type_in_terminal / media_control / recall_conversation
            #     — specialized ergonomics. Direct in-process tools
            #     for things where Bash equivalents are awkward (xdotool
            #     window dance, playerctl player targeting, SQL over
            #     conversations.db).
            tools=[
                run_jarvis_cli,
                bash,
                read_file,
                web_fetch,
                glob_files,
                grep_files,
                type_in_terminal,
                media_control,
                recall_conversation,
            ],
        ),
        # Critical: keep the agent session alive when the voice-
        # client disconnects. Default is True — session closes on
        # first client leave — which means when systemd restarts
        # jarvis-voice-client (or the client drops briefly), the
        # agent tears down, the room persists, and LiveKit refuses
        # to re-dispatch a worker to the same room. Result: user
        # reconnects but JARVIS is silent.
        # (Use RoomOptions, not RoomInputOptions — the -Input- /
        # -Output- variants were deprecated in livekit-agents 1.5.)
        room_options=RoomOptions(close_on_disconnect=False),
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

    # Auto-greeting intentionally removed — JARVIS stays silent until
    # the user speaks or a /speak message arrives. Keeps reboots + any
    # reconnect churn from making him chatter at the user unprompted.
    # To re-enable, restore the session.generate_reply() call here.


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        ),
    )
