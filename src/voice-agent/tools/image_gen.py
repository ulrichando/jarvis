"""Image generation tool for the JARVIS voice agent.

Registers a single ``image_generate(prompt, aspect_ratio)`` tool on the
supervisor. The actual generation is delegated to a pluggable backend resolved
from the generic provider registry (:mod:`tools._provider_registry`), under the
``"image"`` kind. Two backends ship here:

  * **openai** — OpenAI ``gpt-image-2`` (low/medium/high quality tiers). Gated
    on ``OPENAI_API_KEY`` + the ``openai`` SDK. Returns base64 → saved locally.
  * **xai** — xAI ``grok-imagine-image``. Gated on ``XAI_API_KEY`` (resolved via
    :mod:`tools.xai_http`). Returns base64 or a URL.

Output location
---------------
Generated images are written under ``<jarvis-home>/generated/`` (i.e.
``~/.jarvis/generated/`` by default, overridable via ``JARVIS_HOME``). The voice
reply is a concise ``"Generated → <path>"`` and the JSON result carries the
absolute path in ``image``.

Gating
------
``check_fn`` passes only when at least one image provider reports available
(an API key is set). With no key set the tool is filtered out of the
supervisor's surface entirely (inert), so JARVIS never offers image gen it
cannot perform. ``requires_env`` documents the keys for operators.

Ported from the upstream image-generation tool + provider abstraction. The
``agent.*`` / gateway / ``config.yaml`` coupling was stripped; provider
selection is env-key driven through the registry. No upstream brand tokens.
"""
from __future__ import annotations

import base64
import datetime
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import _provider_registry as provider_registry
from .registry import registry, tool_error, tool_result
from .runtime import get_jarvis_dir

logger = logging.getLogger(__name__)

# The provider-registry kind this tool consumes.
PROVIDER_KIND = "image"

VALID_ASPECT_RATIOS: Tuple[str, ...] = ("landscape", "square", "portrait")
DEFAULT_ASPECT_RATIO = "landscape"


# ---------------------------------------------------------------------------
# Provider response + file-save helpers (JARVIS-native; ported from the
# upstream image_gen_provider module). Providers return these dict shapes; the
# tool handler post-processes them into a voice-friendly result.
# ---------------------------------------------------------------------------


def resolve_aspect_ratio(value: Optional[str]) -> str:
    """Clamp an aspect_ratio value to the valid set, defaulting to landscape.

    Invalid values are coerced rather than rejected so the surface is forgiving
    of agent mistakes.
    """
    if not isinstance(value, str):
        return DEFAULT_ASPECT_RATIO
    v = value.strip().lower()
    return v if v in VALID_ASPECT_RATIOS else DEFAULT_ASPECT_RATIO


def generated_images_dir() -> Path:
    """Return ``<jarvis-home>/generated/``, creating it as needed."""
    return get_jarvis_dir("generated")


def save_b64_image(b64_data: str, *, prefix: str = "image", extension: str = "png") -> Path:
    """Decode base64 image data and write it under ``<jarvis-home>/generated/``.

    Returns the absolute :class:`Path` to the saved file. Filename format:
    ``<prefix>_<YYYYMMDD_HHMMSS>_<short-uuid>.<ext>``.
    """
    raw = base64.b64decode(b64_data)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:8]
    path = generated_images_dir() / f"{prefix}_{ts}_{short}.{extension}"
    path.write_bytes(raw)
    return path


