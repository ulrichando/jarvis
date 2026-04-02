"""JARVIS Token Vault — store and use API tokens for any platform.

Tokens are stored encrypted-at-rest in ~/.jarvis/vault.json using a
machine-derived key (hostname + username + random salt) and AES-CTR
via the `cryptography` library when available, falling back to a
PBKDF2-keyed XOR stream cipher + HMAC integrity check (stdlib only).

Salt is kept in ~/.jarvis/.vault_salt (auto-generated on first use).
"""

import getpass
import hashlib
import hmac
import json
import os
import platform
import secrets
import struct
import base64
from brain.config import JARVIS_HOME

VAULT_PATH = JARVIS_HOME / "vault.json"
SALT_PATH = JARVIS_HOME / ".vault_salt"

# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------

def _get_or_create_salt() -> bytes:
    """Return the 32-byte salt, creating it if it doesn't exist."""
    JARVIS_HOME.mkdir(parents=True, exist_ok=True)
    if SALT_PATH.exists():
        salt = SALT_PATH.read_bytes()
        if len(salt) >= 16:
            return salt
    salt = secrets.token_bytes(32)
    SALT_PATH.write_bytes(salt)
    os.chmod(SALT_PATH, 0o600)
    return salt


def _derive_key() -> bytes:
    """Derive a 32-byte encryption key from machine identity + salt."""
    machine_secret = f"{platform.node()}|{getpass.getuser()}".encode()
    salt = _get_or_create_salt()
    return hashlib.pbkdf2_hmac("sha256", machine_secret, salt, 100_000)


# ---------------------------------------------------------------------------
# Encryption backends
# ---------------------------------------------------------------------------

try:
    from cryptography.fernet import Fernet as _Fernet  # type: ignore[import]
    _HAS_CRYPTOGRAPHY = True
except ImportError:
    _HAS_CRYPTOGRAPHY = False


class _FernetBackend:
    """AES-128-CBC via the `cryptography` package (preferred)."""

    def __init__(self, key_bytes: bytes):
        # Fernet wants a url-safe-b64 encoded 32-byte key; we have 32 raw
        # bytes — take the first 32 bytes and b64-encode them.
        self._fernet = _Fernet(base64.urlsafe_b64encode(key_bytes[:32]))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode()).decode()


class _StdlibBackend:
    """XOR stream cipher (PBKDF2-derived keystream) + HMAC-SHA256 integrity.

    Format:  base64( iv(16) || ciphertext || hmac(32) )

    Not a replacement for AES, but far better than plaintext for local-only
    storage where the threat model is casual file access.
    """

    def __init__(self, key_bytes: bytes):
        self._key = key_bytes  # 32 bytes

    # -- helpers --
    @staticmethod
    def _xor_bytes(data: bytes, stream: bytes) -> bytes:
        return bytes(a ^ b for a, b in zip(data, stream))

    def _keystream(self, iv: bytes, length: int) -> bytes:
        """Generate a keystream of *length* bytes using HMAC-SHA256 in CTR mode."""
        blocks = []
        counter = 0
        while len(b"".join(blocks)) < length:
            block_input = iv + struct.pack(">Q", counter)
            block = hmac.new(self._key, block_input, hashlib.sha256).digest()
            blocks.append(block)
            counter += 1
        return b"".join(blocks)[:length]

    def encrypt(self, plaintext: str) -> str:
        data = plaintext.encode()
        iv = secrets.token_bytes(16)
        stream = self._keystream(iv, len(data))
        ct = self._xor_bytes(data, stream)
        tag = hmac.new(self._key, iv + ct, hashlib.sha256).digest()
        return base64.b64encode(iv + ct + tag).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        raw = base64.b64decode(ciphertext)
        if len(raw) < 48:  # 16 iv + 0 ct + 32 hmac minimum
            raise ValueError("Ciphertext too short")
        iv, ct, tag = raw[:16], raw[16:-32], raw[-32:]
        expected = hmac.new(self._key, iv + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError("HMAC verification failed — wrong key or tampered data")
        stream = self._keystream(iv, len(ct))
        return self._xor_bytes(ct, stream).decode()


def _make_backend():
    key = _derive_key()
    if _HAS_CRYPTOGRAPHY:
        return _FernetBackend(key)
    return _StdlibBackend(key)


# ---------------------------------------------------------------------------
# Vault
# ---------------------------------------------------------------------------

# Marker stored alongside encrypted entries so we can detect plaintext vaults.
_ENCRYPTED_MARKER = "__vault_encrypted__"


class TokenVault:
    """Manages API tokens and credentials for platform access."""

    def __init__(self):
        self._vault: dict = {}
        self._backend = _make_backend()
        self._load()

    # -- persistence --------------------------------------------------------

    def _encrypt_entry(self, entry: dict) -> str:
        """Serialize and encrypt a single vault entry."""
        return self._backend.encrypt(json.dumps(entry))

    def _decrypt_entry(self, blob: str) -> dict:
        """Decrypt and deserialize a single vault entry."""
        return json.loads(self._backend.decrypt(blob))

    def _load(self):
        if not VAULT_PATH.exists():
            return
        raw = json.loads(VAULT_PATH.read_text())

        if raw.get(_ENCRYPTED_MARKER):
            # Encrypted vault — decrypt each entry.
            for key, blob in raw.items():
                if key == _ENCRYPTED_MARKER:
                    continue
                try:
                    self._vault[key] = self._decrypt_entry(blob)
                except Exception:
                    # Entry unreadable (key changed?) — skip but don't crash.
                    pass
        else:
            # Legacy plaintext vault — migrate.
            self._vault = raw
            self._save()  # re-save encrypted

    def _save(self):
        JARVIS_HOME.mkdir(parents=True, exist_ok=True)
        encrypted: dict[str, str | bool] = {_ENCRYPTED_MARKER: True}
        for key, entry in self._vault.items():
            encrypted[key] = self._encrypt_entry(entry)
        VAULT_PATH.write_text(json.dumps(encrypted, indent=2))
        os.chmod(VAULT_PATH, 0o600)

    # -- public API (unchanged signatures) ----------------------------------

    def store(self, platform: str, token: str, extra: dict | None = None):
        """Store a token for a platform."""
        self._vault[platform.lower()] = {
            "token": token,
            "extra": extra or {},
        }
        self._save()

    def get(self, platform: str) -> str | None:
        """Get a token for a platform."""
        entry = self._vault.get(platform.lower())
        return entry["token"] if entry else None

    def get_with_extra(self, platform: str) -> dict | None:
        """Get token + extra config for a platform."""
        return self._vault.get(platform.lower())

    def delete(self, platform: str):
        """Remove a token."""
        self._vault.pop(platform.lower(), None)
        self._save()

    def list_platforms(self) -> list[str]:
        """List all platforms with stored tokens."""
        return list(self._vault.keys())

    def use_token(self, platform: str, command: str) -> str:
        """Inject a token into a command or API call."""
        token = self.get(platform)
        if not token:
            return f"No token stored for {platform}. Tell me the token and I'll save it."
        return command.replace("{TOKEN}", token).replace("{token}", token)
