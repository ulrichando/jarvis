"""FAL.ai video generation backend (registry kind ``video``).

User-facing surface: pick a **model family** (e.g. "Pixverse v6", "Veo 3.1",
"Seedance 2.0", "Kling v3 4K", "LTX 2.3", "Happy Horse"). The plugin auto-routes
to the family's text-to-video endpoint when called without ``image_url``, and to
its image-to-video endpoint when ``image_url`` is provided. The agent never sees
the routing — it just calls ``video_generate(prompt=..., image_url=...)`` and the
``video_generate`` tool resolves whichever video backend is available.

Model families (each with t2v + i2v endpoints):

  Cheap tier:
    ltx-2.3       fal-ai/ltx-2.3-22b/text-to-video        /  fal-ai/ltx-2.3-22b/image-to-video
    pixverse-v6   fal-ai/pixverse/v6/text-to-video        /  fal-ai/pixverse/v6/image-to-video

  Premium tier:
    veo3.1        fal-ai/veo3.1                           /  fal-ai/veo3.1/image-to-video
    seedance-2.0  bytedance/seedance-2.0/text-to-video    /  bytedance/seedance-2.0/image-to-video
    kling-v3-4k   fal-ai/kling-video/v3/4k/text-to-video  /  fal-ai/kling-video/v3/4k/image-to-video
    happy-horse   fal-ai/happy-horse/text-to-video        /  fal-ai/happy-horse/image-to-video

Family selection precedence:
    1. ``model=`` arg from the tool call
    2. ``FAL_VIDEO_MODEL`` env var
    3. ``DEFAULT_MODEL``

Authentication via ``FAL_KEY``. Inert (filtered from the supervisor surface) when
the key is unset or the ``fal-client`` SDK is not installed.

Ported from the upstream FAL video plugin; the ``agent.*`` base + ``config.yaml``
/ gateway coupling was stripped. Returns the same response-dict shape
:mod:`tools.video_gen` expects (its ``success_response`` / ``error_response``).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from tools.video_gen import error_response, success_response

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Family catalog
# ---------------------------------------------------------------------------
#
# Each family declares both endpoints (when available) plus a per-family
# capability sheet. Capability flags drive which keys get added to the request
# payload — keys a family doesn't advertise are dropped before send.
#
#   aspect_ratios  : tuple of supported ratios (None = endpoint decides)
#   resolutions    : tuple of supported resolutions (None = endpoint decides)
#   durations      : tuple of supported durations OR (min, max) range
#                    (heuristic: 2-element with gap > 1 is a range)
#   audio          : True if generate_audio is supported
#   negative       : True if negative_prompt is supported

FAL_FAMILIES: Dict[str, Dict[str, Any]] = {
    # cheap / fast tier
    "ltx-2.3": {
        "display": "LTX 2.3 (22B)",
        "speed": "~30-60s",
        "price": "cheap",
        "strengths": "22B model with native audio generation. Affordable.",
        "tier": "cheap",
        "text_endpoint": "fal-ai/ltx-2.3-22b/text-to-video",
        "image_endpoint": "fal-ai/ltx-2.3-22b/image-to-video",
        "aspect_ratios": None,
        "resolutions": None,
        "durations": None,
        "audio": True,
        "negative": True,
    },
    "pixverse-v6": {
        "display": "Pixverse v6",
        "speed": "~30-90s",
        "price": "cheap",
        "strengths": "Affordable. Negative prompts. 1-15s durations.",
        "tier": "cheap",
        "text_endpoint": "fal-ai/pixverse/v6/text-to-video",
        "image_endpoint": "fal-ai/pixverse/v6/image-to-video",
        "aspect_ratios": None,
        "resolutions": ("360p", "540p", "720p", "1080p"),
        "durations": (1, 15),
        "audio": True,
        "negative": True,
    },
    # premium tier
    "veo3.1": {
        "display": "Veo 3.1",
        "speed": "~60-120s",
        "price": "premium",
        "strengths": "Google DeepMind. Cinematic, native audio, strong prompt adherence.",
        "tier": "premium",
        "text_endpoint": "fal-ai/veo3.1",
        "image_endpoint": "fal-ai/veo3.1/image-to-video",
        "aspect_ratios": ("16:9", "9:16"),
        "resolutions": ("720p", "1080p"),
        "durations": (4, 6, 8),
        "audio": True,
        "negative": True,
    },
    "seedance-2.0": {
        "display": "Seedance 2.0",
        "speed": "~60-120s",
        "price": "premium",
        "strengths": "ByteDance. Cinematic, synchronized audio + lip-sync, 4-15s.",
        "tier": "premium",
        "text_endpoint": "bytedance/seedance-2.0/text-to-video",
        "image_endpoint": "bytedance/seedance-2.0/image-to-video",
        "aspect_ratios": ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16"),
        "resolutions": ("480p", "720p", "1080p"),
        "durations": (4, 15),
        "audio": True,
        "negative": False,
    },
    "kling-v3-4k": {
        "display": "Kling v3 4K",
        "speed": "~120-300s",
        "price": "premium",
        "strengths": "4K output, native audio (Chinese/English), 3-15s.",
        "tier": "premium",
        "text_endpoint": "fal-ai/kling-video/v3/4k/text-to-video",
        "image_endpoint": "fal-ai/kling-video/v3/4k/image-to-video",
        # Kling 4K image-to-video uses `start_image_url` instead of `image_url`.
        "image_param_key": "start_image_url",
        "aspect_ratios": ("16:9", "9:16", "1:1"),
        "resolutions": None,  # 4K is implicit
        "durations": (3, 15),
        "audio": True,
        "negative": True,
    },
    "happy-horse": {
        "display": "Happy Horse 1.0",
        "speed": "~60-120s",
        "price": "premium",
        "strengths": "Alibaba. New model, sparse public docs — conservative defaults.",
        "tier": "premium",
        "text_endpoint": "fal-ai/happy-horse/text-to-video",
        "image_endpoint": "fal-ai/happy-horse/image-to-video",
        "aspect_ratios": None,
        "resolutions": None,
        "durations": None,
        "audio": False,
        "negative": False,
    },
}

DEFAULT_MODEL = "pixverse-v6"  # cheap, both modalities, sane defaults


def _is_duration_range(durations: Any) -> bool:
    """Heuristic: a 2-tuple of ints with a gap > 1 is treated as ``(min, max)``."""
    if not isinstance(durations, tuple) or len(durations) != 2:
        return False
    if not all(isinstance(d, int) for d in durations):
        return False
    return durations[1] - durations[0] > 1


def _clamp_duration(family: Dict[str, Any], duration: Optional[int]) -> Optional[int]:
    durations = family.get("durations")
    if not durations:
        return duration
    if duration is None:
        return durations[0]
    if _is_duration_range(durations):
        lo, hi = durations
        return max(lo, min(hi, duration))
    if duration in durations:
        return duration
    return min(durations, key=lambda d: abs(d - duration))


def _resolve_family(explicit: Optional[str]) -> Tuple[str, Dict[str, Any]]:
    """Decide which FAL family to use. Returns ``(family_id, meta)``.

    Precedence: explicit ``model=`` arg → ``FAL_VIDEO_MODEL`` env → default.
    (The upstream config.yaml lookup is dropped — JARVIS is env-var driven.)
    """
    for candidate in (explicit, os.environ.get("FAL_VIDEO_MODEL")):
        if isinstance(candidate, str) and candidate.strip() in FAL_FAMILIES:
            fid = candidate.strip()
            return fid, FAL_FAMILIES[fid]
    return DEFAULT_MODEL, FAL_FAMILIES[DEFAULT_MODEL]


def _build_payload(
    family: Dict[str, Any],
    *,
    prompt: str,
    image_url: Optional[str],
    duration: Optional[int],
    aspect_ratio: str,
    resolution: str,
    negative_prompt: Optional[str],
    audio: Optional[bool],
    seed: Optional[int],
) -> Dict[str, Any]:
    """Build a family-specific payload, dropping keys the family doesn't declare."""
    payload: Dict[str, Any] = {}

    if prompt:
        payload["prompt"] = prompt
    if image_url:
        # Some endpoints (e.g. Kling v3 4K image-to-video) expect
        # `start_image_url` instead of `image_url`.
        key = family.get("image_param_key") or "image_url"
        payload[key] = image_url
    if seed is not None:
        payload["seed"] = seed

    if family.get("aspect_ratios") and aspect_ratio in family["aspect_ratios"]:
        payload["aspect_ratio"] = aspect_ratio

    if family.get("resolutions") and resolution in family["resolutions"]:
        payload["resolution"] = resolution

    clamped = _clamp_duration(family, duration)
    if clamped is not None and family.get("durations"):
        # FAL exposes duration as a string in the queue API ("8" not 8).
        payload["duration"] = str(clamped)

    if family.get("audio") and audio is not None:
        payload["generate_audio"] = bool(audio)

    if family.get("negative") and negative_prompt:
        payload["negative_prompt"] = negative_prompt

    return payload


