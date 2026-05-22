"""Video generation tool for the JARVIS voice agent.

Registers a single ``video_generate(prompt, ...)`` tool on the supervisor.
Generation is delegated to a pluggable backend resolved from the generic
provider registry (:mod:`tools._provider_registry`) under the ``"video"`` kind.
One backend ships here:

  * **xai** — xAI ``grok-imagine-video`` (text-to-video + image-to-video).
    Gated on ``XAI_API_KEY`` (resolved via :mod:`tools.xai_http`). xAI returns
    an HTTPS CDN URL; the tool downloads the bytes and saves them locally.

Output location
---------------
Generated videos are written under ``<jarvis-home>/generated/`` (i.e.
``~/.jarvis/generated/`` by default, overridable via ``JARVIS_HOME``). The voice
reply is a concise ``"Generated → <path>"`` and the JSON result carries the
absolute path in ``path`` / ``video``. If the download fails for any reason the
remote URL is returned instead (still usable, just not cached locally).

Gating
------
``check_fn`` passes only when at least one video provider reports available (an
API key is set). With no key set the tool is filtered out of the supervisor's
surface entirely (inert), so JARVIS never offers video gen it cannot perform.
``requires_env`` documents the keys for operators.

Mirrors :mod:`tools.image_gen`. The upstream ``agent.*`` / gateway / plugin /
``config.yaml`` coupling was stripped; provider selection is env-key driven
through the registry. No upstream brand tokens.
"""
from __future__ import annotations

import asyncio
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
PROVIDER_KIND = "video"

# Advertised enums (providers clamp to their own supported sets).
COMMON_ASPECT_RATIOS: Tuple[str, ...] = ("16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3")
DEFAULT_ASPECT_RATIO = "16:9"
COMMON_RESOLUTIONS: Tuple[str, ...] = ("480p", "540p", "720p", "1080p")
DEFAULT_RESOLUTION = "720p"


# ---------------------------------------------------------------------------
# Response + file-save helpers (JARVIS-native; ported from the upstream
# video_gen_provider module). Providers return these dict shapes; the tool
# handler post-processes them into a voice-friendly result.
# ---------------------------------------------------------------------------


def generated_dir() -> Path:
    """Return ``<jarvis-home>/generated/``, creating it as needed."""
    return get_jarvis_dir("generated")


def save_bytes_video(raw: bytes, *, prefix: str = "video", extension: str = "mp4") -> Path:
    """Write raw video bytes under ``<jarvis-home>/generated/``.

    Returns the absolute :class:`Path`. Filename:
    ``<prefix>_<YYYYMMDD_HHMMSS>_<short-uuid>.<ext>``.
    """
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:8]
    path = generated_dir() / f"{prefix}_{ts}_{short}.{extension}"
    path.write_bytes(raw)
    return path


