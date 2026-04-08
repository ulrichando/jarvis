"""Master Orchestrator — single entry point for all JARVIS requests.

Mirrors Claude's native orchestration pipeline:

  1. Analyze   — complexity score, multi-domain detection, sequential vs parallel
  2. Decompose — TaskGraph with dependency DAG → ordered execution stages
  3. Route     — per-task agent selection (keyword scoring + SQLite-backed EMA learning)
  4. Execute   — parallel stages via the brain's agent executor
  5. Resolve   — quality-weighted conflict resolution across agent outputs
  6. Synthesize — coherent final response from multi-agent results
  7. Learn     — update EMA routing weights from each outcome

Single entry point consumed by Brain._run_agent_loop():

    orch_result = await orchestrator.route(
        query,
        executor=_agent_executor,   # closure over brain's context
    )
    response = orch_result.response
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("jarvis.orchestrator")

# ─── Thresholds ──────────────────────────────────────────────────────────────

COMPLEXITY_THRESHOLD = 0.40   # score ≥ this → multi-agent path
LEARNING_DECAY       = 0.85   # EMA decay for routing weight updates
DEFAULT_TOP_K        = 3      # agents evaluated per task

# ─── Domain signal tables ────────────────────────────────────────────────────

_DOMAIN_SIGNALS: dict[str, list[str]] = {
    "security_offensive": [
        "exploit", "pentest", "cve", "payload", "c2", "red team", "redteam",
        "privilege escalation", "privesc", "recon", "shellcode", "bypass",
        "buffer overflow", "injection", "xss", "sqli", "metasploit", "burp",
        "nmap", "nikto", "gobuster", "mimikatz", "bloodhound", "cobalt strike",
    ],
    "security_defensive": [
        "soc", "incident response", "ioc", "threat hunt", "siem", "splunk",
        "firewall", "ids", "ips", "patch", "vulnerability management",
        "blue team", "forensics", "malware analysis", "threat intel",
        "devsecops", "zero trust", "grc", "compliance", "audit log",
    ],
    "engineering": [
        "code", "function", "class", "bug", "implement", "refactor", "api",
        "database", "test", "debug", "compile", "runtime", "dependency",
        "framework", "backend", "microservice", "async", "concurrency",
        "deploy", "docker", "kubernetes", "ci/cd", "pipeline", "nginx",
        "devops", "bash", "shell", "cron", "systemd", "ansible", "terraform",
        "git", "repo", "branch", "pull request",
    ],
    "language_specific": [
        "golang", "go routine", "goroutine", "go module",
        "rust", "borrow checker", "lifetime", "ownership", "cargo",
        "java", "spring boot", "jvm", "maven", "gradle",
        "php", "laravel", "composer",
        "vue.js", "vuejs", "pinia",
        "angular", "rxjs", "ngrx",
        "web3", "solidity", "smart contract", "dapp",
        "nosql", "mongodb", "cassandra", "dynamodb",
    ],
    "legal": [
        "law", "contract", "clause", "compliance", "gdpr", "hipaa",
        "lawsuit", "jurisdiction", "intellectual property", "patent",
        "trademark", "copyright", "terms of service", "privacy policy",
        "arbitration", "litigation", "discovery", "indemnification",
    ],
    "financial": [
        "portfolio", "investment", "stock", "crypto", "tax",
        "balance sheet", "revenue", "roi", "options", "etf",
        "equity", "valuation", "cap table", "safe note", "series a",
        "retirement", "ira", "401k", "hedge fund", "leverage", "margin",
    ],
    "design": [
        "ux", "ui", "wireframe", "prototype", "figma", "accessibility",
        "user research", "design system", "color palette", "typography",
        "information architecture", "user journey", "usability",
        "brand", "motion design", "ar/vr", "game ux",
    ],
    "analysis": [
        "analyze", "performance", "metrics", "review", "audit",
        "benchmark", "profile", "measure", "evaluate", "report",
        "data pipeline", "etl", "dashboard", "kpi", "anomaly",
    ],
    "system_ops": [
        "sysadmin", "linux", "network", "dns", "routing", "server",
        "cloud", "aws", "gcp", "azure", "database", "dba",
        "monitoring", "alerting", "backup", "disaster recovery",
    ],
}

# Domain name → archetype (dispatch target)
_DOMAIN_TO_ARCHETYPE: dict[str, str] = {
    "security_offensive": "red_team",
    "security_defensive": "blue_team",
    "engineering":        "engineer",
    "language_specific":  "language_specialist",
    "legal":              "legal",
    "financial":          "financial",
    "design":             "designer",
    "analysis":           "analyst",
    "system_ops":         "engineer",
}

# Sequential connectives — indicate ordered subtasks
_SEQ_RE = re.compile(
    r"\b(first|then|after\s+that|next|step\s+\d|finally|lastly|and\s+then|"
    r"once\s+you|before\s+that|prior\s+to)\b",
    re.I,
)

# Parallel connectives — independent subtasks
_PAR_RE = re.compile(
    r"\b(also|additionally|furthermore|as\s+well\s+as|plus|and\s+also|"
    r"simultaneously|at\s+the\s+same\s+time)\b",
    re.I,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class QueryAnalysis:
    complexity_score: float
    is_complex:       bool
    domains:          list[str]
    is_multi_domain:  bool
    is_sequential:    bool
    is_parallel:      bool
    primary_domain:   str | None
    estimated_tasks:  int


@dataclass
class SubTask:
    """Atomic unit of work in a TaskGraph."""
    id:          str
    description: str
    domain:      str | None          = None   # archetype name, not domain key
    depends_on:  list[str]           = field(default_factory=list)
    parallel_ok: bool                = True


@dataclass
class TaskGraph:
    """Dependency DAG of subtasks with ordered execution stages."""
    original_query: str
    tasks:          list[SubTask]
    is_complex:     bool = False

    def stages(self) -> list[list[SubTask]]:
        """Topological sort → list of parallel execution stages."""
        completed: set[str]            = set()
        remaining: list[SubTask]       = list(self.tasks)
        stages:    list[list[SubTask]] = []

        while remaining:
            ready = [t for t in remaining if all(d in completed for d in t.depends_on)]
            if not ready:
                stages.append(remaining)    # cycle guard: flush remainder
                break
            parallel = [t for t in ready if t.parallel_ok]
            seq      = [t for t in ready if not t.parallel_ok]

            promoted: set[str] = set()
            if parallel:
                stages.append(parallel)
                for t in parallel:
                    completed.add(t.id)
                    promoted.add(t.id)
            for t in seq:
                stages.append([t])
                completed.add(t.id)
                promoted.add(t.id)

            remaining = [t for t in remaining if t.id not in promoted]

        return stages or [[]]


@dataclass
class RoutingDecision:
    """Agent selection for a subtask."""
    task_id:         str
    agent_name:      str
    confidence:      float
    fallback_agents: list[str] = field(default_factory=list)


@dataclass
class AgentResult:
    """Output from a single agent execution."""
    task_id:       str
    agent_name:    str
    response:      str
    quality_score: float = 0.0
    latency_ms:    int   = 0
    status:        str   = "success"   # success | timeout | error | merged


@dataclass
class OrchestratorResult:
    """Final result from the full orchestration pipeline."""
    response:          str
    used_parallel:     bool              = False
    task_count:        int               = 1
    agent_results:     list[AgentResult] = field(default_factory=list)
    routing:           list[RoutingDecision] = field(default_factory=list)
    synthesis_strategy: str             = "single"  # single | parallel_merge | sequential_chain
    total_latency_ms:  int              = 0
    metadata:          dict             = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════════
# Component: Routing Learner
# ═══════════════════════════════════════════════════════════════════════════════

class RoutingLearner:
    """SQLite-backed EMA weight learner. Persists agent quality per domain."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS routing_history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    query_hash  TEXT,
                    domain      TEXT,
                    agent_name  TEXT    NOT NULL,
                    quality     REAL    NOT NULL,
                    latency_ms  INTEGER,
                    ts          TEXT    DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS agent_weights (
                    agent_name  TEXT    NOT NULL,
                    domain      TEXT,
                    ema_quality REAL    DEFAULT 0.50,
                    obs_count   INTEGER DEFAULT 0,
                    last_updated TEXT   DEFAULT (datetime('now')),
                    PRIMARY KEY (agent_name, domain)
                );
                CREATE INDEX IF NOT EXISTS idx_rh_domain ON routing_history(domain);
                CREATE INDEX IF NOT EXISTS idx_rh_agent  ON routing_history(agent_name);
                CREATE INDEX IF NOT EXISTS idx_aw_domain ON agent_weights(domain);
            """)

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_weights(self, domain: str | None = None) -> dict[str, float]:
        """Return {agent_name: ema_quality} for a domain (or global average)."""
        with sqlite3.connect(self.db_path, timeout=5.0) as conn:
            if domain:
                rows = conn.execute(
                    "SELECT agent_name, ema_quality FROM agent_weights WHERE domain = ?",
                    (domain,),
                ).fetchall()
                if not rows:
                    # Fall back to global weights
                    rows = conn.execute(
                        "SELECT agent_name, AVG(ema_quality) FROM agent_weights GROUP BY agent_name"
                    ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT agent_name, AVG(ema_quality) FROM agent_weights GROUP BY agent_name"
                ).fetchall()
        return {r[0]: r[1] for r in rows if r[1] is not None}

    def get_stats(self) -> list[dict]:
        """Routing stats for diagnostics (/orchestrator stats)."""
        with sqlite3.connect(self.db_path, timeout=5.0) as conn:
            rows = conn.execute(
                "SELECT agent_name, domain, ema_quality, obs_count, last_updated "
                "FROM agent_weights ORDER BY ema_quality DESC"
            ).fetchall()
        return [
            {
                "agent": r[0], "domain": r[1], "ema_quality": round(r[2], 4),
                "observations": r[3], "last_updated": r[4],
            }
            for r in rows
        ]

    # ── Write ─────────────────────────────────────────────────────────────────

    def record(
        self,
        domain: str | None,
        agent_name: str,
        quality: float,
        latency_ms: int,
        query_hash: str | None = None,
    ) -> None:
        """Record outcome and update EMA quality for this agent×domain."""
        with self._lock, sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.execute(
                "INSERT INTO routing_history (query_hash, domain, agent_name, quality, latency_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (query_hash, domain, agent_name, quality, latency_ms),
            )
            row = conn.execute(
                "SELECT ema_quality, obs_count FROM agent_weights "
                "WHERE agent_name = ? AND domain IS ?",
                (agent_name, domain),
            ).fetchone()
            if row:
                new_ema = LEARNING_DECAY * row[0] + (1.0 - LEARNING_DECAY) * quality
                conn.execute(
                    "UPDATE agent_weights SET ema_quality = ?, obs_count = ?, "
                    "last_updated = datetime('now') WHERE agent_name = ? AND domain IS ?",
                    (new_ema, row[1] + 1, agent_name, domain),
                )
            else:
                conn.execute(
                    "INSERT INTO agent_weights (agent_name, domain, ema_quality, obs_count) "
                    "VALUES (?, ?, ?, 1)",
                    (agent_name, domain, quality),
                )

    def reset(self) -> None:
        with self._lock, sqlite3.connect(self.db_path, timeout=5.0) as conn:
            conn.execute("DELETE FROM agent_weights")
            conn.execute("DELETE FROM routing_history")
        log.info("Routing weights reset.")


# ═══════════════════════════════════════════════════════════════════════════════
# Component: Query Analyzer
# ═══════════════════════════════════════════════════════════════════════════════

class QueryAnalyzer:
    """Scores query complexity and detects domains — no LLM calls."""

    def analyze(self, query: str) -> QueryAnalysis:
        q_lower = query.lower()
        score   = 0.0

        # Length
        words = len(query.split())
        if words > 40:  score += 0.15
        if words > 80:  score += 0.15
        if words > 150: score += 0.10

        # Multiple questions
        n_q = query.count("?")
        if n_q > 1: score += 0.25
        if n_q > 3: score += 0.15

        # Sequential markers
        is_seq = bool(_SEQ_RE.search(query))
        if is_seq: score += 0.20

        # Parallel markers
        is_par = bool(_PAR_RE.search(query))
        if is_par: score += 0.15

        # Domain hits
        hit_domains: list[str] = [
            d for d, signals in _DOMAIN_SIGNALS.items()
            if any(s in q_lower for s in signals)
        ]
        n_domains = len(hit_domains)
        if n_domains > 1: score += 0.20 * min(n_domains, 3)

        score = min(1.0, score)
        primary = hit_domains[0] if hit_domains else None
        estimated = max(1, n_q if n_q > 1 else (n_domains if n_domains > 1 else 1))

        return QueryAnalysis(
            complexity_score = score,
            is_complex       = score >= COMPLEXITY_THRESHOLD,
            domains          = hit_domains,
            is_multi_domain  = n_domains > 1,
            is_sequential    = is_seq,
            is_parallel      = is_par,
            primary_domain   = primary,
            estimated_tasks  = min(estimated, 6),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Component: Task Decomposer
# ═══════════════════════════════════════════════════════════════════════════════

class TaskDecomposer:
    """Breaks complex queries into a TaskGraph using structural heuristics."""

    def __init__(self, analyzer: QueryAnalyzer) -> None:
        self.analyzer = analyzer

    def decompose(self, query: str, analysis: QueryAnalysis | None = None) -> TaskGraph:
        if analysis is None:
            analysis = self.analyzer.analyze(query)

        if not analysis.is_complex:
            arch = _DOMAIN_TO_ARCHETYPE.get(analysis.primary_domain or "", None)
            return TaskGraph(
                original_query = query,
                tasks          = [SubTask(id="t1", description=query, domain=arch)],
                is_complex     = False,
            )

        tasks = self._heuristic_decompose(query, analysis)
        return TaskGraph(original_query=query, tasks=tasks, is_complex=True)

    # ── Private ───────────────────────────────────────────────────────────────

    def _heuristic_decompose(self, query: str, analysis: QueryAnalysis) -> list[SubTask]:
        """Split on sequential markers, multi-domain sentences, or parallel connectives."""

        # Strategy 1: Sequential markers
        if analysis.is_sequential:
            parts = _SEQ_RE.split(query)
            parts = [p.strip() for p in parts if p.strip() and len(p.strip()) > 10]
            if len(parts) >= 2:
                tasks: list[SubTask] = []
                prev_id: str | None  = None
                for i, part in enumerate(parts):
                    tid = f"t{i + 1}"
                    arch = _DOMAIN_TO_ARCHETYPE.get(self._detect_domain(part) or "", None)
                    tasks.append(SubTask(
                        id          = tid,
                        description = part,
                        domain      = arch,
                        depends_on  = [prev_id] if prev_id else [],
                        parallel_ok = False,
                    ))
                    prev_id = tid
                return tasks

        # Strategy 2: Multi-domain → one task per domain area
        if analysis.is_multi_domain and len(analysis.domains) >= 2:
            sentences = re.split(r"(?<=[.!?])\s+", query)
            domain_tasks: dict[str, list[str]] = {}
            for s in sentences:
                d = self._detect_domain(s.lower())
                if d:
                    domain_tasks.setdefault(d, []).append(s)
            if len(domain_tasks) >= 2:
                result: list[SubTask] = []
                for i, (domain, sents) in enumerate(domain_tasks.items()):
                    result.append(SubTask(
                        id          = f"t{i + 1}",
                        description = " ".join(sents),
                        domain      = _DOMAIN_TO_ARCHETYPE.get(domain, None),
                        parallel_ok = True,
                    ))
                return result

        # Strategy 3: Parallel connectives
        if analysis.is_parallel:
            parts = _PAR_RE.split(query)
            parts = [p.strip() for p in parts if p.strip() and len(p.strip()) > 10]
            if len(parts) >= 2:
                result = []
                for i, part in enumerate(parts):
                    arch = _DOMAIN_TO_ARCHETYPE.get(self._detect_domain(part) or "", None)
                    result.append(SubTask(
                        id          = f"t{i + 1}",
                        description = part,
                        domain      = arch,
                        parallel_ok = True,
                    ))
                return result

        # Fallback: single task
        arch = _DOMAIN_TO_ARCHETYPE.get(analysis.primary_domain or "", None)
        return [SubTask(id="t1", description=query, domain=arch)]

    def _detect_domain(self, text: str) -> str | None:
        """Return the domain with the most keyword hits, or None."""
        best_domain: str | None = None
        best_hits   = 0
        for domain, signals in _DOMAIN_SIGNALS.items():
            hits = sum(1 for s in signals if s in text)
            if hits > best_hits:
                best_hits  = hits
                best_domain = domain
        return best_domain if best_hits > 0 else None


# ═══════════════════════════════════════════════════════════════════════════════
# Component: Agent Router
# ═══════════════════════════════════════════════════════════════════════════════

class AgentRouter:
    """Combines keyword scoring with learned EMA weights to select agents."""

    def __init__(self, learner: RoutingLearner) -> None:
        self.learner = learner

    def select(self, task: SubTask, top_k: int = DEFAULT_TOP_K) -> list[RoutingDecision]:
        """Return ranked RoutingDecisions for a task."""
        try:
            from src.agent.parallel_dispatch import route_query
            keyword_scores: list[tuple[str, float]] = route_query(task.description, top_k=top_k)
        except Exception as _e:
            log.warning("route_query failed (%s), using fallback scorer", _e)
            keyword_scores = self._fallback_score(task, top_k)

        learned = self.learner.get_weights(task.domain)

        # Combine: 70% keyword, 30% learned
        combined: dict[str, float] = {}
        for agent, kw in keyword_scores:
            boost = learned.get(agent, 0.50)
            combined[agent] = kw * 0.70 + boost * 0.30

        # If task has an explicit domain archetype not in top results, inject it
        if task.domain and task.domain not in combined:
            boost = learned.get(task.domain, 0.50)
            combined[task.domain] = 0.10 + boost * 0.30

        sorted_agents = sorted(combined.items(), key=lambda x: x[1], reverse=True)

        decisions: list[RoutingDecision] = []
        for agent_name, confidence in sorted_agents[:top_k]:
            fallbacks = [a for a, _ in sorted_agents if a != agent_name][:2]
            decisions.append(RoutingDecision(
                task_id         = task.id,
                agent_name      = agent_name,
                confidence      = round(confidence, 4),
                fallback_agents = fallbacks,
            ))
        return decisions

    def _fallback_score(self, task: SubTask, top_k: int) -> list[tuple[str, float]]:
        desc = task.description.lower()
        scores: dict[str, float] = {}
        for domain, arch in _DOMAIN_TO_ARCHETYPE.items():
            hits = sum(1 for s in _DOMAIN_SIGNALS[domain] if s in desc)
            if hits:
                scores[arch] = scores.get(arch, 0.0) + hits * 0.10
        if not scores:
            scores["engineer"] = 0.30
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]


# ═══════════════════════════════════════════════════════════════════════════════
# Component: Quality Scorer
# ═══════════════════════════════════════════════════════════════════════════════

def _score_quality(text: str) -> float:
    """Heuristic quality score 0.0–1.0 for an agent response."""
    if not text or len(text) < 20:
        return 0.05

    score  = 0.0
    length = len(text)

    # Length sweet spot: 100–4000 chars
    if 100 <= length <= 4000:
        score += 0.30
    elif length > 4000:
        score += 0.20
    else:
        score += max(0.0, length / 100 * 0.15)

    # Structured content
    if re.search(r"^#{1,3}\s",     text, re.M): score += 0.10  # headers
    if re.search(r"^\s*[-*•]\s",   text, re.M): score += 0.08  # bullets
    if re.search(r"```[\s\S]+?```", text):       score += 0.12  # code block
    if re.search(r"^\s*\d+\.\s",   text, re.M): score += 0.07  # numbered list
    if re.search(r"\b\w+\(\)",     text):        score += 0.05  # function refs
    if re.search(r"\b(CVE-|RFC\s?\d|ISO\s?\d)", text): score += 0.08  # standards

    # Confidence signals
    if re.search(r"\b(confident|recommend|should|must)\b", text, re.I): score += 0.05
    elif re.search(r"\b(unsure|might|maybe|not sure)\b",   text, re.I): score -= 0.05

    # Failure signals
    if re.search(r"\b(error|failed|unable|cannot|sorry)\b", text, re.I): score -= 0.10

    return max(0.0, min(1.0, score))


# ═══════════════════════════════════════════════════════════════════════════════
# Component: Conflict Resolver
# ═══════════════════════════════════════════════════════════════════════════════

class ConflictResolver:
    """Quality-weighted selection; merges hard contradictions."""

    _CONTRADICTIONS: list[tuple[str, str]] = [
        ("yes", "no"), ("safe", "unsafe"), ("allow", "deny"),
        ("secure", "insecure"), ("correct", "incorrect"),
        ("recommended", "not recommended"),
    ]

    def resolve(self, results: list[AgentResult]) -> list[AgentResult]:
        """Group by task_id, pick best per group, return one result per task."""
        by_task: dict[str, list[AgentResult]] = {}
        for r in results:
            by_task.setdefault(r.task_id, []).append(r)

        resolved: list[AgentResult] = []
        for task_results in by_task.values():
            if len(task_results) == 1:
                r = task_results[0]
                if r.quality_score == 0.0:
                    r.quality_score = _score_quality(r.response)
                resolved.append(r)
            else:
                resolved.append(self._pick_best(task_results))
        return resolved

    def _pick_best(self, results: list[AgentResult]) -> AgentResult:
        for r in results:
            if r.quality_score == 0.0:
                r.quality_score = _score_quality(r.response)

        ranked = sorted(results, key=lambda r: r.quality_score, reverse=True)
        best   = ranked[0]

        # Hard contradiction between top two → merge both perspectives
        if len(ranked) >= 2 and self._contradiction(best.response, ranked[1].response):
            second = ranked[1]
            return AgentResult(
                task_id       = best.task_id,
                agent_name    = f"{best.agent_name}+{second.agent_name}",
                response      = (
                    f"**{best.agent_name.replace('_', ' ').title()} perspective**\n"
                    f"{best.response}\n\n"
                    f"**{second.agent_name.replace('_', ' ').title()} perspective**\n"
                    f"{second.response}\n\n"
                    f"*These represent distinct analytical viewpoints.*"
                ),
                quality_score = (best.quality_score + second.quality_score) / 2,
                latency_ms    = max(best.latency_ms, second.latency_ms),
                status        = "merged",
            )
        return best

    def _contradiction(self, a: str, b: str) -> bool:
        a_l, b_l = a.lower(), b.lower()
        for pos, neg in self._CONTRADICTIONS:
            if (pos in a_l and neg in b_l) or (neg in a_l and pos in b_l):
                return True
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# Component: Response Synthesizer
# ═══════════════════════════════════════════════════════════════════════════════

class ResponseSynthesizer:
    """Assembles coherent final response from multi-task, multi-agent results."""

    def synthesize(
        self,
        task_graph:  TaskGraph,
        results:     list[AgentResult],
        attribution: bool = False,
    ) -> str:
        if not results:
            return ""
        if len(results) == 1:
            return results[0].response

        task_map = {t.id: t for t in task_graph.tasks}
        parts: list[str] = []

        for result in results:
            task = task_map.get(result.task_id)
            if attribution and task and len(results) > 2:
                label = result.agent_name.replace("_", " ").title()
                sub   = task.description
                blurb = sub if sub == task_graph.original_query else f"{sub[:60]}{'…' if len(sub)>60 else ''}"
                header = f"## {label} — {blurb}" if blurb else f"## {label}"
                parts.append(f"{header}\n\n{result.response}")
            else:
                parts.append(result.response)

        return "\n\n---\n\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# Master Orchestrator
# ═══════════════════════════════════════════════════════════════════════════════

class MasterOrchestrator:
    """Single entry point for all JARVIS requests.

    The Brain calls ``route()`` once per user turn.  For simple queries the
    orchestrator runs a single agent via the provided *executor* callable.
    For complex / multi-domain queries it decomposes the request, assigns a
    specialist archetype to each subtask, runs parallel execution stages, and
    synthesizes a coherent response — all transparently.

    Routing decisions improve automatically over time through EMA learning
    stored in ``~/.jarvis/routing.db``.
    """

    _instance: Optional["MasterOrchestrator"] = None

    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            db_path = Path.home() / ".jarvis" / "routing.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        self.learner     = RoutingLearner(db_path)
        self.analyzer    = QueryAnalyzer()
        self.decomposer  = TaskDecomposer(self.analyzer)
        self.router      = AgentRouter(self.learner)
        self.resolver    = ConflictResolver()
        self.synthesizer = ResponseSynthesizer()

        log.info("MasterOrchestrator ready (db=%s)", db_path)

    # ── Singleton ─────────────────────────────────────────────────────────────

    @classmethod
    def get(cls) -> "MasterOrchestrator":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Force re-initialization (tests / hot-reload)."""
        cls._instance = None

    # ── Main entry point ──────────────────────────────────────────────────────

    async def route(
        self,
        query: str,
        *,
        executor: Callable[..., Any],
        on_agent_start: Callable[[str, str], None] | None = None,
        on_agent_done:  Callable[[str, str, float], None] | None = None,
        force_parallel: bool  = False,
        top_k:          int   = DEFAULT_TOP_K,
    ) -> OrchestratorResult:
        """
        Route ``query`` through the full orchestration pipeline.

        Parameters
        ----------
        query          : User's raw input text.
        executor       : ``async (agent_name, task_description, **kw) → str``
                         Provided by Brain — executes a named archetype agent.
        on_agent_start : Optional UI callback ``(agent_name, task_id)``.
        on_agent_done  : Optional UI callback ``(agent_name, task_id, quality)``.
        force_parallel : Skip complexity check; always use multi-agent path.
        top_k          : Number of candidate agents evaluated per task.

        Returns
        -------
        OrchestratorResult — always contains ``.response`` (str).
        """
        t_start    = time.monotonic()
        query_hash = hashlib.md5(query.encode()).hexdigest()[:12]

        # ── 1. Analyze ────────────────────────────────────────────────────────
        analysis = self.analyzer.analyze(query)
        log.debug(
            "analyze query=%r score=%.2f complex=%s domains=%s",
            query[:60], analysis.complexity_score, analysis.is_complex, analysis.domains,
        )

        # ── 2. Decompose ──────────────────────────────────────────────────────
        if force_parallel or analysis.is_complex:
            task_graph = self.decomposer.decompose(query, analysis)
        else:
            arch = _DOMAIN_TO_ARCHETYPE.get(analysis.primary_domain or "", None)
            task_graph = TaskGraph(
                original_query = query,
                tasks          = [SubTask(id="t1", description=query, domain=arch)],
                is_complex     = False,
            )

        log.debug("tasks=%d stages=%d", len(task_graph.tasks), len(task_graph.stages()))

        # ── 3. Route ──────────────────────────────────────────────────────────
        routing:      list[RoutingDecision]    = []
        task_routing: dict[str, RoutingDecision] = {}

        for task in task_graph.tasks:
            decisions = self.router.select(task, top_k=top_k)
            if decisions:
                task_routing[task.id] = decisions[0]
                routing.append(decisions[0])

        # ── 4. Execute stages ─────────────────────────────────────────────────
        all_results: list[AgentResult] = []

        for stage in task_graph.stages():
            coros = [
                self._execute_task(
                    task       = task,
                    agent_name = task_routing[task.id].agent_name if task.id in task_routing else "engineer",
                    executor   = executor,
                    on_start   = on_agent_start,
                    on_done    = on_agent_done,
                )
                for task in stage
            ]
            stage_results = await asyncio.gather(*coros, return_exceptions=True)

            for i, result in enumerate(stage_results):
                if isinstance(result, Exception):
                    task  = stage[i]
                    agent = task_routing.get(task.id, RoutingDecision("", "unknown", 0.0)).agent_name
                    log.warning("Agent %s failed for task %s: %s", agent, task.id, result)
                    all_results.append(AgentResult(
                        task_id       = task.id,
                        agent_name    = agent,
                        response      = f"[{agent}: execution error — {result}]",
                        quality_score = 0.0,
                        status        = "error",
                    ))
                else:
                    all_results.append(result)  # type: ignore[arg-type]

        # ── 5. Resolve conflicts ───────────────────────────────────────────────
        resolved = self.resolver.resolve(all_results)

        # ── 6. Synthesize ──────────────────────────────────────────────────────
        attribution = task_graph.is_complex and len(resolved) > 2
        response    = self.synthesizer.synthesize(task_graph, resolved, attribution=attribution)

        # ── 7. Learn ──────────────────────────────────────────────────────────
        total_ms = int((time.monotonic() - t_start) * 1000)
        self._learn(resolved, analysis, query_hash)

        used_parallel = task_graph.is_complex and len(resolved) > 1
        strategy = (
            "single"            if len(task_graph.tasks) == 1  else
            "sequential_chain"  if analysis.is_sequential      else
            "parallel_merge"
        )

        log.info(
            "orchestrated tasks=%d agents=%s strategy=%s latency=%dms",
            len(task_graph.tasks),
            [r.agent_name for r in routing],
            strategy,
            total_ms,
        )

        return OrchestratorResult(
            response           = response,
            used_parallel      = used_parallel,
            task_count         = len(task_graph.tasks),
            agent_results      = resolved,
            routing            = routing,
            synthesis_strategy = strategy,
            total_latency_ms   = total_ms,
            metadata           = {
                "query_hash":        query_hash,
                "complexity_score":  analysis.complexity_score,
                "domains":           analysis.domains,
                "is_complex":        analysis.is_complex,
                "is_sequential":     analysis.is_sequential,
            },
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _execute_task(
        self,
        task:       SubTask,
        agent_name: str,
        executor:   Callable,
        on_start:   Callable | None,
        on_done:    Callable | None,
    ) -> AgentResult:
        t0 = time.monotonic()

        if on_start:
            try:
                on_start(agent_name, task.id)
            except Exception as _e:
                log.debug("on_start callback error: %s", _e)

        try:
            response = await executor(agent_name, task.description)
            quality  = _score_quality(str(response))
            status   = "success"
        except asyncio.TimeoutError:
            response = f"[{agent_name}: timed out]"
            quality  = 0.0
            status   = "timeout"
        except Exception as exc:
            log.warning("Executor error agent=%s task=%s: %s", agent_name, task.id, exc)
            response = f"[{agent_name}: error — {exc}]"
            quality  = 0.0
            status   = "error"

        latency_ms = int((time.monotonic() - t0) * 1000)

        if on_done:
            try:
                on_done(agent_name, task.id, quality)
            except Exception as _e:
                log.debug("on_done callback error: %s", _e)

        return AgentResult(
            task_id       = task.id,
            agent_name    = agent_name,
            response      = str(response),
            quality_score = quality,
            latency_ms    = latency_ms,
            status        = status,
        )

    def _learn(
        self,
        results:    list[AgentResult],
        analysis:   QueryAnalysis,
        query_hash: str,
    ) -> None:
        domain = _DOMAIN_TO_ARCHETYPE.get(analysis.primary_domain or "", None)
        for result in results:
            if result.status == "success" and result.quality_score > 0:
                base_agent = result.agent_name.split("+")[0]  # strip merged names
                try:
                    self.learner.record(
                        domain     = domain,
                        agent_name = base_agent,
                        quality    = result.quality_score,
                        latency_ms = result.latency_ms,
                        query_hash = query_hash,
                    )
                except Exception as exc:
                    log.debug("Learning update failed: %s", exc)

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def routing_stats(self) -> list[dict]:
        """Return all learned routing weights (for /orchestrator stats)."""
        return self.learner.get_stats()

    def reset_weights(self) -> None:
        """Clear learned weights. Does NOT reset the singleton."""
        self.learner.reset()

    def analyze(self, query: str) -> dict:
        """Public analysis for debugging / tooling."""
        a = self.analyzer.analyze(query)
        graph = self.decomposer.decompose(query, a)
        routing = [self.router.select(t, top_k=3) for t in graph.tasks]
        return {
            "complexity_score": a.complexity_score,
            "is_complex":       a.is_complex,
            "domains":          a.domains,
            "is_sequential":    a.is_sequential,
            "tasks":            [
                {
                    "id": t.id,
                    "description": t.description[:80],
                    "domain": t.domain,
                    "parallel_ok": t.parallel_ok,
                    "routing": [
                        {"agent": d.agent_name, "confidence": d.confidence}
                        for d in (routing[i] if i < len(routing) else [])
                    ],
                }
                for i, t in enumerate(graph.tasks)
            ],
        }


# ─── Public convenience ───────────────────────────────────────────────────────

def get_orchestrator() -> MasterOrchestrator:
    """Return the singleton MasterOrchestrator, creating it on first call."""
    return MasterOrchestrator.get()
