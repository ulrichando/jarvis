"""API key verification state management."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class VerificationStatus(Enum):
    LOADING = "loading"
    VALID = "valid"
    INVALID = "invalid"
    MISSING = "missing"
    ERROR = "error"


@dataclass
class ApiKeyVerificationResult:
    status: VerificationStatus = VerificationStatus.LOADING
    error: Optional[Exception] = None
    _reverify_fn: Optional[Callable] = field(default=None, repr=False)

    async def reverify(self) -> None:
        if self._reverify_fn:
            await self._reverify_fn()


class ApiKeyVerifier:
    """Manages API key verification state.

    Equivalent to useApiKeyVerification React hook.
    """

    def __init__(
        self,
        is_auth_enabled: Callable[[], bool],
        is_subscriber: Callable[[], bool],
        get_api_key_with_source: Callable,
        get_api_key_from_helper: Callable,
        verify_api_key: Callable,
        is_non_interactive: Callable[[], bool],
    ):
        self._is_auth_enabled = is_auth_enabled
        self._is_subscriber = is_subscriber
        self._get_api_key_with_source = get_api_key_with_source
        self._get_api_key_from_helper = get_api_key_from_helper
        self._verify_api_key = verify_api_key
        self._is_non_interactive = is_non_interactive

        self.status = self._compute_initial_status()
        self.error: Optional[Exception] = None

    def _compute_initial_status(self) -> VerificationStatus:
        if not self._is_auth_enabled() or self._is_subscriber():
            return VerificationStatus.VALID

        key_info = self._get_api_key_with_source(
            skip_retrieving_key_from_helper=True
        )
        key = key_info.get("key")
        source = key_info.get("source")

        if key or source == "apiKeyHelper":
            return VerificationStatus.LOADING
        return VerificationStatus.MISSING

    async def verify(self) -> None:
        """Re-verify the API key."""
        if not self._is_auth_enabled() or self._is_subscriber():
            self.status = VerificationStatus.VALID
            return

        await self._get_api_key_from_helper(self._is_non_interactive())
        key_info = self._get_api_key_with_source()
        api_key = key_info.get("key")
        source = key_info.get("source")

        if not api_key:
            if source == "apiKeyHelper":
                self.status = VerificationStatus.ERROR
                self.error = Exception("API key helper did not return a valid key")
                return
            self.status = VerificationStatus.MISSING
            return

        try:
            is_valid = await self._verify_api_key(api_key, False)
            self.status = (
                VerificationStatus.VALID if is_valid else VerificationStatus.INVALID
            )
        except Exception as e:
            self.error = e
            self.status = VerificationStatus.ERROR

    def get_result(self) -> ApiKeyVerificationResult:
        return ApiKeyVerificationResult(
            status=self.status,
            error=self.error,
            _reverify_fn=self.verify,
        )
