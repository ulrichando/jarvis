"""Multi-agent parallel dispatch — route, run, rank across 94 specialist agents.

Dispatch targets (12 archetypes covering 94 personas, 9 domain profiles):

  engineer          SW engineering, devops, systems            (13 personas)
  language_specialist  Language-specific dev                   (8 personas)
  analyst           Code review, perf, data engineering        (3 personas)
  red_team          Offensive security                         (9 personas)
  blue_team         Defensive security                         (13 personas)
  legal             Legal analysis, contracts, IP              (12 personas)
  financial         Finance, investing, tax                    (16 personas)
  designer          UX research & strategy                     (9 personas)
  ui-design         UI implementation & accessibility          (8 personas)
  ghost             Stealth executor                           (1 persona)
  mentor            Patient teacher                            (1 persona)
  creative          Brainstorming partner                      (1 persona)

Usage:
    dispatcher = ParallelDispatcher(reasoner)
    results = await dispatcher.dispatch("How do I model equity dilution in a SAFE?")
    print(dispatcher.format_results(results))
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("jarvis.agent.parallel_dispatch")

# ─── All valid dispatch targets ───────────────────────────────────────────────

ALL_TARGETS: list[str] = [
    "engineer", "language_specialist", "analyst",
    "red_team", "blue_team",
    "legal", "financial",
    "designer", "ui-design",
    "ghost", "mentor", "creative",
]

# ─── Domain keyword scoring tables ───────────────────────────────────────────

_KEYWORDS: dict[str, dict[str, list[str]]] = {
    "engineer": {
        "hard": [
            "code", "bug", "function", "class", "implement", "algorithm", "api",
            "database", "refactor", "test", "debug", "compile", "runtime",
            "dependency", "library", "framework", "backend", "microservice",
            "async", "concurrency", "thread", "orm", "schema", "migration",
            "deploy", "infrastructure", "docker", "kubernetes", "ci/cd", "pipeline",
            "nginx", "server", "sysadmin", "linux", "cloud", "aws", "azure", "gcp",
            "devops", "bash", "shell", "cron", "systemd", "ansible", "terraform",
            "git", "repo", "branch", "pr", "pull request", "merge",
        ],
        "soft": [
            "architecture", "design pattern", "software", "build", "technical",
            "system design", "engineering", "program", "develop", "ship", "ops",
            "stack", "endpoint", "service", "platform", "environment",
        ],
    },
    "language_specialist": {
        "hard": [
            "golang", "go lang", "go routine", "goroutine", "go module",
            "rust", "borrow checker", "lifetime", "ownership", "cargo",
            "java", "spring boot", "jvm", "maven", "gradle",
            "php", "laravel", "composer", "symfony",
            "vue.js", "vuejs", "pinia", "vite",
            "angular", "rxjs", "ngrx", "ngmodule",
            "web3", "solidity", "smart contract", "dapp", "abi", "hardhat", "foundry",
            "nosql", "mongodb", "cassandra", "couchdb", "dynamodb",
        ],
        "soft": [
            "go ", "rust ", "java ", "php ", "vue ", "angular ",
            "blockchain", "document database", "key-value",
        ],
    },
    "analyst": {
        "hard": [
            "code review", "performance profiling", "benchmark", "flame graph",
            "big o", "complexity", "data pipeline", "etl", "spark", "pandas",
            "perf test", "profiler", "latency", "throughput", "bottleneck",
            "flamegraph", "memory usage", "cpu usage", "heap", "gc pressure",
            "data modeling", "dataframe", "sql query", "explain plan",
        ],
        "soft": [
            "analyze", "audit", "review", "optimize", "measure", "profile",
            "data engineering", "metrics", "trace", "slow", "performance",
        ],
    },
    "red_team": {
        "hard": [
            "exploit", "pentest", "ctf", "payload", "shellcode", "buffer overflow",
            "sql injection", "xss", "csrf", "ssrf", "lfi", "rce", "privesc",
            "lateral movement", "c2", "metasploit", "burp suite", "burp",
            "nmap", "recon", "enumeration", "brute force", "bypass", "evasion",
            "red team", "kerberos", "mimikatz", "bloodhound", "cobalt strike",
            "0day", "zero day", "cve", "persistence", "obfuscation", "dropper",
            "reverse shell", "bind shell", "web shell", "path traversal",
            "xxe", "deserialization", "idor", "open redirect", "race condition",
        ],
        "soft": [
            "hack", "attack", "offensive", "pwn", "crack", "reverse engineer",
            "vulnerability research", "exploit dev", "pentesting",
        ],
    },
    "blue_team": {
        "hard": [
            "siem", "threat hunting", "incident response", "forensics", "ioc", "ioa",
            "log analysis", "detection rule", "sigma rule", "yara", "ids", "ips",
            "edr", "threat intelligence", "vulnerability management", "grc",
            "malware analysis", "ransomware", "phishing", "patch management",
            "hardening", "playbook", "runbook", "chain of custody", "triage",
            "soc analyst", "blue team", "devsecops", "sast", "dast", "sbom",
            "dlp", "iam policy", "zero trust", "mitre att&ck", "purple team",
            "cloud security", "s3 policy", "iam role", "security group",
        ],
        "soft": [
            "detect", "monitor", "defend", "protect", "investigate", "remediate",
            "patch", "harden", "security posture", "compliance", "alert",
        ],
    },
    "legal": {
        "hard": [
            "contract", "clause", "statute", "regulation", "litigation", "patent",
            "trademark", "copyright", "gdpr", "hipaa", "ccpa", "employment law",
            "corporate law", "liability", "damages", "jurisdiction", "due diligence",
            "nda", "terms of service", "privacy policy", "arbitration", "mediation",
            "injunction", "discovery", "pleading", "brief", "licensing",
            "force majeure", "indemnification", "mou", "sla", "eula",
            "shareholder agreement", "articles of incorporation", "bylaws",
            "immigration", "visa", "work permit", "asylum",
        ],
        "soft": [
            "legal", "law", "attorney", "lawyer", "court", "agreement",
            "terms", "policy", "rights", "dispute", "regulatory", "counsel",
        ],
    },
    "financial": {
        "hard": [
            "investment", "portfolio", "stock", "bond", "etf", "crypto", "tax",
            "revenue", "profit", "cost", "budget", "equity", "debt", "return",
            "yield", "dividend", "valuation", "m&a", "ipo", "venture capital",
            "hedge fund", "options", "futures", "forex", "inflation", "gdp",
            "interest rate", "balance sheet", "p&l", "cashflow", "capex", "opex",
            "ebitda", "roi", "irr", "npv", "reit", "401k", "ira", "retirement",
            "safe note", "convertible note", "cap table", "dilution", "pretermoney",
            "aum", "nav", "sharpe ratio", "var", "swap", "cds", "basis point",
        ],
        "soft": [
            "money", "financial", "economic", "market", "fund", "capital",
            "accounting", "fiscal", "wealth", "income", "spend", "save", "invest",
        ],
    },
    "designer": {
        "hard": [
            "user research", "usability testing", "user journey", "personas",
            "information architecture", "interaction design", "motion design",
            "brand strategy", "gamification", "ar/vr", "heuristic evaluation",
            "card sorting", "tree testing", "a/b test", "cognitive load",
            "mental model", "affordance", "design critique", "ux audit",
            "empathy map", "jobs to be done", "jtbd", "kano model",
        ],
        "soft": [
            "ux", "user experience", "design thinking", "prototype", "iterate",
            "empathy", "user needs", "usability", "research", "friction",
        ],
    },
    "ui-design": {
        "hard": [
            "component library", "design system", "figma", "tailwind", "wcag",
            "responsive design", "typography", "color palette", "spacing",
            "grid layout", "a11y", "storybook", "dark mode", "color contrast",
            "icon set", "animation", "micro-interaction", "shadcn", "material ui",
            "radix ui", "aria", "focus ring", "keyboard navigation", "screen reader",
            "css variable", "design token", "z-index", "breakpoint",
        ],
        "soft": [
            "ui", "interface", "visual design", "look and feel", "theme",
            "style guide", "pixel", "mockup", "component", "widget", "accessibility",
        ],
    },
    "ghost": {
        "hard": ["stealth mode", "silent mode", "ghost mode", "no narration", "no explanation needed"],
        "soft": ["silent", "minimal", "just do it", "quietly"],
    },
    "mentor": {
        "hard": ["teach me", "explain step by step", "help me understand", "how does it work", "from scratch"],
        "soft": ["explain", "teach", "guide", "walk me through", "beginner", "learn", "new to"],
    },
    "creative": {
        "hard": ["brainstorm", "think outside the box", "wild ideas", "creative ideas", "ideation"],
        "soft": ["imagine", "what if", "creative", "innovate", "invent", "explore ideas", "alternatives"],
    },
}


def _kw_match(kw: str, text: str) -> bool:
    """Match keyword in text, using word boundaries for short tokens to avoid false positives."""
    if len(kw) <= 3:
        # Require word boundary so "ui" doesn't hit "equity", "go" doesn't hit "google"
        return bool(re.search(r"(?<!\w)" + re.escape(kw) + r"(?!\w)", text))
    return kw in text


def score_target(query: str, target: str) -> float:
    """Score relevance of a query against one dispatch target (0.0 – 1.0)."""
    q = query.lower()
    kws = _KEYWORDS.get(target, {})
    hard = sum(1.0 for kw in kws.get("hard", []) if _kw_match(kw, q))
    soft = sum(0.3 for kw in kws.get("soft", []) if _kw_match(kw, q))
    return min(1.0, hard * 0.22 + soft)


def route_query(
    query: str,
    top_k: int = 3,
    threshold: float = 0.15,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> list[tuple[str, float]]:
    """Return the top_k most relevant dispatch targets for a query.

    Args:
        query:     User query text.
        top_k:     Maximum number of targets to return.
        threshold: Minimum score to include a target (skipped if nothing qualifies).
        include:   Force-include these targets regardless of score.
        exclude:   Never include these targets.

    Returns:
        List of (target_name, score) tuples sorted by score descending.
    """
    targets = [t for t in ALL_TARGETS if t not in (exclude or [])]
    scores = [(t, score_target(query, t)) for t in targets]
    scores.sort(key=lambda x: x[1], reverse=True)

    result = [(t, s) for t, s in scores if s >= threshold][:top_k]

    # If nothing passes threshold, return the single best
    if not result and scores:
        result = [scores[0]]

    # Force-include extras (deduped, appended with their scores)
    if include:
        existing = {t for t, _ in result}
        for t in include:
            if t not in existing and t in _KEYWORDS:
                result.append((t, score_target(query, t)))

    result.sort(key=lambda x: x[1], reverse=True)
    return result


# ─── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class DispatchResult:
    """Result from a single specialist agent."""
    target: str           # archetype / agent type
    response: str
    domain_score: float   # routing relevance score (0-1)
    quality_score: float  # response quality heuristic (0-1)
    confidence: float     # confidence estimate (0-1)
    final_score: float    # composite ranking score
    duration_ms: int
    status: str = "done"  # "done" | "failed" | "timeout"
    provider: str = ""    # which LLM provider answered


# ─── Dispatcher ───────────────────────────────────────────────────────────────

class ParallelDispatcher:
    """Route a query to N specialist agents, run in parallel, rank results.

    Two execution modes:
      single_shot (default) — one LLM call per agent, fast, no tools
      full_loop             — full agent loop with tools, slower, deeper
    """

    def __init__(self, reasoner=None):
        self._reasoner = reasoner

    @property
    def reasoner(self):
        if self._reasoner is None:
            from src.reasoning.providers import ProviderRouter
            self._reasoner = ProviderRouter()
        return self._reasoner

    def set_reasoner(self, reasoner) -> None:
        self._reasoner = reasoner

    # ── Public API ────────────────────────────────────────────────────

    async def dispatch(
        self,
        query: str,
        top_k: int = 3,
        threshold: float = 0.15,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        mode: str = "single_shot",
        timeout: float = 45.0,
        max_iterations: int = 5,
    ) -> list[DispatchResult]:
        """Route query to top-K agents, run concurrently, return ranked results.

        Args:
            query:          User query.
            top_k:          How many agents to select via routing.
            threshold:      Min relevance score to select an agent.
            include:        Force-include these targets.
            exclude:        Never dispatch to these targets.
            mode:           "single_shot" (fast, 1 LLM call) or
                            "full_loop" (tools enabled, slower).
            timeout:        Per-agent timeout in seconds.
            max_iterations: Iterations for full_loop mode.

        Returns:
            List of DispatchResult sorted by final_score descending.
        """
        routed = route_query(query, top_k=top_k, threshold=threshold,
                             include=include, exclude=exclude)
        if not routed:
            return []

        log.info("Dispatching to %d agents: %s",
                 len(routed), [t for t, _ in routed])

        tasks = [
            self._run_agent(query, target, domain_score, mode, timeout, max_iterations)
            for target, domain_score in routed
        ]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[DispatchResult] = []
        for (target, domain_score), result in zip(routed, raw_results):
            if isinstance(result, Exception):
                results.append(DispatchResult(
                    target=target,
                    response=f"[exception: {result}]",
                    domain_score=domain_score,
                    quality_score=0.0,
                    confidence=0.0,
                    final_score=0.0,
                    duration_ms=0,
                    status="failed",
                ))
            else:
                results.append(result)

        return self._rank(results)

    async def dispatch_all(
        self,
        query: str,
        mode: str = "single_shot",
        timeout: float = 60.0,
        max_iterations: int = 3,
    ) -> list[DispatchResult]:
        """Broadcast query to ALL 12 archetypes simultaneously.

        Expensive — use for comprehensive multi-perspective analysis.
        """
        all_scored = [(t, score_target(query, t)) for t in ALL_TARGETS]
        log.info("Broadcasting to all %d agents", len(ALL_TARGETS))

        tasks = [
            self._run_agent(query, target, domain_score, mode, timeout, max_iterations)
            for target, domain_score in all_scored
        ]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[DispatchResult] = []
        for (target, domain_score), result in zip(all_scored, raw_results):
            if isinstance(result, Exception):
                results.append(DispatchResult(
                    target=target,
                    response=f"[exception: {result}]",
                    domain_score=domain_score,
                    quality_score=0.0,
                    confidence=0.0,
                    final_score=0.0,
                    duration_ms=0,
                    status="failed",
                ))
            else:
                results.append(result)

        return self._rank(results)

    async def dispatch_domains(
        self,
        query: str,
        domains: list[str],
        mode: str = "single_shot",
        timeout: float = 45.0,
        max_iterations: int = 5,
    ) -> list[DispatchResult]:
        """Dispatch to an explicit list of targets (bypass routing).

        Useful for targeted multi-domain queries, e.g.:
            await dispatcher.dispatch_domains(query, ["legal", "financial"])
        """
        scored = [(t, score_target(query, t)) for t in domains if t in _KEYWORDS]
        if not scored:
            return []

        tasks = [
            self._run_agent(query, target, domain_score, mode, timeout, max_iterations)
            for target, domain_score in scored
        ]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[DispatchResult] = []
        for (target, domain_score), result in zip(scored, raw_results):
            if isinstance(result, Exception):
                results.append(DispatchResult(
                    target=target,
                    response=f"[exception: {result}]",
                    domain_score=domain_score,
                    quality_score=0.0,
                    confidence=0.0,
                    final_score=0.0,
                    duration_ms=0,
                    status="failed",
                ))
            else:
                results.append(result)

        return self._rank(results)

    # ── Execution ─────────────────────────────────────────────────────

    async def _run_agent(
        self,
        query: str,
        target: str,
        domain_score: float,
        mode: str,
        timeout: float,
        max_iterations: int,
    ) -> DispatchResult:
        start = time.time()
        provider = ""
        try:
            if mode == "full_loop":
                response, provider = await asyncio.wait_for(
                    self._full_loop(target, query, max_iterations),
                    timeout=timeout,
                )
            else:
                response, provider = await asyncio.wait_for(
                    self._single_shot(target, query),
                    timeout=timeout,
                )
        except asyncio.TimeoutError:
            return DispatchResult(
                target=target,
                response="[timeout]",
                domain_score=domain_score,
                quality_score=0.0,
                confidence=0.0,
                final_score=0.0,
                duration_ms=int((time.time() - start) * 1000),
                status="timeout",
                provider=provider,
            )
        except Exception as e:
            log.debug("Agent %s failed: %s", target, e)
            return DispatchResult(
                target=target,
                response=f"[error: {e}]",
                domain_score=domain_score,
                quality_score=0.0,
                confidence=0.0,
                final_score=0.0,
                duration_ms=int((time.time() - start) * 1000),
                status="failed",
                provider=provider,
            )

        quality = _compute_quality(response)
        confidence = _extract_confidence(response)
        final = _composite_score(domain_score, quality, confidence)

        return DispatchResult(
            target=target,
            response=response,
            domain_score=domain_score,
            quality_score=quality,
            confidence=confidence,
            final_score=final,
            duration_ms=int((time.time() - start) * 1000),
            status="done" if response and not response.startswith("[") else "failed",
            provider=provider,
        )

    async def _single_shot(self, target: str, query: str) -> tuple[str, str]:
        """One LLM call with the agent's system prompt. Returns (response, provider)."""
        system = _get_system_prompt(target)
        response, provider = await self.reasoner.query(query, system_prompt=system, history=None)
        return response, provider

    async def _full_loop(self, target: str, query: str, max_iterations: int) -> tuple[str, str]:
        """Full agent loop with tools. Returns (response, provider)."""
        from src.agent.loop import _run_sub_agent
        result = await _run_sub_agent(
            self.reasoner, target, query,
            context=f"[parallel dispatch — {target}]",
        )
        return result, ""

    # ── Ranking ───────────────────────────────────────────────────────

    def _rank(self, results: list[DispatchResult]) -> list[DispatchResult]:
        """Sort results by final_score descending. Failed/timeout always last."""
        def sort_key(r: DispatchResult) -> float:
            if r.status != "done":
                return -1.0
            return r.final_score
        return sorted(results, key=sort_key, reverse=True)

    # ── Formatting ────────────────────────────────────────────────────

    def format_results(
        self,
        results: list[DispatchResult],
        verbose: bool = False,
        max_response_chars: int = 800,
    ) -> str:
        """Render ranked dispatch results as a human-readable report."""
        if not results:
            return "No results."

        done = [r for r in results if r.status == "done"]
        failed = [r for r in results if r.status != "done"]

        lines = [
            f"Parallel dispatch — {len(done)}/{len(results)} agents responded",
            "",
        ]

        for i, r in enumerate(done, 1):
            score_bar = _score_bar(r.final_score)
            lines.append(
                f"  [{i}] {r.target:<20s}  {score_bar}  "
                f"score={r.final_score:.2f}  "
                f"({r.duration_ms}ms)"
            )
            if verbose:
                lines.append(f"       domain={r.domain_score:.2f}  "
                             f"quality={r.quality_score:.2f}  "
                             f"confidence={r.confidence:.2f}  "
                             f"provider={r.provider}")
            snippet = r.response[:max_response_chars].rstrip()
            if len(r.response) > max_response_chars:
                snippet += " ..."
            # Indent the response block
            for line in snippet.split("\n"):
                lines.append(f"       {line}")
            lines.append("")

        if failed:
            lines.append(f"  Failed ({len(failed)}): "
                         + ", ".join(f"{r.target} [{r.status}]" for r in failed))

        return "\n".join(lines)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_system_prompt(target: str) -> str:
    """Resolve system prompt for a dispatch target via the agent registry."""
    try:
        from src.agent.agents import resolve_agent
        config = resolve_agent(target)
        if config and config.system_prompt:
            return config.system_prompt
    except Exception as e:
        log.debug("Could not resolve agent config for %s: %s", target, e)

    # Minimal fallback
    return (
        f"You are a specialist agent: {target}. "
        "Answer the user's question with domain expertise. "
        "Be concise, specific, and practical."
    )


