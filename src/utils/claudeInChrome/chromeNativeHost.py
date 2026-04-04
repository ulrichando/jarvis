"""Chrome Native Host - Python implementation.

Provides Chrome native messaging host functionality.
"""

from __future__ import annotations

import json
import logging
import os
import struct
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

VERSION = "1.0.0"
MAX_MESSAGE_SIZE = 1024 * 1024  # 1MB


def send_chrome_message(message: str) -> None:
    """Send a message to stdout using Chrome native messaging protocol."""
    json_bytes = message.encode("utf-8")
    length_bytes = struct.pack("<I", len(json_bytes))
    sys.stdout.buffer.write(length_bytes)
    sys.stdout.buffer.write(json_bytes)
    sys.stdout.buffer.flush()


async def run_chrome_native_host() -> None:
    """Initialize and run the Chrome native host."""
    logger.info("Initializing Chrome Native Host...")

    while True:
        try:
            # Read message length (4 bytes, little-endian)
            raw_length = sys.stdin.buffer.read(4)
            if not raw_length or len(raw_length) < 4:
                break

            msg_length = struct.unpack("<I", raw_length)[0]
            if msg_length > MAX_MESSAGE_SIZE:
                logger.error(f"Message too large: {msg_length}")
                break

            # Read message body
            raw_message = sys.stdin.buffer.read(msg_length)
            if not raw_message or len(raw_message) < msg_length:
                break

            message = raw_message.decode("utf-8")
            data = json.loads(message)

            # Process message
            response = {"version": VERSION, "status": "ok"}
            send_chrome_message(json.dumps(response))

        except (json.JSONDecodeError, struct.error) as e:
            logger.error(f"Protocol error: {e}")
            break
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            break
