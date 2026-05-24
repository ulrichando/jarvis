"""``python -m acp_adapter`` entry point.

Loads ``src/voice-agent/.env`` (so the supervisor LLM picks up keys)
and routes logging to stderr so the stdio JSON-RPC channel stays clean.
Then constructs the agent and hands stdin/stdout to ``acp.run_agent``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path


# Liveness-probe methods clients send periodically. They aren't in the
# ACP schema, so the router returns -32601; the supervisor task that
# dispatched the call surfaces the RequestError as a traceback in stderr
# every interval. Suppress just that one line — keep every other error
# visible, including method_not_found for non-probe methods.
_BENIGN_PROBE_METHODS = frozenset({"ping", "health", "healthcheck"})


class _BenignProbeMethodFilter(logging.Filter):
    """Drop ``Background task failed`` for harmless unknown-method probes."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.getMessage() != "Background task failed":
            return True
        exc_info = record.exc_info
        if not exc_info:
            return True
        exc = exc_info[1]
        try:
            from acp.exceptions import RequestError
        except ImportError:
            return True
        if not isinstance(exc, RequestError):
            return True
        if getattr(exc, "code", None) != -32601:
            return True
        data = getattr(exc, "data", None)
        method = data.get("method") if isinstance(data, dict) else None
        return method not in _BENIGN_PROBE_METHODS


def _setup_logging() -> None:
    """Route every logger to stderr so stdout stays JSON-RPC only."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler.addFilter(_BenignProbeMethodFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    for noisy in ("httpx", "httpcore", "openai", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _load_env() -> None:
    """Read ``src/voice-agent/.env`` if present so provider keys land."""
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        return
    # acp_adapter/entry.py -> acp_adapter/ -> src/voice-agent/
    candidates = [
        Path(__file__).resolve().parent.parent / ".env",
        Path.home() / ".jarvis" / ".env",
    ]
    for env_path in candidates:
        if env_path.exists():
            load_dotenv(env_path, override=False)
            logging.getLogger(__name__).info("Loaded env from %s", env_path)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="jarvis-acp",
        description="Run JARVIS as an ACP stdio agent for IDEs (Zed, Cursor, ...).",
    )
    parser.add_argument(
        "--version", action="store_true",
        help="Print the JARVIS ACP adapter version and exit.",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Verify ACP + supervisor imports succeed, then exit.",
    )
    return parser.parse_args(argv)


def _print_version() -> None:
    from .server import _JARVIS_VERSION

    print(f"jarvis-acp {_JARVIS_VERSION}")


def _run_check() -> None:
    import acp  # noqa: F401
    from .server import JarvisACPAgent  # noqa: F401

    print("jarvis-acp check OK")


def main(argv: list[str] | None = None) -> None:
    """Entry point: parse args, load env, run the ACP stdio agent."""
    args = _parse_args(argv)
    if args.version:
        _print_version()
        return
    if args.check:
        _setup_logging()
        _run_check()
        return

    _setup_logging()
    _load_env()

    # Ensure src/voice-agent is on sys.path so the registry / providers /
    # tools imports resolve when this is invoked from anywhere.
    voice_agent_root = str(Path(__file__).resolve().parent.parent)
    if voice_agent_root not in sys.path:
        sys.path.insert(0, voice_agent_root)

    logger = logging.getLogger(__name__)
    logger.info("Starting JARVIS ACP adapter")

    import acp
    from .server import JarvisACPAgent

    agent = JarvisACPAgent()
    try:
        asyncio.run(acp.run_agent(agent, use_unstable_protocol=True))
    except KeyboardInterrupt:
        logger.info("Shutting down (KeyboardInterrupt)")
    except Exception:
        logger.exception("ACP agent crashed")
        sys.exit(1)


if __name__ == "__main__":
    main()
