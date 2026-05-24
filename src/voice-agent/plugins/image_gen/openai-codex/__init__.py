"""OpenAI image generation backend — ChatGPT/Codex OAuth variant (kind ``image``).

Identical model catalog and tier semantics to the built-in ``openai`` image
backend (``gpt-image-2`` at low/medium/high quality), but routes the request
through the Codex Responses API ``image_generation`` tool instead of the
``images.generate`` REST endpoint. This lets users already authenticated with
ChatGPT/Codex generate images without configuring a separate ``OPENAI_API_KEY``.

Tier selection: ``OPENAI_IMAGE_MODEL`` env var (if it names a known tier) else
``DEFAULT_MODEL`` (gpt-image-2-medium).

Token resolution (JARVIS-native — replaces the upstream credential-pool reader):
  1. ``CODEX_ACCESS_TOKEN`` env var (explicit override)
  2. the Codex CLI auth file — ``~/.codex/auth.json`` (override via
     ``CODEX_AUTH_FILE``), shape ``{"tokens": {"access_token": "..."}}``, with a
     JWT-expiry skip.

Inert (filtered from the supervisor surface) when no token is resolvable or the
``openai`` SDK is missing. Ported from the upstream openai-codex image plugin;
``agent.*`` / ``config.yaml`` coupling stripped. Images save under
``<jarvis-home>/generated/`` via :mod:`tools.image_gen`'s helpers.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from tools.image_gen import (
    DEFAULT_ASPECT_RATIO,
    error_response,
    resolve_aspect_ratio,
    save_b64_image,
    success_response,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model catalog — mirrors the built-in ``openai`` image backend.
# ---------------------------------------------------------------------------

API_MODEL = "gpt-image-2"

_MODELS: Dict[str, Dict[str, Any]] = {
    "gpt-image-2-low": {
        "display": "GPT Image 2 (Low)",
        "speed": "~15s",
        "strengths": "Fast iteration, lowest cost",
        "quality": "low",
    },
    "gpt-image-2-medium": {
        "display": "GPT Image 2 (Medium)",
        "speed": "~40s",
        "strengths": "Balanced — default",
        "quality": "medium",
    },
    "gpt-image-2-high": {
        "display": "GPT Image 2 (High)",
        "speed": "~2min",
        "strengths": "Highest fidelity, strongest prompt adherence",
        "quality": "high",
    },
}

DEFAULT_MODEL = "gpt-image-2-medium"

_SIZES = {
    "landscape": "1536x1024",
    "square": "1024x1024",
    "portrait": "1024x1536",
}

# Codex Responses surface. The chat model is only the host that calls the
# ``image_generation`` tool; the actual image work is done by ``API_MODEL``.
_CODEX_CHAT_MODEL = "gpt-5.4"
_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
_CODEX_INSTRUCTIONS = (
    "You are an assistant that must fulfill image generation requests by "
    "using the image_generation tool when provided."
)


def _resolve_model() -> Tuple[str, Dict[str, Any]]:
    """Decide which tier to use and return ``(model_id, meta)`` (env or default)."""
    env_override = os.environ.get("OPENAI_IMAGE_MODEL")
    if env_override and env_override in _MODELS:
        return env_override, _MODELS[env_override]
    return DEFAULT_MODEL, _MODELS[DEFAULT_MODEL]


def _read_codex_access_token() -> Optional[str]:
    """Return a usable Codex OAuth access token, or None.

    JARVIS-native: ``CODEX_ACCESS_TOKEN`` env var → the Codex CLI auth file
    (``~/.codex/auth.json``, override via ``CODEX_AUTH_FILE``), with a JWT-expiry
    skip for the file token.
    """
    direct = os.environ.get("CODEX_ACCESS_TOKEN", "").strip()
    if direct:
        return direct

    path = os.environ.get("CODEX_AUTH_FILE", "").strip() or os.path.expanduser("~/.codex/auth.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:  # noqa: BLE001 — no file / bad JSON → no token
        return None

    if not isinstance(data, dict):
        return None
    tokens = data.get("tokens")
    access_token = tokens.get("access_token") if isinstance(tokens, dict) else data.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        return None

    # JWT expiry check — skip expired tokens.
    try:
        import base64
        import time

        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        exp = claims.get("exp", 0)
        if exp and time.time() > exp:
            logger.debug("Codex access token expired (exp=%s), skipping", exp)
            return None
    except Exception:  # noqa: BLE001 — non-JWT / decode error → use as-is
        pass
    return access_token.strip()


def _codex_cloudflare_headers(access_token: str) -> Dict[str, str]:
    """Headers required to avoid Cloudflare 403s on the Codex backend endpoint.

    The Cloudflare layer whitelists a small set of first-party originators. We
    pin ``originator: codex_cli_rs`` and a codex_cli_rs-shaped User-Agent, and
    extract ``ChatGPT-Account-ID`` from the OAuth JWT's ``chatgpt_account_id``
    claim. Malformed tokens drop the account-ID header rather than raise.
    """
    headers = {
        "User-Agent": "codex_cli_rs/0.0.0 (JARVIS Agent)",
        "originator": "codex_cli_rs",
    }
    if not isinstance(access_token, str) or not access_token.strip():
        return headers
    try:
        import base64

        parts = access_token.split(".")
        if len(parts) < 2:
            return headers
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
        acct_id = claims.get("https://api.openai.com/auth", {}).get("chatgpt_account_id")
        if isinstance(acct_id, str) and acct_id:
            headers["ChatGPT-Account-ID"] = acct_id
    except Exception:  # noqa: BLE001
        pass
    return headers


def _build_codex_client():
    """Return an OpenAI client pointed at the ChatGPT/Codex backend, or None."""
    token = _read_codex_access_token()
    if not token:
        return None
    try:
        import openai

        return openai.OpenAI(
            api_key=token,
            base_url=_CODEX_BASE_URL,
            default_headers=_codex_cloudflare_headers(token),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not build Codex image client: %s", exc)
        return None


def _collect_image_b64(client: Any, *, prompt: str, size: str, quality: str) -> Optional[str]:
    """Stream a Codex Responses image_generation call and return the b64 image."""
    image_b64: Optional[str] = None

    with client.responses.stream(
        model=_CODEX_CHAT_MODEL,
        store=False,
        instructions=_CODEX_INSTRUCTIONS,
        input=[{
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": prompt}],
        }],
        tools=[{
            "type": "image_generation",
            "model": API_MODEL,
            "size": size,
            "quality": quality,
            "output_format": "png",
            "background": "opaque",
            "partial_images": 1,
        }],
        tool_choice={
            "type": "allowed_tools",
            "mode": "required",
            "tools": [{"type": "image_generation"}],
        },
    ) as stream:
        for event in stream:
            event_type = getattr(event, "type", "")
            if event_type == "response.output_item.done":
                item = getattr(event, "item", None)
                if getattr(item, "type", None) == "image_generation_call":
                    result = getattr(item, "result", None)
                    if isinstance(result, str) and result:
                        image_b64 = result
            elif event_type == "response.image_generation_call.partial_image":
                partial = getattr(event, "partial_image_b64", None)
                if isinstance(partial, str) and partial:
                    image_b64 = partial
        final = stream.get_final_response()

    # Final-response sweep covers the case where the stream finished before we
    # observed the ``output_item.done`` event for the image call.
    for item in getattr(final, "output", None) or []:
        if getattr(item, "type", None) == "image_generation_call":
            result = getattr(item, "result", None)
            if isinstance(result, str) and result:
                image_b64 = result

    return image_b64


class OpenAICodexImageGenProvider:
    """OpenAI gpt-image-2 via ChatGPT/Codex OAuth. Duck-typed for the registry."""

    name = "openai-codex"
    display_name = "OpenAI (Codex auth)"

    def is_available(self) -> bool:
        if not _read_codex_access_token():
            return False
        try:
            import openai  # noqa: F401
        except ImportError:
            return False
        return True

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": model_id,
                "display": meta["display"],
                "speed": meta["speed"],
                "strengths": meta["strengths"],
            }
            for model_id, meta in _MODELS.items()
        ]

    def default_model(self) -> str:
        return DEFAULT_MODEL

    def generate(self, prompt: str, aspect_ratio: str = DEFAULT_ASPECT_RATIO, **kwargs: Any) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)

        if not prompt:
            return error_response(
                error="Prompt is required and must be a non-empty string",
                error_type="invalid_argument",
                provider="openai-codex",
                aspect_ratio=aspect,
            )

        if not _read_codex_access_token():
            return error_response(
                error="No Codex/ChatGPT OAuth token available. Set CODEX_ACCESS_TOKEN "
                "or sign in with the Codex CLI (~/.codex/auth.json).",
                error_type="auth_required",
                provider="openai-codex",
                aspect_ratio=aspect,
            )

        try:
            import openai  # noqa: F401
        except ImportError:
            return error_response(
                error="openai Python package not installed (pip install openai)",
                error_type="missing_dependency",
                provider="openai-codex",
                aspect_ratio=aspect,
            )

        tier_id, meta = _resolve_model()
        size = _SIZES.get(aspect, _SIZES["square"])

        client = _build_codex_client()
        if client is None:
            return error_response(
                error="Could not initialize Codex image client",
                error_type="auth_required",
                provider="openai-codex",
                model=tier_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        try:
            b64 = _collect_image_b64(client, prompt=prompt, size=size, quality=meta["quality"])
        except Exception as exc:  # noqa: BLE001
            logger.debug("Codex image generation failed", exc_info=True)
            return error_response(
                error=f"OpenAI image generation via Codex auth failed: {exc}",
                error_type="api_error",
                provider="openai-codex",
                model=tier_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        if not b64:
            return error_response(
                error="Codex response contained no image_generation_call result",
                error_type="empty_response",
                provider="openai-codex",
                model=tier_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        try:
            saved_path = save_b64_image(b64, prefix=f"openai_codex_{tier_id}")
        except Exception as exc:  # noqa: BLE001
            return error_response(
                error=f"Could not save image: {exc}",
                error_type="io_error",
                provider="openai-codex",
                model=tier_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        return success_response(
            image=str(saved_path),
            model=tier_id,
            prompt=prompt,
            aspect_ratio=aspect,
            provider="openai-codex",
            extra={"size": size, "quality": meta["quality"]},
        )


def register(ctx) -> None:
    """Plugin entry point — register the Codex-backed image-gen provider."""
    ctx.register_image_gen_provider(OpenAICodexImageGenProvider())
