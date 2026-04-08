"""JARVIS Persona Agent Factory.

Dynamically builds AgentConfig objects from PersonalityProfile specs.
The factory is the bridge between the 125-persona system in persona.py
and the dispatchable agent system in agents.py.

How it works:
────────────────────────────────────────────────────────────────────────
1. Static lookup   — factory.build("red_team") → AgentConfig
2. Persona mapping — factory.from_persona("hacker") → AgentConfig (red_team)
3. Awareness-based — factory.from_awareness("commanding", "frustrated") → ghost
4. Dynamic detect  — factory.detect_and_build(brain_state) → AgentConfig | None
5. List all        — factory.list_profiles() → list of profile dicts

Prompt assembly (per profile):
────────────────────────────────────────────────────────────────────────
  [preamble]
  ══════════════════
  [persona_1 prompt]   ← primary persona goes first
  [persona_2 prompt]   ← remaining personas follow
  ...
  ══════════════════
  [trait_overrides]
  ══════════════════
  BEHAVIORAL CONSTRAINTS (verbosity, caution, tone)

Agent types registered by this factory are transparently accessible via
dispatch(agent_type="red_team", ...) just like built-in scout/worker/etc.
"""

import logging
from functools import lru_cache
from typing import TYPE_CHECKING

from src.agent.personality_profiles import (
    PROFILES,
    PERSONA_TO_PROFILE,
    detect_profile_from_awareness,
    ProfileSpec,
)

if TYPE_CHECKING:
    from src.agent.agents import AgentConfig

log = logging.getLogger("jarvis.agent.factory")


# ── Prompt Assembly ───────────────────────────────────────────────────────────

def _get_persona_prompt(key: str) -> str:
    """Fetch a persona's prompt text from the PERSONAS dict."""
    try:
        from src.reasoning.persona import PERSONAS
        p = PERSONAS.get(key, {})
        return p.get("prompt", "").strip()
    except Exception as exc:
        log.debug("Could not load persona '%s': %s", key, exc)
        return ""


def _get_persona_name(key: str) -> str:
    """Fetch a persona's display name."""
    try:
        from src.reasoning.persona import PERSONAS
        return PERSONAS.get(key, {}).get("name", key.upper())
    except Exception:
        return key.upper()


def _assemble_system_prompt(spec: ProfileSpec) -> str:
    """Build the full system prompt for a profile agent.

    Structure:
        [preamble]
        ════ EXPERTISE ════
        [primary persona prompt]
        [additional persona prompts...]
        ════ BEHAVIORAL CONSTRAINTS ════
        [verbosity / caution / tone rules]
        [trait_overrides]
    """
    sections: list[str] = []

    # 1. Preamble (agent identity + mission)
    if spec.preamble:
        sections.append(spec.preamble)

    # 2. Expertise stack — primary persona first, then supplemental
    expertise_parts: list[str] = []
    ordered_keys = [spec.primary_persona] + [
        k for k in spec.persona_keys if k != spec.primary_persona
    ]
    for key in ordered_keys:
        prompt_text = _get_persona_prompt(key)
        if prompt_text:
            expertise_parts.append(prompt_text)

    if expertise_parts:
        sections.append("═══ EXPERTISE ═══\n" + "\n\n".join(expertise_parts))

    # 3. Behavioral constraints
    behavior_rules: list[str] = []

    if spec.verbosity == "minimal":
        behavior_rules.append(
            "OUTPUT: 1-5 words maximum unless code is required. "
            "No preamble, no explanation, no confirmation."
        )
    elif spec.verbosity == "verbose":
        behavior_rules.append(
            "OUTPUT: Be thorough. Explain reasoning. Use examples. "
            "Structure with headers when output is long."
        )
    else:
        behavior_rules.append("OUTPUT: Concise but complete. Lead with the answer.")

    if spec.caution_level == "high":
        behavior_rules.append(
            "CAUTION: Flag risks explicitly. "
            "Never take irreversible actions without warning. "
            "Prefer read before write. Ask when genuinely ambiguous."
        )
    elif spec.caution_level == "low":
        behavior_rules.append(
            "SPEED: Bias toward action. Ask only when truly blocked."
        )

    if spec.bash_readonly:
        behavior_rules.append(
            "BASH POLICY: READ-ONLY. "
            "Use: ls, cat, find, grep, head, tail, wc, stat, ps, df, du, curl (GET only). "
            "NEVER: rm, mv, cp, chmod, apt, pip, systemctl, >, >>, tee, kill, git commit/push."
        )

    if spec.tone_affinity and spec.tone_affinity != "default":
        try:
            from src.reasoning.persona import TONE_OVERRIDES
            tone_text = TONE_OVERRIDES.get(spec.tone_affinity, "")
            if tone_text:
                behavior_rules.append(f"TONE: {tone_text}")
        except Exception:
            pass

    if behavior_rules:
        sections.append("═══ BEHAVIORAL CONSTRAINTS ═══\n" + "\n".join(behavior_rules))

    # 4. Trait overrides
    if spec.trait_overrides:
        sections.append("═══ CORE PRINCIPLES ═══\n" + spec.trait_overrides)

    return "\n\n".join(sections)


