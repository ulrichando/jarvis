"""JARVIS Reinforcement Learning — learn from every interaction.

A lightweight Q-learning system that learns which response strategies
work best in different situations. No heavy ML frameworks — just numpy
and a Q-table that maps (state) → (action preferences).

The RL system doesn't replace JARVIS's reasoning. It nudges strategy
selection: when to be brief vs detailed, cautious vs confident,
when to force the agent loop vs standard response.
"""

import json
import time
import numpy as np
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── State Space ─────────────────────────────────────────────────────
# 6 dimensions from SelfAwareness, discretized into bins
# Total: 5 × 5 × 3 × 3 × 3 × 3 = 2,025 states

STATE_DIMS = (5, 5, 3, 3, 3, 3)
NUM_STATES = 2025
NUM_ACTIONS = 6

ACTION_NAMES = [
    "brief_confident",     # Short, no hedging, skip deep_think
    "brief_cautious",      # Short, include caveats, may clarify
    "detailed_confident",  # Thorough, no hedging
    "detailed_cautious",   # Thorough with caveats, verify first
    "agent_loop_force",    # Force tool-calling agent loop
    "standard_force",      # Force standard LLM response
]

INTENT_MAP = {
    "asking": 0, "commanding": 1, "exploring": 2,
    "venting": 3, "teaching": 4, "unknown": 2,
}
ENERGY_MAP = {
    "frustrated": 0, "low": 1, "neutral": 2,
    "high": 3, "excited": 4,
}

# ── Sentiment Detection (for delayed reward) ───────────────────────

POSITIVE_SIGNALS = {
    "awesome", "perfect", "yes!", "nice", "cool", "love it",
    "amazing", "great", "thanks", "thank you", "exactly",
    "good", "works", "nailed it", "spot on",
}
NEGATIVE_SIGNALS = {
    "wtf", "broken", "doesn't work", "still not", "wrong",
    "fuck", "shit", "damn", "nope", "bad", "terrible",
    "useless", "stop",
}
CORRECTION_SIGNALS = {
    "actually", "i meant", "not that", "correction",
    "no,", "no.", "wrong,", "that's not",
}


@dataclass
class InteractionRecord:
    """Record of a single interaction for delayed reward."""
    state_idx: int
    action_idx: int
    immediate_reward: float
    timestamp: float
    needs_delayed_update: bool = True


