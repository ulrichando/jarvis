"""Spotify integration plugin — bundled, gated on credentials.

Registers 7 tools (playback, devices, queue, search, playlists, albums,
library) into the ``spotify`` toolset. Every tool is gated by
``_check_spotify_available()``: until the user connects Spotify (an
``SPOTIFY_ACCESS_TOKEN`` env var or a ``~/.jarvis/spotify/auth.json`` file),
the tools are NOT adapted onto the LLM surface — they stay completely inert,
so the supervisor never sees a music tool it can't actually use.

Connecting Spotify:

  * Quick path — export ``SPOTIFY_ACCESS_TOKEN`` (a Web API OAuth bearer
    token). Expires in ~1h; no auto-refresh.
  * Durable path — write ``~/.jarvis/spotify/auth.json``::

        {
          "access_token": "...",
          "refresh_token": "...",
          "client_id": "...",
          "client_secret": "...",
          "expires_at": 1700000000
        }

    With a refresh token + client credentials, an expired/401 token is
    refreshed in-process and written back to the file.

Only stdlib + ``httpx`` (already in the voice-agent venv) — no music SDK.
"""

from __future__ import annotations

from plugins.spotify.tools import (
    SPOTIFY_ALBUMS_SCHEMA,
    SPOTIFY_DEVICES_SCHEMA,
    SPOTIFY_LIBRARY_SCHEMA,
    SPOTIFY_PLAYBACK_SCHEMA,
    SPOTIFY_PLAYLISTS_SCHEMA,
    SPOTIFY_QUEUE_SCHEMA,
    SPOTIFY_SEARCH_SCHEMA,
    _check_spotify_available,
    _handle_spotify_albums,
    _handle_spotify_devices,
    _handle_spotify_library,
    _handle_spotify_playback,
    _handle_spotify_playlists,
    _handle_spotify_queue,
    _handle_spotify_search,
)

_TOOLS = (
    ("spotify_playback",  SPOTIFY_PLAYBACK_SCHEMA,  _handle_spotify_playback,  "🎵"),
    ("spotify_devices",   SPOTIFY_DEVICES_SCHEMA,   _handle_spotify_devices,   "🔈"),
    ("spotify_queue",     SPOTIFY_QUEUE_SCHEMA,     _handle_spotify_queue,     "📻"),
    ("spotify_search",    SPOTIFY_SEARCH_SCHEMA,    _handle_spotify_search,    "🔎"),
    ("spotify_playlists", SPOTIFY_PLAYLISTS_SCHEMA, _handle_spotify_playlists, "📚"),
    ("spotify_albums",    SPOTIFY_ALBUMS_SCHEMA,    _handle_spotify_albums,    "💿"),
    ("spotify_library",   SPOTIFY_LIBRARY_SCHEMA,   _handle_spotify_library,   "❤️"),
)


def register(ctx) -> None:
    """Register all Spotify tools. Called once by the plugin loader."""
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="spotify",
            schema=schema,
            handler=handler,
            check_fn=_check_spotify_available,
            emoji=emoji,
        )
