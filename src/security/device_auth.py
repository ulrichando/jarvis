"""Ed25519 device authentication for JARVIS.

Each JARVIS instance generates a persistent Ed25519 keypair on first run.
Every WebSocket connection signs a challenge with the private key so the
server can verify the client is a legitimate JARVIS device.

Wire-up summary
───────────────
  Client side (desktop / CLI / mobile):
    auth = get_device_auth()
    token = auth.sign_challenge(server_nonce)   # include in WS handshake header

  Server side (web_server.py):
    auth = get_device_auth()
    ok = auth.verify_token(token, server_nonce, client_public_key_b64)

Payload format (pipe-delimited, same as OpenClaw V3):
  <timestamp_ms>|<device_id>|<nonce>
  Signed with Ed25519 private key → base64url-encoded signature.

Token format (sent as HTTP header or JSON field):
  <base64url_public_key>.<base64url_signature>.<base64url_payload>
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import time
from pathlib import Path

log = logging.getLogger("jarvis.security.device_auth")

# ── Optional cryptography import ─────────────────────────────────────────────
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
        load_pem_private_key,
    )
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    pad = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * (pad % 4))


class DeviceAuth:
    """Ed25519 device identity — generate once, sign every connection."""

    def __init__(self, key_path: Path | None = None) -> None:
        self._key_path = key_path or (
            Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
            / "device_key.pem"
        )
        self._private_key: object | None = None
        self._public_key: object | None = None
        self._device_id: str = ""
        self._available = _CRYPTO_AVAILABLE
        if self._available:
            self._load_or_generate()
        else:
            # Generate a stable device ID from hostname without crypto
            self._device_id = hashlib.sha256(
                os.uname().nodename.encode()
            ).hexdigest()[:16]
            log.warning(
                "cryptography package not installed — Ed25519 auth disabled. "
                "Run: pip install cryptography"
            )

    # ── Setup ─────────────────────────────────────────────────────────

    def _load_or_generate(self) -> None:
        if self._key_path.exists():
            try:
                pem = self._key_path.read_bytes()
                self._private_key = load_pem_private_key(pem, password=None)
                self._public_key = self._private_key.public_key()
                self._device_id = self._derive_device_id()
                log.debug("Loaded Ed25519 device key from %s", self._key_path)
                return
            except Exception as e:
                log.warning("Failed to load device key (%s), regenerating", e)

        # Generate new keypair
        self._private_key = Ed25519PrivateKey.generate()
        self._public_key = self._private_key.public_key()
        self._device_id = self._derive_device_id()

        # Persist private key (chmod 600)
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        pem = self._private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        self._key_path.write_bytes(pem)
        self._key_path.chmod(0o600)
        log.info("Generated new Ed25519 device key → %s", self._key_path)

    def _derive_device_id(self) -> str:
        """Stable device ID = first 16 hex chars of SHA256(public key bytes)."""
        pub_bytes = self._public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        return hashlib.sha256(pub_bytes).hexdigest()[:16]

    # ── Public API ────────────────────────────────────────────────────

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def public_key_b64(self) -> str:
        """Base64url-encoded raw public key (32 bytes for Ed25519)."""
        if not self._available or self._public_key is None:
            return ""
        pub_bytes = self._public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        return _b64url(pub_bytes)

    def sign_challenge(self, server_nonce: str = "") -> str:
        """Sign a connection challenge.  Returns a token string.

        Token format: <pubkey_b64>.<signature_b64>.<payload_b64>

        Call this on the *client* side before opening a WebSocket.
        Pass the returned token in the ``X-JARVIS-Device-Token`` header.
        """
        if not self._available or self._private_key is None:
            return ""

        nonce = server_nonce or secrets.token_hex(16)
        ts = str(int(time.time() * 1000))
        payload = f"{ts}|{self._device_id}|{nonce}"
        payload_bytes = payload.encode()

        sig = self._private_key.sign(payload_bytes)

        pub_bytes = self._public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        return f"{_b64url(pub_bytes)}.{_b64url(sig)}.{_b64url(payload_bytes)}"

    def verify_token(
        self,
        token: str,
        max_age_seconds: int = 60,
        trusted_keys: set[str] | None = None,
    ) -> tuple[bool, str]:
        """Verify a device token from a connecting client.

        Args:
            token:           The ``X-JARVIS-Device-Token`` header value.
            max_age_seconds: Reject tokens older than this (replay protection).
            trusted_keys:    Optional allowlist of base64url public keys.
                             When None, any valid signature is accepted (open).

        Returns:
            ``(ok, device_id)``  — device_id is empty on failure.
        """
        if not self._available:
            return True, "no-crypto"  # graceful degradation

        if not token:
            return False, ""

        try:
            parts = token.split(".")
            if len(parts) != 3:
                return False, ""

            pub_b64, sig_b64, payload_b64 = parts
            payload_bytes = _b64url_decode(payload_b64)
            payload = payload_bytes.decode()

            fields = payload.split("|")
            if len(fields) != 3:
                return False, ""
            ts_ms, device_id, _nonce = fields

            # Replay protection — reject stale tokens
            age_s = (time.time() * 1000 - int(ts_ms)) / 1000
            if age_s > max_age_seconds or age_s < -5:
                log.warning("Rejected stale device token (age=%.1fs)", age_s)
                return False, ""

            # Optional allowlist check
            if trusted_keys is not None and pub_b64 not in trusted_keys:
                log.warning("Rejected untrusted device key: %s", pub_b64[:16])
                return False, ""

            # Verify Ed25519 signature
            pub_bytes = _b64url_decode(pub_b64)
            sig_bytes = _b64url_decode(sig_b64)

            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
            from cryptography.exceptions import InvalidSignature

            pub_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
            try:
                pub_key.verify(sig_bytes, payload_bytes)
            except InvalidSignature:
                log.warning("Invalid Ed25519 signature from device %s", device_id[:8])
                return False, ""

            return True, device_id

        except Exception as e:
            log.warning("Token verification error: %s", e)
            return False, ""


# ── Singleton ─────────────────────────────────────────────────────────────────

_instance: DeviceAuth | None = None


def get_device_auth() -> DeviceAuth:
    """Return the global DeviceAuth singleton."""
    global _instance
    if _instance is None:
        _instance = DeviceAuth()
    return _instance
