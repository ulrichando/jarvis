"""HTTP /status server for direct-mode bins (Gemini Live / OpenAI Realtime).

When the tray switches into a direct mode via `bin/jarvis-mode {gemini|openai}`,
the tray icon polls `voice-client:8767/status` for state — but voice-client
sits on the JARVIS-Claude pipeline and is muted during direct modes, so the
icon goes idle/gray even when the user is actively in a Gemini/OpenAI
conversation.

This module gives each direct-mode bin its own `/status` endpoint with the
SAME field shape voice-client uses (subset of fields the tray's React
useVoiceClient hook reads), so the existing FROZEN tray_image_for code at
`src-tauri/src/main.rs` is unchanged — only the source URL flips.

Lifecycle:
    server = StatusServer(port=8768, mode="gemini")
    await server.start()
    ...
    server.set_speaking(True)        # while audio is flowing out
    server.set_listening(True)       # while user is speaking
    server.set_tool_running(True)    # while a function call is in flight
    server.set_agent_thinking(True)  # between user-end and audio-start
    server.set_agent_present(True)   # once the upstream connection is up
    ...
    await server.stop()
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from aiohttp import web


logger = logging.getLogger("jarvis.direct_mode_status")

# Same file the voice-client serves on :8767/cli-model (see
# voice_client_tray_config.CLI_MODEL_FILE — not imported to keep this
# module's deps to aiohttp only). The tool/CLI model is a GLOBAL,
# mode-independent setting: the tray's "Tool:" header must show it in
# Gemini/OpenAI modes too, where this server is the /status source.
# Reported live per snapshot so a tray switch mid-mode is reflected on
# the next poll. (Before 2026-06-09 this was reported as null in the
# direct modes and the tray's Tool line sat on "(loading…)" forever.)
_CLI_MODEL_FILE: Path = Path.home() / ".jarvis" / "cli-model"


def _read_cli_model() -> Optional[str]:
    try:
        return _CLI_MODEL_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


__all__ = ["StatusServer"]


class StatusServer:
    """Tiny aiohttp server that mirrors voice-client's `/status` shape.

    Only the fields the tray's React poller reads (see
    `src/voice-agent/desktop-tauri/src/hooks/useVoiceClient.js`) are reported:

      connected, agent_present, muted, listening, speaking, silent_mode,
      tool_running, agent_thinking, sharing_screen, cli_model,
      speech_model, tts_provider, mode

    Setters are sync and idempotent. State reads off the running task's
    asyncio loop are safe — Python dict reads are atomic under the GIL.
    """

    def __init__(
        self,
        *,
        port: int,
        mode: str,
        host: str = "127.0.0.1",
        cli_model: Optional[str] = None,
        speech_model: Optional[str] = None,
        tts_provider: Optional[str] = None,
    ) -> None:
        self.port = port
        self.mode = mode
        self.host = host
        self._cli_model = cli_model
        self._speech_model = speech_model
        self._tts_provider = tts_provider

        self._connected = True
        self._agent_present = False
        self._muted = False
        self._listening = False
        self._speaking = False
        self._silent_mode = False
        self._tool_running = False
        self._agent_thinking = False
        self._sharing_screen = False

        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

    # ── State setters ────────────────────────────────────────────────

    def set_agent_present(self, v: bool) -> None: self._agent_present = bool(v)
    def set_muted(self, v: bool) -> None:         self._muted = bool(v)
    def set_listening(self, v: bool) -> None:     self._listening = bool(v)
    def set_speaking(self, v: bool) -> None:      self._speaking = bool(v)
    def set_silent_mode(self, v: bool) -> None:   self._silent_mode = bool(v)
    def set_tool_running(self, v: bool) -> None:  self._tool_running = bool(v)
    def set_agent_thinking(self, v: bool) -> None: self._agent_thinking = bool(v)
    def set_sharing_screen(self, v: bool) -> None: self._sharing_screen = bool(v)

    # ── State read (for tests) ───────────────────────────────────────

    def snapshot(self) -> dict:
        return {
            "connected":       self._connected,
            "agent_present":   self._agent_present,
            "muted":           self._muted,
            "listening":       self._listening,
            "speaking":        self._speaking,
            "silent_mode":     self._silent_mode,
            "tool_running":    self._tool_running,
            "agent_thinking":  self._agent_thinking,
            "sharing_screen":  self._sharing_screen,
            "cli_model":       self._cli_model or _read_cli_model(),
            "speech_model":    self._speech_model,
            "tts_provider":    self._tts_provider,
            "mode":            self.mode,
        }

    # ── HTTP handlers ────────────────────────────────────────────────

    async def _status(self, _req: web.Request) -> web.Response:
        return web.json_response(
            self.snapshot(),
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def _health(self, _req: web.Request) -> web.Response:
        return web.json_response({"ok": True, "mode": self.mode})

    async def _cors(self, _req: web.Request) -> web.Response:
        return web.Response(
            headers={
                "Access-Control-Allow-Origin":  "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
        )

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/status", self._status)
        app.router.add_get("/health", self._health)
        app.router.add_route("OPTIONS", "/{tail:.*}", self._cors)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        logger.info(
            "[direct-mode-status] %s listening on http://%s:%d/status",
            self.mode, self.host, self.port,
        )

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        logger.info("[direct-mode-status] %s server stopped", self.mode)

    async def serve_until(self, stop_event: asyncio.Event) -> None:
        """Convenience: start the server, wait on `stop_event`, then stop.

        Use this as a single task in `asyncio.gather` alongside the bin's
        main loops. Cancellation is handled cleanly — `stop_event` is the
        graceful path, asyncio cancellation also stops the server."""
        await self.start()
        try:
            await stop_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()
