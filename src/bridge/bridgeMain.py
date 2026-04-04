"""Main bridge loop for standalone Remote Control."""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any, Optional

from .bridgeApi import BridgeFatalError, create_bridge_api_client, validate_bridge_id
from .bridgeStatusUtil import format_duration
from .types import BridgeConfig, SessionActivity, SpawnMode

logger = logging.getLogger(__name__)


async def run_bridge_main(
    config: BridgeConfig,
    api_client: Any,
    spawner: Any,
    bridge_logger: Any,
    signal: Optional[asyncio.Event] = None,
) -> None:
    """Run the standalone bridge main loop.

    Registers the environment, polls for work, spawns sessions,
    and manages the session lifecycle.
    """
    stop_event = signal or asyncio.Event()

    try:
        result = await api_client.register_bridge_environment(config)
        environment_id = result["environment_id"]
        environment_secret = result["environment_secret"]
    except BridgeFatalError as err:
        bridge_logger.log_error(str(err))
        return
    except Exception as err:
        bridge_logger.log_error(f"Registration failed: {err}")
        return

    bridge_logger.print_banner(config, environment_id)
    bridge_logger.update_idle_status()

    active_sessions: dict[str, Any] = {}

    try:
        while not stop_event.is_set():
            try:
                work = await api_client.poll_for_work(
                    environment_id, environment_secret,
                )
            except BridgeFatalError:
                break
            except Exception:
                await asyncio.sleep(2)
                continue

            if not work:
                await asyncio.sleep(2)
                continue

            work_data = work.get("data", {}) if isinstance(work, dict) else {}
            work_type = work_data.get("type")
            session_id = work_data.get("id", "")

            if work_type == "healthcheck":
                continue

            if work_type == "session":
                bridge_logger.log_session_start(session_id, "")
                bridge_logger.set_attached(session_id)

    except asyncio.CancelledError:
        pass
    finally:
        bridge_logger.clear_status()
        try:
            await api_client.deregister_environment(environment_id)
        except Exception:
            pass
