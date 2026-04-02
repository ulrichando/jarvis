"""
UserModel — Jarvis's Theory of Mind.

Tracks who the user is, what they know, what they want, and how they
communicate so that Jarvis can adapt every response to the specific human
it is talking to.

No ML — just counters, keyword heuristics, and exponential decay.
"""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Keyword banks used to gauge expertise from raw user input
# ---------------------------------------------------------------------------

BEGINNER_SIGNALS: dict[str, list[str]] = {
    "cybersecurity": [
        "what is nmap", "what is a firewall", "what is a vpn",
        "what is ssh", "how to hack", "what is metasploit",
        "what is wireshark", "what is a port", "what is tcp",
        "what is an exploit", "what is a vulnerability",
    ],
    "programming": [
        "what is python", "what is a variable", "what is a loop",
        "what is a function", "what is an object", "what is a class",
        "how to code", "what is javascript", "what is html",
        "what is git", "what is an api",
    ],
    "networking": [
        "what is dns", "what is dhcp", "what is a router",
        "what is a subnet", "what is an ip address", "what is nat",
        "what is arp", "what is icmp",
    ],
    "linux": [
        "what is linux", "what is a terminal", "what is bash",
        "how to use the command line", "what is sudo", "what is root",
        "what is a package manager",
    ],
}

ADVANCED_SIGNALS: dict[str, list[str]] = {
    "cybersecurity": [
        "syn scan", "evasion", "cve-", "payload", "reverse shell",
        "buffer overflow", "privilege escalation", "lateral movement",
        "c2 beacon", "sigint", "opsec", "zero day", "rop chain",
        "shellcode", "msfvenom", "cobalt strike", "bloodhound",
        "mimikatz", "kerberoast", "pass the hash",
    ],
    "programming": [
        "decorator", "metaclass", "coroutine", "async", "generator",
        "type hint", "protocol", "abc", "dataclass", "descriptor",
        "garbage collector", "bytecode", "ast", "cffi", "cython",
        "monkeypatch", "dependency injection",
    ],
    "networking": [
        "bgp", "ospf", "vlan", "mpls", "qos", "sdn",
        "netflow", "snmp trap", "ipsec tunnel", "gre",
        "spanning tree", "vxlan", "segment routing",
    ],
    "linux": [
        "cgroup", "namespace", "ebpf", "seccomp", "systemd unit",
        "inotify", "dbus", "udev", "selinux", "apparmor",
        "overlayfs", "btrfs snapshot", "nftables",
    ],
}

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_STYLE: dict[str, Any] = {
    "preferred_length": "normal",
    "formality": 0.5,
    "uses_emoji": False,
    "prefers_examples": True,
}

_INTEREST_DECAY_HALF_LIFE = 7 * 24 * 3600  # 1 week in seconds


# ---------------------------------------------------------------------------
# UserModel
# ---------------------------------------------------------------------------

