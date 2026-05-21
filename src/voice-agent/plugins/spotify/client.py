"""Thin Spotify Web API client for the JARVIS Spotify plugin.

JARVIS-native credential model (no CLI auth subsystem dependency):

    1. ``SPOTIFY_ACCESS_TOKEN`` env var — a bare OAuth bearer token. Simplest
       path; the token expires after ~1h and is not auto-refreshed.
    2. ``~/.jarvis/spotify/auth.json`` — a JSON file holding at minimum
       ``access_token``; optionally ``refresh_token`` + ``client_id`` +
       ``client_secret`` + ``expires_at`` (unix seconds). When a refresh token
       and client credentials are present, an expired/401 token is refreshed
       in-process against ``https://accounts.spotify.com/api/token`` and the
       new token is written back to the file.

The file location is overridable with ``SPOTIFY_AUTH_FILE``. State lands under
``~/.jarvis`` via :func:`tools.runtime.get_jarvis_home`, consistent with the
rest of the agent.

Only stdlib + ``httpx`` (already in the voice-agent venv). No external SDK.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlparse

import httpx

from tools.runtime import get_jarvis_home

_API_BASE = "https://api.spotify.com/v1"
_TOKEN_URL = "https://accounts.spotify.com/api/token"
# Refresh slightly ahead of the real expiry so a long tool call doesn't 401.
_EXPIRY_SKEW_SECONDS = 60


class SpotifyError(RuntimeError):
    """Base Spotify tool error."""


class SpotifyAuthRequiredError(SpotifyError):
    """Raised when the user needs to connect Spotify first."""


class SpotifyAPIError(SpotifyError):
    """Structured Spotify API failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        response_body: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body
        self.path: Optional[str] = None


# ---------------------------------------------------------------------------
# Credential resolution (JARVIS-native)
# ---------------------------------------------------------------------------


def _auth_file_path() -> Path:
    """Return the JARVIS-native Spotify auth file path."""
    override = os.environ.get("SPOTIFY_AUTH_FILE", "").strip()
    if override:
        return Path(override)
    return get_jarvis_home() / "spotify" / "auth.json"


def _read_auth_file() -> Dict[str, Any]:
    """Read the auth file; return {} when absent or malformed."""
    path = _auth_file_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_auth_file(data: Dict[str, Any]) -> None:
    """Persist refreshed credentials back to the auth file (best-effort)."""
    path = _auth_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        # A failed write-back must not break the in-memory token we just got.
        pass


def spotify_credentials_present() -> bool:
    """Return True when a usable Spotify credential source is configured.

    Cheap, no network: just checks for an env token or an ``access_token`` /
    ``refresh_token`` in the auth file. Used as the plugin's ``check_fn`` gate,
    so the tools stay off the LLM surface entirely until Spotify is connected.
    """
    if os.environ.get("SPOTIFY_ACCESS_TOKEN", "").strip():
        return True
    data = _read_auth_file()
    return bool(data.get("access_token") or data.get("refresh_token"))


