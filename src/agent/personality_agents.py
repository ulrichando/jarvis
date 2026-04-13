"""Personality-Driven Agent Factory — two-layer agent system.

LAYER 1 — Per-persona agents (94 total across 8 domains)
    resolve_personality_agent("hacker")  →  AgentConfig tuned for that persona

LAYER 2 — Archetype agents (11 composite agents)
    resolve_archetype_agent("red_team")  →  AgentConfig merging 9 offensive
                                             security persona prompts

Resolution order in agents.py:
    built-ins → archetypes → per-persona → custom file agents

The two layers are independent:
  - Domain profiles configure HOW individual personas run (tools, bash policy)
  - Archetypes define WHICH personas to merge into one composite agent
  - A persona key can appear in one domain profile AND one archetype (different paths)
  - Within the archetype system: each persona appears in exactly ONE archetype
    (enforced at import time by _validate_archetype_coverage)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("jarvis.agent.personality")


# ═══════════════════════════════════════════════════════════════════════
# LAYER 1 — Domain profiles (per-persona agent configuration)
# ═══════════════════════════════════════════════════════════════════════

DOMAIN_BEHAVIOR        = "behavior"
DOMAIN_DEVOPS          = "devops"
DOMAIN_ENGINEERING     = "engineering"
DOMAIN_SEC_OFFENSIVE   = "security-offensive"
DOMAIN_SEC_DEFENSIVE   = "security-defensive"
DOMAIN_LEGAL           = "legal"
DOMAIN_UX_DESIGN       = "ux-design"
DOMAIN_UI_DESIGN       = "ui-design"
DOMAIN_FINANCE         = "finance"


@dataclass
class PersonalityProfile:
    """Execution context for a domain group of personas."""
    domain: str
    description: str
    persona_keys: list[str]
    allowed_tools: list[str]
    bash_readonly: bool
    max_iterations: int
    domain_prefix: str


PERSONALITY_PROFILES: dict[str, PersonalityProfile] = {

    DOMAIN_BEHAVIOR: PersonalityProfile(
        domain=DOMAIN_BEHAVIOR,
        description="Behavioral modes — stealth, mentoring, creative brainstorming",
        persona_keys=["ghost", "mentor", "creative"],
        allowed_tools=[
            "bash", "read_file", "write_file", "edit_file",
            "search_files", "web_search", "web_fetch", "think",
        ],
        bash_readonly=False,
        max_iterations=20,
        domain_prefix=(
            "DOMAIN: Behavioral Mode\n"
            "EXECUTION POLICY:\n"
            "  - Adapt output style to the active persona.\n"
            "  - Ghost: silent, minimal, pure action — no narration.\n"
            "  - Mentor: patient, first-principles, guided — never skip steps.\n"
            "  - Creative: divergent, unexpected — quantity over polish in ideation."
        ),
    ),

    DOMAIN_DEVOPS: PersonalityProfile(
        domain=DOMAIN_DEVOPS,
        description="Infrastructure, systems, cloud, databases, and platform engineering",
        persona_keys=[
            "sysadmin", "network", "cloud", "devops",
            "dba", "helpdesk", "linux",
        ],
        allowed_tools=[
            "bash", "read_file", "write_file", "edit_file",
            "search_files", "web_search", "web_fetch", "think",
        ],
        bash_readonly=False,
        max_iterations=25,
        domain_prefix=(
            "DOMAIN: DevOps / Systems / Infrastructure\n"
            "EXECUTION POLICY:\n"
            "  - Full bash access. Prefer idempotent, repeatable operations.\n"
            "  - Always show a rollback command beside every destructive op.\n"
            "  - Warn before: package installs, service restarts, config changes.\n"
            "  - Provide real commands (bash/PowerShell) — no pseudoconfig.\n"
            "  - For config files: read before writing, back up if modifying in-place."
        ),
    ),

    DOMAIN_ENGINEERING: PersonalityProfile(
        domain=DOMAIN_ENGINEERING,
        description="Software development — all languages, stacks, and quality concerns",
        persona_keys=[
            "backend", "mobile", "ai", "data",
            "architect", "itpm",
            "vue", "angular", "golang", "rust", "java", "php",
            "nosql", "web3", "qa", "review", "perf",
        ],
        allowed_tools=[
            "bash", "read_file", "write_file", "edit_file",
            "search_files", "web_search", "web_fetch", "think",
        ],
        bash_readonly=False,
        max_iterations=30,
        domain_prefix=(
            "DOMAIN: Software Engineering\n"
            "EXECUTION POLICY:\n"
            "  - Read files before editing them.\n"
            "  - Run tests after code changes (pytest / npm test / cargo test / etc.).\n"
            "  - Production-grade output only — no placeholder stubs, no TODOs in code.\n"
            "  - Check for existing patterns and components before introducing new abstractions.\n"
            "  - Flag N+1 risks, missing error handling, and security issues inline."
        ),
    ),

    DOMAIN_SEC_OFFENSIVE: PersonalityProfile(
        domain=DOMAIN_SEC_OFFENSIVE,
        description="Authorized offensive security — pentesting, exploitation, red team, recon",
        persona_keys=[
            "hacker", "recon", "webapp", "ad",
            "privesc", "wireless", "exploitdev",
            "redteam", "pentester",
        ],
        allowed_tools=[
            "bash", "read_file", "search_files",
            "web_search", "web_fetch", "think",
        ],
        bash_readonly=False,
        max_iterations=20,
        domain_prefix=(
            "DOMAIN: Offensive Security\n"
            "SCOPE: Authorized testing only. Document all findings with evidence.\n"
            "EXECUTION POLICY:\n"
            "  - Real tools, real techniques, exact commands.\n"
            "  - Every offensive technique MUST include: ATTACK + DETECTION + REMEDIATION.\n"
            "  - Label MITRE ATT&CK IDs for all TTPs.\n"
            "  - WARNING label on techniques that risk availability impact.\n"
            "  - Read hacking-reference.md before answering complex technique questions.\n"
            "  - No write_file or edit_file — findings go to stdout only."
        ),
    ),

    DOMAIN_SEC_DEFENSIVE: PersonalityProfile(
        domain=DOMAIN_SEC_DEFENSIVE,
        description="Security monitoring, detection engineering, IR, forensics, threat hunting, GRC",
        persona_keys=[
            "security", "forensics", "soc", "ir",
            "threathunt", "secarch", "vulnmgmt",
            "cloudsec", "iam", "purple", "threatintel",
            "devsecops", "grc",
        ],
        allowed_tools=[
            "read_file", "search_files", "bash",
            "web_search", "web_fetch", "think",
        ],
        bash_readonly=True,
        max_iterations=20,
        domain_prefix=(
            "DOMAIN: Defensive Security\n"
            "EXECUTION POLICY:\n"
            "  - Read-only bash — preserve evidence chain of custody.\n"
            "  - Document every finding: timestamp, source artifact, raw value.\n"
            "  - Map all findings to MITRE ATT&CK where applicable.\n"
            "  - For detections: provide DETECT + HUNT + DEFEND.\n"
            "  - Read cybersec-defense-reference.md for playbook details."
        ),
    ),

    DOMAIN_LEGAL: PersonalityProfile(
        domain=DOMAIN_LEGAL,
        description="Legal analysis — contracts, litigation, IP, corporate, privacy, international",
        persona_keys=[
            "adr", "litigator", "criminal", "corporate",
            "contract", "startup", "ip", "techlaw",
            "employment", "immigration", "realestate", "international",
        ],
        allowed_tools=[
            "read_file", "search_files",
            "web_search", "web_fetch", "think",
        ],
        bash_readonly=True,
        max_iterations=15,
        domain_prefix=(
            "DOMAIN: Legal\n"
            "EXECUTION POLICY:\n"
            "  - Analysis and drafting only — no code execution.\n"
            "  - Always identify jurisdiction before advising.\n"
            "  - Use IRAC format (Issue / Rule / Application / Conclusion) when appropriate.\n"
            "  - Cite statutes, rules, and case law.\n"
            "  - Flag deadlines with WARNING: prefix.\n"
            "  - Draft complete, professional-grade clauses — no [PLACEHOLDER] gaps.\n"
            "  - Distinguish between legal information (available) and legal advice (requires counsel)."
        ),
    ),

    DOMAIN_UX_DESIGN: PersonalityProfile(
        domain=DOMAIN_UX_DESIGN,
        description="UX research, interaction design, visual design, design systems, and accessibility",
        persona_keys=[
            "uxr", "ia", "uxstrat", "journey",
            "motion", "brand", "gameux", "arvr", "critique",
        ],
        allowed_tools=[
            "read_file", "search_files",
            "web_search", "web_fetch", "think",
        ],
        bash_readonly=True,
        max_iterations=15,
        domain_prefix=(
            "DOMAIN: UX & Design\n"
            "EXECUTION POLICY:\n"
            "  - Research and design output only — no code execution.\n"
            "  - Ground recommendations in user needs and validated patterns.\n"
            "  - Reference established frameworks: Nielsen heuristics, WCAG 2.2, Material/HIG where relevant.\n"
            "  - For critique: separate observations (what) from inferences (why) from recommendations (how).\n"
            "  - Deliverables: wireframe descriptions, user flows, copy, research plans, design specs — no vague advice."
        ),
    ),

    DOMAIN_UI_DESIGN: PersonalityProfile(
        domain=DOMAIN_UI_DESIGN,
        description="UI implementation — component libraries, design systems, Figma-to-code, accessibility",
        persona_keys=[
            "frontend", "visual", "wireframe",
            "designsystem", "a11y", "mobileux", "webux", "uxcopy",
        ],
        allowed_tools=[
            "read_file", "write_file", "edit_file",
            "search_files", "web_search", "web_fetch", "think",
        ],
        bash_readonly=False,
        max_iterations=20,
        domain_prefix=(
            "DOMAIN: UI Design & Implementation\n"
            "EXECUTION POLICY:\n"
            "  - Bridge between design and code — output must be implementable.\n"
            "  - Prefer existing design system tokens over raw hex/px values.\n"
            "  - Always check WCAG 2.2 contrast and keyboard navigation.\n"
            "  - For component work: include all states (default, hover, focus, disabled, error).\n"
            "  - Wireframes as structured text/ASCII; visual specs as precise CSS/Tailwind."
        ),
    ),

    DOMAIN_FINANCE: PersonalityProfile(
        domain=DOMAIN_FINANCE,
        description="Personal finance, investing, tax, corporate finance, M&A, and macroeconomics",
        persona_keys=[
            "personalfin", "retirement", "tax",
            "realestatefin", "equity", "etf", "crypto",
            "options", "cfo", "vcfin", "ma", "acct",
            "macro", "riskfin", "intlfin", "wealth",
        ],
        allowed_tools=[
            "read_file", "search_files",
            "web_search", "web_fetch", "think",
        ],
        bash_readonly=True,
        max_iterations=15,
        domain_prefix=(
            "DOMAIN: Finance\n"
            "EXECUTION POLICY:\n"
            "  - Analysis and modeling only — no code execution.\n"
            "  - Always state assumptions explicitly (rate, horizon, tax treatment, jurisdiction).\n"
            "  - Show calculations step-by-step; use tables for comparisons.\n"
            "  - Flag: liquidity risk, concentration risk, regulatory constraints, and tax implications.\n"
            "  - Distinguish financial information (available) from personalized investment advice (requires licensed advisor).\n"
            "  - Use real market data from web_fetch/web_search when current figures are needed."
        ),
    ),
}

# Reverse lookup: persona_key → domain
_PERSONA_TO_DOMAIN: dict[str, str] = {}
for _domain, _profile in PERSONALITY_PROFILES.items():
    for _key in _profile.persona_keys:
        _PERSONA_TO_DOMAIN[_key] = _domain


# ═══════════════════════════════════════════════════════════════════════
# LAYER 2 — Archetype agents (composite, one-name-many-personas)
# ═══════════════════════════════════════════════════════════════════════

# Named tool presets for archetype declarations
TOOL_PRESETS: dict[str, list[str]] = {
    # Full access — bash + file ops + web
    "full":       ["bash", "read_file", "write_file", "edit_file",
                   "search_files", "web_search", "web_fetch", "think"],
    "executor":   ["bash", "read_file", "write_file", "edit_file",
                   "search_files", "web_search", "web_fetch", "think"],
    "worker":     ["bash", "read_file", "write_file", "edit_file",
                   "search_files", "web_search", "web_fetch", "think"],
    # Write but no bash
    "writer":     ["read_file", "write_file", "edit_file",
                   "search_files", "web_search", "web_fetch", "think"],
    # Read + readonly bash + web (bash_readonly=True required alongside)
    "analyst":    ["read_file", "search_files", "bash",
                   "web_search", "web_fetch", "think"],
    # Read + web only, no bash
    "researcher": ["read_file", "search_files",
                   "web_search", "web_fetch", "think"],
}


@dataclass
class ArchetypeSpec:
    """Defines a composite agent merging N persona prompts into one AgentConfig."""
    name: str
    description: str
    persona_keys: list[str]   # merged into composite system prompt
    tool_preset: str          # key into TOOL_PRESETS
    bash_readonly: bool
    max_iterations: int


# Each persona appears in EXACTLY ONE archetype — enforced by _validate_archetype_coverage()
ARCHETYPES: dict[str, ArchetypeSpec] = {

    "ghost": ArchetypeSpec(
        name="ghost",
        description="Stealth executor — silent, minimal output, pure action",
        persona_keys=["ghost"],
        tool_preset="executor",
        bash_readonly=False,
        max_iterations=20,
    ),

    "mentor": ArchetypeSpec(
        name="mentor",
        description="Patient teacher — first principles, analogies, guided learning",
        persona_keys=["mentor"],
        tool_preset="researcher",
        bash_readonly=True,
        max_iterations=15,
    ),

    "creative": ArchetypeSpec(
        name="creative",
        description="Brainstorming partner — wild ideas, unexpected connections",
        persona_keys=["creative"],
        tool_preset="writer",
        bash_readonly=False,
        max_iterations=15,
    ),

    "red_team": ArchetypeSpec(
        name="red_team",
        description="Offensive security composite — 9 attack specialists",
        persona_keys=[
            "hacker", "recon", "webapp", "ad",
            "privesc", "wireless", "exploitdev",
            "redteam", "pentester",
        ],
        tool_preset="full",
        bash_readonly=False,
        max_iterations=20,
    ),

    "blue_team": ArchetypeSpec(
        name="blue_team",
        description="Defensive security composite — 13 detection & response specialists",
        persona_keys=[
            "security", "forensics", "soc", "ir",
            "threathunt", "secarch", "vulnmgmt",
            "cloudsec", "iam", "purple", "threatintel",
            "devsecops", "grc",
        ],
        tool_preset="analyst",
        bash_readonly=True,
        max_iterations=20,
    ),

    "engineer": ArchetypeSpec(
        name="engineer",
        description="Software & platform engineering composite — 13 specialists",
        persona_keys=[
            "sysadmin", "network", "cloud", "devops",
            "backend", "mobile", "dba",
            "ai", "linux", "architect", "qa",
            "itpm", "helpdesk",
        ],
        tool_preset="full",
        bash_readonly=False,
        max_iterations=30,
    ),

    "language_specialist": ArchetypeSpec(
        name="language_specialist",
        description="Language-specific engineering — 8 language experts",
        persona_keys=[
            "golang", "rust", "java", "php",
            "vue", "angular", "web3", "nosql",
        ],
        tool_preset="worker",
        bash_readonly=False,
        max_iterations=30,
    ),

    "analyst": ArchetypeSpec(
        name="analyst",
        description="Analysis composite — data engineering, performance, code review",
        persona_keys=["data", "perf", "review"],
        tool_preset="analyst",
        bash_readonly=True,
        max_iterations=15,
    ),

    "legal": ArchetypeSpec(
        name="legal",
        description="Legal composite — 12 practice area specialists",
        persona_keys=[
            "adr", "litigator", "criminal", "corporate",
            "contract", "startup", "ip", "techlaw",
            "employment", "immigration", "realestate", "international",
        ],
        tool_preset="researcher",
        bash_readonly=True,
        max_iterations=15,
    ),

    "financial": ArchetypeSpec(
        name="financial",
        description="Finance composite — 16 financial domain specialists",
        persona_keys=[
            "cfo", "equity", "crypto", "personalfin",
            "tax", "macro", "options", "vcfin",
            "ma", "acct", "riskfin", "wealth",
            "retirement", "realestatefin", "etf", "intlfin",
        ],
        tool_preset="analyst",
        bash_readonly=True,
        max_iterations=15,
    ),

    "designer": ArchetypeSpec(
        name="designer",
        description="UX strategy & research composite — 9 research and experience specialists",
        persona_keys=[
            "uxr", "ia", "uxstrat", "journey",
            "motion", "brand", "gameux", "arvr", "critique",
        ],
        tool_preset="writer",
        bash_readonly=False,
        max_iterations=15,
    ),

    "ui-design": ArchetypeSpec(
        name="ui-design",
        description="UI implementation composite — frontend code, components, design systems, accessibility",
        persona_keys=[
            "frontend", "visual", "wireframe",
            "designsystem", "a11y", "mobileux", "webux", "uxcopy",
        ],
        tool_preset="writer",
        bash_readonly=False,
        max_iterations=20,
    ),
}


def _validate_archetype_coverage() -> list[str]:
    """Check that every persona appears in exactly one archetype.

    Returns a list of error strings (empty = valid).
    """
    from src.reasoning.persona import PERSONAS

    seen: dict[str, str] = {}       # persona_key → archetype_name
    errors: list[str] = []

    for archetype_name, spec in ARCHETYPES.items():
        for key in spec.persona_keys:
            if key in seen:
                errors.append(
                    f"Duplicate: '{key}' appears in both "
                    f"'{seen[key]}' and '{archetype_name}'"
                )
            else:
                seen[key] = archetype_name
            if key not in PERSONAS:
                errors.append(
                    f"Unknown persona: '{key}' in archetype '{archetype_name}'"
                )

    # Check every non-default persona is covered
    all_persona_keys = [k for k in PERSONAS if k != "default"]
    uncovered = [k for k in all_persona_keys if k not in seen]
    if uncovered:
        errors.append(f"Personas not in any archetype: {uncovered}")

    return errors


# Run validation at import time — loud failure if archetypes are misconfigured
try:
    _archetype_errors = _validate_archetype_coverage()
    if _archetype_errors:
        for _err in _archetype_errors:
            log.error("ARCHETYPE VALIDATION: %s", _err)
except Exception:
    pass  # PERSONAS not yet importable in test isolation; skip


# ═══════════════════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════════════════

class PersonalityAgentFactory:
    """Builds AgentConfig instances for both layers.

    Layer 1 — create_agent(persona_key):
        Combines domain execution policy + persona expertise prompt.

    Layer 2 — create_archetype_agent(archetype_name):
        Merges N persona prompts into one composite AgentConfig.
    """

    # ── Layer 1: per-persona ──────────────────────────────────────────

    def detect_profile(self, persona_key: str) -> Optional[PersonalityProfile]:
        domain = _PERSONA_TO_DOMAIN.get(persona_key)
        return PERSONALITY_PROFILES.get(domain) if domain else None

    def create_agent(self, persona_key: str) -> Optional["AgentConfig"]:
        """Build an AgentConfig for a single persona key."""
        from src.agent.agents import AgentConfig
        from src.reasoning.persona import PERSONAS

        profile = self.detect_profile(persona_key)
        if not profile:
            return None
        persona = PERSONAS.get(persona_key) or {
            "description": f"{persona_key} specialist",
            "prompt": f"You are a {persona_key} specialist operating in the {profile.domain} domain.",
        }

        parts = [profile.domain_prefix]
        if persona.get("prompt"):
            parts.append(persona["prompt"])

        return AgentConfig(
            name=persona_key,
            description=persona.get("description", ""),
            system_prompt="\n\n".join(parts),
            allowed_tools=list(profile.allowed_tools),
            max_iterations=profile.max_iterations,
            bash_readonly=profile.bash_readonly,
        )

    def generate_all(self) -> dict[str, "AgentConfig"]:
        """Generate AgentConfig for every registered persona key."""
        agents = {}
        for key in _PERSONA_TO_DOMAIN:
            config = self.create_agent(key)
            if config:
                agents[key] = config
        log.info("Generated %d personality agents across %d domains",
                 len(agents), len(PERSONALITY_PROFILES))
        return agents

    def list_profiles(self) -> list[dict]:
        return [
            {
                "domain": p.domain,
                "description": p.description,
                "persona_count": len(p.persona_keys),
                "personas": p.persona_keys,
                "tools": p.allowed_tools,
                "bash_readonly": p.bash_readonly,
                "max_iterations": p.max_iterations,
            }
            for p in PERSONALITY_PROFILES.values()
        ]

    def list_persona_names(self) -> list[str]:
        return list(_PERSONA_TO_DOMAIN.keys())

    # ── Layer 2: archetypes ───────────────────────────────────────────

    def create_archetype_agent(self, archetype_name: str) -> Optional["AgentConfig"]:
        """Build a composite AgentConfig from an archetype spec.

        The system prompt merges all persona prompts under a composite header.
        Single-persona archetypes (ghost, mentor, creative) skip the header.
        """
        from src.agent.agents import AgentConfig
        from src.reasoning.persona import PERSONAS

        spec = ARCHETYPES.get(archetype_name)
        if not spec:
            return None

        tools = TOOL_PRESETS.get(spec.tool_preset, TOOL_PRESETS["full"])

        if len(spec.persona_keys) == 1:
            # Single-persona archetype: use persona prompt directly
            persona = PERSONAS.get(spec.persona_keys[0], {})
            system_prompt = persona.get("prompt", "") or f"You are a specialist agent: {spec.description}."
        else:
            # Composite: header + each persona section
            lines = [
                f"ARCHETYPE: {spec.name}",
                f"COVERS: {spec.description}",
                f"PERSONAS: {len(spec.persona_keys)} profiles — "
                + ", ".join(spec.persona_keys),
                "",
            ]
            if spec.bash_readonly:
                lines.append("BASH: Read-only — do not modify state.")
            else:
                lines.append("BASH: Full access.")
            lines.append("")

            for key in spec.persona_keys:
                persona = PERSONAS.get(key, {})
                prompt_body = persona.get("prompt", "").strip()
                if prompt_body:
                    lines.append(f"{'═' * 40}")
                    lines.append(prompt_body)
                    lines.append("")

            system_prompt = "\n".join(lines).strip()

        return AgentConfig(
            name=archetype_name,
            description=spec.description,
            system_prompt=system_prompt,
            allowed_tools=list(tools),
            max_iterations=spec.max_iterations,
            bash_readonly=spec.bash_readonly,
        )

    def list_archetypes(self) -> list[dict]:
        return [
            {
                "name": s.name,
                "description": s.description,
                "persona_count": len(s.persona_keys),
                "personas": s.persona_keys,
                "tool_preset": s.tool_preset,
                "tools": TOOL_PRESETS.get(s.tool_preset, []),
                "bash_readonly": s.bash_readonly,
                "max_iterations": s.max_iterations,
            }
            for s in ARCHETYPES.values()
        ]

    def list_archetype_names(self) -> list[str]:
        return list(ARCHETYPES.keys())


# ── Module-level singleton ─────────────────────────────────────────────

_factory: Optional[PersonalityAgentFactory] = None


def get_factory() -> PersonalityAgentFactory:
    global _factory
    if _factory is None:
        _factory = PersonalityAgentFactory()
    return _factory


# ── Layer 1 public API ────────────────────────────────────────────────

def resolve_personality_agent(persona_key: str) -> Optional["AgentConfig"]:
    """Resolve a persona key → AgentConfig (layer 1)."""
    return get_factory().create_agent(persona_key)


def get_all_personality_names() -> list[str]:
    """All persona keys resolvable as individual personality agents."""
    return get_factory().list_persona_names()


# ── Layer 2 public API ────────────────────────────────────────────────

def resolve_archetype_agent(archetype_name: str) -> Optional["AgentConfig"]:
    """Resolve an archetype name → composite AgentConfig (layer 2)."""
    return get_factory().create_archetype_agent(archetype_name)


def get_all_archetype_names() -> list[str]:
    """All archetype names resolvable as composite agents."""
    return get_factory().list_archetype_names()