def _compute_quality(response: str) -> float:
    """Heuristic quality score based on response structure and length (0-1)."""
    if not response or response.startswith("["):
        return 0.0

    score = 0.0
    length = len(response)

    # Length contribution (sweet spot: 200-2000 chars)
    if length < 50:
        score += 0.05
    elif length < 200:
        score += 0.2
    elif length < 2000:
        score += 0.4
    else:
        score += 0.3  # very long = slightly less focused

    # Structure signals
    if re.search(r"^#{1,3} ", response, re.MULTILINE):
        score += 0.1   # has headers
    if re.search(r"^[-*] ", response, re.MULTILINE):
        score += 0.1   # has bullet points
    if re.search(r"```", response):
        score += 0.15  # has code blocks
    if re.search(r"\b\d+\.\s", response):
        score += 0.05  # has numbered list

    # Specificity signals (numbers, paths, commands, citations)
    if re.search(r"\d{4}|\b\d+\.\d+\b", response):
        score += 0.05  # specific numbers/versions
    if re.search(r"[A-Z]{2,}\d|CVE-|RFC \d|§\s*\d", response):
        score += 0.05  # technical identifiers

    # Negative signals
    if re.search(r"\[placeholder\]|\[todo\]|\[your.*here\]", response, re.IGNORECASE):
        score -= 0.2
    if re.search(r"I don't have enough information|I cannot answer|as an AI", response, re.IGNORECASE):
        score -= 0.1

    return max(0.0, min(1.0, score))