def success_response(
    *,
    image: str,
    model: str,
    prompt: str,
    aspect_ratio: str,
    provider: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a uniform success response dict.

    ``image`` is an absolute filesystem path (for b64 providers) or an HTTP URL.
    """
    payload: Dict[str, Any] = {
        "success": True,
        "image": image,
        "model": model,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "provider": provider,
    }
    if extra:
        for k, v in extra.items():
            payload.setdefault(k, v)
    return payload


def error_response(
    *,
    error: str,
    error_type: str = "provider_error",
    provider: str = "",
    model: str = "",
    prompt: str = "",
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
) -> Dict[str, Any]:
    """Build a uniform error response dict."""
    return {
        "success": False,
        "image": None,
        "error": error,
        "error_type": error_type,
        "model": model,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "provider": provider,
    }


# ---------------------------------------------------------------------------
# OpenAI provider — gpt-image-2 at three quality tiers
# ---------------------------------------------------------------------------
#
# All three tier IDs resolve to the same underlying API model with a different
# ``quality`` setting. ``OPENAI_IMAGE_MODEL`` env var selects a tier (escape
# hatch for scripts / tests); otherwise the medium tier is the default.

_OPENAI_API_MODEL = "gpt-image-2"

_OPENAI_MODELS: Dict[str, Dict[str, Any]] = {
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

_OPENAI_DEFAULT_MODEL = "gpt-image-2-medium"

_OPENAI_SIZES = {
    "landscape": "1536x1024",
    "square": "1024x1024",
    "portrait": "1024x1536",
}


def _resolve_openai_model() -> Tuple[str, Dict[str, Any]]:
    """Decide which OpenAI tier to use and return ``(model_id, meta)``.

    Honors ``OPENAI_IMAGE_MODEL`` when it names a known tier; else medium.
    """
    env_override = os.environ.get("OPENAI_IMAGE_MODEL", "").strip()
    if env_override and env_override in _OPENAI_MODELS:
        return env_override, _OPENAI_MODELS[env_override]
    return _OPENAI_DEFAULT_MODEL, _OPENAI_MODELS[_OPENAI_DEFAULT_MODEL]


class OpenAIImageGenProvider:
    """OpenAI ``images.generate`` backend — gpt-image-2 at low/medium/high."""

    name = "openai"
    display_name = "OpenAI"

    def is_available(self) -> bool:
        if not os.environ.get("OPENAI_API_KEY", "").strip():
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
            for model_id, meta in _OPENAI_MODELS.items()
        ]

    def default_model(self) -> str:
        return _OPENAI_DEFAULT_MODEL

    def generate(self, prompt: str, aspect_ratio: str = DEFAULT_ASPECT_RATIO, **kwargs: Any) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)

        if not prompt:
            return error_response(
                error="Prompt is required and must be a non-empty string",
                error_type="invalid_argument",
                provider="openai",
                aspect_ratio=aspect,
            )

        if not os.environ.get("OPENAI_API_KEY", "").strip():
            return error_response(
                error="OPENAI_API_KEY is not set in the voice-agent environment.",
                error_type="auth_required",
                provider="openai",
                aspect_ratio=aspect,
            )

        try:
            import openai
        except ImportError:
            return error_response(
                error="openai Python package not installed (pip install openai)",
                error_type="missing_dependency",
                provider="openai",
                aspect_ratio=aspect,
            )

        tier_id, meta = _resolve_openai_model()
        size = _OPENAI_SIZES.get(aspect, _OPENAI_SIZES["square"])

        # gpt-image-2 returns b64_json unconditionally and REJECTS
        # ``response_format`` as an unknown parameter. Don't send it.
        payload: Dict[str, Any] = {
            "model": _OPENAI_API_MODEL,
            "prompt": prompt,
            "size": size,
            "n": 1,
            "quality": meta["quality"],
        }

        try:
            client = openai.OpenAI()
            response = client.images.generate(**payload)
        except Exception as exc:  # noqa: BLE001 — surfaced as a structured error
            logger.debug("OpenAI image generation failed", exc_info=True)
            return error_response(
                error=f"OpenAI image generation failed: {exc}",
                error_type="api_error",
                provider="openai",
                model=tier_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        data = getattr(response, "data", None) or []
        if not data:
            return error_response(
                error="OpenAI returned no image data",
                error_type="empty_response",
                provider="openai",
                model=tier_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        first = data[0]
        b64 = getattr(first, "b64_json", None)
        url = getattr(first, "url", None)
        revised_prompt = getattr(first, "revised_prompt", None)

        if b64:
            try:
                saved_path = save_b64_image(b64, prefix=f"openai_{tier_id}")
            except Exception as exc:  # noqa: BLE001
                return error_response(
                    error=f"Could not save image: {exc}",
                    error_type="io_error",
                    provider="openai",
                    model=tier_id,
                    prompt=prompt,
                    aspect_ratio=aspect,
                )
            image_ref = str(saved_path)
        elif url:
            # Defensive — gpt-image-2 returns b64 today, but fall back
            # gracefully if the API ever changes.
            image_ref = url
        else:
            return error_response(
                error="OpenAI response contained neither b64_json nor URL",
                error_type="empty_response",
                provider="openai",
                model=tier_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        extra: Dict[str, Any] = {"size": size, "quality": meta["quality"]}
        if revised_prompt:
            extra["revised_prompt"] = revised_prompt

        return success_response(
            image=image_ref,
            model=tier_id,
            prompt=prompt,
            aspect_ratio=aspect,
            provider="openai",
            extra=extra,
        )


# ---------------------------------------------------------------------------
# xAI provider — grok-imagine-image
# ---------------------------------------------------------------------------

_XAI_MODELS: Dict[str, Dict[str, Any]] = {
    "grok-imagine-image": {
        "display": "Grok Imagine Image",
        "speed": "~5-10s",
        "strengths": "Fast, high-quality",
    },
    "grok-imagine-image-quality": {
        "display": "Grok Imagine Image (Quality)",
        "speed": "~10-20s",
        "strengths": "Higher fidelity / detail; slower than the standard model.",
    },
}

_XAI_DEFAULT_MODEL = "grok-imagine-image"

_XAI_ASPECT_RATIOS = {
    "landscape": "16:9",
    "square": "1:1",
    "portrait": "9:16",
}

_XAI_RESOLUTIONS = {"1k", "2k"}
_XAI_DEFAULT_RESOLUTION = "1k"


def _resolve_xai_model() -> Tuple[str, Dict[str, Any]]:
    """Decide which xAI model to use and return ``(model_id, meta)``.

    Honors ``XAI_IMAGE_MODEL`` when it names a known model; else the default.
    """
    env_override = os.environ.get("XAI_IMAGE_MODEL", "").strip()
    if env_override and env_override in _XAI_MODELS:
        return env_override, _XAI_MODELS[env_override]
    return _XAI_DEFAULT_MODEL, _XAI_MODELS[_XAI_DEFAULT_MODEL]


def _resolve_xai_resolution() -> str:
    """Get configured resolution from ``XAI_IMAGE_RESOLUTION``; else 1k."""
    res = os.environ.get("XAI_IMAGE_RESOLUTION", "").strip().lower()
    return res if res in _XAI_RESOLUTIONS else _XAI_DEFAULT_RESOLUTION


class XAIImageGenProvider:
    """xAI ``grok-imagine-image`` backend (env-only credentials)."""

    name = "xai"
    display_name = "xAI (Grok)"

    def is_available(self) -> bool:
        from .xai_http import has_xai_credentials

        return has_xai_credentials()

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": model_id,
                "display": meta.get("display", model_id),
                "speed": meta.get("speed", ""),
                "strengths": meta.get("strengths", ""),
            }
            for model_id, meta in _XAI_MODELS.items()
        ]

    def default_model(self) -> str:
        return _XAI_DEFAULT_MODEL

    def generate(self, prompt: str, aspect_ratio: str = DEFAULT_ASPECT_RATIO, **kwargs: Any) -> Dict[str, Any]:
        import requests

        from .xai_http import resolve_xai_http_credentials, xai_user_agent

        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)

        if not prompt:
            return error_response(
                error="Prompt is required and must be a non-empty string",
                error_type="invalid_argument",
                provider="xai",
                aspect_ratio=aspect,
            )

        creds = resolve_xai_http_credentials()
        api_key = str(creds.get("api_key") or "").strip()
        provider_name = str(creds.get("provider") or "xai").strip() or "xai"
        if not api_key:
            return error_response(
                error="XAI_API_KEY is not set in the voice-agent environment.",
                error_type="missing_api_key",
                provider=provider_name,
                aspect_ratio=aspect,
            )

        model_id, _meta = _resolve_xai_model()
        xai_ar = _XAI_ASPECT_RATIOS.get(aspect, "1:1")
        xai_res = _resolve_xai_resolution()

        payload: Dict[str, Any] = {
            "model": model_id,
            "prompt": prompt,
            "aspect_ratio": xai_ar,
            "resolution": xai_res,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": xai_user_agent(),
        }
        base_url = str(creds.get("base_url") or "https://api.x.ai/v1").strip().rstrip("/")

        try:
            response = requests.post(
                f"{base_url}/images/generations",
                headers=headers,
                json=payload,
                timeout=120,
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            resp = exc.response
            status = resp.status_code if resp is not None else 0
            try:
                err_msg = resp.json().get("error", {}).get("message", resp.text[:300])
            except Exception:  # noqa: BLE001
                err_msg = resp.text[:300] if resp is not None else str(exc)
            logger.error("xAI image gen failed (%s): %s", status, err_msg)
            return error_response(
                error=f"xAI image generation failed ({status}): {err_msg}",
                error_type="api_error",
                provider=provider_name,
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        except requests.Timeout:
            return error_response(
                error="xAI image generation timed out (120s)",
                error_type="timeout",
                provider=provider_name,
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        except requests.ConnectionError as exc:
            return error_response(
                error=f"xAI connection error: {exc}",
                error_type="connection_error",
                provider=provider_name,
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        try:
            result = response.json()
        except Exception as exc:  # noqa: BLE001
            return error_response(
                error=f"xAI returned invalid JSON: {exc}",
                error_type="invalid_response",
                provider=provider_name,
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        data = result.get("data", [])
        if not data:
            return error_response(
                error="xAI returned no image data",
                error_type="empty_response",
                provider=provider_name,
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        first = data[0]
        b64 = first.get("b64_json")
        url = first.get("url")

        if b64:
            try:
                saved_path = save_b64_image(b64, prefix=f"xai_{model_id}")
            except Exception as exc:  # noqa: BLE001
                return error_response(
                    error=f"Could not save image: {exc}",
                    error_type="io_error",
                    provider="xai",
                    model=model_id,
                    prompt=prompt,
                    aspect_ratio=aspect,
                )
            image_ref = str(saved_path)
        elif url:
            image_ref = url
        else:
            return error_response(
                error="xAI response contained neither b64_json nor URL",
                error_type="empty_response",
                provider="xai",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        return success_response(
            image=image_ref,
            model=model_id,
            prompt=prompt,
            aspect_ratio=aspect,
            provider="xai",
            extra={"resolution": xai_res},
        )


# ---------------------------------------------------------------------------
# Provider self-registration (import-time side effect)
# ---------------------------------------------------------------------------
#
# Mirrors how tool modules self-register: importing this module registers both
# image providers into the generic registry. Registration is unconditional —
# availability (API key presence) is decided per-call by each provider's
# ``is_available()``, so a key set after import is picked up without re-import.

provider_registry.register_provider(PROVIDER_KIND, "openai", OpenAIImageGenProvider())
provider_registry.register_provider(PROVIDER_KIND, "xai", XAIImageGenProvider())


# ---------------------------------------------------------------------------
# Tool gate + handler
# ---------------------------------------------------------------------------


def check_image_generation_requirements() -> bool:
    """True when at least one image provider is currently available.

    This is the tool's ``check_fn`` — when no provider has its API key set the
    tool is filtered out of the supervisor surface entirely (inert).
    """
    return provider_registry.has_available_provider(PROVIDER_KIND)


def _handle_image_generate(args: Dict[str, Any], **_kw: Any) -> str:
    prompt = (args.get("prompt") or "").strip() if isinstance(args, dict) else ""
    if not prompt:
        return tool_error("prompt is required for image generation")
    aspect_ratio = resolve_aspect_ratio(args.get("aspect_ratio"))

    provider = provider_registry.get_provider(PROVIDER_KIND)
    if provider is None:
        return tool_error(
            "No image generation backend is available. Set OPENAI_API_KEY "
            "(OpenAI gpt-image-2) or XAI_API_KEY (xAI grok-imagine-image) in "
            "the voice-agent environment to enable image generation."
        )

    try:
        result = provider.generate(prompt=prompt, aspect_ratio=aspect_ratio)
    except Exception as exc:  # noqa: BLE001 — a provider error must not crash the turn
        logger.warning("image provider %r raised: %s", getattr(provider, "name", "?"), exc)
        return tool_error(f"Image generation failed: {exc}")

    if not isinstance(result, dict):
        return tool_error("Image provider returned a non-dict result")

    if not result.get("success"):
        return tool_error(
            result.get("error", "Image generation failed"),
            error_type=result.get("error_type", "provider_error"),
            provider=result.get("provider", getattr(provider, "name", "")),
        )

    image_ref = result.get("image") or ""
    # Voice-friendly one-liner: the supervisor reads ``result`` back; the path
    # is also exposed explicitly so the UI/markdown layer can render it.
    return tool_result(
        result=f"Generated → {image_ref}",
        path=image_ref,
        image=image_ref,
        provider=result.get("provider", getattr(provider, "name", "")),
        model=result.get("model", ""),
        aspect_ratio=result.get("aspect_ratio", aspect_ratio),
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

IMAGE_GENERATE_SCHEMA = {
    "name": "image_generate",
    "description": (
        "Generate an image from a text prompt and save it to a file. The "
        "backend (OpenAI gpt-image-2 or xAI grok-imagine-image) is selected "
        "automatically from whichever API key is configured. Returns the "
        "absolute file path of the saved image in the `path` field."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Detailed description of the desired image.",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": list(VALID_ASPECT_RATIOS),
                "description": (
                    "Aspect ratio of the generated image. 'landscape' is 16:9 "
                    "wide, 'portrait' is 16:9 tall, 'square' is 1:1."
                ),
                "default": DEFAULT_ASPECT_RATIO,
            },
        },
        "required": ["prompt"],
    },
}


registry.register(
    name="image_generate",
    toolset="image_gen",
    schema=IMAGE_GENERATE_SCHEMA,
    handler=_handle_image_generate,
    check_fn=check_image_generation_requirements,
    requires_env=["OPENAI_API_KEY", "XAI_API_KEY"],
    is_async=False,  # sync SDK / HTTP; the adapter offloads sync handlers via asyncio.to_thread.
    emoji="🎨",
)
