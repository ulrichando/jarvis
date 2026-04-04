"""Chrome extension installation notification."""

from __future__ import annotations

import sys
from typing import Any, Callable, Optional


async def check_chrome_extension_notification(
    is_subscriber: Callable[[], bool] = lambda: False,
    is_extension_installed: Optional[Callable] = None,
    should_enable: Callable = lambda flag: False,
) -> Optional[dict]:
    """Check and return Chrome extension notification if needed.

    Equivalent to useChromeExtensionNotification React hook.
    """
    chrome_flag = None
    if "--chrome" in sys.argv:
        chrome_flag = True
    elif "--no-chrome" in sys.argv:
        chrome_flag = False

    if not should_enable(chrome_flag):
        return None

    if not is_subscriber():
        return {
            "key": "chrome-requires-subscription",
            "text": "JARVIS Chrome extension requires a subscription",
            "priority": "immediate",
            "timeout_ms": 5000,
        }

    if is_extension_installed:
        installed = await is_extension_installed()
        if not installed:
            return {
                "key": "chrome-extension-not-detected",
                "text": "Chrome extension not detected",
                "priority": "immediate",
                "timeout_ms": 3000,
            }

    if chrome_flag is None:
        return {
            "key": "jarvis-in-chrome-default-enabled",
            "text": "JARVIS in Chrome enabled",
            "priority": "low",
        }

    return None
