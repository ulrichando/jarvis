"""Media size limits by broad category.

Mirrors OpenClaw src/media/constants.ts.
"""

# Per-category byte limits
IMAGE_MAX_BYTES: int = 6 * 1024 * 1024      #   6 MB
AUDIO_MAX_BYTES: int = 16 * 1024 * 1024     #  16 MB
VIDEO_MAX_BYTES: int = 16 * 1024 * 1024     #  16 MB
DOCUMENT_MAX_BYTES: int = 100 * 1024 * 1024 # 100 MB

# Lookup by broad category string
SIZE_LIMITS: dict[str, int] = {
    "image":    IMAGE_MAX_BYTES,
    "audio":    AUDIO_MAX_BYTES,
    "video":    VIDEO_MAX_BYTES,
    "document": DOCUMENT_MAX_BYTES,
    "text":     DOCUMENT_MAX_BYTES,
    "binary":   DOCUMENT_MAX_BYTES,
}
