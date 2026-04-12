"""
Tool executor — real implementations for all 6 tools.

Weather  : Open-Meteo (free, no API key)
Search   : DuckDuckGo via duckduckgo-search (free, no API key)
Music    : playerctl (desktop) with yt-dlp+mpv fallback
Reminder : `at` daemon + notify-send; falls back to in-process store
App/Sys  : subprocess via xdg-open / pactl / brightnessctl / systemctl
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── In-process reminder store (fallback when `at` is unavailable) ─────────────
_reminders: list[dict[str, Any]] = []

# ── WMO weather code → human description ──────────────────────────────────────
_WMO_CODES: dict[int, str] = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    80: "Slight showers", 81: "Moderate showers", 82: "Violent showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Heavy thunderstorm",
}


# ── Dispatcher ────────────────────────────────────────────────────────────────

async def execute_tool(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """
    Dispatch a tool call to its real implementation.
    Never raises — wraps all errors and returns an error dict so the pipeline
    can always produce a response to the user.
    """
    dispatch: dict[str, Any] = {
        "get_weather":    _get_weather,
        "play_music":     _play_music,
        "set_reminder":   _set_reminder,
        "web_search":     _web_search,
        "open_app":       _open_app,
        "system_control": _system_control,
    }

    handler = dispatch.get(tool_name)
    if handler is None:
        logger.warning(f"[executor] unknown tool: {tool_name}")
        return {"error": f"Unknown tool: {tool_name}"}

    try:
        result = await handler(tool_input)
        logger.info(f"[executor] tool={tool_name} ok")
        return result
    except Exception as e:
        logger.error(f"[executor] tool={tool_name} failed: {e}", exc_info=True)
        return {"error": str(e), "tool": tool_name}


# ── Tool 1: Weather ───────────────────────────────────────────────────────────

async def _get_weather(params: dict[str, Any]) -> dict[str, Any]:
    """
    Fetch live weather from Open-Meteo (free, no API key required).
    Step 1 — geocode city name to lat/lon.
    Step 2 — fetch current conditions for those coordinates.
    """
    city  = params.get("city", "Yaoundé")
    units = params.get("units", "celsius")

    async with httpx.AsyncClient(timeout=8.0) as client:
        # ── Geocoding ──────────────────────────────────────────────────────────
        geo_resp = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "en", "format": "json"},
        )
        geo_resp.raise_for_status()
        geo_data = geo_resp.json()

        results = geo_data.get("results")
        if not results:
            return {"error": f"City not found: {city}"}

        lat      = results[0]["latitude"]
        lon      = results[0]["longitude"]
        resolved = results[0].get("name", city)

        # ── Current weather ────────────────────────────────────────────────────
        temp_unit = "celsius" if units == "celsius" else "fahrenheit"
        wx_resp   = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":               lat,
                "longitude":              lon,
                "current":                "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
                "temperature_unit":       temp_unit,
                "wind_speed_unit":        "kmh",
                "timezone":               "auto",
            },
        )
        wx_resp.raise_for_status()
        wx = wx_resp.json().get("current", {})

    code      = wx.get("weather_code", 0)
    condition = _WMO_CODES.get(code, "Unknown")
    temp      = wx.get("temperature_2m")
    humidity  = wx.get("relative_humidity_2m")
    wind      = wx.get("wind_speed_10m")

    return {
        "city":      resolved,
        "temp":      round(temp) if temp is not None else "?",
        "units":     units,
        "condition": condition,
        "humidity":  humidity,
        "wind_kmh":  wind,
    }


# ── Tool 2: Web search ────────────────────────────────────────────────────────

async def _web_search(params: dict[str, Any]) -> dict[str, Any]:
    """
    Search the web via DuckDuckGo (free, no API key).
    Uses the duckduckgo-search library in a thread pool to avoid blocking the event loop.
    Returns up to 5 results with title, url, and snippet.
    """
    query   = params.get("query", "")
    max_res = 5

    def _ddg_search() -> list[dict[str, str]]:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            return [
                {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
                for r in ddgs.text(query, max_results=max_res)
            ]

    loop    = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, _ddg_search)

    return {"query": query, "results": results}


# ── Tool 3: Music playback ────────────────────────────────────────────────────

async def _play_music(params: dict[str, Any]) -> dict[str, Any]:
    """
    Play music on the user's desktop.

    Strategy (tries in order):
      1. playerctl open — works when Spotify/VLC/any MPRIS player is running
      2. yt-dlp + mpv  — stream from YouTube in the background (requires both installed)
      3. xdg-open      — open default music handler with a search URL as last resort
    """
    query = params.get("query", "").strip()
    if not query:
        return {"error": "No query provided"}

    # ── Try playerctl (MPRIS) ──────────────────────────────────────────────────
    if _cmd_exists("playerctl"):
        try:
            result = await _run(["playerctl", "--all-players", "open", f"spotify:search:{query}"])
            if result["returncode"] == 0:
                return {"track": query, "player": "spotify/playerctl", "status": "playing"}
        except Exception:
            pass

    # ── Try yt-dlp + mpv ──────────────────────────────────────────────────────
    if _cmd_exists("yt-dlp") and _cmd_exists("mpv"):
        try:
            result = await _run([
                "mpv",
                "--no-video",
                "--really-quiet",
                f"ytdl://ytsearch1:{query}",
            ], detach=True)
            return {"track": query, "player": "mpv+yt-dlp", "status": "playing"}
        except Exception as e:
            logger.warning(f"[play_music] mpv failed: {e}")

    # ── Fallback: open a YouTube search in the browser ────────────────────────
    import urllib.parse
    search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
    await _run(["xdg-open", search_url], detach=True)
    return {"track": query, "player": "browser", "status": "search opened"}


# ── Tool 4: Reminders ─────────────────────────────────────────────────────────

async def _set_reminder(params: dict[str, Any]) -> dict[str, Any]:
    """
    Schedule a reminder.

    Strategy (tries in order):
      1. `at` daemon  — schedules a notify-send notification at the given time
      2. in-process   — stores the reminder in memory and logs a warning that
                        it won't survive server restart
    """
    task      = params.get("task", "").strip()
    time_spec = params.get("time", "").strip()

    if not task or not time_spec:
        return {"error": "Both 'task' and 'time' are required"}

    # ── Try `at` ───────────────────────────────────────────────────────────────
    if _cmd_exists("at"):
        try:
            at_cmd  = f'notify-send "JARVIS Reminder" "{task}"'
            proc    = await asyncio.create_subprocess_exec(
                "at", time_spec,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate(input=at_cmd.encode())
            if proc.returncode == 0:
                logger.info(f"[set_reminder] at-job created for '{task}' at '{time_spec}'")
                return {"task": task, "time": time_spec, "status": "set", "backend": "at"}
            logger.warning(f"[set_reminder] at failed: {stderr.decode()}")
        except Exception as e:
            logger.warning(f"[set_reminder] at error: {e}")

    # ── In-process fallback ────────────────────────────────────────────────────
    _reminders.append({"task": task, "time": time_spec, "created_at": datetime.now().isoformat()})
    logger.warning(
        "[set_reminder] `at` unavailable — reminder stored in memory only "
        "(will not survive restart). Install `at` for persistent reminders."
    )
    return {"task": task, "time": time_spec, "status": "set", "backend": "memory"}


# ── Tool 5: Open application ──────────────────────────────────────────────────

async def _open_app(params: dict[str, Any]) -> dict[str, Any]:
    """
    Launch an application by name using xdg-open or direct exec.
    Maps common spoken app names to their binary/command.
    """
    app_name = params.get("app_name", "").strip().lower()
    if not app_name:
        return {"error": "No app name provided"}

    # Common spoken → binary mappings
    _APP_MAP: dict[str, list[str]] = {
        "chrome":      ["google-chrome", "chromium-browser", "chromium"],
        "firefox":     ["firefox"],
        "terminal":    ["gnome-terminal", "xterm", "konsole", "alacritty"],
        "files":       ["nautilus", "thunar", "dolphin"],
        "vscode":      ["code"],
        "spotify":     ["spotify"],
        "calculator":  ["gnome-calculator", "kcalc", "galculator"],
        "settings":    ["gnome-control-center", "systemsettings"],
    }

    candidates = _APP_MAP.get(app_name, [app_name])

    for cmd in candidates:
        if _cmd_exists(cmd):
            await _run([cmd], detach=True)
            logger.info(f"[open_app] launched: {cmd}")
            return {"app": app_name, "command": cmd, "status": "launched"}

    # Last resort: xdg-open
    await _run(["xdg-open", app_name], detach=True)
    return {"app": app_name, "command": "xdg-open", "status": "launched"}


# ── Tool 6: System control ────────────────────────────────────────────────────

async def _system_control(params: dict[str, Any]) -> dict[str, Any]:
    """
    Perform a system action: volume, brightness, screenshot, or power state.
    Uses pactl (audio), brightnessctl (screen), scrot (screenshot), systemctl (power).
    """
    action = params.get("action", "").strip()
    value  = params.get("value")  # optional numeric value (e.g. volume %)

    _STEP = 10  # default step for up/down actions when no value given

    action_map: dict[str, list[str]] = {
        "volume_up":       ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"+{value or _STEP}%"],
        "volume_down":     ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"-{value or _STEP}%"],
        "mute":            ["pactl", "set-sink-mute",   "@DEFAULT_SINK@", "1"],
        "unmute":          ["pactl", "set-sink-mute",   "@DEFAULT_SINK@", "0"],
        "brightness_up":   ["brightnessctl", "set", f"+{value or _STEP}%"],
        "brightness_down": ["brightnessctl", "set", f"{value or _STEP}%-"],
        "lock":            ["loginctl", "lock-session"],
        "shutdown":        ["systemctl", "poweroff"],
        "restart":         ["systemctl", "reboot"],
        "sleep":           ["systemctl", "suspend"],
    }

    if action == "screenshot":
        return await _take_screenshot()

    cmd = action_map.get(action)
    if cmd is None:
        return {"error": f"Unknown system action: {action}"}

    result = await _run(cmd)
    if result["returncode"] != 0:
        return {"error": result["stderr"], "action": action}

    return {"action": action, "value": value, "status": "ok"}


async def _take_screenshot() -> dict[str, Any]:
    """Capture a screenshot to /tmp using scrot or gnome-screenshot."""
    ts       = int(time.time())
    out_path = f"/tmp/jarvis_screenshot_{ts}.png"

    if _cmd_exists("scrot"):
        result = await _run(["scrot", out_path])
    elif _cmd_exists("gnome-screenshot"):
        result = await _run(["gnome-screenshot", "-f", out_path])
    else:
        return {"error": "No screenshot tool found (install scrot or gnome-screenshot)"}

    if result["returncode"] != 0:
        return {"error": result["stderr"]}

    return {"action": "screenshot", "path": out_path, "status": "ok"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cmd_exists(cmd: str) -> bool:
    """Return True if a command is available on PATH."""
    import shutil
    return shutil.which(cmd) is not None


async def _run(
    cmd: list[str],
    detach: bool = False,
) -> dict[str, Any]:
    """
    Run a subprocess asynchronously.
    If detach=True, starts the process and returns immediately without waiting.
    """
    if detach:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"returncode": 0, "stdout": "", "stderr": ""}

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return {
        "returncode": proc.returncode,
        "stdout":     stdout.decode(errors="replace").strip(),
        "stderr":     stderr.decode(errors="replace").strip(),
    }
