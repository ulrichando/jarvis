"""xAI Grok web backend — agentic search-only provider via XAI_API_KEY."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from tools.web_providers import WebSearchProvider

logger = logging.getLogger(__name__)

_XAI_DEFAULT_BASE_URL = "https://api.x.ai/v1"
_XAI_DEFAULT_MODEL = "grok-3"
_XAI_DEFAULT_TIMEOUT = 90
_MAX_DOMAIN_FILTERS = 5  # xAI hard cap on allowed/excluded_domains

# Match the JSON object Grok is asked to emit. Tolerates leading/trailing prose
# since reasoning models occasionally narrate before the JSON block.
_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)

# Optional domain-filter env vars (comma-separated domain lists).
# Only one of these may be set at a time (xAI restriction).
_JARVIS_XAI_ALLOWED_DOMAINS_ENV = "JARVIS_XAI_ALLOWED_DOMAINS"
_JARVIS_XAI_EXCLUDED_DOMAINS_ENV = "JARVIS_XAI_EXCLUDED_DOMAINS"
_JARVIS_XAI_MODEL_ENV = "JARVIS_XAI_WEB_MODEL"
_JARVIS_XAI_TIMEOUT_ENV = "JARVIS_XAI_WEB_TIMEOUT"


def _read_domain_list(env_var: str) -> List[str]:
    """Read a comma-separated domain list from an env var, capped at 5."""
    raw = os.getenv(env_var, "").strip()
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts[:_MAX_DOMAIN_FILTERS]


class XAIWebSearchProvider(WebSearchProvider):
    """Search-only provider backed by xAI's agentic Web Search tool.

    Sends a structured prompt to Grok with ``tools=[{"type": "web_search"}]``
    enabled and asks it to return the top *limit* results as JSON. Falls
    back to the Responses API ``citations`` list if Grok ignores the JSON
    schema instruction (rare but handled).

    Authentication is via ``XAI_API_KEY`` only — the env-var-driven JARVIS
    approach. Optional knobs (all env-var-driven):
      - ``JARVIS_XAI_WEB_MODEL``          model name (default: grok-3)
      - ``JARVIS_XAI_WEB_TIMEOUT``        request timeout in seconds (default: 90)
      - ``JARVIS_XAI_ALLOWED_DOMAINS``    comma-separated, max 5 (mutually
                                          exclusive with excluded)
      - ``JARVIS_XAI_EXCLUDED_DOMAINS``   comma-separated, max 5

    Trust note: unlike index-backed providers, this backend is an LLM that
    decides which URLs to surface. Treat returned URLs the same as any
    model-generated link — validate before fetching when downstream trust matters.
    """

    name = "xai"

    def is_available(self) -> bool:
        """Return True when XAI_API_KEY is set to a non-empty value."""
        return bool(os.getenv("XAI_API_KEY", "").strip())

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    def supports_crawl(self) -> bool:
        return False

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Execute a Grok-backed web search.

        Returns ``{"success": True, "data": {"web": [{title, url, description, position}, ...]}}``
        on success, ``{"success": False, "error": str}`` on failure.
        """
        api_key = os.getenv("XAI_API_KEY", "").strip()
        if not api_key:
            return {
                "success": False,
                "error": "XAI_API_KEY is not set.",
            }

        base_url = os.getenv("XAI_BASE_URL", _XAI_DEFAULT_BASE_URL).strip().rstrip("/")
        model = os.getenv(_JARVIS_XAI_MODEL_ENV, _XAI_DEFAULT_MODEL).strip() or _XAI_DEFAULT_MODEL
        try:
            timeout = float(os.getenv(_JARVIS_XAI_TIMEOUT_ENV, str(_XAI_DEFAULT_TIMEOUT)))
        except (TypeError, ValueError):
            timeout = _XAI_DEFAULT_TIMEOUT

        try:
            limit = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            limit = 5

        allowed = _read_domain_list(_JARVIS_XAI_ALLOWED_DOMAINS_ENV)
        excluded = _read_domain_list(_JARVIS_XAI_EXCLUDED_DOMAINS_ENV)
        if allowed and excluded:
            return {
                "success": False,
                "error": (
                    f"{_JARVIS_XAI_ALLOWED_DOMAINS_ENV} and "
                    f"{_JARVIS_XAI_EXCLUDED_DOMAINS_ENV} cannot both be set "
                    "(xAI restriction)."
                ),
            }

        web_search_tool: Dict[str, Any] = {"type": "web_search"}
        if allowed:
            web_search_tool["filters"] = {"allowed_domains": allowed}
        elif excluded:
            web_search_tool["filters"] = {"excluded_domains": excluded}

        payload: Dict[str, Any] = {
            "model": model,
            "input": [{"role": "user", "content": self._build_prompt(query, limit)}],
            "tools": [web_search_tool],
            "include": ["no_inline_citations"],
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "jarvis-agent/1.0",
        }

        try:
            import httpx
        except ImportError:
            return {
                "success": False,
                "error": "httpx is not installed (required for xAI web search)",
            }

        logger.info(
            "xAI web search via %s: '%s' (limit=%d, model=%s)",
            base_url, query, limit, model,
        )

        try:
            resp = httpx.post(
                f"{base_url}/responses",
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            body = ""
            try:
                body = exc.response.text[:300] if exc.response is not None else ""
            except Exception:
                body = ""
            logger.warning("xAI web search HTTP %d: %s", status, body)
            return {
                "success": False,
                "error": f"xAI web search returned HTTP {status}: {body}".rstrip(),
            }
        except httpx.RequestError as exc:
            logger.warning("xAI web search request error: %s", exc)
            return {"success": False, "error": f"Could not reach xAI: {exc}"}

        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("xAI web search bad JSON: %s", exc)
            return {
                "success": False,
                "error": "Could not parse xAI Responses API reply as JSON",
            }

        # Check for API-level error envelope (HTTP 200 with error body).
        api_error = data.get("error") if isinstance(data, dict) else None
        if isinstance(api_error, dict):
            err_msg = (
                api_error.get("message")
                or api_error.get("code")
                or "unknown error"
            )
            logger.warning("xAI web search returned error envelope: %s", err_msg)
            return {"success": False, "error": f"xAI returned an error: {err_msg}"}

        web_results = self._extract_results(data, limit=limit)
        return {"success": True, "data": {"web": web_results}}

    @staticmethod
    def _build_prompt(query: str, limit: int) -> str:
        return (
            "Use the web_search tool to find current information for the query below, "
            "then respond with ONLY a single JSON object — no prose, no markdown "
            "fences, no inline citation links — matching this exact schema:\n\n"
            '{"results": [{"title": "string", "url": "string", '
            '"description": "1-2 sentence summary"}]}\n\n'
            f"Return at most {limit} results, ordered by relevance, with absolute "
            "https:// URLs. If no usable results exist, return "
            '{"results": []}.\n\n'
            f"Query: {query}"
        )

    @classmethod
    def _extract_results(
        cls,
        response_data: Dict[str, Any],
        *,
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Pull a ``[{title, url, description, position}, ...]`` list out of a
        Responses-API reply.

        Strategy:
        1. Walk ``output[*].content[*].text`` for ``output_text`` blocks and
           try to parse the first JSON object with a ``results`` list.
        2. Fall back to message annotations (``url_citation`` entries).
        3. Last-ditch: raw ``citations`` list.
        """
        text_blocks, annotations = cls._collect_output_text(response_data)

        for block in text_blocks:
            parsed = cls._try_parse_json_results(block, limit=limit)
            if parsed:
                return parsed

        if annotations:
            joined = "\n".join(text_blocks)
            ann_results = cls._results_from_annotations(annotations, joined, limit=limit)
            if ann_results:
                return ann_results

        citations = response_data.get("citations") or []
        if isinstance(citations, list):
            return [
                {
                    "title": "",
                    "url": str(u),
                    "description": "",
                    "position": i + 1,
                }
                for i, u in enumerate(citations[:limit])
                if isinstance(u, str) and u.strip()
            ]

        return []

    @staticmethod
    def _collect_output_text(
        response_data: Dict[str, Any],
    ) -> tuple[List[str], List[Dict[str, Any]]]:
        text_blocks: List[str] = []
        annotations: List[Dict[str, Any]] = []
        output = response_data.get("output")
        if not isinstance(output, list):
            return text_blocks, annotations

        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for chunk in content:
                if not isinstance(chunk, dict) or chunk.get("type") != "output_text":
                    continue
                text = chunk.get("text")
                if isinstance(text, str) and text.strip():
                    text_blocks.append(text)
                chunk_annotations = chunk.get("annotations")
                if isinstance(chunk_annotations, list):
                    for ann in chunk_annotations:
                        if isinstance(ann, dict):
                            annotations.append(ann)
        return text_blocks, annotations

    @staticmethod
    def _try_parse_json_results(
        text: str,
        *,
        limit: int,
    ) -> Optional[List[Dict[str, Any]]]:
        candidates = [text]
        match = _JSON_BLOCK_RE.search(text)
        if match and match.group(0) != text:
            candidates.append(match.group(0))

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(parsed, dict):
                continue
            results = parsed.get("results")
            if not isinstance(results, list):
                continue
            normalized: List[Dict[str, Any]] = []
            for row in results[:limit]:
                if not isinstance(row, dict):
                    continue
                url = str(row.get("url", "")).strip()
                if not url:
                    continue
                normalized.append(
                    {
                        "title": str(row.get("title", "")).strip(),
                        "url": url,
                        "description": str(row.get("description", "")).strip(),
                        "position": len(normalized) + 1,
                    }
                )
            if normalized:
                return normalized
        return None

    @staticmethod
    def _results_from_annotations(
        annotations: List[Dict[str, Any]],
        joined_text: str,
        *,
        limit: int,
    ) -> List[Dict[str, Any]]:
        seen: set[str] = set()
        results: List[Dict[str, Any]] = []
        for ann in annotations:
            if ann.get("type") != "url_citation":
                continue
            url = str(ann.get("url", "")).strip()
            if not url or url in seen:
                continue
            seen.add(url)

            description = ""
            start = ann.get("start_index")
            end = ann.get("end_index")
            if (
                isinstance(start, int)
                and isinstance(end, int)
                and 0 <= start < end <= len(joined_text)
            ):
                window_start = max(0, start - 200)
                description = joined_text[window_start:start].strip()
                if len(description) > 200:
                    description = description[-200:].strip()

            results.append(
                {
                    "title": "",
                    "url": url,
                    "description": description,
                    "position": len(results) + 1,
                }
            )
            if len(results) >= limit:
                break
        return results


def register(ctx) -> None:
    ctx.register_web_search_provider(XAIWebSearchProvider())