# ── Factory Class ─────────────────────────────────────────────────────────────

class PersonaAgentFactory:
    """Builds and caches AgentConfig objects from ProfileSpec definitions.

    Usage:
        factory = PersonaAgentFactory()

        # By profile name (used in dispatch)
        cfg = factory.build("red_team")

        # From active persona key
        cfg = factory.from_persona("hacker")     # → red_team config

        # From awareness state
        cfg = factory.from_awareness("commanding", "frustrated")  # → ghost

        # Detect and build from brain state dict
        cfg = factory.detect_and_build({"active_persona": "soc",
                                         "user_intent": "exploring"})

        # Register all profiles with the agent system
        factory.register_all()
    """

    def __init__(self):
        self._cache: dict[str, "AgentConfig"] = {}

    # ── Core builders ────────────────────────────────────────────────────

    def build(self, profile_name: str) -> "AgentConfig | None":
        """Build AgentConfig for a profile (cached).

        Returns None if profile_name is not found.
        """
        if profile_name in self._cache:
            return self._cache[profile_name]

        spec = PROFILES.get(profile_name)
        if spec is None:
            log.debug("PersonaAgentFactory: unknown profile '%s'", profile_name)
            return None

        config = self._spec_to_config(spec)
        self._cache[profile_name] = config
        log.debug("PersonaAgentFactory: built '%s' (%d tools, %d personas)",
                  profile_name, len(config.allowed_tools), len(spec.persona_keys))
        return config

    def build_for_task(
        self,
        profile_name: str,
        task_context: str = "",
    ) -> "AgentConfig | None":
        """Build AgentConfig with optional task-context appended to the prompt.

        Use this when you want to give the agent specific task awareness
        without polluting the cached base config.
        """
        base = self.build(profile_name)
        if base is None:
            return None
        if not task_context:
            return base

        from src.agent.agents import AgentConfig
        return AgentConfig(
            name=base.name,
            description=base.description,
            system_prompt=base.system_prompt + f"\n\n═══ TASK CONTEXT ═══\n{task_context}",
            allowed_tools=base.allowed_tools,
            max_iterations=base.max_iterations,
            bash_readonly=base.bash_readonly,
        )

    # ── Detection ────────────────────────────────────────────────────────

    def from_persona(self, persona_key: str) -> "AgentConfig | None":
        """Map a persona key (e.g. 'hacker') to its parent profile agent."""
        profile_name = PERSONA_TO_PROFILE.get(persona_key)
        if not profile_name:
            log.debug("No profile owns persona '%s'", persona_key)
            return None
        return self.build(profile_name)

    def from_awareness(
        self,
        user_intent: str,
        user_energy: str,
    ) -> "AgentConfig | None":
        """Build agent from awareness state (intent + energy)."""
        profile_name = detect_profile_from_awareness(user_intent, user_energy)
        if not profile_name:
            return None
        return self.build(profile_name)

    def detect_and_build(self, brain_state: dict) -> "AgentConfig | None":
        """Primary detection entry point given a brain state snapshot.

        brain_state keys (all optional):
            active_persona: str    — currently active persona key
            user_intent: str       — from awareness
            user_energy: str       — from awareness
            profile_override: str  — explicit profile name (highest priority)

        Priority: profile_override > active_persona > awareness state
        """
        # Explicit override
        override = brain_state.get("profile_override")
        if override and override in PROFILES:
            return self.build(override)

        # Active persona → profile
        persona = brain_state.get("active_persona", "")
        if persona and persona != "default":
            cfg = self.from_persona(persona)
            if cfg:
                return cfg

        # Awareness fallback
        intent  = brain_state.get("user_intent", "unknown")
        energy  = brain_state.get("user_energy", "neutral")
        return self.from_awareness(intent, energy)

    def profile_for_persona(self, persona_key: str) -> ProfileSpec | None:
        """Return the ProfileSpec that owns a given persona key."""
        profile_name = PERSONA_TO_PROFILE.get(persona_key)
        return PROFILES.get(profile_name) if profile_name else None

    def profile_name_for_persona(self, persona_key: str) -> str | None:
        """Return just the profile name for a persona key."""
        return PERSONA_TO_PROFILE.get(persona_key)

    # ── Registry integration ──────────────────────────────────────────────

    def register_all(self) -> int:
        """Pre-build all profiles and inject them into AGENT_CONFIGS.

        After calling this, dispatch(agent_type="red_team") works without
        any file-based custom agents.  Returns count of registered profiles.
        """
        try:
            from src.agent.agents import AGENT_CONFIGS
        except Exception as exc:
            log.warning("Failed to import AGENT_CONFIGS: %s", exc)
            return 0

        count = 0
        for name in PROFILES:
            if name in AGENT_CONFIGS:
                continue  # never overwrite built-ins (scout/worker/planner/verifier)
            cfg = self.build(name)
            if cfg:
                AGENT_CONFIGS[name] = cfg
                count += 1

        log.info("PersonaAgentFactory: registered %d profile agents", count)
        return count

    # ── Introspection ─────────────────────────────────────────────────────

    def list_profiles(self) -> list[dict]:
        """Return serialisable summaries of all profiles."""
        result = []
        for name, spec in PROFILES.items():
            result.append({
                "name":           name,
                "display_name":   spec.display_name,
                "domain":         spec.domain,
                "description":    spec.description,
                "persona_count":  len(spec.persona_keys),
                "personas":       spec.persona_keys,
                "tools":          spec.allowed_tools,
                "bash_readonly":  spec.bash_readonly,
                "tone":           spec.tone_affinity,
                "verbosity":      spec.verbosity,
                "caution":        spec.caution_level,
                "max_iterations": spec.max_iterations,
                "triggers":       spec.trigger_keywords[:5],
            })
        return result

    def profile_summary(self, profile_name: str) -> dict | None:
        """Return detailed summary of one profile."""
        spec = PROFILES.get(profile_name)
        if not spec:
            return None
        return {
            "name":            spec.name,
            "display_name":    spec.display_name,
            "domain":          spec.domain,
            "description":     spec.description,
            "persona_keys":    spec.persona_keys,
            "primary_persona": spec.primary_persona,
            "allowed_tools":   spec.allowed_tools,
            "bash_readonly":   spec.bash_readonly,
            "tone_affinity":   spec.tone_affinity,
            "verbosity":       spec.verbosity,
            "caution_level":   spec.caution_level,
            "max_iterations":  spec.max_iterations,
            "trigger_keywords": spec.trigger_keywords,
            "intent_affinity": spec.intent_affinity,
            "energy_affinity": spec.energy_affinity,
            "persona_to_profile_map": {
                pk: profile_name for pk in spec.persona_keys_owned
            },
        }

    def detect_from_text(self, text: str) -> str | None:
        """Detect a profile name from free text (keyword matching).

        Returns the profile name with the most keyword matches, or None.
        """
        text_lower = text.lower()
        scores: dict[str, int] = {}
        for name, spec in PROFILES.items():
            score = sum(1 for kw in spec.trigger_keywords if kw in text_lower)
            if score > 0:
                scores[name] = score
        if not scores:
            return None
        return max(scores, key=lambda n: scores[n])

    # ── Internal ──────────────────────────────────────────────────────────

    def _spec_to_config(self, spec: ProfileSpec) -> "AgentConfig":
        from src.agent.agents import AgentConfig
        system_prompt = _assemble_system_prompt(spec)
        return AgentConfig(
            name=spec.name,
            description=spec.description,
            system_prompt=system_prompt,
            allowed_tools=spec.allowed_tools,
            max_iterations=spec.max_iterations,
            bash_readonly=spec.bash_readonly,
        )

    def invalidate(self, profile_name: str | None = None):
        """Invalidate cache. Pass None to clear all."""
        if profile_name:
            self._cache.pop(profile_name, None)
        else:
            self._cache.clear()


