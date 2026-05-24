"""Firecrawl cloud-browser backend (registry kind ``browser``).

Registers a remote cloud-browser provider that the local ``browser_task`` tool
can optionally drive over CDP. Opt-in only: consumed solely when the operator
sets ``JARVIS_BROWSER_PROVIDER=firecrawl`` AND ``FIRECRAWL_API_KEY`` is set.
With the env unset, ``browser_task`` uses its local subprocess path unchanged.

This is the cloud-BROWSER path — distinct from the firecrawl WEB plugin at
``plugins/web/firecrawl/`` which handles search/extract/crawl on
``/v2/search`` / ``/v2/scrape`` / ``/v2/crawl``. The two plugins share the
``FIRECRAWL_API_KEY`` env var but hit different endpoints: this one POSTs
``/v2/browser`` which returns a real CDP-addressable session (``cdpUrl``,
normalized here to the JARVIS-native ``cdp_url`` key). So Firecrawl genuinely
hosts a CDP session here — it is not scrape-only in this role.

Auth env vars::

    FIRECRAWL_API_KEY=...           # https://firecrawl.dev
    FIRECRAWL_API_URL=...           # optional override (default https://api.firecrawl.dev)
    FIRECRAWL_BROWSER_TTL=...       # optional, default 300 seconds

Ported from the upstream firecrawl browser plugin; the ``agent.*`` base +
``config.yaml`` coupling was stripped. The provider class is inline (no
separate ``provider.py``) so it loads cleanly under the plugin namespace and
via ``spec_from_file_location`` in tests.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict

import requests

from tools.browser_providers import BrowserProvider

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.firecrawl.dev"


class FirecrawlBrowserProvider(BrowserProvider):
    """Firecrawl (https://firecrawl.dev) cloud browser backend.

    Cloud-browser path only — search/extract/crawl live in the separate
    ``plugins/web/firecrawl/`` plugin. ``create_session`` POSTs ``/v2/browser``
    and returns a real ``cdp_url`` the ``browser_task`` runner attaches to.
    """

    name = "firecrawl"

    @property
    def display_name(self) -> str:
        return "Firecrawl"

    def is_available(self) -> bool:
        return bool(os.environ.get("FIRECRAWL_API_KEY"))

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def _api_url(self) -> str:
        return os.environ.get("FIRECRAWL_API_URL", _BASE_URL)

    def _headers(self) -> Dict[str, str]:
        api_key = os.environ.get("FIRECRAWL_API_KEY")
        if not api_key:
            raise ValueError(
                "FIRECRAWL_API_KEY environment variable is required. "
                "Get your key at https://firecrawl.dev"
            )
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

    def create_session(self, task_id: str) -> Dict[str, Any]:
        ttl = int(os.environ.get("FIRECRAWL_BROWSER_TTL", "300"))
        body: Dict[str, object] = {"ttl": ttl}

        try:
            response = requests.post(
                f"{self._api_url()}/v2/browser",
                headers=self._headers(),
                json=body,
                timeout=30,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Firecrawl API connection failed: {exc}") from exc

        if not response.ok:
            raise RuntimeError(
                f"Failed to create Firecrawl browser session: "
                f"{response.status_code} {response.text}"
            )

        data = response.json()
        session_name = f"jarvis_{task_id}_{uuid.uuid4().hex[:8]}"
        logger.info("Created Firecrawl browser session %s", session_name)

        # Normalized JARVIS-native session shape: cdp_url + session_id.
        return {
            "cdp_url": data["cdpUrl"],
            "session_id": data["id"],
            "session_name": session_name,
            "features": {"firecrawl": True},
        }

    def close_session(self, session_id: str) -> bool:
        try:
            response = requests.delete(
                f"{self._api_url()}/v2/browser/{session_id}",
                headers=self._headers(),
                timeout=10,
            )
            if response.status_code in {200, 201, 204}:
                logger.debug("Closed Firecrawl session %s", session_id)
                return True
            logger.warning(
                "Failed to close Firecrawl session %s: HTTP %s - %s",
                session_id,
                response.status_code,
                response.text[:200],
            )
            return False
        except Exception as exc:  # noqa: BLE001 — cleanup must not raise
            logger.error("Exception closing Firecrawl session %s: %s", session_id, exc)
            return False

    def emergency_cleanup(self, session_id: str) -> None:
        if not self.is_available():
            logger.warning(
                "Cannot emergency-cleanup Firecrawl session %s — missing credentials",
                session_id,
            )
            return
        try:
            requests.delete(
                f"{self._api_url()}/v2/browser/{session_id}",
                headers=self._headers(),
                timeout=5,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.debug(
                "Emergency cleanup failed for Firecrawl session %s: %s", session_id, exc
            )


def register(ctx) -> None:
    """Register the Firecrawl cloud-browser provider with the plugin context."""
    ctx.register_browser_provider(FirecrawlBrowserProvider())
