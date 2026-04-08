"""MIME type detection from magic bytes.

Detects common file types from the first few bytes of a file,
independent of its extension (which can be spoofed).

Mirrors the intent of OpenClaw's src/media/mime.ts (file-type library).

Usage:
    mime, category = detect_mime("/path/to/file.bin")
    # → ("image/png", "image")

    err = check_media_size("/path/to/file.png")
    # → None if OK, or "File too large for image (7.2 MB > 6 MB limit)"
"""

from __future__ import annotations

from pathlib import Path

# ── Magic byte signatures ─────────────────────────────────────────────────────
# Each entry: (byte_offset, magic_bytes, mime_type, broad_category)
# Listed in order of specificity (longer/more-specific patterns first).

_SIGNATURES: list[tuple[int, bytes, str, str]] = [
    # Images
    (0, b"\x89PNG\r\n\x1a\n",  "image/png",            "image"),
    (0, b"\xff\xd8\xff",        "image/jpeg",           "image"),
    (0, b"GIF89a",             "image/gif",            "image"),
    (0, b"GIF87a",             "image/gif",            "image"),
    (0, b"BM",                 "image/bmp",            "image"),
    (0, b"\x00\x00\x01\x00",  "image/x-icon",         "image"),
    (0, b"II\x2a\x00",         "image/tiff",           "image"),
    (0, b"MM\x00\x2a",         "image/tiff",           "image"),
    (0, b"\x00\x00\x00\x0cftyp","image/avif",          "image"),  # AVIF/HEIF
    # RIFF containers (WEBP / WAV / AVI) — disambiguated by bytes 8-11
    (0, b"RIFF",               "application/riff",     "binary"),
    # Audio
    (0, b"ID3",                "audio/mpeg",           "audio"),
    (0, b"\xff\xfb",           "audio/mpeg",           "audio"),
    (0, b"\xff\xf3",           "audio/mpeg",           "audio"),
    (0, b"\xff\xf2",           "audio/mpeg",           "audio"),
    (0, b"OggS",               "audio/ogg",            "audio"),
    (0, b"fLaC",               "audio/flac",           "audio"),
    (0, b"MAC ",               "audio/ape",            "audio"),
    # Video
    (4, b"ftypisom",           "video/mp4",            "video"),
    (4, b"ftypmp42",           "video/mp4",            "video"),
    (4, b"ftyp",              "video/mp4",            "video"),
    (0, b"\x1aE\xdf\xa3",     "video/webm",           "video"),
    (0, b"\x00\x00\x01\xba",  "video/mpeg",           "video"),
    (0, b"\x00\x00\x01\xb3",  "video/mpeg",           "video"),
    # Documents / archives
    (0, b"%PDF-",             "application/pdf",      "document"),
    (0, b"PK\x03\x04",        "application/zip",      "document"),
    (0, b"PK\x05\x06",        "application/zip",      "document"),
    (0, b"\xd0\xcf\x11\xe0",  "application/msword",   "document"),  # OLE2 (doc/xls/ppt)
    (0, b"\x1f\x8b",          "application/gzip",     "document"),
    (0, b"BZh",               "application/x-bzip2",  "document"),
    (0, b"\xfd7zXZ\x00",      "application/x-xz",     "document"),
    (0, b"7z\xbc\xaf\x27\x1c","application/x-7z-compressed", "document"),
    (0, b"Rar!\x1a\x07",      "application/x-rar",    "document"),
    # Scripts / text magic lines
    (0, b"#!/",               "text/x-script",        "text"),
    (0, b"<?xml",             "text/xml",             "text"),
    (0, b"<?php",             "text/x-php",           "text"),
]

# RIFF sub-type disambiguation: bytes[8:12] → (mime, category)
_RIFF_SUBTYPES: dict[bytes, tuple[str, str]] = {
    b"WAVE": ("audio/wav",   "audio"),
    b"WEBP": ("image/webp",  "image"),
    b"AVI ": ("video/avi",   "video"),
    b"AIFF": ("audio/aiff",  "audio"),
}


def detect_mime(path: str | Path) -> tuple[str, str]:
    """Detect MIME type and broad category from a file's magic bytes.

    Returns:
        ``(mime_type, category)`` where *category* is one of:
        ``"image"``, ``"audio"``, ``"video"``, ``"document"``,
        ``"text"``, ``"binary"``, ``"unknown"``
    """
    try:
        # Read 512 bytes once — enough for all magic signatures + NUL heuristic
        data = Path(path).read_bytes()[:512]
    except OSError:
        return "application/octet-stream", "unknown"

    for offset, magic, mime, category in _SIGNATURES:
        end = offset + len(magic)
        if data[offset:end] == magic:
            # Disambiguate RIFF containers (sub-type at bytes 8-11)
            if magic == b"RIFF" and len(data) >= 12:
                sub = data[8:12]
                mime, category = _RIFF_SUBTYPES.get(sub, ("application/octet-stream", "binary"))
            return mime, category

    # Heuristic: no NUL bytes in sample → treat as text
    if b"\x00" not in data:
        return "text/plain", "text"

    return "application/octet-stream", "binary"


def check_media_size(path: str | Path, category: str | None = None) -> str | None:
    """Check whether a file exceeds the size limit for its category.

    Returns:
        ``None`` if the file is within the limit, or an error message string
        if it exceeds the limit.
    """
    from src.media.constants import SIZE_LIMITS

    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        return None  # file not found — caller handles it

    if category is None:
        _, category = detect_mime(p)

    limit = SIZE_LIMITS.get(category, SIZE_LIMITS["document"])
    if size > limit:
        limit_mb = limit / (1024 * 1024)
        size_mb = size / (1024 * 1024)
        return (
            f"File too large for {category}: "
            f"{size_mb:.1f} MB exceeds {limit_mb:.0f} MB limit"
        )
    return None