class ReinforcementLearner:
    """Q-learning policy for JARVIS response strategy selection.

    State: (user_intent, user_energy, confidence, energy, depth, streak)
    Action: one of 6 strategy presets
    Reward: composite of quality score + latency + streak + delayed sentiment
    Update: Q-learning with adaptive learning rate
    """

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.policy_file = data_dir / "rl_policy.json"

        # Q-table: state × action → expected reward
        self.q_table = np.zeros((NUM_STATES, NUM_ACTIONS), dtype=np.float64)
        self.visit_counts = np.zeros((NUM_STATES, NUM_ACTIONS), dtype=np.int32)

        # Hyperparameters
        self.alpha = 0.1          # base learning rate
        self.gamma = 0.9          # discount factor
        self.epsilon = 0.15       # exploration rate
        self.epsilon_min = 0.02   # floor — always explore a little
        self.epsilon_decay = 0.999
        self.total_updates = 0

        # Interaction buffer for delayed rewards
        self._last_interaction: Optional[InteractionRecord] = None
        self._current_state_idx: int = 0

        self._load()

    # ── State Encoding ──────────────────────────────────────────────

    def encode_state(self, awareness) -> int:
        """Convert SelfAwareness into a Q-table index (0–2024)."""
        intent = INTENT_MAP.get(awareness.user_intent, 2)
        energy = ENERGY_MAP.get(awareness.user_energy, 2)
        conf = 0 if awareness.confidence < 0.4 else (2 if awareness.confidence > 0.7 else 1)
        eng = 0 if awareness.energy < 0.4 else (2 if awareness.energy > 0.7 else 1)
        depth = min(2, awareness.conversation_depth // 2)
        streak = (0 if awareness.consecutive_failures >= 2
                  else 2 if awareness.consecutive_successes >= 3 else 1)

        # Mixed-radix encoding → single index
        # STATE_DIMS = (5, 5, 3, 3, 3, 3)
        # idx = intent*675 + energy*135 + conf*27 + eng*9 + depth*3 + streak
        idx = ((((intent * 5 + energy) * 3 + conf) * 3 + eng) * 3 + depth) * 3 + streak
        return min(idx, NUM_STATES - 1)

    # ── Action Selection ────────────────────────────────────────────

    def select_action(self, state_idx: int) -> int:
        """Epsilon-greedy: explore with probability epsilon, exploit otherwise."""
        if np.random.random() < self.epsilon:
            return int(np.random.randint(NUM_ACTIONS))
        return int(np.argmax(self.q_table[state_idx]))

    def get_strategy(self, awareness) -> dict:
        """Main entry point: return strategy dict for current state."""
        state_idx = self.encode_state(awareness)
        action_idx = self.select_action(state_idx)
        self._current_state_idx = state_idx

        action_name = ACTION_NAMES[action_idx]
        return {
            "action_idx": action_idx,
            "action_name": action_name,
            "state_idx": state_idx,
            "force_agent": action_name == "agent_loop_force",
            "force_standard": action_name == "standard_force",
            "be_brief": action_name.startswith("brief_"),
            "be_cautious": "cautious" in action_name,
            "be_detailed": action_name.startswith("detailed_"),
        }

    # ── Reward & Learning ───────────────────────────────────────────

    def record_outcome(self, state_idx: int, action_idx: int,
                       quality_score: float, latency_ms: float,
                       consec_successes: int):
        """Record immediate reward after a response. Delayed reward comes later."""
        # Quality: map 0.0–1.0 → -1.0–1.0
        r_quality = (quality_score - 0.5) * 2.0

        # Latency: faster is better (avg ~2000ms, cap bonus at 0.2)
        r_latency = max(0.0, min(0.2, (3000 - latency_ms) / 15000))

        # Streak: consecutive successes
        r_streak = min(0.3, consec_successes * 0.05)

        immediate = 0.5 * r_quality + 0.15 * r_latency + 0.1 * r_streak

        self._last_interaction = InteractionRecord(
            state_idx=state_idx,
            action_idx=action_idx,
            immediate_reward=immediate,
            timestamp=time.time(),
        )

    def apply_delayed_reward(self, user_input: str):
        """Called when new user message arrives.

        Classifies sentiment of the new message, retroactively updates
        the Q-value for the previous interaction.
        """
        if self._last_interaction is None or not self._last_interaction.needs_delayed_update:
            return

        sentiment = self._classify_sentiment(user_input)
        r_sentiment = 0.0
        if sentiment == "positive":
            r_sentiment = 0.4
        elif sentiment == "negative":
            r_sentiment = -0.5
        elif sentiment == "correction":
            r_sentiment = -0.3

        total_reward = self._last_interaction.immediate_reward + 0.25 * r_sentiment
        total_reward = max(-1.0, min(1.0, total_reward))

        # Q-learning update
        s = self._last_interaction.state_idx
        a = self._last_interaction.action_idx
        s_next = self._current_state_idx

        n = self.visit_counts[s, a]
        adaptive_alpha = self.alpha / (1 + 0.01 * n)

        self.q_table[s, a] += adaptive_alpha * (
            total_reward + self.gamma * np.max(self.q_table[s_next]) - self.q_table[s, a]
        )
        self.visit_counts[s, a] += 1
        self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)
        self.total_updates += 1

        self._last_interaction.needs_delayed_update = False

        # Auto-save every 20 updates
        if self.total_updates % 20 == 0:
            self._save()

    # ── Sentiment Classification ────────────────────────────────────

    @staticmethod
    def _classify_sentiment(user_input: str) -> str:
        """Classify user's next message as feedback on previous response."""
        q = user_input.lower().strip()
        words = set(q.replace(",", " ").replace(".", " ").replace("!", " ").split())
        # Check multi-word phrases first (substring), then single words (exact)
        if any(phrase in q for phrase in ("i meant", "not that", "that's not", "no, ", "no. ")):
            return "correction"
        if any(phrase in q for phrase in ("doesn't work", "still not")):
            return "negative"
        if words & {"correction"}:
            return "correction"
        if words & {"wtf", "broken", "wrong", "fuck", "shit", "damn", "nope", "bad", "terrible", "useless", "stop"}:
            return "negative"
        if words & {"awesome", "perfect", "nice", "cool", "amazing", "great", "thanks", "exactly", "good", "works"}:
            return "positive"
        if "thank you" in q or "love it" in q or "nailed it" in q or "spot on" in q:
            return "positive"
        return "neutral"

    # ── Persistence ─────────────────────────────────────────────────

    def _save(self):
        """Save policy to JSON."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.policy_file.with_suffix(".tmp")
        data = {
            "version": 1,
            "total_updates": self.total_updates,
            "epsilon": self.epsilon,
            "q_table": self.q_table.tolist(),
            "visit_counts": self.visit_counts.tolist(),
        }
        with open(tmp, "w") as f:
            json.dump(data, f)
        tmp.rename(self.policy_file)

    def _load(self):
        """Load saved policy if it exists."""
        if not self.policy_file.exists():
            return
        try:
            with open(self.policy_file) as f:
                data = json.load(f)
            self.q_table = np.array(data["q_table"], dtype=np.float64)
            self.visit_counts = np.array(data["visit_counts"], dtype=np.int32)
            self.epsilon = data.get("epsilon", 0.15)
            self.total_updates = data.get("total_updates", 0)
        except Exception:
            pass  # Start fresh if corrupted

    def save(self):
        """Public save — call on shutdown."""
        self._save()

    # ── Stats ───────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Human-readable stats for diagnostics."""
        visited = self.visit_counts.sum()
        if visited == 0:
            return {
                "total_updates": 0,
                "epsilon": round(self.epsilon, 4),
                "status": "cold — no interactions yet",
            }

        # Most-used actions
        action_totals = self.visit_counts.sum(axis=0)
        favorite = ACTION_NAMES[int(np.argmax(action_totals))]

        return {
            "total_updates": self.total_updates,
            "epsilon": round(self.epsilon, 4),
            "total_visits": int(visited),
            "favorite_action": favorite,
            "avg_q": round(float(self.q_table.mean()), 4),
            "max_q": round(float(self.q_table.max()), 4),
            "min_q": round(float(self.q_table.min()), 4),
        }
