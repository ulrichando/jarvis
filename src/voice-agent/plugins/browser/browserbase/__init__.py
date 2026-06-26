"""Browserbase cloud-browser backend (registry kind ``browser``).

Registers a remote cloud-browser provider that the local ``browser_task`` tool
can optionally drive over CDP. Opt-in only: it is consumed solely when the
operator sets ``JARVIS_BROWSER_PROVIDER=browserbase`` AND the provider is
available (``BROWSERBASE_API_KEY`` + ``BROWSERBASE_PROJECT_ID`` set). With the
env unset, ``browser_task`` uses its local subprocess path unchanged.

Browserbase needs direct ``BROWSERBASE_API_KEY`` + ``BROWSERBASE_PROJECT_ID``
credentials. ``create_session`` returns a real CDP connect URL (Browserbase's
``connectUrl``, normalized here to the JARVIS-native ``cdp_url`` key) so the
browser_use runner can attach to the remote browser.

Auth env vars::

    BROWSERBASE_API_KEY=...       # https://browserbase.com
    BROWSERBASE_PROJECT_ID=...

Optional feature knobs::

    BROWSERBASE_BASE_URL=...      # default https://api.browserbase.com
    BROWSERBASE_PROXIES=true      # default true
    BROWSERBASE_ADVANCED_STEALTH=false
    BROWSERBASE_KEEP_ALIVE=true   # default true
    BROWSERBASE_SESSION_TIMEOUT=... (ms, integer)

Ported from the upstream browserbase browser plugin; the ``agent.*`` base +
``config.yaml`` / managed-gateway coupling was stripped. The provider class is
inline (no separate ``provider.py``) so it loads cleanly under the plugin
namespace and via ``spec_from_file_location`` in tests.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, Optional

import requests

from tools.browser_providers import BrowserProvider

logger = logging.getLogger(__name__)


class BrowserbaseProvider(BrowserProvider):
    """Browserbase (https://browserbase.com) cloud browser backend.

    Direct credentials only — no managed gateway. ``create_session`` opens a
    remote CDP-addressable browser; the returned ``cdp_url`` is what the
    ``browser_task`` runner attaches to.
    """

    name = "browserbase"

    @property
    def display_name(self) -> str:
        return "Browserbase"

    def is_available(self) -> bool:
        return self._get_config_or_none() is not None

    # ------------------------------------------------------------------
    # Config resolution
    # ------------------------------------------------------------------

    def _get_config_or_none(self) -> Optional[Dict[str, Any]]:
        api_key = os.environ.get("BROWSERBASE_API_KEY")
        project_id = os.environ.get("BROWSERBASE_PROJECT_ID")
        if api_key and project_id:
            return {
                "api_key": api_key,
                "project_id": project_id,
                "base_url": os.environ.get(
                    "BROWSERBASE_BASE_URL", "https://api.browserbase.com"
                ).rstrip("/"),
            }
        return None

    def _get_config(self) -> Dict[str, Any]:
        config = self._get_config_or_none()
        if config is None:
            raise ValueError(
                "Browserbase requires BROWSERBASE_API_KEY and BROWSERBASE_PROJECT_ID "
                "environment variables."
            )
        return config

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def create_session(self, task_id: str) -> Dict[str, Any]:
        config = self._get_config()

        # Optional env-var knobs.
        enable_proxies = os.environ.get("BROWSERBASE_PROXIES", "true").lower() != "false"
        enable_advanced_stealth = (
            os.environ.get("BROWSERBASE_ADVANCED_STEALTH", "false").lower() == "true"
        )
        enable_keep_alive = (
            os.environ.get("BROWSERBASE_KEEP_ALIVE", "true").lower() != "false"
        )
        custom_timeout_ms = os.environ.get("BROWSERBASE_SESSION_TIMEOUT")

        features_enabled = {
            "basic_stealth": True,
            "proxies": False,
            "advanced_stealth": False,
            "keep_alive": False,
            "custom_timeout": False,
        }

        session_config: Dict[str, object] = {"projectId": config["project_id"]}

        if enable_keep_alive:
            session_config["keepAlive"] = True

        if custom_timeout_ms:
            try:
                timeout_val = int(custom_timeout_ms)
                if timeout_val > 0:
                    session_config["timeout"] = timeout_val
            except ValueError:
                logger.warning(
                    "Invalid BROWSERBASE_SESSION_TIMEOUT value: %s", custom_timeout_ms
                )

        if enable_proxies:
            session_config["proxies"] = True

        if enable_advanced_stealth:
            session_config["browserSettings"] = {"advancedStealth": True}

        headers = {
            "Content-Type": "application/json",
            "X-BB-API-Key": config["api_key"],
        }

        try:
            response = requests.post(
                f"{config['base_url']}/v1/sessions",
                headers=headers,
                json=session_config,
                timeout=30,
            )

            proxies_fallback = False
            keepalive_fallback = False

            # Handle 402 — paid features unavailable; retry without them.
            if response.status_code == 402:
                if enable_keep_alive:
                    keepalive_fallback = True
                    logger.warning(
                        "keepAlive may require a paid plan (402), retrying without it. "
                        "Sessions may time out during long operations."
                    )
                    session_config.pop("keepAlive", None)
                    response = requests.post(
                        f"{config['base_url']}/v1/sessions",
                        headers=headers,
                        json=session_config,
                        timeout=30,
                    )

                if response.status_code == 402 and enable_proxies:
                    proxies_fallback = True
                    logger.warning(
                        "Proxies unavailable (402), retrying without proxies. "
                        "Bot detection may be less effective."
                    )
                    session_config.pop("proxies", None)
                    response = requests.post(
                        f"{config['base_url']}/v1/sessions",
                        headers=headers,
                        json=session_config,
                        timeout=30,
                    )
        except requests.RequestException as exc:
            raise RuntimeError(f"Browserbase API connection failed: {exc}") from exc

        if not response.ok:
            raise RuntimeError(
                f"Failed to create Browserbase session: "
                f"{response.status_code} {response.text}"
            )

        session_data = response.json()
        session_name = f"jarvis_{task_id}_{uuid.uuid4().hex[:8]}"

        if enable_proxies and not proxies_fallback:
            features_enabled["proxies"] = True
        if enable_advanced_stealth:
            features_enabled["advanced_stealth"] = True
        if enable_keep_alive and not keepalive_fallback:
            features_enabled["keep_alive"] = True
        if custom_timeout_ms and "timeout" in session_config:
            features_enabled["custom_timeout"] = True

        feature_str = ", ".join(k for k, v in features_enabled.items() if v)
        logger.info(
            "Created Browserbase session %s with features: %s", session_name, feature_str
        )

        # Normalized JARVIS-native session shape: cdp_url + session_id are the
        # contract the browser_task resolver consumes. The extra keys are
        # informational.
        return {
            "cdp_url": session_data["connectUrl"],
            "session_id": session_data["id"],
            "session_name": session_name,
            "features": features_enabled,
        }

    def close_session(self, session_id: str) -> bool:
        try:
            config = self._get_config()
        except ValueError:
            logger.warning(
                "Cannot close Browserbase session %s — missing credentials", session_id
            )
            return False

        try:
            response = requests.post(
                f"{config['base_url']}/v1/sessions/{session_id}",
                headers={
                    "X-BB-API-Key": config["api_key"],
                    "Content-Type": "application/json",
                },
                json={"projectId": config["project_id"], "status": "REQUEST_RELEASE"},
                timeout=10,
            )
            if response.status_code in {200, 201, 204}:
                logger.debug("Closed Browserbase session %s", session_id)
                return True
            # Log the status only — the API response body can carry session/
            # project detail (py/clear-text-logging-sensitive-data).
            logger.warning(
                "Failed to close Browserbase session %s: HTTP %s",
                session_id,
                response.status_code,
            )
            return False
        except Exception as exc:  # noqa: BLE001 — cleanup must not raise
            logger.error("Exception closing Browserbase session %s: %s", session_id, exc)
            return False

    def emergency_cleanup(self, session_id: str) -> None:
        config = self._get_config_or_none()
        if config is None:
            logger.warning(
                "Cannot emergency-cleanup Browserbase session %s — missing credentials",
                session_id,
            )
            return
        try:
            requests.post(
                f"{config['base_url']}/v1/sessions/{session_id}",
                headers={
                    "X-BB-API-Key": config["api_key"],
                    "Content-Type": "application/json",
                },
                json={"projectId": config["project_id"], "status": "REQUEST_RELEASE"},
                timeout=5,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.debug(
                "Emergency cleanup failed for Browserbase session %s: %s", session_id, exc
            )


def register(ctx) -> None:
    """Register the Browserbase cloud-browser provider with the plugin context."""
    ctx.register_browser_provider(BrowserbaseProvider())