# ── Module-level singleton ────────────────────────────────────────────────────

_factory: PersonaAgentFactory | None = None


def get_factory() -> PersonaAgentFactory:
    """Return the module-level factory singleton."""
    global _factory
    if _factory is None:
        _factory = PersonaAgentFactory()
    return _factory


# ── Convenience functions (mirror factory methods at module level) ─────────────

def build_profile_agent(profile_name: str) -> "AgentConfig | None":
    """Build an AgentConfig for the named personality profile."""
    return get_factory().build(profile_name)


def agent_from_persona(persona_key: str) -> "AgentConfig | None":
    """Get the profile agent for an active persona key."""
    return get_factory().from_persona(persona_key)


def agent_from_awareness(user_intent: str, user_energy: str) -> "AgentConfig | None":
    """Get a profile agent based on user intent + energy."""
    return get_factory().from_awareness(user_intent, user_energy)


def detect_and_build(brain_state: dict) -> "AgentConfig | None":
    """Full detection pipeline: override > persona > awareness."""
    return get_factory().detect_and_build(brain_state)


def list_profile_agents() -> list[dict]:
    """List all available personality profile agents."""
    return get_factory().list_profiles()


def register_profile_agents() -> int:
    """Register all profile agents into AGENT_CONFIGS. Call at startup."""
    return get_factory().register_all()