class UserModel:
    """Lightweight, heuristic-based model of a single user."""

    def __init__(self) -> None:
        # domain -> 0.0 (novice) .. 1.0 (expert)
        self.expertise: dict[str, float] = defaultdict(lambda: 0.5)
        # topic -> cumulative weight
        self.interests: dict[str, float] = defaultdict(float)
        # timestamp of last interest update per topic
        self._interest_ts: dict[str, float] = {}
        # communication preferences
        self.style: dict[str, Any] = dict(_DEFAULT_STYLE)
        # things the user told Jarvis to change
        self.corrections: list[str] = []
        # ephemeral session context
        self.context: dict[str, Any] = {
            "current_project": None,
            "current_mood": "neutral",
            "session_topic": None,
        }
        # bookkeeping
        self._observation_count: int = 0
        self._created_at: float = time.time()

    # ------------------------------------------------------------------
    # Core update
    # ------------------------------------------------------------------

    def observe(self, user_input: str, intent: str, entities: dict) -> None:
        """Ingest one user turn and update every facet of the model."""
        self._observation_count += 1
        low = user_input.lower()

        # --- expertise adjustment ---
        for domain, phrases in BEGINNER_SIGNALS.items():
            if any(p in low for p in phrases):
                self._adjust_expertise(domain, -0.15)

        for domain, phrases in ADVANCED_SIGNALS.items():
            if any(p in low for p in phrases):
                self._adjust_expertise(domain, 0.10)

        # --- interests ---
        self._track_interests(low, entities)

        # --- communication style signals ---
        if any(e in low for e in [":)", ":-)", ":D", "lol", "haha"]):
            self.style["uses_emoji"] = True
            self.style["formality"] = max(0.0, self.style["formality"] - 0.05)

        # --- context ---
        subject = entities.get("subject")
        if subject:
            self.context["session_topic"] = subject

    # ------------------------------------------------------------------
    # Expertise helpers
    # ------------------------------------------------------------------

    def _adjust_expertise(self, domain: str, delta: float) -> None:
        current = self.expertise[domain]
        self.expertise[domain] = max(0.0, min(1.0, current + delta))

    def get_expertise(self, domain: str) -> float:
        """Return expertise level for *domain* (0.0 .. 1.0)."""
        return self.expertise[domain]

    # ------------------------------------------------------------------
    # Interest tracking (with time-decay)
    # ------------------------------------------------------------------

    def _track_interests(self, text: str, entities: dict) -> None:
        now = time.time()
        # Extract topics from entities and raw text
        topics: list[str] = []
        for v in entities.values():
            if isinstance(v, str):
                topics.append(v.lower())
        # Also pull meaningful words (> 3 chars) that aren't stop-words
        _stop = {
            "what", "that", "this", "with", "from", "have", "will",
            "been", "they", "their", "your", "about", "would", "there",
            "could", "other", "into", "some", "than", "them", "these",
            "then", "when",
        }
        for word in text.split():
            w = word.strip(".,!?\"'()[]{}:;")
            if len(w) > 3 and w not in _stop:
                topics.append(w)

        for topic in topics:
            # Apply decay to existing weight
            if topic in self._interest_ts:
                elapsed = now - self._interest_ts[topic]
                decay = math.exp(-0.693 * elapsed / _INTEREST_DECAY_HALF_LIFE)
                self.interests[topic] *= decay
            self.interests[topic] += 1.0
            self._interest_ts[topic] = now

    def top_interests(self, n: int = 5) -> list[tuple[str, float]]:
        """Return the *n* topics with highest (decayed) weight."""
        now = time.time()
        scored: list[tuple[str, float]] = []
        for topic, weight in self.interests.items():
            elapsed = now - self._interest_ts.get(topic, now)
            decay = math.exp(-0.693 * elapsed / _INTEREST_DECAY_HALF_LIFE)
            scored.append((topic, round(weight * decay, 4)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:n]

    # ------------------------------------------------------------------
    # Communication / corrections
    # ------------------------------------------------------------------

    def record_correction(self, correction: str) -> None:
        """Record an explicit user correction and update style."""
        self.corrections.append(correction)
        low = correction.lower()
        if any(k in low for k in ["concise", "shorter", "brief", "less"]):
            self.style["preferred_length"] = "brief"
        elif any(k in low for k in ["explain", "detail", "more", "longer"]):
            self.style["preferred_length"] = "detailed"
        if "formal" in low:
            self.style["formality"] = min(1.0, self.style["formality"] + 0.2)
        if "casual" in low or "chill" in low:
            self.style["formality"] = max(0.0, self.style["formality"] - 0.2)

    def get_response_guidance(self) -> dict:
        """
        Produce a guidance dict that downstream response generators can
        use to shape their output.
        """
        # Average expertise across all observed domains
        if self.expertise:
            avg_exp = sum(self.expertise.values()) / len(self.expertise)
        else:
            avg_exp = 0.5

        # Derive tone from formality
        if self.style["formality"] > 0.7:
            tone = "formal"
        elif self.style["formality"] < 0.3:
            tone = "casual"
        else:
            tone = "neutral"

        return {
            "length": self.style["preferred_length"],
            "technical_level": round(avg_exp, 2),
            "tone": tone,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        """Serialize the model to a JSON file."""
        data = {
            "expertise": dict(self.expertise),
            "interests": dict(self.interests),
            "interest_ts": self._interest_ts,
            "style": self.style,
            "corrections": self.corrections,
            "context": self.context,
            "observation_count": self._observation_count,
            "created_at": self._created_at,
        }
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: str) -> "UserModel":
        """Deserialize a model from a JSON file."""
        data = json.loads(Path(path).read_text())
        model = cls()
        model.expertise = defaultdict(lambda: 0.5, data.get("expertise", {}))
        model.interests = defaultdict(float, data.get("interests", {}))
        model._interest_ts = data.get("interest_ts", {})
        model.style = data.get("style", dict(_DEFAULT_STYLE))
        model.corrections = data.get("corrections", [])
        model.context = data.get("context", {
            "current_project": None,
            "current_mood": "neutral",
            "session_topic": None,
        })
        model._observation_count = data.get("observation_count", 0)
        model._created_at = data.get("created_at", time.time())
        return model

    # ------------------------------------------------------------------
    # Stats / introspection
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        """Quick summary for debugging / dashboards."""
        return {
            "observations": self._observation_count,
            "domains_tracked": len(self.expertise),
            "topics_tracked": len(self.interests),
            "corrections_count": len(self.corrections),
            "style": dict(self.style),
            "top_interests": self.top_interests(3),
            "expertise_snapshot": {
                k: round(v, 2) for k, v in self.expertise.items()
            },
        }

    def __repr__(self) -> str:
        return (
            f"<UserModel observations={self._observation_count} "
            f"domains={len(self.expertise)} "
            f"interests={len(self.interests)}>"
        )
