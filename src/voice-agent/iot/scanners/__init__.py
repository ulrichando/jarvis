"""Scanner protocol — every scanner is async and returns Observations."""
from __future__ import annotations

from typing import Protocol

from iot.models import Observation


class Scanner(Protocol):
    name: str

    async def scan(self, timeout: float) -> list[Observation]: ...