def success_response(
    *,
    video: str,
    model: str,
    prompt: str,
    modality: str = "text",
    aspect_ratio: str = "",
    duration: int = 0,
    provider: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a uniform success response dict.

    ``video`` is an absolute filesystem path (when the download succeeded) or an
    HTTPS URL. ``modality`` is ``"text"`` or ``"image"`` — which endpoint ran.
    """
    payload: Dict[str, Any] = {
        "success": True,
        "video": video,
        "model": model,
        "prompt": prompt,
        "modality": modality,
        "aspect_ratio": aspect_ratio,
        "duration": int(duration) if duration else 0,
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
    aspect_ratio: str = "",
) -> Dict[str, Any]:
    """Build a uniform error response dict."""
    return {
        "success": False,
        "video": None,
        "error": error,
        "error_type": error_type,
        "model": model,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "provider": provider,
    }


# ---------------------------------------------------------------------------
# xAI provider — grok-imagine-video (text-to-video + image-to-video)
# ---------------------------------------------------------------------------
#
# Reshaped from the upstream xAI video plugin onto JARVIS's env-only xAI
# credential resolver (tools.xai_http) and the generic provider registry. The
# OAuth / config-store resolution was dropped — a bare XAI_API_KEY is the only
# credential path JARVIS supports for xAI.

_XAI_BASE_URL_DEFAULT = "https://api.x.ai/v1"
_XAI_DEFAULT_MODEL = "grok-imagine-video"
_XAI_DEFAULT_DURATION = 8
_XAI_TIMEOUT_SECONDS = 240
_XAI_POLL_INTERVAL_SECONDS = 5
_XAI_VALID_ASPECT_RATIOS = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"}
_XAI_VALID_RESOLUTIONS = {"480p", "720p"}
_XAI_MAX_REFERENCE_IMAGES = 7

_XAI_MODELS: Dict[str, Dict[str, Any]] = {
    "grok-imagine-video": {
        "display": "Grok Imagine Video",
        "speed": "~60-240s",
        "strengths": "Text-to-video + image-to-video; up to 7 reference images.",
        "modalities": ["text", "image"],
    },
}


def _clamp_duration(duration: Optional[int], has_reference_images: bool) -> int:
    value = duration if duration is not None else _XAI_DEFAULT_DURATION
    value = max(1, min(int(value), 15))
    if has_reference_images and value > 10:
        value = 10
    return value


def _normalize_reference_images(reference_image_urls: Optional[List[str]]):
    refs = []
    for url in reference_image_urls or []:
        normalized = (url or "").strip()
        if normalized:
            refs.append({"url": normalized})
    return refs or None


class XAIVideoGenProvider:
    """xAI ``grok-imagine-video`` backend (env-only credentials)."""

    name = "xai"
    display_name = "xAI (Grok Imagine)"

    def is_available(self) -> bool:
        from .xai_http import has_xai_credentials

        return has_xai_credentials()

    def list_models(self) -> List[Dict[str, Any]]:
        return [{"id": mid, **meta} for mid, meta in _XAI_MODELS.items()]

    def default_model(self) -> str:
        return _XAI_DEFAULT_MODEL

    def capabilities(self) -> Dict[str, Any]:
        return {
            "modalities": ["text", "image"],
            "aspect_ratios": sorted(_XAI_VALID_ASPECT_RATIOS),
            "resolutions": sorted(_XAI_VALID_RESOLUTIONS),
            "max_duration": 15,
            "min_duration": 1,
            "supports_audio": False,
            "supports_negative_prompt": False,
            "max_reference_images": _XAI_MAX_REFERENCE_IMAGES,
        }

    # -- credential + HTTP plumbing -----------------------------------------

    def _resolve_credentials(self) -> Tuple[str, str]:
        from .xai_http import resolve_xai_http_credentials

        creds = resolve_xai_http_credentials() or {}
        api_key = str(creds.get("api_key") or "").strip()
        base_url = str(creds.get("base_url") or _XAI_BASE_URL_DEFAULT).strip().rstrip("/")
        return api_key, base_url

    def _headers(self, api_key: str) -> Dict[str, str]:
        from .xai_http import xai_user_agent

        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": xai_user_agent(),
        }

    # -- public entry point --------------------------------------------------

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
        duration: Optional[int] = None,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        resolution: str = DEFAULT_RESOLUTION,
        negative_prompt: Optional[str] = None,
        audio: Optional[bool] = None,
        seed: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        try:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(
                    self._generate_async(
                        prompt=prompt,
                        model=model,
                        image_url=image_url,
                        reference_image_urls=reference_image_urls,
                        duration=duration,
                        aspect_ratio=aspect_ratio,
                        resolution=resolution,
                    )
                )
            finally:
                loop.close()
        except Exception as exc:  # noqa: BLE001 — surfaced as a structured error
            logger.warning("xAI video gen unexpected failure: %s", exc, exc_info=True)
            return error_response(
                error=f"xAI video generation failed: {exc}",
                error_type="api_error",
                provider="xai",
                model=model or _XAI_DEFAULT_MODEL,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
            )

    async def _generate_async(
        self,
        *,
        prompt: str,
        model: Optional[str],
        image_url: Optional[str],
        reference_image_urls: Optional[List[str]],
        duration: Optional[int],
        aspect_ratio: str,
        resolution: str,
    ) -> Dict[str, Any]:
        import httpx

        api_key, base_url = self._resolve_credentials()
        if not api_key:
            return error_response(
                error="XAI_API_KEY is not set in the voice-agent environment.",
                error_type="auth_required",
                provider="xai",
                prompt=prompt,
            )

        prompt = (prompt or "").strip()
        if not prompt:
            return error_response(
                error="prompt is required for xAI video generation",
                error_type="missing_prompt",
                provider="xai",
                prompt=prompt,
            )

        image_url_norm = (image_url or "").strip() or None
        ar = (aspect_ratio or DEFAULT_ASPECT_RATIO).strip()
        res = (resolution or DEFAULT_RESOLUTION).strip().lower()
        modality_used = "image" if image_url_norm else "text"

        refs = _normalize_reference_images(reference_image_urls)
        if refs and len(refs) > _XAI_MAX_REFERENCE_IMAGES:
            return error_response(
                error=f"reference_image_urls supports at most {_XAI_MAX_REFERENCE_IMAGES} images on xAI",
                error_type="too_many_references",
                provider="xai",
                prompt=prompt,
            )
        if image_url_norm and refs:
            return error_response(
                error="image_url and reference_image_urls cannot be combined on xAI",
                error_type="conflicting_inputs",
                provider="xai",
                prompt=prompt,
            )

        clamped_duration = _clamp_duration(duration, has_reference_images=bool(refs))
        if ar not in _XAI_VALID_ASPECT_RATIOS:
            ar = DEFAULT_ASPECT_RATIO
        if res not in _XAI_VALID_RESOLUTIONS:
            res = DEFAULT_RESOLUTION

        payload: Dict[str, Any] = {
            "model": model or _XAI_DEFAULT_MODEL,
            "prompt": prompt,
            "duration": clamped_duration,
            "aspect_ratio": ar,
            "resolution": res,
        }
        if image_url_norm:
            payload["image"] = {"url": image_url_norm}
        if refs:
            payload["reference_images"] = refs

        async with httpx.AsyncClient() as client:
            # Submit
            try:
                submit = await client.post(
                    f"{base_url}/videos/generations",
                    headers={**self._headers(api_key), "x-idempotency-key": str(uuid.uuid4())},
                    json=payload,
                    timeout=60,
                )
                submit.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = ""
                try:
                    detail = exc.response.text[:500]
                except Exception:  # noqa: BLE001
                    pass
                return error_response(
                    error=f"xAI submit failed ({exc.response.status_code}): {detail or exc}",
                    error_type="api_error",
                    provider="xai",
                    model=model or _XAI_DEFAULT_MODEL,
                    prompt=prompt,
                    aspect_ratio=ar,
                )
            except Exception as exc:  # noqa: BLE001 — connection/timeout
                return error_response(
                    error=f"xAI submit error: {exc}",
                    error_type="connection_error",
                    provider="xai",
                    model=model or _XAI_DEFAULT_MODEL,
                    prompt=prompt,
                    aspect_ratio=ar,
                )

            request_id = (submit.json() or {}).get("request_id")
            if not request_id:
                return error_response(
                    error="xAI video response did not include request_id",
                    error_type="empty_response",
                    provider="xai",
                    model=model or _XAI_DEFAULT_MODEL,
                    prompt=prompt,
                    aspect_ratio=ar,
                )

            # Poll
            elapsed = 0.0
            last_status = "queued"
            body: Dict[str, Any] = {}
            while elapsed < _XAI_TIMEOUT_SECONDS:
                poll = await client.get(
                    f"{base_url}/videos/{request_id}",
                    headers=self._headers(api_key),
                    timeout=30,
                )
                poll.raise_for_status()
                body = poll.json() or {}
                last_status = (body.get("status") or "").lower()
                if last_status == "done":
                    break
                if last_status in {"failed", "error", "expired", "cancelled"}:
                    break
                await asyncio.sleep(_XAI_POLL_INTERVAL_SECONDS)
                elapsed += _XAI_POLL_INTERVAL_SECONDS

            if last_status != "done":
                if elapsed >= _XAI_TIMEOUT_SECONDS and last_status not in {
                    "failed", "error", "expired", "cancelled",
                }:
                    return error_response(
                        error=f"Timed out waiting for video generation after {_XAI_TIMEOUT_SECONDS}s",
                        error_type="timeout",
                        provider="xai",
                        model=model or _XAI_DEFAULT_MODEL,
                        prompt=prompt,
                        aspect_ratio=ar,
                    )
                message = (
                    (body.get("error", {}) or {}).get("message")
                    or body.get("message")
                    or f"xAI video generation ended with status '{last_status}'"
                )
                return error_response(
                    error=message,
                    error_type=f"xai_{last_status or 'unknown'}",
                    provider="xai",
                    model=model or _XAI_DEFAULT_MODEL,
                    prompt=prompt,
                    aspect_ratio=ar,
                )

            video = body.get("video") or {}
            url = video.get("url")
            if not url:
                return error_response(
                    error="xAI video generation completed without a video URL",
                    error_type="empty_response",
                    provider="xai",
                    model=body.get("model") or model or _XAI_DEFAULT_MODEL,
                    prompt=prompt,
                    aspect_ratio=ar,
                )

            # Download the CDN URL → save locally under ~/.jarvis/generated/.
            # If the download fails, fall back to returning the URL itself.
            video_ref = url
            try:
                dl = await client.get(url, timeout=120)
                dl.raise_for_status()
                saved = save_bytes_video(dl.content, prefix=f"xai_{model or _XAI_DEFAULT_MODEL}")
                video_ref = str(saved)
            except Exception as exc:  # noqa: BLE001 — keep the URL on download failure
                logger.warning("xAI video download/save failed (returning URL): %s", exc)

            extra: Dict[str, Any] = {"request_id": request_id, "resolution": res, "url": url}
            if body.get("usage"):
                extra["usage"] = body["usage"]
            return success_response(
                video=video_ref,
                model=body.get("model") or model or _XAI_DEFAULT_MODEL,
                prompt=prompt,
                modality=modality_used,
                aspect_ratio=ar,
                duration=video.get("duration") or clamped_duration,
                provider="xai",
                extra=extra,
            )


# ---------------------------------------------------------------------------
# Provider self-registration (import-time side effect)
# ---------------------------------------------------------------------------
#
# Mirrors image_gen.py: importing this module registers the video provider into
# the generic registry. Registration is unconditional — availability (API key
# presence) is decided per-call by the provider's ``is_available()``, so a key
# set after import is picked up without re-import.

provider_registry.register_provider(PROVIDER_KIND, "xai", XAIVideoGenProvider())


# ---------------------------------------------------------------------------
# Tool gate + handler
# ---------------------------------------------------------------------------


def check_video_generation_requirements() -> bool:
    """True when at least one video provider is currently available.

    This is the tool's ``check_fn`` — when no provider has its API key set the
    tool is filtered out of the supervisor surface entirely (inert).
    """
    return provider_registry.has_available_provider(PROVIDER_KIND)


def _coerce_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_reference_image_arg(value: Any) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return None
    out: List[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out or None


def _handle_video_generate(args: Dict[str, Any], **_kw: Any) -> str:
    if not isinstance(args, dict):
        args = {}
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return tool_error("prompt is required for video generation")

    image_url = (args.get("image_url") or "").strip() or None
    reference_image_urls = _normalize_reference_image_arg(args.get("reference_image_urls"))
    duration = _coerce_int(args.get("duration"))
    aspect_ratio = (args.get("aspect_ratio") or DEFAULT_ASPECT_RATIO).strip() or DEFAULT_ASPECT_RATIO
    resolution = (args.get("resolution") or DEFAULT_RESOLUTION).strip() or DEFAULT_RESOLUTION
    model_override = (args.get("model") or "").strip() or None

    provider = provider_registry.get_provider(PROVIDER_KIND)
    if provider is None:
        return tool_error(
            "No video generation backend is available. Set XAI_API_KEY (xAI "
            "grok-imagine-video) in the voice-agent environment to enable "
            "video generation."
        )

    kwargs: Dict[str, Any] = {
        "model": model_override,
        "image_url": image_url,
        "reference_image_urls": reference_image_urls,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
    }
    kwargs = {k: v for k, v in kwargs.items() if v is not None}

    try:
        result = provider.generate(prompt=prompt, **kwargs)
    except Exception as exc:  # noqa: BLE001 — a provider error must not crash the turn
        logger.warning("video provider %r raised: %s", getattr(provider, "name", "?"), exc)
        return tool_error(f"Video generation failed: {exc}")

    if not isinstance(result, dict):
        return tool_error("Video provider returned a non-dict result")

    if not result.get("success"):
        return tool_error(
            result.get("error", "Video generation failed"),
            error_type=result.get("error_type", "provider_error"),
            provider=result.get("provider", getattr(provider, "name", "")),
        )

    video_ref = result.get("video") or ""
    return tool_result(
        result=f"Generated → {video_ref}",
        path=video_ref,
        video=video_ref,
        provider=result.get("provider", getattr(provider, "name", "")),
        model=result.get("model", ""),
        modality=result.get("modality", ""),
        aspect_ratio=result.get("aspect_ratio", aspect_ratio),
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

VIDEO_GENERATE_SCHEMA: Dict[str, Any] = {
    "name": "video_generate",
    "description": (
        "Generate a video from a text prompt (text-to-video) or animate a "
        "still image (image-to-video) using the configured video backend (xAI "
        "grok-imagine-video). Pass `image_url` to animate that image; omit it "
        "to generate from text alone. Generation can take 30 seconds to "
        "several minutes — the call blocks until the video is ready. Returns "
        "the absolute file path of the saved video in the `path` field."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "Text instruction describing the desired video — subject, "
                    "motion, style, camera movement, etc."
                ),
            },
            "image_url": {
                "type": "string",
                "description": (
                    "Optional public URL of a still image. When provided, the "
                    "backend animates the image (image-to-video); when omitted "
                    "it routes to text-to-video."
                ),
            },
            "reference_image_urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional list of reference image URLs (style/character "
                    "refs), up to 7. Cannot be combined with image_url."
                ),
            },
            "duration": {
                "type": "integer",
                "description": (
                    "Desired duration in seconds. Clamped to 1-15 (10 max when "
                    "reference images are supplied). Omit for the default (8s)."
                ),
            },
            "aspect_ratio": {
                "type": "string",
                "enum": list(COMMON_ASPECT_RATIOS),
                "description": "Output aspect ratio. Provider clamps to its supported set.",
                "default": DEFAULT_ASPECT_RATIO,
            },
            "resolution": {
                "type": "string",
                "enum": list(COMMON_RESOLUTIONS),
                "description": "Output resolution. Provider clamps to its supported set.",
                "default": DEFAULT_RESOLUTION,
            },
            "model": {
                "type": "string",
                "description": (
                    "Optional model override. Defaults to the provider's "
                    "default model (grok-imagine-video)."
                ),
            },
        },
        "required": ["prompt"],
    },
}


registry.register(
    name="video_generate",
    toolset="video_gen",
    schema=VIDEO_GENERATE_SCHEMA,
    handler=_handle_video_generate,
    check_fn=check_video_generation_requirements,
    requires_env=["XAI_API_KEY"],
    is_async=False,  # sync HTTP/poll; the adapter runs sync handlers fine.
    emoji="🎬",
)
