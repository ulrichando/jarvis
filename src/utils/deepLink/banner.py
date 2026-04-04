"""Deep Link Origin Banner."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

STALE_FETCH_WARN_MS = 7 * 24 * 60 * 60 * 1000
LONG_PREFILL_THRESHOLD = 1000


@dataclass
class DeepLinkBannerInfo:
    cwd: str
    prefill_length: Optional[int] = None
    repo: Optional[str] = None
    last_fetch: Optional[datetime] = None


def _tildify(p: str) -> str:
    """Shorten home-dir-prefixed paths to ~ notation."""
    home = str(Path.home())
    if p == home:
        return "~"
    if p.startswith(home + os.sep):
        return "~" + p[len(home):]
    return p


def _format_relative_time(dt: datetime) -> str:
    """Format a datetime as relative time ago."""
    delta = time.time() - dt.timestamp()
    if delta < 60:
        return "just now"
    if delta < 3600:
        m = int(delta / 60)
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if delta < 86400:
        h = int(delta / 3600)
        return f"{h} hour{'s' if h != 1 else ''} ago"
    d = int(delta / 86400)
    return f"{d} day{'s' if d != 1 else ''} ago"


def build_deep_link_banner(info: DeepLinkBannerInfo) -> str:
    """Build the multi-line warning banner for a deep-link-originated session."""
    lines = [
        f"This session was opened by an external deep link in {_tildify(info.cwd)}"
    ]

    if info.repo:
        age = _format_relative_time(info.last_fetch) if info.last_fetch else "never"
        stale = (
            not info.last_fetch
            or (time.time() - info.last_fetch.timestamp()) * 1000 > STALE_FETCH_WARN_MS
        )
        stale_msg = " -- CLAUDE.md may be stale" if stale else ""
        lines.append(
            f"Resolved {info.repo} from local clones -- last fetched {age}{stale_msg}"
        )

    if info.prefill_length:
        if info.prefill_length > LONG_PREFILL_THRESHOLD:
            lines.append(
                f"The prompt below ({info.prefill_length} chars) was supplied by the link "
                "-- scroll to review the entire prompt before pressing Enter."
            )
        else:
            lines.append(
                "The prompt below was supplied by the link "
                "-- review carefully before pressing Enter."
            )

    return "\n".join(lines)


async def read_last_fetch_time(cwd: str) -> Optional[datetime]:
    """Read the mtime of .git/FETCH_HEAD."""
    git_dir = os.path.join(cwd, ".git")
    fetch_head = os.path.join(git_dir, "FETCH_HEAD")
    try:
        mtime = os.path.getmtime(fetch_head)
        return datetime.fromtimestamp(mtime)
    except OSError:
        return None
