"""Deep Link URI Parser.

Parses jarvis-cli://open URIs with optional q, cwd, repo params.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlparse

DEEP_LINK_PROTOCOL = "jarvis-cli"
MAX_QUERY_LENGTH = 5000
MAX_CWD_LENGTH = 4096
REPO_SLUG_PATTERN = re.compile(r"^[\w.\-]+/[\w.\-]+$")


@dataclass
class DeepLinkAction:
    query: Optional[str] = None
    cwd: Optional[str] = None
    repo: Optional[str] = None


def _contains_control_chars(s: str) -> bool:
    """Check if string contains ASCII control characters."""
    for ch in s:
        code = ord(ch)
        if code <= 0x1F or code == 0x7F:
            return True
    return False


def parse_deep_link(uri: str) -> DeepLinkAction:
    """Parse a jarvis-cli:// URI into a structured action.

    Raises ValueError if the URI is malformed or contains dangerous characters.
    """
    if uri.startswith(f"{DEEP_LINK_PROTOCOL}://"):
        normalized = uri
    elif uri.startswith(f"{DEEP_LINK_PROTOCOL}:"):
        normalized = uri.replace(
            f"{DEEP_LINK_PROTOCOL}:", f"{DEEP_LINK_PROTOCOL}://", 1
        )
    else:
        raise ValueError(
            f"Invalid deep link: expected {DEEP_LINK_PROTOCOL}:// scheme, got \"{uri}\""
        )

    parsed = urlparse(normalized)
    if parsed.hostname != "open":
        raise ValueError(f'Unknown deep link action: "{parsed.hostname}"')

    params = parse_qs(parsed.query)
    cwd = params.get("cwd", [None])[0]
    repo = params.get("repo", [None])[0]
    raw_query = params.get("q", [None])[0]

    if cwd:
        if not cwd.startswith("/") and not re.match(r"^[a-zA-Z]:[/\\]", cwd):
            raise ValueError(
                f'Invalid cwd in deep link: must be an absolute path, got "{cwd}"'
            )
        if _contains_control_chars(cwd):
            raise ValueError("Deep link cwd contains disallowed control characters")
        if len(cwd) > MAX_CWD_LENGTH:
            raise ValueError(
                f"Deep link cwd exceeds {MAX_CWD_LENGTH} characters (got {len(cwd)})"
            )

    if repo and not REPO_SLUG_PATTERN.match(repo):
        raise ValueError(
            f'Invalid repo in deep link: expected "owner/repo", got "{repo}"'
        )

    query: Optional[str] = None
    if raw_query and raw_query.strip():
        query = raw_query.strip()
        if _contains_control_chars(query):
            raise ValueError("Deep link query contains disallowed control characters")
        if len(query) > MAX_QUERY_LENGTH:
            raise ValueError(
                f"Deep link query exceeds {MAX_QUERY_LENGTH} characters (got {len(query)})"
            )

    return DeepLinkAction(query=query, cwd=cwd, repo=repo)


def build_deep_link(action: DeepLinkAction) -> str:
    """Build a jarvis-cli:// deep link URL."""
    from urllib.parse import urlencode

    params: dict[str, str] = {}
    if action.query:
        params["q"] = action.query
    if action.cwd:
        params["cwd"] = action.cwd
    if action.repo:
        params["repo"] = action.repo

    base = f"{DEEP_LINK_PROTOCOL}://open"
    if params:
        return f"{base}?{urlencode(params)}"
    return base
