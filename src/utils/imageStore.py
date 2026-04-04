"""
Image store for caching pasted images to disk.

Stores base64-encoded images from paste operations to the filesystem
for later retrieval.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

IMAGE_STORE_DIR = "image-cache"
MAX_STORED_IMAGE_PATHS = 200

# In-memory cache of stored image paths
_stored_image_paths: dict[int, str] = {}


def _get_config_home() -> str:
    """Get the configuration home directory."""
    return os.environ.get("JARVIS_HOME", os.path.expanduser("~/.jarvis"))


def _get_session_id() -> str:
    """Get the current session ID."""
    return os.environ.get("JARVIS_SESSION_ID", "default")


def _get_image_store_dir() -> str:
    """Get the image store directory for the current session."""
    return os.path.join(_get_config_home(), IMAGE_STORE_DIR, _get_session_id())


def _get_image_path(image_id: int, media_type: str) -> str:
    """Get the file path for an image by ID."""
    extension = media_type.split("/")[1] if "/" in media_type else "png"
    return os.path.join(_get_image_store_dir(), f"{image_id}.{extension}")


def _evict_oldest_if_at_cap() -> None:
    """Evict oldest entries if at capacity."""
    while len(_stored_image_paths) >= MAX_STORED_IMAGE_PATHS:
        oldest_key = next(iter(_stored_image_paths))
        del _stored_image_paths[oldest_key]


def cache_image_path(content: dict[str, Any]) -> Optional[str]:
    """
    Cache the image path immediately (fast, no file I/O).

    Args:
        content: Dict with 'type', 'id', 'mediaType', and 'content' keys.

    Returns:
        The image path if content is an image, None otherwise.
    """
    if content.get("type") != "image":
        return None

    image_id = content["id"]
    media_type = content.get("mediaType", "image/png")
    image_path = _get_image_path(image_id, media_type)
    _evict_oldest_if_at_cap()
    _stored_image_paths[image_id] = image_path
    return image_path


async def store_image(content: dict[str, Any]) -> Optional[str]:
    """
    Store an image from pasted content to disk.

    Args:
        content: Dict with 'type', 'id', 'mediaType', and 'content' keys.

    Returns:
        The image path if successful, None otherwise.
    """
    if content.get("type") != "image":
        return None

    try:
        store_dir = _get_image_store_dir()
        os.makedirs(store_dir, exist_ok=True)

        image_id = content["id"]
        media_type = content.get("mediaType", "image/png")
        image_path = _get_image_path(image_id, media_type)

        # Decode base64 and write to file
        image_data = base64.b64decode(content["content"])
        with open(image_path, "wb") as f:
            f.write(image_data)
        os.chmod(image_path, 0o600)

        _evict_oldest_if_at_cap()
        _stored_image_paths[image_id] = image_path
        logger.debug(f"Stored image {image_id} to {image_path}")
        return image_path
    except Exception as e:
        logger.debug(f"Failed to store image: {e}")
        return None


async def store_images(
    pasted_contents: dict[int, dict[str, Any]]
) -> dict[int, str]:
    """
    Store all images from pasted contents to disk.

    Returns:
        Map of image IDs to file paths.
    """
    path_map: dict[int, str] = {}
    for image_id, content in pasted_contents.items():
        if content.get("type") == "image":
            path = await store_image(content)
            if path:
                path_map[image_id] = path
    return path_map


def get_stored_image_path(image_id: int) -> Optional[str]:
    """Get the file path for a stored image by ID."""
    return _stored_image_paths.get(image_id)


def clear_stored_image_paths() -> None:
    """Clear the in-memory cache of stored image paths."""
    _stored_image_paths.clear()


async def cleanup_old_image_caches() -> None:
    """Clean up old image cache directories from previous sessions."""
    base_dir = os.path.join(_get_config_home(), IMAGE_STORE_DIR)
    current_session_id = _get_session_id()

    try:
        if not os.path.isdir(base_dir):
            return

        for entry in os.scandir(base_dir):
            if entry.name == current_session_id:
                continue
            if entry.is_dir():
                try:
                    import shutil
                    shutil.rmtree(entry.path, ignore_errors=True)
                    logger.debug(f"Cleaned up old image cache: {entry.path}")
                except Exception:
                    pass

        # Remove base dir if empty
        try:
            remaining = list(os.scandir(base_dir))
            if not remaining:
                os.rmdir(base_dir)
        except Exception:
            pass
    except Exception:
        pass
