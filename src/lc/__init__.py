"""JARVIS LangChain / LangSmith adapter layer.

Does NOT replace JARVIS's agent loop, memory, or providers.
Adds where JARVIS has genuine gaps:

  tracing        — opt-in LangSmith observability (LANGCHAIN_TRACING_V2=true)
                   trace_call() context manager with proper nesting, tags, metadata
  feedback       — user feedback on runs (thumbs_up/thumbs_down, per-dimension scoring)
                   setup_feedback_configs() creates quality/accuracy/helpful dimensions
  dataset        — curate conversations into LangSmith test datasets
  evaluators     — automated evaluation, annotation queues, run sharing, project stats
  prompts        — LangSmith Prompt Hub: version/push/pull JARVIS system prompts
  model_adapter  — wrap JARVIS providers as LangChain BaseChatModel for LCEL chains
  structured     — structured output extraction via Pydantic schemas
  tools_bridge   — expose JARVIS tools as LangChain BaseTool objects
"""
