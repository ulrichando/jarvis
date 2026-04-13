"""JARVIS LangChain adapter layer.

Does NOT replace JARVIS's agent loop, memory, or providers.
Adds where JARVIS has genuine gaps:

  model_adapter  — wrap JARVIS providers as LangChain BaseLLM/BaseChatModel
  structured     — structured output extraction via Pydantic schemas
  tools_bridge   — expose JARVIS tools as LangChain BaseTool objects
  tracing        — opt-in LangSmith observability (LANGCHAIN_TRACING_V2=true)
"""