_fal_client: Any = None


def _load_fal_client() -> Any:
    """Lazy-import and cache the ``fal_client`` SDK."""
    global _fal_client
    if _fal_client is not None:
        return _fal_client
    import fal_client  # type: ignore

    _fal_client = fal_client
    return fal_client


class FALVideoGenProvider:
    """FAL.ai multi-family video backend.

    Routes between text-to-video and image-to-video endpoints automatically based
    on whether ``image_url`` was provided. Duck-typed for the provider registry
    (``name`` + ``is_available`` + ``generate``).
    """

    name = "fal"
    display_name = "FAL"

    def is_available(self) -> bool:
        if not os.environ.get("FAL_KEY", "").strip():
            return False
        try:
            import fal_client  # noqa: F401
        except ImportError:
            return False
        return True

    def list_models(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for fid, meta in FAL_FAMILIES.items():
            modalities: List[str] = []
            if meta.get("text_endpoint"):
                modalities.append("text")
            if meta.get("image_endpoint"):
                modalities.append("image")
            out.append({
                "id": fid,
                "display": meta["display"],
                "speed": meta["speed"],
                "strengths": meta["strengths"],
                "price": meta["price"],
                "tier": meta.get("tier", "premium"),
                "modalities": modalities,
            })
        return out

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    def capabilities(self) -> Dict[str, Any]:
        return {
            "modalities": ["text", "image"],
            "aspect_ratios": ["16:9", "9:16", "1:1"],
            "resolutions": ["360p", "540p", "720p", "1080p"],
            "max_duration": 15,
            "min_duration": 1,
            "supports_audio": True,
            "supports_negative_prompt": True,
            "max_reference_images": 0,
        }

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
        duration: Optional[int] = None,
        aspect_ratio: str = "16:9",
        resolution: str = "720p",
        negative_prompt: Optional[str] = None,
        audio: Optional[bool] = None,
        seed: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if not os.environ.get("FAL_KEY", "").strip():
            return error_response(
                error="FAL_KEY is not set in the voice-agent environment "
                "(https://fal.ai/dashboard/keys).",
                error_type="auth_required",
                provider="fal",
                prompt=prompt,
            )

        try:
            fal_client = _load_fal_client()
        except ImportError:
            return error_response(
                error="fal-client Python package not installed (pip install fal-client)",
                error_type="missing_dependency",
                provider="fal",
                prompt=prompt,
            )

        prompt = (prompt or "").strip()
        family_id, family = _resolve_family(model)

        # Route: image_url → image-to-video endpoint; else → text-to-video.
        image_url_norm = (image_url or "").strip() or None
        if image_url_norm:
            endpoint = family.get("image_endpoint")
            modality_used = "image"
            if not endpoint:
                return error_response(
                    error=f"FAL family {family_id} has no image-to-video endpoint. "
                    "Pick a family with image-to-video support.",
                    error_type="modality_unsupported",
                    provider="fal", model=family_id, prompt=prompt,
                )
        else:
            endpoint = family.get("text_endpoint")
            modality_used = "text"
            if not endpoint:
                return error_response(
                    error=f"FAL family {family_id} has no text-to-video endpoint. "
                    "Pass an image_url to use its image-to-video endpoint, or pick "
                    "a different family.",
                    error_type="modality_unsupported",
                    provider="fal", model=family_id, prompt=prompt,
                )

        if not prompt:
            return error_response(
                error="prompt is required.",
                error_type="missing_prompt",
                provider="fal", model=family_id, prompt=prompt,
            )

        payload = _build_payload(
            family,
            prompt=prompt,
            image_url=image_url_norm,
            duration=duration,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            negative_prompt=negative_prompt,
            audio=audio,
            seed=seed,
        )

        try:
            result = fal_client.subscribe(endpoint, arguments=payload, with_logs=False)
        except Exception as exc:  # noqa: BLE001 — surfaced as a structured error
            logger.warning(
                "FAL video gen failed (family=%s, endpoint=%s): %s",
                family_id, endpoint, exc, exc_info=True,
            )
            return error_response(
                error=f"FAL video generation failed: {exc}",
                error_type="api_error",
                provider="fal", model=family_id, prompt=prompt,
                aspect_ratio=aspect_ratio,
            )

        video = (result or {}).get("video") if isinstance(result, dict) else None
        url: Optional[str] = None
        if isinstance(video, dict):
            url = video.get("url")
        elif isinstance(video, str):
            url = video

        if not url:
            return error_response(
                error="FAL returned no video URL in response",
                error_type="empty_response",
                provider="fal", model=family_id, prompt=prompt,
            )

        extra: Dict[str, Any] = {"endpoint": endpoint}
        if isinstance(video, dict):
            if video.get("file_size"):
                extra["file_size"] = video["file_size"]
            if video.get("content_type"):
                extra["content_type"] = video["content_type"]

        return success_response(
            video=url,
            model=family_id,
            prompt=prompt,
            modality=modality_used,
            aspect_ratio=aspect_ratio if "aspect_ratio" in payload else "",
            duration=int(payload["duration"]) if "duration" in payload else 0,
            provider="fal",
            extra=extra,
        )


def register(ctx) -> None:
    """Plugin entry point — wire ``FALVideoGenProvider`` into the registry."""
    ctx.register_video_gen_provider(FALVideoGenProvider())
