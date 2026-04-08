"""JARVIS agent route resolver.

Resolves which agent should handle a given request using a priority-tiered
matching system inspired by OpenClaw's src/routing/resolve-route.ts.

Tiers (highest priority first):
  1. direct    — exact agent_id match in the request
  2. channel   — agent bound to the inbound channel (e.g. "telegram")
  3. skill     — request matches a skill's trigger pattern
  4. tag       — agent tagged for this type of work (e.g. "code", "research")
  5. user      — agent assigned to a specific user_id
  6. session   — agent bound to this session_id
  7. default   — the configured default agent for the deployment

Usage:
    resolver = get_resolver()

    # Register routes
    resolver.add_route(Route(tier="tag", match="code", agent_id="worker"))
    resolver.add_route(Route(tier="default", match="*", agent_id="brain"))

    # Resolve
    agent_id = resolver.resolve(
        user_id="ulrich",
        channel_id="web",
        tags=["code"],
        skill="refactor",
    )
    # → "worker"
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("jarvis.routing")

# ── Tier ordering (lower index = higher priority) ─────────────────────────────
_TIER_ORDER = ["direct", "channel", "skill", "tag", "user", "session", "default"]


@dataclass
class Route:
    """A single routing rule."""
    tier: str          # one of _TIER_ORDER
    match: str         # value to match against (exact or glob pattern; "*" = catch-all)
    agent_id: str      # which agent to invoke
    meta: dict[str, Any] = field(default_factory=dict)

    def matches(self, value: str) -> bool:
        if self.match in ("*", ""):
            return True
        try:
            return bool(re.fullmatch(self.match.replace("*", ".*"), value or ""))
        except re.error:
            return self.match == value


class RouteResolver:
    """Resolve agent routes from a priority-ordered rule set."""

    def __init__(self, default_agent: str = "brain") -> None:
        self._routes: list[Route] = []
        self._default_agent = default_agent

    # ── Registration ──────────────────────────────────────────────────

    def add_route(self, route: Route) -> None:
        """Add a routing rule.  Rules are evaluated in tier priority order."""
        if route.tier not in _TIER_ORDER:
            raise ValueError(f"Unknown tier {route.tier!r}. Valid: {_TIER_ORDER}")
        self._routes.append(route)
        # Keep sorted by tier priority
        self._routes.sort(key=lambda r: _TIER_ORDER.index(r.tier))
        log.debug("Route added: tier=%s match=%r → %s", route.tier, route.match, route.agent_id)

    def remove_routes(self, agent_id: str) -> int:
        """Remove all routes pointing to *agent_id*.  Returns count removed."""
        before = len(self._routes)
        self._routes = [r for r in self._routes if r.agent_id != agent_id]
        return before - len(self._routes)

    def list_routes(self) -> list[dict]:
        return [
            {"tier": r.tier, "match": r.match, "agent_id": r.agent_id}
            for r in self._routes
        ]

    # ── Resolution ────────────────────────────────────────────────────

    def resolve(
        self,
        *,
        direct: str = "",       # explicit agent_id override from request
        channel_id: str = "",   # inbound channel (e.g. "web", "telegram")
        skill: str = "",        # skill name being invoked
        tags: list[str] | None = None,  # content tags (e.g. ["code", "search"])
        user_id: str = "",      # authenticated user id
        session_id: str = "",   # conversation session id
    ) -> str:
        """Return the agent_id that should handle this request.

        Falls back to the configured default if no rule matches.
        """
        check_values: dict[str, list[str]] = {
            "direct":  [direct] if direct else [],
            "channel": [channel_id] if channel_id else [],
            "skill":   [skill] if skill else [],
            "tag":     list(tags or []),
            "user":    [user_id] if user_id else [],
            "session": [session_id] if session_id else [],
            "default": ["*"],
        }

        for route in self._routes:
            candidates = check_values.get(route.tier, [])
            for val in candidates:
                if route.matches(val):
                    log.debug(
                        "Resolved tier=%s match=%r val=%r → %s",
                        route.tier, route.match, val, route.agent_id,
                    )
                    return route.agent_id

        log.debug("No route matched — using default: %s", self._default_agent)
        return self._default_agent

    def set_default(self, agent_id: str) -> None:
        self._default_agent = agent_id


# ── Singleton ─────────────────────────────────────────────────────────────────

_resolver: RouteResolver | None = None


def get_resolver(default_agent: str = "brain") -> RouteResolver:
    """Return the global RouteResolver singleton."""
    global _resolver
    if _resolver is None:
        _resolver = RouteResolver(default_agent=default_agent)
        # Wire sensible built-in defaults
        _resolver.add_route(Route(tier="tag",  match="code",     agent_id="worker"))
        _resolver.add_route(Route(tier="tag",  match="research", agent_id="scout"))
        _resolver.add_route(Route(tier="tag",  match="plan",     agent_id="planner"))
        _resolver.add_route(Route(tier="default", match="*",     agent_id=default_agent))
    return _resolver
