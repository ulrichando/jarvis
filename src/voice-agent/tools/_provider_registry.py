"""Generic provider registry for JARVIS pluggable tool backends.

A tiny, JARVIS-native abstraction ported from the upstream image-gen registry +
provider ABC. Generalized one level up: instead of an image-gen-only map, this
keeps a per-*kind* map of named providers, so future capabilities (vision, web
search, video) can reuse the exact same machinery without a second copy.

Design
------
* A *kind* is a capability namespace — ``"image"`` today; ``"vision"`` /
  ``"web"`` / ``"video"`` later. Each kind owns an independent name→provider map.
* A *provider* is any object exposing two duck-typed members:
    - ``name`` — stable lowercase identifier (str), unique within its kind.
    - ``is_available()`` — bool; True when the backend can service calls
      (typically: required API key present + SDK importable).
  Capability-specific call methods (e.g. an image provider's ``generate(...)``)
  live on the provider itself — this registry never calls them, it only stores,
  lists, and resolves providers. That keeps the shim capability-agnostic.

Resolution
----------
``get_provider(kind, name=None)``:
  * ``name`` given → that provider (or None) — caller decides how to handle an
    unavailable-but-registered provider.
  * ``name`` omitted → the first *available* provider for the kind (sorted by
    name for determinism), else None.

How a new capability plugs in later
-----------------------------------
1. Write a provider class with ``name`` + ``is_available()`` + whatever call
   method that capability needs (``describe()``, ``search()``, ...).
2. At import time call ``register_provider("vision", inst.name, inst)``.
3. The consuming tool resolves a backend with
   ``get_provider("vision")`` (or by explicit name) and invokes the
   capability method directly. No change to this module is required.

Stripped from the upstream port: all ``agent.*`` / gateway / ``config.yaml``
coupling and the FAL-legacy fallback preference. Availability is env-key driven
via each provider's own ``is_available()``. Stdlib-only and import-safe at
module scope so tool modules can import it during the registry walk.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "register_provider",
    "get_provider",
    "list_providers",
    "available_providers",
    "has_available_provider",
    "deregister_provider",
    "reset_providers",
]


# kind -> {name -> provider}
_providers: Dict[str, Dict[str, Any]] = {}
_lock = threading.RLock()


def _is_available_safe(provider: Any) -> bool:
    """``bool(provider.is_available())`` with exceptions swallowed as False.

    A buggy provider must never break resolution for the rest of the kind.
    Providers without an ``is_available`` attribute are treated as always
    available (parity with the upstream ABC default).
    """
    probe = getattr(provider, "is_available", None)
    if probe is None:
        return True
    try:
        return bool(probe())
    except Exception as exc:  # noqa: BLE001 — a provider probe must not raise out
        logger.debug("provider %r is_available() raised: %s", getattr(provider, "name", "?"), exc)
        return False


def register_provider(kind: str, name: str, provider: Any) -> None:
    """Register *provider* under (``kind``, ``name``).

    Re-registration of the same (kind, name) overwrites the previous entry and
    logs at debug — predictable for hot-reload (tests, dev loops). The
    ``name`` argument is authoritative; if the provider also carries a ``name``
    attribute it is not consulted here.

    Raises ``ValueError`` for an empty ``kind`` or ``name`` so a malformed
    self-registration fails loudly at import rather than silently vanishing.
    """
    if not isinstance(kind, str) or not kind.strip():
        raise ValueError("register_provider() requires a non-empty kind")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("register_provider() requires a non-empty name")
    if provider is None:
        raise ValueError("register_provider() requires a provider instance")
    kind = kind.strip()
    name = name.strip()
    with _lock:
        bucket = _providers.setdefault(kind, {})
        existing = bucket.get(name)
        bucket[name] = provider
    if existing is not None:
        logger.debug("provider %r/%r re-registered (was %r)", kind, name, type(existing).__name__)
    else:
        logger.debug("registered provider %r/%r (%s)", kind, name, type(provider).__name__)


def deregister_provider(kind: str, name: str) -> None:
    """Remove a single provider (no-op if absent)."""
    with _lock:
        bucket = _providers.get(kind)
        if bucket is not None:
            bucket.pop(name, None)


def list_providers(kind: str) -> List[Any]:
    """Return every provider registered for *kind*, sorted by ``name``.

    Includes currently-unavailable providers — callers that only want usable
    backends should use :func:`available_providers`.
    """
    with _lock:
        items = list(_providers.get(kind, {}).values())
    return sorted(items, key=lambda p: str(getattr(p, "name", "")))


def available_providers(kind: str) -> List[Any]:
    """Return providers for *kind* whose ``is_available()`` is True, name-sorted."""
    return [p for p in list_providers(kind) if _is_available_safe(p)]


def has_available_provider(kind: str) -> bool:
    """True when at least one provider for *kind* is currently available.

    Cheap and exception-safe — suitable for a tool's ``check_fn`` gate.
    """
    with _lock:
        providers = list(_providers.get(kind, {}).values())
    return any(_is_available_safe(p) for p in providers)


def get_provider(kind: str, name: Optional[str] = None) -> Optional[Any]:
    """Resolve a provider for *kind*.

    * ``name`` provided → the provider registered under that name, or None.
      Returned regardless of availability so the caller can surface a precise
      "X_API_KEY not set" error rather than silently switching backends.
    * ``name`` omitted → the first *available* provider (name-sorted), or None
      when none is available.
    """
    if name is not None:
        with _lock:
            return _providers.get(kind, {}).get(name.strip() if isinstance(name, str) else name)
    avail = available_providers(kind)
    return avail[0] if avail else None


def reset_providers(kind: Optional[str] = None) -> None:
    """Clear registered providers. **Test-only helper.**

    ``kind`` given clears just that kind; omitted clears everything.
    """
    with _lock:
        if kind is None:
            _providers.clear()
        else:
            _providers.pop(kind, None)
