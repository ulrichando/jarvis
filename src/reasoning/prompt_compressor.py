"""Prompt compression pipeline — cuts token count before LLM API calls.

Layer 1  shrinkprompt  rule-based, zero cost, instant — for system prompts
Layer 2  LLMLingua-2   neural, lazy-loaded, ~560 MB — for long history
"""

import asyncio
import logging

log = logging.getLogger("jarvis.prompt_compressor")

_CHARS_PER_TOKEN = 4


def _tokens(texts: list[str]) -> int:
    return sum(len(t) for t in texts) // _CHARS_PER_TOKEN


class PromptCompressor:
    """Two-layer prompt compressor for LLM API calls.

    Layer 1 (always on)  — shrinkprompt on system prompts.
        Zero model, instant, good for boilerplate / instruction text.

    Layer 2 (on demand)  — LLMLingua-2 on conversation history.
        Lazy-loads ``microsoft/llmlingua-2-xlm-roberta-large-meetingbank``
        on first use. Compresses only when total history exceeds
        ``threshold_tokens``.

    Both layers degrade gracefully — if the library is not installed the
    original text is returned unchanged.
    """

    def __init__(self, threshold_tokens: int = 2000, rate: float = 0.5):
        """
        threshold_tokens
            Min tokens in history before LLMLingua-2 kicks in.
        rate
            Target compression ratio (0.5 = keep 50 % of tokens).
        """
        self._threshold = threshold_tokens
        self._rate = rate
        self._lingua: object | None = None
        self._lingua_failed = False  # don't retry after a load failure

    # ── public ────────────────────────────────────────────────────────

    async def compress_history(
        self,
        history: list[dict],
        user_input: str = "",
    ) -> list[dict]:
        """Layer 2 — LLMLingua-2 on conversation history.

        Only runs when the total token estimate of ``history`` exceeds
        ``threshold_tokens``.  Messages shorter than 200 tokens are kept
        verbatim — only the long ones are compressed.

        Returns the (possibly shortened) history list with the same
        ``role``/``content`` structure.
        """
        if not history:
            return history

        total = _tokens([str(m.get("content", "")) for m in history])
        if total <= self._threshold:
            return history

        if not self._load_lingua():
            return history

        # Run synchronous LLMLingua inference in a thread — never blocks event loop
        def _run() -> list[dict]:
            out: list[dict] = []
            for msg in history:
                content = str(msg.get("content", ""))
                role = msg.get("role", "user")
                if _tokens([content]) > 200:
                    try:
                        result = self._lingua.compress_prompt(  # type: ignore[attr-defined]
                            context=[content],
                            question=user_input or None,
                            rate=self._rate,
                            force_tokens=["\n", ".", "!", "?", ":"],
                        )
                        content = result.get("compressed_prompt", content) or content
                    except Exception as exc:
                        log.debug("LLMLingua compress failed for msg: %s", exc)
                out.append({"role": role, "content": content})
            return out

        compressed = await asyncio.to_thread(_run)

        before = total
        after = _tokens([str(m.get("content", "")) for m in compressed])
        log.debug("Prompt compression: %d → %d tokens (%.0f%%)", before, after, 100 * after / max(before, 1))
        return compressed

    # ── private ───────────────────────────────────────────────────────

    def _load_lingua(self) -> bool:
        if self._lingua is not None:
            return True
        if self._lingua_failed:
            return False
        try:
            from llmlingua import PromptCompressor as _LC  # type: ignore[import-untyped]
            log.info("Loading LLMLingua-2 model (first use — may take a moment)…")
            self._lingua = _LC(
                model_name="microsoft/llmlingua-2-xlm-roberta-large-meetingbank",
                use_llmlingua2=True,
                device_map="cpu",
            )
            log.info("LLMLingua-2 ready")
            return True
        except Exception as exc:
            log.warning("LLMLingua-2 not available: %s", exc)
            self._lingua_failed = True
            return False
