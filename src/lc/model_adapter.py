"""Wraps JARVIS's provider system as a LangChain BaseChatModel.

This lets LangChain components (chains, structured output, LCEL, etc.) call
through JARVIS's existing multi-provider routing instead of needing
their own API keys configured separately.

Usage:
    from src.lc.model_adapter import JARVISChatModel
    llm = JARVISChatModel()
    response = llm.invoke("What is 2 + 2?")
"""

import logging
from typing import Any, Iterator, List, Optional, Sequence

log = logging.getLogger(__name__)


def _make_chat_model(
    prefer_code: bool = False,
    prefer_smart: bool = False,
    prefer_tool_calling: bool = False,
    temperature: float = 0.7,
    max_tokens: int = 2048,
):
    """Return a LangChain BaseChatModel backed by JARVIS providers.

    Tries to build a proper BaseChatModel subclass so LCEL / with_structured_output()
    work. Falls back to the thin wrapper if langchain_core is unavailable.
    """
    try:
        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.messages import AIMessage, BaseMessage
        from langchain_core.outputs import ChatGeneration, ChatResult
        import asyncio

        class _JARVISChatModel(BaseChatModel):
            prefer_code: bool = prefer_code
            prefer_smart: bool = prefer_smart
            prefer_tool_calling: bool = prefer_tool_calling
            temperature: float = temperature
            max_tokens: int = max_tokens

            @property
            def _llm_type(self) -> str:
                return "jarvis"

            def _messages_to_dicts(self, messages: List[BaseMessage]) -> List[dict]:
                role_map = {
                    "human": "user",
                    "ai": "assistant",
                    "system": "system",
                    "tool": "tool",
                }
                out = []
                for m in messages:
                    if hasattr(m, "type") and hasattr(m, "content"):
                        out.append({"role": role_map.get(m.type, "user"), "content": m.content})
                    elif isinstance(m, dict):
                        out.append(m)
                    else:
                        out.append({"role": "user", "content": str(m)})
                return out

            def _get_providers(self):
                from src.reasoning.providers import get_active_providers
                return get_active_providers(
                    prefer_code=self.prefer_code,
                    prefer_smart=self.prefer_smart,
                    prefer_tool_calling=self.prefer_tool_calling,
                )

            async def _acall_providers(self, messages: List[dict]) -> str:
                for provider in self._get_providers():
                    try:
                        resp = await provider.chat(
                            messages=messages,
                            temperature=self.temperature,
                            max_tokens=self.max_tokens,
                        )
                        if isinstance(resp, dict):
                            choices = resp.get("choices", [])
                            if choices:
                                return choices[0].get("message", {}).get("content", "")
                            return resp.get("content", "")
                        if hasattr(resp, "content"):
                            return resp.content
                        return str(resp)
                    except Exception as e:
                        log.debug("Provider %s failed: %s", provider, e)
                return "Error: all providers failed."

            def _generate(
                self,
                messages: List[BaseMessage],
                stop: Optional[List[str]] = None,
                **kwargs: Any,
            ) -> ChatResult:
                dicts = self._messages_to_dicts(messages)
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        import concurrent.futures
                        fut = asyncio.run_coroutine_threadsafe(
                            self._acall_providers(dicts), loop
                        )
                        content = fut.result(timeout=60)
                    else:
                        content = loop.run_until_complete(self._acall_providers(dicts))
                except Exception as e:
                    log.error("JARVISChatModel._generate error: %s", e)
                    content = f"Error: {e}"
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

            async def _agenerate(
                self,
                messages: List[BaseMessage],
                stop: Optional[List[str]] = None,
                **kwargs: Any,
            ) -> ChatResult:
                dicts = self._messages_to_dicts(messages)
                content = await self._acall_providers(dicts)
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

            def get_num_tokens(self, text: str) -> int:
                return len(text) // 4

        return _JARVISChatModel()

    except ImportError:
        log.debug("langchain_core not available — using thin JARVISChatModel wrapper")
        return _JARVISChatModelFallback(
            prefer_code=prefer_code,
            prefer_smart=prefer_smart,
            prefer_tool_calling=prefer_tool_calling,
            temperature=temperature,
            max_tokens=max_tokens,
        )


class _JARVISChatModelFallback:
    """Fallback when langchain_core is not installed.

    Implements invoke/ainvoke only — no LCEL or structured output support.
    """

    def __init__(
        self,
        prefer_code: bool = False,
        prefer_smart: bool = False,
        prefer_tool_calling: bool = False,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ):
        self.prefer_code = prefer_code
        self.prefer_smart = prefer_smart
        self.prefer_tool_calling = prefer_tool_calling
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._providers = None

    def _get_providers(self):
        if self._providers is None:
            from src.reasoning.providers import get_active_providers
            self._providers = get_active_providers(
                prefer_code=self.prefer_code,
                prefer_smart=self.prefer_smart,
                prefer_tool_calling=self.prefer_tool_calling,
            )
        return self._providers

    def _messages_to_dicts(self, messages) -> List[dict]:
        out = []
        for m in messages:
            if hasattr(m, "content") and hasattr(m, "type"):
                role_map = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
                out.append({"role": role_map.get(m.type, "user"), "content": m.content})
            elif isinstance(m, dict):
                out.append(m)
            elif isinstance(m, str):
                out.append({"role": "user", "content": m})
        return out

    def invoke(self, input_: Any, **kwargs) -> Any:
        import asyncio
        if isinstance(input_, str):
            messages = [{"role": "user", "content": input_}]
        elif isinstance(input_, list):
            messages = self._messages_to_dicts(input_)
        else:
            messages = self._messages_to_dicts([input_])

        async def _call():
            for provider in self._get_providers():
                try:
                    return await provider.chat(
                        messages=messages,
                        temperature=self.temperature,
                        max_tokens=self.max_tokens,
                    )
                except Exception as e:
                    log.debug("Provider %s failed: %s", provider, e)
            return {"content": "Error: all providers failed."}

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                fut = asyncio.run_coroutine_threadsafe(_call(), loop)
                return fut.result(timeout=60)
            return loop.run_until_complete(_call())
        except Exception as e:
            log.error("JARVISChatModel.invoke error: %s", e)
            return {"content": f"Error: {e}"}

    async def ainvoke(self, input_: Any, **kwargs) -> Any:
        if isinstance(input_, str):
            messages = [{"role": "user", "content": input_}]
        elif isinstance(input_, list):
            messages = self._messages_to_dicts(input_)
        else:
            messages = self._messages_to_dicts([input_])

        for provider in self._get_providers():
            try:
                return await provider.chat(
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
            except Exception as e:
                log.debug("Provider %s failed: %s", provider, e)
        return {"content": "Error: all providers failed."}

    def get_num_tokens(self, text: str) -> int:
        return len(text) // 4


# Public constructor — always use this instead of instantiating directly
def JARVISChatModel(
    prefer_code: bool = False,
    prefer_smart: bool = False,
    prefer_tool_calling: bool = False,
    temperature: float = 0.7,
    max_tokens: int = 2048,
):
    """Factory that returns the best available LangChain model wrapper."""
    return _make_chat_model(
        prefer_code=prefer_code,
        prefer_smart=prefer_smart,
        prefer_tool_calling=prefer_tool_calling,
        temperature=temperature,
        max_tokens=max_tokens,
    )