def _extract_confidence(response: str) -> float:
    """Extract or estimate confidence from response text (0-1)."""
    if not response or response.startswith("["):
        return 0.0

    # Explicit confidence statements
    m = re.search(
        r"confidence[:\s]+(\d{1,3})\s*%|(\d{1,3})\s*%\s+confident",
        response, re.IGNORECASE,
    )
    if m:
        pct = int(m.group(1) or m.group(2))
        return min(1.0, pct / 100.0)

    # Sentiment-based estimate
    score = 0.55  # baseline

    high_conf = [
        "definitely", "clearly", "certainly", "this is correct",
        "the answer is", "confirmed", "verified", "documented",
    ]
    low_conf = [
        "might be", "possibly", "not sure", "i think", "maybe",
        "uncertain", "depends on", "could be", "approximately",
        "i'm not certain", "i cannot guarantee",
    ]

    text = response.lower()
    score += sum(0.05 for kw in high_conf if kw in text)
    score -= sum(0.05 for kw in low_conf if kw in text)

    return max(0.1, min(0.95, score))


def _composite_score(domain: float, quality: float, confidence: float) -> float:
    """Weighted composite score for ranking (0-1)."""
    return round(domain * 0.40 + quality * 0.35 + confidence * 0.25, 4)


def _score_bar(score: float, width: int = 10) -> str:
    """ASCII progress bar for a 0-1 score."""
    filled = round(score * width)
    return "[" + "█" * filled + "░" * (width - filled) + "]"


# ─── Module-level singleton ───────────────────────────────────────────────────

_dispatcher: Optional[ParallelDispatcher] = None


def get_dispatcher(reasoner=None) -> ParallelDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = ParallelDispatcher(reasoner)
    elif reasoner is not None:
        _dispatcher.set_reasoner(reasoner)
    return _dispatcher


# ─── Public API ───────────────────────────────────────────────────────────────

async def parallel_dispatch(
    query: str,
    reasoner=None,
    top_k: int = 3,
    threshold: float = 0.15,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    mode: str = "single_shot",
    timeout: float = 45.0,
) -> list[DispatchResult]:
    """Convenience function — route and dispatch a query, return ranked results."""
    return await get_dispatcher(reasoner).dispatch(
        query,
        top_k=top_k,
        threshold=threshold,
        include=include,
        exclude=exclude,
        mode=mode,
        timeout=timeout,
    )


async def broadcast_dispatch(
    query: str,
    reasoner=None,
    mode: str = "single_shot",
    timeout: float = 60.0,
) -> list[DispatchResult]:
    """Broadcast to all 12 archetypes simultaneously."""
    return await get_dispatcher(reasoner).dispatch_all(query, mode=mode, timeout=timeout)
