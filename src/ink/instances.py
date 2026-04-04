"""Store all Ink instances to ensure consecutive render() calls reuse the same instance.

This map is stored in a separate file because render creates instances,
but the instance should delete itself from the map on unmount.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

instances: dict[io.IOBase, Any] = {}
