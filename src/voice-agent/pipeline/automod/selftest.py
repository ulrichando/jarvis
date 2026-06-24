"""Synthetic smoke-turn — the night-time "one turn" health signal.

The deploy watchdog's gate is "liveness + one successful turn". Self-evolution
deploys land at ~3am when there are no real user turns, so we manufacture one:
build the supervisor LLM from the FRESHLY-DEPLOYED code and run a single
completion. If a bad self-edit broke the brain (import error, broken provider
wiring, a model the runtime can't reach), this fails and the watchdog rolls back.

Run as a subprocess so a hung LLM call can't wedge the watchdog tick:

    .venv/bin/python -m pipeline.automod.selftest        # exit 0 ok / 1 fail / 2 build-error

`run()` is also importable for tests. It is deliberately tolerant of TRANSIENT
failures (the watchdog retries it across the whole window) — only a persistent
failure rolls a deploy back.
"""
from __future__ import annotations

import asyncio
import sys
from typing import Tuple

# How long one completion may take before we call it a failed attempt. The
# watchdog retries across its window, so this is per-attempt, not total.
SELFTEST_TIMEOUT_S = 30


def _load_keys() -> None:
    """Load the voice-agent env stack for a bare watchdog subprocess.

    The real service gets EnvironmentFile entries before jarvis_agent imports.
    The watchdog's unit intentionally does not, so the smoke turn has to recreate
    that stack itself or it can test a different provider/model setup than the
    deployed agent. Values already supplied by systemd or the caller still win;
    otherwise later files in this local load override earlier files, matching
    systemd's EnvironmentFile order.
    """
    import os
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[4]
    original_env = set(os.environ)
    for src in (
        repo_root / "src" / "voice-agent" / ".env",
        repo_root / ".env",
        repo_root / "src" / "cli" / ".env.local",
        Path.home() / ".jarvis" / "local-api-token.env",
        Path.home() / ".jarvis" / "keys.env",
    ):
        try:
            if not src.exists():
                continue
            for line in src.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                # Don't clobber an already-set external value (systemd/caller
                # wins), but do let later files override earlier files loaded
                # by this helper.
                if k and v and k not in original_env:
                    os.environ[k] = v
        except Exception:  # noqa: BLE001 - best effort
            continue


def _build_chat_ctx():
    try:
        from livekit.agents.llm import ChatContext, ChatMessage
    except Exception:  # pragma: no cover - older/newer layout
        from livekit.agents import llm as _lk
        ChatContext, ChatMessage = _lk.ChatContext, _lk.ChatMessage
    return ChatContext(
        items=[
            ChatMessage(
                id="evolution-selftest-u1",
                role="user",
                content=["Reply with exactly one word: OK"],
            )
        ]
    )


async def _one_completion() -> Tuple[bool, str]:
    # Importing + building exercises the deployed code's brain. A bad edit that
    # somehow passed pytest but breaks at runtime dies here. We build a CONCRETE
    # speech model (make_speech_llm → a livekit LLM that has .chat) rather than
    # the DispatchingLLM, which is driven through the Agent, not a direct chat().
    from providers.llm import make_speech_llm

    _name, llm = make_speech_llm()
    ctx = _build_chat_ctx()

    text = ""
    stream = llm.chat(chat_ctx=ctx)
    try:
        async for chunk in stream:
            delta = getattr(chunk, "delta", None)
            piece = getattr(delta, "content", None) if delta is not None else None
            if piece:
                text += piece
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose:
            try:
                await aclose()
            except Exception:  # noqa: BLE001
                pass

    if text.strip():
        return True, f"completion ok ({len(text)} chars)"
    return False, "empty completion"


def run() -> Tuple[bool, str]:
    """One smoke-turn attempt. Returns (ok, detail).

    Distinguishes a BUILD failure (deployed code is broken — the rollback-worthy
    case) from a transient completion failure (the watchdog will retry)."""
    _load_keys()
    try:
        return asyncio.run(asyncio.wait_for(_one_completion(), SELFTEST_TIMEOUT_S))
    except asyncio.TimeoutError:
        return False, f"timed out after {SELFTEST_TIMEOUT_S}s"
    except (ImportError, SyntaxError, AttributeError, NameError, TypeError) as e:
        # Strong signal that a self-edit broke the deployed code itself.
        return False, f"BUILD-ERROR {type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001 - anything else = a failed attempt
        return False, f"{type(e).__name__}: {e}"


def main() -> int:
    ok, detail = run()
    print(("ok: " if ok else "fail: ") + detail)
    if ok:
        return 0
    return 2 if detail.startswith("BUILD-ERROR") else 1


if __name__ == "__main__":
    sys.exit(main())