class SpotifyClient:
    """Minimal authenticated Spotify Web API client."""

    def __init__(self) -> None:
        self._access_token: str = ""
        self._refresh_token: str = ""
        self._client_id: str = ""
        self._client_secret: str = ""
        self._expires_at: float = 0.0
        self._load_credentials()

    # -- credential loading / refresh ---------------------------------------

    def _load_credentials(self) -> None:
        env_token = os.environ.get("SPOTIFY_ACCESS_TOKEN", "").strip()
        data = _read_auth_file()
        self._refresh_token = str(data.get("refresh_token") or "").strip()
        self._client_id = str(
            data.get("client_id") or os.environ.get("SPOTIFY_CLIENT_ID", "")
        ).strip()
        self._client_secret = str(
            data.get("client_secret") or os.environ.get("SPOTIFY_CLIENT_SECRET", "")
        ).strip()
        try:
            self._expires_at = float(data.get("expires_at") or 0.0)
        except (TypeError, ValueError):
            self._expires_at = 0.0

        # Env token wins as the immediate bearer; file token is the fallback.
        self._access_token = env_token or str(data.get("access_token") or "").strip()

        if not self._access_token and not self._refresh_token:
            raise SpotifyAuthRequiredError(
                "Spotify is not connected. Set SPOTIFY_ACCESS_TOKEN or create "
                f"{_auth_file_path()} with an access_token (and optionally a "
                "refresh_token + client_id/client_secret for auto-refresh)."
            )

        # Proactively refresh a token we know is stale, if we can.
        if (
            self._refresh_token
            and self._expires_at
            and time.time() >= self._expires_at - _EXPIRY_SKEW_SECONDS
        ):
            self._refresh_access_token()

    def _can_refresh(self) -> bool:
        return bool(self._refresh_token and self._client_id and self._client_secret)

    def _refresh_access_token(self) -> None:
        if not self._can_refresh():
            if not self._access_token:
                raise SpotifyAuthRequiredError(
                    "Spotify access token expired and no refresh credentials "
                    "(refresh_token + client_id + client_secret) are configured."
                )
            return
        try:
            resp = httpx.post(
                _TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise SpotifyError(f"Spotify token refresh failed: {exc}") from exc
        if resp.status_code >= 400:
            raise SpotifyAuthRequiredError(
                "Spotify token refresh was rejected — reconnect Spotify. "
                f"(status {resp.status_code})"
            )
        payload = resp.json() if resp.content else {}
        new_token = str(payload.get("access_token") or "").strip()
        if not new_token:
            raise SpotifyAuthRequiredError(
                "Spotify token refresh returned no access_token."
            )
        self._access_token = new_token
        expires_in = payload.get("expires_in")
        try:
            self._expires_at = time.time() + float(expires_in) if expires_in else 0.0
        except (TypeError, ValueError):
            self._expires_at = 0.0
        # Spotify may rotate the refresh token; persist whatever we now hold.
        if payload.get("refresh_token"):
            self._refresh_token = str(payload["refresh_token"]).strip()
        merged = _read_auth_file()
        merged.update(
            {
                "access_token": self._access_token,
                "refresh_token": self._refresh_token,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "expires_at": self._expires_at,
            }
        )
        _write_auth_file(merged)

    @property
    def base_url(self) -> str:
        return _API_BASE

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    # -- request plumbing ----------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        allow_retry_on_401: bool = True,
        empty_response: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = httpx.request(
                method,
                url,
                headers=self._headers(),
                params=_strip_none(params),
                json=_strip_none(json_body) if json_body is not None else None,
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise SpotifyError(f"Spotify request failed: {exc}") from exc

        if response.status_code == 401 and allow_retry_on_401 and self._can_refresh():
            self._refresh_access_token()
            return self.request(
                method,
                path,
                params=params,
                json_body=json_body,
                allow_retry_on_401=False,
                empty_response=empty_response,
            )
        if response.status_code >= 400:
            self._raise_api_error(response, method=method, path=path)
        if response.status_code == 204 or not response.content:
            return empty_response or {
                "success": True,
                "status_code": response.status_code,
                "empty": True,
            }
        if "application/json" in response.headers.get("content-type", ""):
            return response.json()
        return {"success": True, "text": response.text}

    def _raise_api_error(self, response: httpx.Response, *, method: str, path: str) -> None:
        detail = response.text.strip()
        message = _friendly_error_message(
            status_code=response.status_code,
            detail=_extract_error_detail(response, fallback=detail),
            path=path,
            retry_after=response.headers.get("Retry-After"),
        )
        error = SpotifyAPIError(message, status_code=response.status_code, response_body=detail)
        error.path = path
        raise error

    # -- playback ------------------------------------------------------------

    def get_devices(self) -> Any:
        return self.request("GET", "/me/player/devices")

    def transfer_playback(self, *, device_id: str, play: bool = False) -> Any:
        return self.request("PUT", "/me/player", json_body={
            "device_ids": [device_id],
            "play": play,
        })

    def get_playback_state(self, *, market: Optional[str] = None) -> Any:
        return self.request(
            "GET",
            "/me/player",
            params={"market": market},
            empty_response={
                "status_code": 204,
                "empty": True,
                "message": (
                    "No active playback session was found. Open a player on a "
                    "device and start playback, or transfer playback to an "
                    "available device."
                ),
            },
        )

    def get_currently_playing(self, *, market: Optional[str] = None) -> Any:
        return self.request(
            "GET",
            "/me/player/currently-playing",
            params={"market": market},
            empty_response={
                "status_code": 204,
                "empty": True,
                "message": "Nothing is currently playing. Start playback and try again.",
            },
        )

    def start_playback(
        self,
        *,
        device_id: Optional[str] = None,
        context_uri: Optional[str] = None,
        uris: Optional[list[str]] = None,
        offset: Optional[Dict[str, Any]] = None,
        position_ms: Optional[int] = None,
    ) -> Any:
        return self.request(
            "PUT",
            "/me/player/play",
            params={"device_id": device_id},
            json_body={
                "context_uri": context_uri,
                "uris": uris,
                "offset": offset,
                "position_ms": position_ms,
            },
        )

    def pause_playback(self, *, device_id: Optional[str] = None) -> Any:
        return self.request("PUT", "/me/player/pause", params={"device_id": device_id})

    def skip_next(self, *, device_id: Optional[str] = None) -> Any:
        return self.request("POST", "/me/player/next", params={"device_id": device_id})

    def skip_previous(self, *, device_id: Optional[str] = None) -> Any:
        return self.request("POST", "/me/player/previous", params={"device_id": device_id})

    def seek(self, *, position_ms: int, device_id: Optional[str] = None) -> Any:
        return self.request("PUT", "/me/player/seek", params={
            "position_ms": position_ms,
            "device_id": device_id,
        })

    def set_repeat(self, *, state: str, device_id: Optional[str] = None) -> Any:
        return self.request("PUT", "/me/player/repeat", params={"state": state, "device_id": device_id})

    def set_shuffle(self, *, state: bool, device_id: Optional[str] = None) -> Any:
        return self.request("PUT", "/me/player/shuffle", params={
            "state": str(bool(state)).lower(),
            "device_id": device_id,
        })

    def set_volume(self, *, volume_percent: int, device_id: Optional[str] = None) -> Any:
        return self.request("PUT", "/me/player/volume", params={
            "volume_percent": volume_percent,
            "device_id": device_id,
        })

    def get_queue(self) -> Any:
        return self.request("GET", "/me/player/queue")

    def add_to_queue(self, *, uri: str, device_id: Optional[str] = None) -> Any:
        return self.request("POST", "/me/player/queue", params={"uri": uri, "device_id": device_id})

    def get_recently_played(
        self,
        *,
        limit: int = 20,
        after: Optional[int] = None,
        before: Optional[int] = None,
    ) -> Any:
        return self.request("GET", "/me/player/recently-played", params={
            "limit": limit,
            "after": after,
            "before": before,
        })

    # -- search --------------------------------------------------------------

    def search(
        self,
        *,
        query: str,
        search_types: list[str],
        limit: int = 10,
        offset: int = 0,
        market: Optional[str] = None,
        include_external: Optional[str] = None,
    ) -> Any:
        return self.request("GET", "/search", params={
            "q": query,
            "type": ",".join(search_types),
            "limit": limit,
            "offset": offset,
            "market": market,
            "include_external": include_external,
        })

    # -- playlists -----------------------------------------------------------

    def get_my_playlists(self, *, limit: int = 20, offset: int = 0) -> Any:
        return self.request("GET", "/me/playlists", params={"limit": limit, "offset": offset})

    def get_playlist(self, *, playlist_id: str, market: Optional[str] = None) -> Any:
        return self.request("GET", f"/playlists/{playlist_id}", params={"market": market})

    def create_playlist(
        self,
        *,
        name: str,
        public: bool = False,
        collaborative: bool = False,
        description: Optional[str] = None,
    ) -> Any:
        return self.request("POST", "/me/playlists", json_body={
            "name": name,
            "public": public,
            "collaborative": collaborative,
            "description": description,
        })

    def add_playlist_items(
        self,
        *,
        playlist_id: str,
        uris: list[str],
        position: Optional[int] = None,
    ) -> Any:
        return self.request("POST", f"/playlists/{playlist_id}/tracks", json_body={
            "uris": uris,
            "position": position,
        })

    def remove_playlist_items(
        self,
        *,
        playlist_id: str,
        uris: list[str],
        snapshot_id: Optional[str] = None,
    ) -> Any:
        return self.request("DELETE", f"/playlists/{playlist_id}/tracks", json_body={
            "tracks": [{"uri": uri} for uri in uris],
            "snapshot_id": snapshot_id,
        })

    def update_playlist_details(
        self,
        *,
        playlist_id: str,
        name: Optional[str] = None,
        public: Optional[bool] = None,
        collaborative: Optional[bool] = None,
        description: Optional[str] = None,
    ) -> Any:
        return self.request("PUT", f"/playlists/{playlist_id}", json_body={
            "name": name,
            "public": public,
            "collaborative": collaborative,
            "description": description,
        })

    # -- albums --------------------------------------------------------------

    def get_album(self, *, album_id: str, market: Optional[str] = None) -> Any:
        return self.request("GET", f"/albums/{album_id}", params={"market": market})

    def get_album_tracks(self, *, album_id: str, limit: int = 20, offset: int = 0, market: Optional[str] = None) -> Any:
        return self.request("GET", f"/albums/{album_id}/tracks", params={
            "limit": limit,
            "offset": offset,
            "market": market,
        })

    # -- library -------------------------------------------------------------

    def get_saved_tracks(self, *, limit: int = 20, offset: int = 0, market: Optional[str] = None) -> Any:
        return self.request("GET", "/me/tracks", params={"limit": limit, "offset": offset, "market": market})

    def save_saved_tracks(self, *, track_ids: list[str]) -> Any:
        return self.request("PUT", "/me/tracks", params={"ids": ",".join(track_ids)})

    def get_saved_albums(self, *, limit: int = 20, offset: int = 0, market: Optional[str] = None) -> Any:
        return self.request("GET", "/me/albums", params={"limit": limit, "offset": offset, "market": market})

    def save_saved_albums(self, *, album_ids: list[str]) -> Any:
        return self.request("PUT", "/me/albums", params={"ids": ",".join(album_ids)})

    def remove_saved_tracks(self, *, track_ids: list[str]) -> Any:
        return self.request("DELETE", "/me/tracks", params={"ids": ",".join(track_ids)})

    def remove_saved_albums(self, *, album_ids: list[str]) -> Any:
        return self.request("DELETE", "/me/albums", params={"ids": ",".join(album_ids)})


# ---------------------------------------------------------------------------
# Error formatting
# ---------------------------------------------------------------------------


def _extract_error_detail(response: httpx.Response, *, fallback: str) -> str:
    detail = fallback
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error_obj = payload.get("error")
            if isinstance(error_obj, dict):
                detail = str(error_obj.get("message") or detail)
            elif isinstance(error_obj, str):
                detail = error_obj
    except Exception:
        pass
    return detail.strip()


def _friendly_error_message(
    *,
    status_code: int,
    detail: str,
    path: str,
    retry_after: Optional[str],
) -> str:
    normalized_detail = detail.lower()
    is_playback_path = path.startswith("/me/player")

    if status_code == 401:
        return (
            "Spotify authentication failed or expired. Reconnect Spotify "
            "(refresh SPOTIFY_ACCESS_TOKEN or the auth file)."
        )
    if status_code == 403:
        if is_playback_path:
            return (
                "Spotify rejected this playback request. Playback control "
                "usually requires a Spotify Premium account and an active "
                "Connect device."
            )
        if "scope" in normalized_detail or "permission" in normalized_detail:
            return (
                "Spotify rejected the request: the current auth scope is "
                "insufficient. Reconnect with the needed scopes."
            )
        return "Spotify rejected the request. The account may lack permission for this action."
    if status_code == 404:
        if is_playback_path:
            return "Spotify could not find an active playback device or player session."
        return "Spotify resource not found."
    if status_code == 429:
        message = "Spotify rate limit exceeded."
        if retry_after:
            message += f" Retry after {retry_after} seconds."
        return message
    if detail:
        return detail
    return f"Spotify API request failed with status {status_code}."


# ---------------------------------------------------------------------------
# URI / ID normalization
# ---------------------------------------------------------------------------


def _strip_none(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not payload:
        return {}
    return {key: value for key, value in payload.items() if value is not None}


def normalize_spotify_id(value: str, expected_type: Optional[str] = None) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise SpotifyError("Spotify id/uri/url is required.")
    if cleaned.startswith("spotify:"):
        parts = cleaned.split(":")
        if len(parts) >= 3:
            item_type = parts[1]
            if expected_type and item_type != expected_type:
                raise SpotifyError(f"Expected a Spotify {expected_type}, got {item_type}.")
            return parts[2]
    if "open.spotify.com" in cleaned:
        parsed = urlparse(cleaned)
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) >= 2:
            item_type, item_id = path_parts[0], path_parts[1]
            if expected_type and item_type != expected_type:
                raise SpotifyError(f"Expected a Spotify {expected_type}, got {item_type}.")
            return item_id
    return cleaned


def normalize_spotify_uri(value: str, expected_type: Optional[str] = None) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise SpotifyError("Spotify URI/url/id is required.")
    if cleaned.startswith("spotify:"):
        if expected_type:
            parts = cleaned.split(":")
            if len(parts) >= 3 and parts[1] != expected_type:
                raise SpotifyError(f"Expected a Spotify {expected_type}, got {parts[1]}.")
        return cleaned
    item_id = normalize_spotify_id(cleaned, expected_type)
    if expected_type:
        return f"spotify:{expected_type}:{item_id}"
    return cleaned


def normalize_spotify_uris(values: Iterable[str], expected_type: Optional[str] = None) -> list[str]:
    uris: list[str] = []
    for value in values:
        uri = normalize_spotify_uri(str(value), expected_type)
        if uri not in uris:
            uris.append(uri)
    if not uris:
        raise SpotifyError("At least one Spotify item is required.")
    return uris
