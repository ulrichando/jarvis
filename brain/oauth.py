"""JARVIS OAuth 2.0 — PKCE authentication flow for API providers.

Supports authorization code flow with S256 PKCE challenge.
Stores tokens in ~/.jarvis/oauth_credentials.json.
"""

import os
import json
import hashlib
import base64
import secrets
import time
import logging
import urllib.parse
from dataclasses import dataclass, field
from brain.config import JARVIS_HOME

log = logging.getLogger("jarvis.oauth")

CREDENTIALS_PATH = JARVIS_HOME / "oauth_credentials.json"


@dataclass
class OAuthTokenSet:
    access_token: str = ""
    refresh_token: str = ""
    expires_at: int = 0  # Unix timestamp
    scopes: list[str] = field(default_factory=list)

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return time.time() >= self.expires_at

    @property
    def can_refresh(self) -> bool:
        return bool(self.refresh_token)


@dataclass
class PkceChallenge:
    verifier: str = ""
    challenge: str = ""
    method: str = "S256"


def generate_pkce() -> PkceChallenge:
    """Generate a PKCE code verifier and S256 challenge."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return PkceChallenge(verifier=verifier, challenge=challenge)


def build_auth_url(
    authorize_url: str, client_id: str, redirect_uri: str,
    scopes: list[str], state: str, pkce: PkceChallenge,
    extra_params: dict = None,
) -> str:
    """Build the OAuth authorization URL."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": pkce.challenge,
        "code_challenge_method": pkce.method,
    }
    if extra_params:
        params.update(extra_params)
    return f"{authorize_url}?{urllib.parse.urlencode(params)}"


def exchange_code(
    token_url: str, code: str, redirect_uri: str,
    client_id: str, pkce: PkceChallenge, state: str = "",
) -> OAuthTokenSet:
    """Exchange authorization code for tokens."""
    import urllib.request
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": pkce.verifier,
        "state": state,
    }).encode()

    req = urllib.request.Request(token_url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())

    return OAuthTokenSet(
        access_token=result.get("access_token", ""),
        refresh_token=result.get("refresh_token", ""),
        expires_at=int(time.time()) + result.get("expires_in", 3600),
        scopes=result.get("scope", "").split(),
    )


def refresh_token(token_url: str, client_id: str, token_set: OAuthTokenSet) -> OAuthTokenSet:
    """Refresh an expired token."""
    import urllib.request
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": token_set.refresh_token,
        "client_id": client_id,
    }).encode()

    req = urllib.request.Request(token_url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())

    return OAuthTokenSet(
        access_token=result.get("access_token", ""),
        refresh_token=result.get("refresh_token", token_set.refresh_token),  # Keep old if not returned
        expires_at=int(time.time()) + result.get("expires_in", 3600),
        scopes=result.get("scope", "").split() or token_set.scopes,
    )


def save_credentials(provider: str, token_set: OAuthTokenSet):
    """Save OAuth credentials to disk."""
    JARVIS_HOME.mkdir(parents=True, exist_ok=True)
    creds = {}
    if CREDENTIALS_PATH.exists():
        try:
            creds = json.loads(CREDENTIALS_PATH.read_text())
        except Exception:
            pass
    creds[provider] = {
        "access_token": token_set.access_token,
        "refresh_token": token_set.refresh_token,
        "expires_at": token_set.expires_at,
        "scopes": token_set.scopes,
    }
    CREDENTIALS_PATH.write_text(json.dumps(creds, indent=2))
    os.chmod(CREDENTIALS_PATH, 0o600)


def load_credentials(provider: str) -> OAuthTokenSet | None:
    """Load saved OAuth credentials."""
    if not CREDENTIALS_PATH.exists():
        return None
    try:
        creds = json.loads(CREDENTIALS_PATH.read_text())
        data = creds.get(provider)
        if not data:
            return None
        return OAuthTokenSet(
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token", ""),
            expires_at=data.get("expires_at", 0),
            scopes=data.get("scopes", []),
        )
    except Exception:
        return None


def clear_credentials(provider: str):
    """Remove saved credentials for a provider."""
    if not CREDENTIALS_PATH.exists():
        return
    try:
        creds = json.loads(CREDENTIALS_PATH.read_text())
        creds.pop(provider, None)
        CREDENTIALS_PATH.write_text(json.dumps(creds, indent=2))
    except Exception:
        pass
