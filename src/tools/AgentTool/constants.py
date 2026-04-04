AGENT_TOOL_NAME = "Agent"
# Legacy wire name for backward compat (permission rules, hooks, resumed sessions)
LEGACY_AGENT_TOOL_NAME = "Task"
VERIFICATION_AGENT_TYPE = "verification"

# Built-in agents that run once and return a report -- the parent never
# SendMessages back to continue them. Skip the agentId/SendMessage/usage
# trailer for these to save tokens.
ONE_SHOT_BUILTIN_AGENT_TYPES: frozenset[str] = frozenset(["Explore", "Plan"])
