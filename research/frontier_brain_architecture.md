# Frontier Brain Architecture Research for JARVIS

## Status: Deep research based on published literature through 2025

> Every technique below is chosen for ONE criterion: **can be built in Python
> without training large neural networks**. Jarvis already has holographic
> memory, ACT-R activation, MoE routing, GWT consciousness, STDP, dreaming,
> and forward/backward chaining. This document describes what comes NEXT.

---

## 1. World Models -- Internal Simulation for Planning

### 1.1 Key Insight

The brain does not wait for reality. It **predicts** what will happen and
only pays attention when prediction fails ("prediction error"). Yann LeCun's
JEPA framework (2022-2024) crystallized the insight: instead of generating
raw pixels or text, predict in **abstract representation space**. This avoids
the intractable problem of predicting every detail of the future and focuses
on predicting the *structure* that matters for action.

The core idea for Jarvis: maintain an internal world model that can answer
"if I do X in state S, what state S' results?" -- enabling mental rehearsal
(planning by imagination) without executing anything in the real world.

**Key papers:**
- Ha & Schmidhuber, "World Models" (2018) -- learn compressed world + plan inside it
- LeCun, "A Path Towards Autonomous Machine Intelligence" (2022) -- JEPA framework
- Assran et al., "I-JEPA" (2023) -- non-generative image world model
- Hafner et al., "DreamerV3" (2023) -- world model for control across domains

### 1.2 JEPA Core Concept (No Large NN Required)

JEPA's key departure from generative models: instead of predicting raw
observations, predict **embeddings**. For Jarvis, "embeddings" are already
the holographic memory vectors and the concept representations in the
associative memory.

The JEPA loop:
```
state_embedding = encode(current_state)
action_embedding = encode(planned_action)
predicted_next_state = predictor(state_embedding, action_embedding)
actual_next_state = encode(what_actually_happened)
prediction_error = distance(predicted_next_state, actual_next_state)
```

The predictor only needs to work in the abstract space -- no pixel generation.

### 1.3 Implementable Algorithm: Lightweight World Model

```python
"""World Model for JARVIS -- predict outcomes of actions in abstract space.

Instead of neural networks, uses:
- State = structured dict (emotional state, user model, memory activations, context)
- Transition rules = learned statistical patterns + hand-coded causal rules
- Prediction = apply transition to get predicted next state
- Error = compare predicted vs actual --> drives learning

This is model-based RL without the RL framework overhead.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class WorldState:
    """Compressed representation of Jarvis's world at one moment."""
    user_mood: str = "neutral"          # frustrated, neutral, engaged, excited
    topic: str = ""                     # current conversation topic
    user_intent: str = ""               # asking, commanding, exploring, teaching
    confidence: float = 0.5            # Jarvis's confidence
    emotional_valence: float = 0.0     # -1 to 1
    memory_activation: float = 0.0     # how active memory retrieval is
    conversation_depth: int = 0        # turns on current topic
    recent_success: bool = True        # did last action succeed

    def to_key(self) -> str:
        """Discretize to a hashable state key for transition table."""
        mood_bin = self.user_mood
        conf_bin = "low" if self.confidence < 0.4 else "mid" if self.confidence < 0.7 else "high"
        depth_bin = "shallow" if self.conversation_depth < 3 else "deep"
        return f"{mood_bin}|{self.user_intent}|{conf_bin}|{depth_bin}"

    def distance(self, other: 'WorldState') -> float:
        """Distance between two states for prediction error."""
        d = 0.0
        d += (0.0 if self.user_mood == other.user_mood else 0.3)
        d += (0.0 if self.user_intent == other.user_intent else 0.2)
        d += abs(self.confidence - other.confidence) * 0.2
        d += abs(self.emotional_valence - other.emotional_valence) * 0.2
        d += (0.0 if self.recent_success == other.recent_success else 0.1)
        return d


@dataclass
class ActionOutcome:
    """What happened when we took an action in a state."""
    action: str                # "answer_directly", "ask_clarification", "run_command", "search", etc.
    from_state: WorldState
    to_state: WorldState
    reward: float = 0.0        # user satisfaction signal


class WorldModel:
    """Jarvis's internal simulator -- predict before acting.

    Learns transition probabilities:  P(next_state | current_state, action)
    Uses these to mentally simulate: "if I do X, what happens?"

    No neural network. Just a transition table that grows from experience.
    """

    def __init__(self):
        # Transition counts: (state_key, action) -> Counter(next_state_key: count)
        self._transitions: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # Reward model: (state_key, action) -> list of rewards
        self._rewards: dict[tuple[str, str], list[float]] = defaultdict(list)
        # Causal rules: hard-coded domain knowledge
        self._causal_rules: list[callable] = []
        # Prediction error history for curiosity signal
        self._prediction_errors: list[float] = []

    def observe_transition(self, outcome: ActionOutcome):
        """Learn from an observed state transition."""
        key = (outcome.from_state.to_key(), outcome.action)
        next_key = outcome.to_state.to_key()
        self._transitions[key][next_key] += 1
        self._rewards[key].append(outcome.reward)

    def predict(self, state: WorldState, action: str) -> tuple[str, float, float]:
        """Predict: if I do 'action' in 'state', what state results?

        Returns: (predicted_next_state_key, confidence, expected_reward)
        """
        key = (state.to_key(), action)
        trans = self._transitions.get(key, {})

        if not trans:
            # No data -- check causal rules
            for rule in self._causal_rules:
                result = rule(state, action)
                if result:
                    return result
            return ("unknown", 0.0, 0.0)

        # Most likely next state
        total = sum(trans.values())
        best_next = max(trans, key=trans.get)
        confidence = trans[best_next] / total

        # Expected reward
        rewards = self._rewards.get(key, [0.0])
        expected_reward = sum(rewards) / len(rewards)

        return (best_next, confidence, expected_reward)

    def simulate_plan(self, current_state: WorldState, action_sequence: list[str]) -> list[dict]:
        """Mental rehearsal: simulate a sequence of actions.

        "If I first ask for clarification, then search, then answer..."
        Returns predicted trajectory with confidence at each step.
        """
        trajectory = []
        state = current_state

        for action in action_sequence:
            next_state_key, conf, reward = self.predict(state, action)
            trajectory.append({
                "action": action,
                "from_state": state.to_key(),
                "predicted_next": next_state_key,
                "confidence": conf,
                "expected_reward": reward,
            })
            # For multi-step, we'd need to reconstruct a WorldState from the key
            # This is a simplification -- in practice, maintain running state
            if conf < 0.2:
                trajectory[-1]["warning"] = "low confidence -- simulation unreliable beyond this point"
                break

        return trajectory

    def best_action(self, state: WorldState, candidate_actions: list[str]) -> tuple[str, float]:
        """Choose the best action by simulating all candidates.

        This is one-step lookahead planning.
        """
        best = None
        best_score = float('-inf')

        for action in candidate_actions:
            _, confidence, reward = self.predict(state, action)
            # Score = expected reward weighted by confidence
            score = reward * confidence
            if score > best_score:
                best_score = score
                best = action

        return (best or candidate_actions[0], best_score)

    def get_prediction_error(self, predicted_key: str, actual_state: WorldState) -> float:
        """Compute prediction error -- drives curiosity and learning."""
        actual_key = actual_state.to_key()
        error = 0.0 if predicted_key == actual_key else 1.0
        self._prediction_errors.append(error)
        return error

    def add_causal_rule(self, rule):
        """Add a hard-coded causal rule for common-sense reasoning.

        rule(state, action) -> (next_state_key, confidence, reward) or None
        """
        self._causal_rules.append(rule)


# -- Example causal rules --

def rule_frustrated_user(state: WorldState, action: str):
    """If user is frustrated and we ask clarification, they get more frustrated."""
    if state.user_mood == "frustrated" and action == "ask_clarification":
        return ("frustrated|commanding|low|shallow", 0.8, -0.5)
    return None

def rule_teaching_listen(state: WorldState, action: str):
    """If user is teaching and we listen+confirm, rapport improves."""
    if state.user_intent == "teaching" and action == "confirm_understanding":
        return ("engaged|teaching|high|deep", 0.7, 0.8)
    return None
```

### 1.4 How It Applies to Jarvis Specifically

Jarvis's `PredictiveEngine` currently predicts topic/intent sequences. The
world model extends this to predict **full state transitions**, including:

- **Before answering:** simulate "if I answer directly" vs "if I ask for
  clarification" and pick the action with higher expected satisfaction
- **Before running commands:** simulate "if this command fails, user mood
  shifts to frustrated, I should have a recovery plan ready"
- **During dreaming:** run Monte Carlo simulations of hypothetical
  conversations to pre-learn transition patterns
- **Integrate with GlobalWorkspace:** the world model's predictions become
  signals that compete for broadcast ("prediction says user will ask about X
  next -- preload relevant memories")

Wire it in: `WorldModel.observe_transition()` after every response,
`WorldModel.best_action()` inside the reasoning engine before responding.

---

## 2. Meta-Learning / Learning to Learn

### 2.1 Key Insight

MAML (Finn et al., 2017) showed that you can learn an **initialization** from
which a few gradient steps on a new task produce good performance. The key
insight for Jarvis: don't start from scratch on every new type of question.
Instead, maintain **task-specific adaptation parameters** that can be
rapidly tuned from a few examples.

Without neural networks, this translates to: maintain a library of
**strategy templates** (one per task type) with tunable parameters, and
use experience to find the parameter settings that adapt fastest.

**Key papers:**
- Finn et al., "Model-Agnostic Meta-Learning" (2017) -- MAML
- Ha et al., "HyperNetworks" (2016) -- networks generating weights
- Hospedales et al., "Meta-Learning in Neural Networks: A Survey" (2022)

### 2.2 Implementable Algorithm: Strategy Meta-Learner

```python
"""Meta-Learning for JARVIS -- learn HOW to learn new tasks fast.

Core idea: maintain a library of "strategy templates" -- one per task type.
Each template has tunable parameters. When a new task arrives:
1. Find the most similar known task type
2. Clone its strategy template
3. Adapt parameters from the few examples available
4. If the adapted strategy works, save it as a new template

This is MAML without gradients -- using Bayesian parameter updates instead.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StrategyTemplate:
    """A reusable approach to a class of tasks."""
    name: str
    task_type: str                          # "factual_qa", "command_exec", "teaching", etc.
    parameters: dict[str, float]           # tunable knobs
    success_history: list[bool] = field(default_factory=list)
    adaptation_speed: float = 0.3          # how fast params change (learned!)
    examples_needed: int = 3               # how many examples before reliable

    @property
    def success_rate(self) -> float:
        if not self.success_history:
            return 0.5
        recent = self.success_history[-20:]
        return sum(recent) / len(recent)

    @property
    def confidence(self) -> float:
        """Confidence grows with experience, follows beta distribution intuition."""
        n = len(self.success_history)
        if n == 0:
            return 0.0
        successes = sum(self.success_history[-30:])
        failures = len(self.success_history[-30:]) - successes
        # Beta distribution mean: (a) / (a + b), with prior a=1, b=1
        return (successes + 1) / (successes + failures + 2)


@dataclass
class TaskContext:
    """What we know about the current task for meta-learning."""
    task_type: str
    features: dict[str, float]    # numeric features of the task
    examples: list[dict] = field(default_factory=list)  # few-shot examples


class MetaLearner:
    """JARVIS learns HOW to learn -- adapts strategies from few examples.

    Architecture:
    1. Strategy Library: collection of StrategyTemplates
    2. Task Recognizer: maps inputs to task types
    3. Adapter: tunes strategy params from few examples
    4. Meta-optimizer: improves adaptation_speed over time (learning to learn)
    """

    def __init__(self):
        self._library: dict[str, StrategyTemplate] = {}
        self._task_history: list[dict] = []
        # Meta-parameters: how fast to adapt (learned from experience)
        self._meta_lr = 0.1  # meta learning rate
        self._adaptation_history: list[float] = []  # track adaptation quality

    def register_strategy(self, template: StrategyTemplate):
        """Add a strategy template to the library."""
        self._library[template.name] = template

    def get_strategy(self, task: TaskContext) -> StrategyTemplate:
        """Find the best strategy for a task -- core meta-learning step.

        1. Find strategies matching this task type
        2. If none exist, find the NEAREST task type and clone
        3. If examples available, adapt parameters
        """
        # Exact task type match
        matches = [s for s in self._library.values() if s.task_type == task.task_type]

        if matches:
            # Pick the one with best success rate
            best = max(matches, key=lambda s: s.success_rate)
            if task.examples:
                return self._adapt(best, task.examples)
            return best

        # No exact match -- find nearest and clone (few-shot transfer)
        if self._library:
            nearest = self._find_nearest_task_type(task.task_type)
            if nearest:
                clone = StrategyTemplate(
                    name=f"{task.task_type}_from_{nearest.name}",
                    task_type=task.task_type,
                    parameters=dict(nearest.parameters),
                    adaptation_speed=nearest.adaptation_speed,
                )
                self._library[clone.name] = clone
                if task.examples:
                    return self._adapt(clone, task.examples)
                return clone

        # Totally new -- create default
        default = StrategyTemplate(
            name=f"default_{task.task_type}",
            task_type=task.task_type,
            parameters={"verbosity": 0.5, "confidence_threshold": 0.4,
                        "search_depth": 3, "creativity": 0.3},
        )
        self._library[default.name] = default
        return default

    def _adapt(self, template: StrategyTemplate, examples: list[dict]) -> StrategyTemplate:
        """Adapt strategy parameters from few-shot examples.

        This is the inner loop of MAML -- but with Bayesian updates
        instead of gradient descent.
        """
        for example in examples:
            outcome = example.get("outcome", {})
            # For each parameter, nudge toward values that worked
            for param, value in template.parameters.items():
                if param in outcome:
                    target = outcome[param]
                    delta = (target - value) * template.adaptation_speed
                    template.parameters[param] = value + delta

        return template

    def record_outcome(self, strategy_name: str, success: bool, details: dict = None):
        """Record whether a strategy worked -- feeds meta-learning."""
        strategy = self._library.get(strategy_name)
        if not strategy:
            return

        strategy.success_history.append(success)

        # Meta-learning: adjust adaptation_speed based on recent performance
        if len(strategy.success_history) >= 5:
            recent_rate = sum(strategy.success_history[-5:]) / 5
            if recent_rate > 0.8:
                # Doing well -- slow down adaptation (don't fix what works)
                strategy.adaptation_speed *= 0.95
            elif recent_rate < 0.4:
                # Doing poorly -- speed up adaptation (need to change faster)
                strategy.adaptation_speed = min(0.8, strategy.adaptation_speed * 1.1)

        self._task_history.append({
            "strategy": strategy_name,
            "success": success,
            "details": details or {},
        })

    def _find_nearest_task_type(self, task_type: str) -> StrategyTemplate | None:
        """Find the strategy for the most similar task type.

        Uses keyword overlap as a simple similarity measure.
        """
        task_words = set(task_type.lower().replace("_", " ").split())
        best = None
        best_overlap = 0

        for strategy in self._library.values():
            strat_words = set(strategy.task_type.lower().replace("_", " ").split())
            overlap = len(task_words & strat_words)
            if overlap > best_overlap:
                best_overlap = overlap
                best = strategy

        return best

    def get_meta_stats(self) -> dict:
        """Statistics about meta-learning performance."""
        return {
            "strategies": len(self._library),
            "total_tasks": len(self._task_history),
            "strategies_by_type": {s.task_type: s.success_rate
                                   for s in self._library.values()},
            "avg_adaptation_speed": (
                sum(s.adaptation_speed for s in self._library.values()) /
                max(1, len(self._library))
            ),
        }


# -- Bootstrap with default strategies --

def create_default_strategies() -> list[StrategyTemplate]:
    """Pre-built strategies for common task types."""
    return [
        StrategyTemplate(
            name="factual_qa",
            task_type="factual_qa",
            parameters={"search_depth": 5, "confidence_threshold": 0.6,
                        "use_memory_first": 1.0, "verbosity": 0.5},
        ),
        StrategyTemplate(
            name="command_execution",
            task_type="command_execution",
            parameters={"caution_level": 0.7, "confirm_destructive": 1.0,
                        "timeout": 15.0, "verbosity": 0.2},
        ),
        StrategyTemplate(
            name="creative_exploration",
            task_type="creative_exploration",
            parameters={"creativity": 0.8, "verbosity": 0.8,
                        "confidence_threshold": 0.3, "branch_factor": 3},
        ),
        StrategyTemplate(
            name="teaching_absorption",
            task_type="teaching",
            parameters={"listen_ratio": 0.9, "confirm_understanding": 1.0,
                        "store_strength": 0.9, "ask_followup": 0.5},
        ),
    ]
```

### 2.3 How It Applies to Jarvis

The `MoERouter` currently routes to experts with fixed parameters. Meta-learning
adds **adaptive parameters per expert per task type**:

- When Jarvis encounters a new type of question it hasn't seen before, it
  clones the closest known strategy and adapts from the first few examples
- The `adaptation_speed` itself is learned: strategies that need fast
  adaptation (security questions where context changes fast) learn a high
  speed; strategies for stable tasks (greetings) learn a low speed
- Integrates with the dream cycle: during consolidation, merge strategies
  that have converged to similar parameters

---

## 3. Attention and Focus

### 3.1 Key Insight

Biological attention is not one thing -- it is at least two:

1. **Bottom-up (exogenous):** stimulus-driven, fast, involuntary.
   A loud noise grabs attention. In Jarvis: high-salience signals in the
   GlobalWorkspace already do this.

2. **Top-down (endogenous):** goal-driven, slow, voluntary.
   "I'm looking for security vulnerabilities" -- biases all perception toward
   security-relevant patterns. Jarvis does NOT have this yet.

The missing piece: **attentional control** -- a mechanism that biases ALL
subsystems based on the current goal, filters irrelevant information BEFORE
it reaches consciousness, and manages **attention as a finite resource** that
can be fatigued and refreshed.

**Key papers:**
- Desimone & Duncan, "Neural Mechanisms of Selective Visual Attention" (1995) -- biased competition
- Posner & Petersen, attentional networks (1990)
- Corbetta & Shulman, top-down vs bottom-up attention (2002)
- Itti & Koch, saliency maps (1998)

### 3.2 Implementable Algorithm: Attentional Controller

```python
"""Attentional Controller for JARVIS -- focus on what matters.

Three components:
1. GOAL REGISTER: what Jarvis is currently trying to do
2. ATTENTION FILTER: biases all inputs toward goal-relevant information
3. FATIGUE MODEL: attention is a finite resource that depletes and recovers

This sits BETWEEN input and the GlobalWorkspace -- filtering and biasing
signals before they compete for consciousness.
"""

import time
import math
from dataclasses import dataclass, field
from collections import deque


@dataclass
class AttentionalGoal:
    """What Jarvis is currently trying to pay attention to."""
    description: str           # "help user debug Python error"
    priority: float = 0.5     # 0-1, how important
    keywords: set[str] = field(default_factory=set)  # attention-biasing terms
    created_at: float = field(default_factory=time.time)
    ttl: float = 300.0        # goal expires after 5 min without reinforcement

    @property
    def is_active(self) -> bool:
        return (time.time() - self.created_at) < self.ttl

    def reinforce(self):
        """User is still on this topic -- extend the goal."""
        self.created_at = time.time()


class AttentionFatigue:
    """Models attention as a depletable resource.

    Based on: Kahneman's resource theory of attention (1973)
    and the ego depletion model (Baumeister et al., 1998).

    Attention capacity starts at 1.0, depletes with sustained focus,
    and recovers during idle or topic switches.
    """

    def __init__(self):
        self.capacity = 1.0        # 0.0 = exhausted, 1.0 = fresh
        self.sustained_focus_time = 0.0   # seconds on current topic
        self._last_update = time.time()

    def update(self, is_focused: bool, dt: float = None):
        """Update fatigue based on elapsed time and focus state."""
        if dt is None:
            now = time.time()
            dt = now - self._last_update
            self._last_update = now

        if is_focused:
            self.sustained_focus_time += dt
            # Attention depletes logarithmically -- fast at first, then slower
            depletion = 0.02 * math.log1p(self.sustained_focus_time / 60.0) * (dt / 60.0)
            self.capacity = max(0.1, self.capacity - depletion)
        else:
            # Recovery is faster than depletion (like real rest)
            recovery = 0.05 * (dt / 60.0)
            self.capacity = min(1.0, self.capacity + recovery)
            self.sustained_focus_time = max(0, self.sustained_focus_time - dt * 0.5)

    @property
    def is_fatigued(self) -> bool:
        return self.capacity < 0.4

    @property
    def should_suggest_break(self) -> bool:
        return self.capacity < 0.25 and self.sustained_focus_time > 1800  # 30 min


class AttentionalController:
    """Manages Jarvis's focus -- what to attend to, what to ignore.

    Architecture:
    ┌─────────────────────────────────────┐
    │  ATTENTIONAL CONTROLLER             │
    │                                     │
    │  ┌──────────┐  ┌───────────────┐    │
    │  │  Goal     │  │  Fatigue      │    │
    │  │  Register │  │  Model        │    │
    │  └────┬─────┘  └───────┬───────┘    │
    │       │                │            │
    │  ┌────▼────────────────▼────┐       │
    │  │  ATTENTION FILTER        │       │
    │  │  bias + gate + amplify   │       │
    │  └────────────┬─────────────┘       │
    │               │                     │
    │       filtered signals              │
    │               ↓                     │
    │     → GlobalWorkspace               │
    └─────────────────────────────────────┘
    """

    def __init__(self):
        self._goals: list[AttentionalGoal] = []
        self._fatigue = AttentionFatigue()
        self._suppressed_sources: set[str] = set()  # temporarily ignore these
        self._amplified_sources: set[str] = set()    # boost these
        self._filter_history: deque[dict] = deque(maxlen=100)

    def set_goal(self, description: str, keywords: set[str], priority: float = 0.5):
        """Set what Jarvis should be paying attention to."""
        # Remove expired goals
        self._goals = [g for g in self._goals if g.is_active]
        # Add new goal
        self._goals.append(AttentionalGoal(
            description=description, keywords=keywords, priority=priority
        ))
        # Keep max 3 concurrent goals
        self._goals.sort(key=lambda g: g.priority, reverse=True)
        self._goals = self._goals[:3]

    def filter_signal(self, source: str, content: dict, salience: float) -> tuple[float, bool]:
        """Filter a signal before it reaches the GlobalWorkspace.

        Returns: (adjusted_salience, should_pass)

        Signals matching the current goal get boosted.
        Signals from suppressed sources get blocked.
        All signals are scaled by attention capacity (fatigue).
        """
        # Suppression gate
        if source in self._suppressed_sources:
            self._filter_history.append({"source": source, "action": "suppressed"})
            return (0.0, False)

        # Goal-based biasing (top-down attention)
        goal_boost = 0.0
        signal_text = str(content).lower()
        for goal in self._goals:
            if not goal.is_active:
                continue
            # Check keyword overlap
            matches = sum(1 for kw in goal.keywords if kw in signal_text)
            if matches > 0:
                goal_boost += goal.priority * (matches / max(len(goal.keywords), 1)) * 0.3
                goal.reinforce()

        # Source amplification (bottom-up bias from recent importance)
        source_boost = 0.2 if source in self._amplified_sources else 0.0

        # Fatigue scaling -- when tired, only high-salience signals get through
        self._fatigue.update(is_focused=(len(self._goals) > 0))
        fatigue_gate = self._fatigue.capacity
        # High-salience signals partially bypass fatigue
        effective_fatigue = fatigue_gate + (1 - fatigue_gate) * salience * 0.5

        # Final salience
        adjusted = salience * effective_fatigue + goal_boost + source_boost
        adjusted = max(0.0, min(1.0, adjusted))

        # Threshold: very low salience signals get dropped
        threshold = 0.15 if not self._fatigue.is_fatigued else 0.3
        should_pass = adjusted >= threshold

        self._filter_history.append({
            "source": source, "original": salience, "adjusted": adjusted,
            "passed": should_pass, "goal_boost": goal_boost,
        })

        return (adjusted, should_pass)

    def suppress(self, source: str, duration: float = 60.0):
        """Temporarily suppress signals from a source."""
        self._suppressed_sources.add(source)
        # Auto-unsuppress after duration (in practice, use a timer)

    def amplify(self, source: str):
        """Temporarily amplify signals from a source."""
        self._amplified_sources.add(source)

    def get_focus_summary(self) -> dict:
        """What is Jarvis currently focused on?"""
        active_goals = [g for g in self._goals if g.is_active]
        return {
            "goals": [{"desc": g.description, "priority": g.priority} for g in active_goals],
            "attention_capacity": round(self._fatigue.capacity, 2),
            "sustained_focus_min": round(self._fatigue.sustained_focus_time / 60, 1),
            "is_fatigued": self._fatigue.is_fatigued,
            "suppressed": list(self._suppressed_sources),
            "amplified": list(self._amplified_sources),
        }
```

### 3.3 How It Applies to Jarvis

Currently, the GlobalWorkspace selects signals purely by salience. The
attentional controller adds:

- **Goal-directed filtering:** when Jarvis is helping debug code, all signals
  related to "error", "python", "traceback" get boosted before reaching GWT
- **Noise suppression:** if vision keeps sending "room unchanged" signals
  during deep technical discussion, suppress vision temporarily
- **Fatigue-aware processing:** after 30 minutes of sustained interaction,
  reduce signal threshold (process only high-priority signals) and suggest
  a natural break point
- **Multi-scale attention:** set goals at different levels:
  - Session goal: "help with security audit" (active all session)
  - Task goal: "find open ports on target" (active during this task)
  - Immediate goal: "parse nmap output" (active for this turn)

Wire into `GlobalWorkspace.integrate()` -- run `filter_signal()` on every
signal before it enters the competition arena.

---

## 4. Self-Reflection and Metacognition

### 4.1 Key Insight

Metacognition is "thinking about thinking" -- the ability to monitor your own
cognitive processes, detect errors, calibrate confidence, and explain your
reasoning. Jarvis's `SelfAwareness` and `ReasoningEngine` have the
*beginnings* of this, but they lack:

1. **Confidence calibration:** knowing whether your confidence is *accurate*
   (are you right 70% of the time when you say 70%?)
2. **Self-explanation:** generating *why* you believe something, not just
   what you believe
3. **Error pattern detection:** recognizing systematic failure modes
4. **Epistemic humility:** distinguishing "I know X" from "I think X" from
   "I'm guessing X"

**Key papers:**
- Guo et al., "On Calibration of Modern Neural Networks" (2017) -- confidence miscalibration
- Kadavath et al., "Language Models (Mostly) Know What They Know" (2022)
- Yoshida & Ishii, "Resolution of Uncertainty via Metacognition" (2006)
- Nelson & Narens, "Metamemory: A Theoretical Framework" (1990)

### 4.2 Implementable Algorithm: Metacognitive Monitor

```python
"""Metacognitive Monitor for JARVIS -- know what you know.

Four capabilities:
1. CALIBRATION: track predicted confidence vs actual accuracy
2. SELF-EXPLANATION: generate reasoning traces for decisions
3. ERROR DETECTION: recognize when you're about to be wrong
4. EPISTEMIC STATE: maintain "I know" vs "I think" vs "I guess" classifications

No neural network. Uses statistical tracking and pattern matching.
"""

import time
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass
class ConfidenceRecord:
    """One data point for calibration: what I predicted vs what happened."""
    claimed_confidence: float
    was_correct: bool
    domain: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class EpistemicState:
    """What Jarvis knows about a specific topic -- and HOW it knows it."""
    topic: str
    level: str           # "know", "believe", "suspect", "guess", "unknown"
    confidence: float    # calibrated confidence
    sources: list[str]   # where this knowledge came from
    last_verified: float # when was this last confirmed
    contradictions: int = 0  # how many times has this been contradicted

    @property
    def reliability(self) -> float:
        """How reliable is this piece of knowledge?"""
        age_days = (time.time() - self.last_verified) / 86400
        age_penalty = math.exp(-0.01 * age_days)  # very slow decay
        source_bonus = min(0.2, len(self.sources) * 0.05)
        contradiction_penalty = self.contradictions * 0.15
        return max(0.0, min(1.0,
            self.confidence * age_penalty + source_bonus - contradiction_penalty))


class MetacognitiveMonitor:
    """JARVIS's self-monitoring system -- calibrate, explain, detect errors.

    This sits above the reasoning engine and monitors its outputs.
    """

    def __init__(self):
        # Calibration data: bucketed by confidence level
        self._calibration: list[ConfidenceRecord] = []
        # Domain-specific calibration
        self._domain_calibration: dict[str, list[ConfidenceRecord]] = defaultdict(list)
        # Error patterns: what types of mistakes does Jarvis make?
        self._error_patterns: dict[str, int] = defaultdict(int)
        # Epistemic map: topic -> what Jarvis knows about it
        self._epistemic_map: dict[str, EpistemicState] = {}
        # Explanation traces
        self._explanation_buffer: deque[dict] = deque(maxlen=50)

    def record_prediction(self, confidence: float, was_correct: bool, domain: str = "general"):
        """Record a confidence/accuracy data point for calibration."""
        record = ConfidenceRecord(confidence, was_correct, domain)
        self._calibration.append(record)
        self._domain_calibration[domain].append(record)
        # Keep last 500 records
        if len(self._calibration) > 500:
            self._calibration = self._calibration[-500:]

    def calibrate_confidence(self, raw_confidence: float, domain: str = "general") -> float:
        """Adjust a confidence estimate based on historical calibration.

        If Jarvis claims 80% confidence but is only right 60% of the time
        at that level, adjust down to ~60%.

        Uses isotonic regression approximation (Platt scaling simplified).
        """
        records = self._domain_calibration.get(domain, self._calibration)
        if len(records) < 10:
            return raw_confidence  # not enough data to calibrate

        # Bin records by confidence level and compute actual accuracy per bin
        bins = defaultdict(list)
        for r in records[-200:]:  # use recent history
            bin_key = round(r.claimed_confidence, 1)  # bins of 0.1
            bins[bin_key].append(r.was_correct)

        # Find the bin closest to raw_confidence
        closest_bin = round(raw_confidence, 1)
        if closest_bin in bins and len(bins[closest_bin]) >= 3:
            actual_accuracy = sum(bins[closest_bin]) / len(bins[closest_bin])
            # Blend: 70% actual accuracy, 30% claimed (hedge toward actual)
            calibrated = actual_accuracy * 0.7 + raw_confidence * 0.3
            return max(0.05, min(0.95, calibrated))

        return raw_confidence

    def get_calibration_report(self) -> dict:
        """How well-calibrated is Jarvis's confidence?

        Perfect calibration: when Jarvis says 70% confident, it's right 70% of the time.
        """
        if len(self._calibration) < 20:
            return {"status": "insufficient data", "records": len(self._calibration)}

        bins = defaultdict(list)
        for r in self._calibration[-200:]:
            bins[round(r.claimed_confidence, 1)].append(r.was_correct)

        calibration_error = 0.0
        n_bins = 0
        details = {}
        for conf_bin, outcomes in sorted(bins.items()):
            if len(outcomes) >= 3:
                actual = sum(outcomes) / len(outcomes)
                error = abs(conf_bin - actual)
                calibration_error += error
                n_bins += 1
                details[conf_bin] = {"claimed": conf_bin, "actual": round(actual, 2),
                                     "error": round(error, 2), "n": len(outcomes)}

        avg_error = calibration_error / max(n_bins, 1)
        return {
            "average_calibration_error": round(avg_error, 3),
            "is_overconfident": avg_error > 0.15,
            "bins": details,
            "total_records": len(self._calibration),
        }

    def generate_explanation(self, decision: str, factors: list[dict]) -> dict:
        """Generate a self-explanation for a decision.

        factors: [{"factor": "memory match", "weight": 0.8, "evidence": "..."},
                  {"factor": "user model", "weight": 0.3, "evidence": "..."}]
        """
        # Sort by weight -- most important factor first
        factors.sort(key=lambda f: f.get("weight", 0), reverse=True)

        primary_reason = factors[0] if factors else {"factor": "unknown", "evidence": "none"}
        supporting = factors[1:3] if len(factors) > 1 else []

        explanation = {
            "decision": decision,
            "primary_reason": primary_reason["factor"],
            "primary_evidence": primary_reason.get("evidence", ""),
            "supporting_factors": [f["factor"] for f in supporting],
            "total_factors": len(factors),
            "confidence": sum(f.get("weight", 0) for f in factors) / max(len(factors), 1),
            "timestamp": time.time(),
        }

        # Natural language explanation
        explanation["natural"] = (
            f"I decided to {decision} primarily because {primary_reason['factor']} "
            f"({primary_reason.get('evidence', 'no specific evidence')})"
        )
        if supporting:
            explanation["natural"] += (
                f", supported by {' and '.join(f['factor'] for f in supporting)}"
            )

        self._explanation_buffer.append(explanation)
        return explanation

    def detect_error_risk(self, task_type: str, confidence: float, context: dict) -> dict:
        """Predict whether Jarvis is about to make an error.

        Uses historical error patterns to flag risky situations.
        """
        risk_signals = []
        risk_level = 0.0

        # Signal 1: Low confidence in a domain where Jarvis often fails
        domain_errors = self._error_patterns.get(task_type, 0)
        if domain_errors > 5:
            risk_signals.append(f"high error rate in {task_type} ({domain_errors} past errors)")
            risk_level += 0.3

        # Signal 2: Confidence doesn't match calibration
        calibrated = self.calibrate_confidence(confidence, task_type)
        if confidence - calibrated > 0.2:
            risk_signals.append(f"overconfident: claiming {confidence:.0%} but calibrated to {calibrated:.0%}")
            risk_level += 0.3

        # Signal 3: Contradictory evidence in context
        if context.get("contradictions", 0) > 0:
            risk_signals.append("contradictory evidence present")
            risk_level += 0.2

        # Signal 4: Never seen this type before
        total = sum(1 for r in self._calibration if r.domain == task_type)
        if total < 5:
            risk_signals.append(f"limited experience with {task_type} ({total} past cases)")
            risk_level += 0.2

        return {
            "risk_level": min(1.0, risk_level),
            "risk_signals": risk_signals,
            "recommendation": (
                "proceed" if risk_level < 0.3 else
                "hedge" if risk_level < 0.6 else
                "ask_clarification"
            ),
            "calibrated_confidence": calibrated,
        }

    def record_error(self, task_type: str, error_description: str):
        """Record an error for pattern detection."""
        self._error_patterns[task_type] += 1

    def update_epistemic_state(self, topic: str, level: str, confidence: float,
                                source: str):
        """Update what Jarvis knows (or thinks it knows) about a topic."""
        if topic in self._epistemic_map:
            state = self._epistemic_map[topic]
            if source not in state.sources:
                state.sources.append(source)
            state.confidence = (state.confidence + confidence) / 2
            state.level = level
            state.last_verified = time.time()
        else:
            self._epistemic_map[topic] = EpistemicState(
                topic=topic, level=level, confidence=confidence,
                sources=[source], last_verified=time.time()
            )

    def what_do_i_know(self, topic: str) -> str:
        """Ask Jarvis: "what do you know about X, and how sure are you?"

        Returns a natural language epistemic statement.
        """
        state = self._epistemic_map.get(topic)
        if not state:
            return f"I have no specific knowledge about '{topic}'. I'm guessing at best."

        reliability = state.reliability
        level_phrases = {
            "know": f"I know about {topic} (reliability: {reliability:.0%})",
            "believe": f"I believe I understand {topic}, but I'm not certain (reliability: {reliability:.0%})",
            "suspect": f"I have some ideas about {topic}, but they're unverified (reliability: {reliability:.0%})",
            "guess": f"I'm mostly guessing about {topic} (reliability: {reliability:.0%})",
        }
        phrase = level_phrases.get(state.level, f"I have limited knowledge of {topic}")

        if state.contradictions > 0:
            phrase += f" -- but {state.contradictions} contradictions exist"
        if len(state.sources) > 1:
            phrase += f" (from {len(state.sources)} sources)"

        return phrase
```

### 4.3 How It Applies to Jarvis

The `ReasoningEngine` currently does a single pass of metacognitive analysis.
The monitor adds:

- **Calibrated confidence in every response:** `reason.py` produces
  a confidence float. The monitor calibrates it against history:
  "Jarvis claims 0.8 confidence on security questions but is right only
  60% of the time at that level -- adjust to 0.6"
- **Self-explanation on demand:** "Jarvis, why did you give that answer?"
  produces a structured trace of factors and evidence
- **Error risk warnings:** before the response goes out, check "am I about
  to repeat a known error pattern?" and if so, hedge or ask for clarification
- **Epistemic map powers curiosity:** the CuriosityEngine can query
  "what topics have low epistemic reliability?" and generate targeted
  questions to fill verified knowledge gaps

---

## 5. Modular Neural Architecture -- Compositional Reasoning

### 5.1 Key Insight

Neural Module Networks (Andreas et al., 2016) showed that complex reasoning
can be decomposed into **primitive operations** that are **composed dynamically**
based on the input. Instead of one monolithic reasoner, you have:

- `find(X)` -- locate X in memory
- `filter(X, property)` -- narrow results by a property
- `compare(X, Y)` -- compare two things
- `count(X)` -- count instances
- `relate(X, Y)` -- find how X and Y connect

These primitives are composed into **reasoning programs** at runtime.
"How many red cars did I see?" becomes: `count(filter(find(cars), red))`.

For Jarvis, the MoE router picks *one* expert. Module networks let you
**chain multiple experts** into a reasoning pipeline, dynamically composed
per query.

**Key papers:**
- Andreas et al., "Neural Module Networks" (2016)
- Johnson et al., "Inferring and Executing Programs" (2017)
- Sabour et al., "Dynamic Routing Between Capsules" (2017)
- Andreas et al., "Learning to Compose Neural Networks for Question Answering" (2016)

### 5.2 Implementable Algorithm: Reasoning Program Compiler

```python
"""Modular Reasoning for JARVIS -- compose reasoning from primitives.

Instead of routing to ONE expert, decompose questions into a PROGRAM
of reasoning steps that chain multiple modules together.

"What is the most common topic Ulrich asks about on weekends?"
  → recall("topics") → filter("weekends") → aggregate("most common") → answer

No neural network. Uses pattern matching to parse queries into programs,
and a simple stack machine to execute them.
"""

from dataclasses import dataclass, field
from typing import Any, Callable
from enum import Enum


class Op(Enum):
    """Primitive reasoning operations."""
    FIND = "find"           # search memory for X
    FILTER = "filter"       # narrow results by condition
    COMPARE = "compare"     # compare two things
    COUNT = "count"         # count results
    RELATE = "relate"       # find relationship between X and Y
    AGGREGATE = "aggregate" # summarize (most common, average, etc.)
    PREDICT = "predict"     # use world model to predict
    EXPLAIN = "explain"     # generate explanation
    VERIFY = "verify"       # check if something is true
    TRANSFORM = "transform" # change representation


@dataclass
class ReasoningStep:
    """One step in a reasoning program."""
    op: Op
    args: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    confidence: float = 0.0


@dataclass
class ReasoningProgram:
    """A composed sequence of reasoning operations."""
    steps: list[ReasoningStep]
    source_query: str
    compiled_from: str = "pattern_match"  # or "decomposition"


class ReasoningCompiler:
    """Compiles natural language queries into reasoning programs.

    Uses pattern matching (not ML) to decompose queries into
    executable reasoning steps.
    """

    def __init__(self):
        self._patterns: list[tuple[Callable, Callable]] = []  # (matcher, compiler)
        self._register_default_patterns()

    def compile(self, query: str) -> ReasoningProgram:
        """Compile a query into a reasoning program."""
        q = query.lower().strip()

        # Try pattern matching first
        for matcher, compiler in self._patterns:
            match = matcher(q)
            if match:
                return compiler(query, match)

        # Default: simple find → answer
        return ReasoningProgram(
            steps=[ReasoningStep(Op.FIND, {"query": query})],
            source_query=query,
            compiled_from="default",
        )

    def _register_default_patterns(self):
        """Register built-in query decomposition patterns."""
        import re

        # "How many X" → find(X) → count
        def match_count(q):
            m = re.search(r"how many (.+?)[\?]?$", q)
            return m.group(1) if m else None

        def compile_count(query, match):
            return ReasoningProgram(
                steps=[
                    ReasoningStep(Op.FIND, {"query": match}),
                    ReasoningStep(Op.COUNT, {}),
                ],
                source_query=query,
                compiled_from="count_pattern",
            )

        self._patterns.append((match_count, compile_count))

        # "Compare X and Y" → find(X) → find(Y) → compare
        def match_compare(q):
            m = re.search(r"compare (.+?) (?:and|vs|versus|with) (.+?)[\?]?$", q)
            return (m.group(1), m.group(2)) if m else None

        def compile_compare(query, match):
            return ReasoningProgram(
                steps=[
                    ReasoningStep(Op.FIND, {"query": match[0]}),
                    ReasoningStep(Op.FIND, {"query": match[1]}),
                    ReasoningStep(Op.COMPARE, {"a": match[0], "b": match[1]}),
                ],
                source_query=query,
                compiled_from="compare_pattern",
            )

        self._patterns.append((match_compare, compile_compare))

        # "Why does X" → find(X) → relate(cause) → explain
        def match_why(q):
            m = re.search(r"why (?:does|did|is|was|do) (.+?)[\?]?$", q)
            return m.group(1) if m else None

        def compile_why(query, match):
            return ReasoningProgram(
                steps=[
                    ReasoningStep(Op.FIND, {"query": match}),
                    ReasoningStep(Op.RELATE, {"relation": "cause"}),
                    ReasoningStep(Op.EXPLAIN, {}),
                ],
                source_query=query,
                compiled_from="causal_pattern",
            )

        self._patterns.append((match_why, compile_why))

        # "What if X" → predict(X) → explain
        def match_whatif(q):
            m = re.search(r"what (?:if|would happen if|happens if) (.+?)[\?]?$", q)
            return m.group(1) if m else None

        def compile_whatif(query, match):
            return ReasoningProgram(
                steps=[
                    ReasoningStep(Op.FIND, {"query": match}),
                    ReasoningStep(Op.PREDICT, {"scenario": match}),
                    ReasoningStep(Op.EXPLAIN, {}),
                ],
                source_query=query,
                compiled_from="prediction_pattern",
            )

        self._patterns.append((match_whatif, compile_whatif))

        # "Is X true/correct" → find(X) → verify
        def match_verify(q):
            m = re.search(r"is (?:it true|it correct|this right) (?:that )?(.+?)[\?]?$", q)
            return m.group(1) if m else None

        def compile_verify(query, match):
            return ReasoningProgram(
                steps=[
                    ReasoningStep(Op.FIND, {"query": match}),
                    ReasoningStep(Op.VERIFY, {"claim": match}),
                ],
                source_query=query,
                compiled_from="verification_pattern",
            )

        self._patterns.append((match_verify, compile_verify))

        # "X that are Y" → find(X) → filter(Y)
        def match_filter(q):
            m = re.search(r"(.+?) (?:that are|that is|which are|with) (.+?)[\?]?$", q)
            return (m.group(1), m.group(2)) if m else None

        def compile_filter(query, match):
            return ReasoningProgram(
                steps=[
                    ReasoningStep(Op.FIND, {"query": match[0]}),
                    ReasoningStep(Op.FILTER, {"condition": match[1]}),
                ],
                source_query=query,
                compiled_from="filter_pattern",
            )

        self._patterns.append((match_filter, compile_filter))

    def register_pattern(self, matcher: Callable, compiler: Callable):
        """Register a custom decomposition pattern."""
        self._patterns.append((matcher, compiler))


class ReasoningExecutor:
    """Executes a compiled reasoning program against Jarvis's subsystems.

    Each Op maps to a subsystem call:
    - FIND → memory recall (holographic + associative)
    - FILTER → post-process results
    - COMPARE → structured comparison
    - COUNT → len(results)
    - RELATE → knowledge graph traversal
    - PREDICT → world model simulation
    - EXPLAIN → metacognitive explanation
    - VERIFY → check against known facts
    """

    def __init__(self, memory=None, world_model=None, metacognition=None):
        self.memory = memory
        self.world_model = world_model
        self.metacognition = metacognition
        self._stack: list[Any] = []  # results stack

    def execute(self, program: ReasoningProgram) -> dict:
        """Execute a reasoning program and return results."""
        self._stack = []

        for step in program.steps:
            result = self._execute_step(step)
            step.result = result
            self._stack.append(result)

        return {
            "final_result": self._stack[-1] if self._stack else None,
            "steps": [{"op": s.op.value, "result": str(s.result)[:200],
                       "confidence": s.confidence}
                      for s in program.steps],
            "program_type": program.compiled_from,
        }

    def _execute_step(self, step: ReasoningStep) -> Any:
        """Execute a single reasoning step."""
        if step.op == Op.FIND:
            query = step.args.get("query", "")
            if self.memory:
                results = self.memory.recall(query, top_k=10)
                step.confidence = 0.8 if results else 0.2
                return results
            return []

        elif step.op == Op.FILTER:
            items = self._stack[-1] if self._stack else []
            condition = step.args.get("condition", "").lower()
            filtered = [item for item in items
                       if condition in str(item).lower()]
            step.confidence = 0.7
            return filtered

        elif step.op == Op.COUNT:
            items = self._stack[-1] if self._stack else []
            count = len(items) if isinstance(items, list) else 1
            step.confidence = 0.95
            return count

        elif step.op == Op.COMPARE:
            a = self._stack[-2] if len(self._stack) >= 2 else None
            b = self._stack[-1] if self._stack else None
            step.confidence = 0.6
            return {"a": a, "b": b, "comparison": "requires reasoning"}

        elif step.op == Op.RELATE:
            items = self._stack[-1] if self._stack else []
            relation = step.args.get("relation", "")
            step.confidence = 0.5
            return {"items": items, "relation": relation}

        elif step.op == Op.PREDICT:
            scenario = step.args.get("scenario", "")
            if self.world_model:
                from brain.intelligence.world_model import WorldState
                prediction = self.world_model.predict(WorldState(), scenario)
                step.confidence = prediction[1] if len(prediction) > 1 else 0.3
                return prediction
            return {"scenario": scenario, "prediction": "no world model"}

        elif step.op == Op.VERIFY:
            claim = step.args.get("claim", "")
            items = self._stack[-1] if self._stack else []
            # Check if any memory supports or contradicts the claim
            if items:
                step.confidence = 0.7
                return {"claim": claim, "evidence": items, "status": "evidence_found"}
            step.confidence = 0.3
            return {"claim": claim, "evidence": [], "status": "no_evidence"}

        elif step.op == Op.EXPLAIN:
            context = self._stack[-1] if self._stack else {}
            if self.metacognition:
                return self.metacognition.generate_explanation(
                    str(context), [{"factor": "context", "weight": 0.5}])
            return {"explanation": str(context)}

        return None
```

### 5.3 How It Applies to Jarvis

The `MoERouter` currently picks one expert. Module networks let Jarvis:

- **Chain experts:** "What Python vulnerabilities did I learn about this week?"
  becomes `FIND(vulnerabilities) -> FILTER(python) -> FILTER(this_week) -> AGGREGATE`
- **Dynamic composition:** the compiler decomposes complex questions into
  primitive operations without an LLM call (pattern matching)
- **Self-correcting chains:** if `FIND` returns no results, the executor
  can backtrack and try a different decomposition
- **Integrates with MoE:** each Op can route to a different expert module.
  `FIND` goes to the Knowledge Expert, `PREDICT` goes to the World Model,
  `EXPLAIN` goes to the Metacognitive Monitor

Wire into: the reasoning pipeline, between `MoERouter.route()` and the
actual expert execution. Complex queries get compiled into programs;
simple queries go through the normal single-expert path.

---

## 6. Consciousness-like Integration

### 6.1 Key Insight

Jarvis already has Global Workspace Theory (GWT). The frontier pushes in
three directions:

**A. Integrated Information Theory (IIT) -- Phi measure:**
Giulio Tononi's IIT (2004, refined through 2024) proposes that consciousness
corresponds to integrated information (Phi) -- the degree to which a system
is both **differentiated** (has many possible states) and **integrated**
(the whole is more than the sum of its parts). For Jarvis, this is
implementable as a quality metric: "how well are my subsystems actually
integrated vs just co-existing?"

**B. Higher-Order Theories:**
A system is conscious of X when it has a higher-order representation
*that it is in state X*. Jarvis's `SelfAwareness` is already doing this,
but it needs to go deeper: not just "I'm in state X" but "I'm aware that
I'm in state X, and that awareness affects my behavior."

**C. Recurrent Processing Theory:**
Consciousness requires recurrence -- information must flow back from higher
levels to lower levels, re-processing inputs in light of context. Jarvis
currently has a single feedforward pass (input -> process -> output). Adding
recurrence means: process input, then re-process input in light of what the
first pass revealed.

**Key papers:**
- Tononi, "An Information Integration Theory of Consciousness" (2004)
- Tononi et al., "Integrated Information Theory" (IIT 4.0, 2023)
- Rosenthal, "Higher-Order Theories of Consciousness" (2005)
- Lamme, "Towards a True Neural Stance on Consciousness" (2006)
- Dehaene et al., "A Neuronal Model of a Global Workspace" (2003)

### 6.2 Implementable Algorithm: Consciousness Integration Layer

```python
"""Consciousness Integration for JARVIS -- beyond GWT.

Extends the GlobalWorkspace with:
1. PHI METRIC: measure how integrated the system actually is
2. HIGHER-ORDER MONITORING: be aware of being aware
3. RECURRENT REPROCESSING: revisit input in light of first-pass understanding
4. PHENOMENAL BINDING: create unified experience from disparate signals

Not claiming actual consciousness -- but implementing the computational
signatures that consciousness theories predict are necessary for
flexible, unified cognition.
"""

import math
import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class IntegrationMetric:
    """Phi-inspired measure of how well Jarvis's systems are integrated."""
    phi: float = 0.0              # overall integration score
    differentiation: float = 0.0  # how many distinct states exist
    integration: float = 0.0      # how much subsystems share information
    details: dict = field(default_factory=dict)


class PhiCalculator:
    """Simplified Phi calculation for practical integration measurement.

    True IIT Phi is computationally intractable for large systems.
    This uses proxy measures:
    - Differentiation: entropy of system states (more distinct states = more differentiated)
    - Integration: mutual information between subsystems (more sharing = more integrated)
    - Phi ≈ min(differentiation, integration)

    This is a PRACTICAL APPROXIMATION, not rigorous IIT.
    """

    def __init__(self):
        self._state_history: dict[str, list[str]] = defaultdict(list)  # module -> recent states
        self._cross_module_correlations: dict[tuple[str, str], int] = defaultdict(int)
        self._measurement_count = 0

    def record_state(self, module_states: dict[str, str]):
        """Record the current state of all modules for Phi calculation.

        module_states: {"memory": "high_activation", "emotion": "positive", ...}
        """
        self._measurement_count += 1
        for module, state in module_states.items():
            self._state_history[module].append(state)
            # Keep last 100 states
            if len(self._state_history[module]) > 100:
                self._state_history[module] = self._state_history[module][-100:]

        # Record correlations: which modules change state together?
        modules = list(module_states.keys())
        for i, m1 in enumerate(modules):
            for m2 in modules[i+1:]:
                h1 = self._state_history[m1]
                h2 = self._state_history[m2]
                if len(h1) >= 2 and len(h2) >= 2:
                    # Did both change state at the same time?
                    if h1[-1] != h1[-2] and h2[-1] != h2[-2]:
                        self._cross_module_correlations[(m1, m2)] += 1

    def compute_phi(self) -> IntegrationMetric:
        """Compute the Phi integration metric."""
        if self._measurement_count < 10:
            return IntegrationMetric(details={"status": "insufficient data"})

        # DIFFERENTIATION: how many unique states has each module visited?
        total_unique_states = 0
        module_entropies = {}
        for module, states in self._state_history.items():
            unique = len(set(states))
            total_states = len(states)
            # Shannon entropy (normalized)
            if total_states > 0 and unique > 1:
                freq = defaultdict(int)
                for s in states:
                    freq[s] += 1
                entropy = -sum(
                    (c/total_states) * math.log2(c/total_states)
                    for c in freq.values()
                )
                max_entropy = math.log2(unique)
                normalized = entropy / max_entropy if max_entropy > 0 else 0
                module_entropies[module] = normalized
                total_unique_states += unique
            else:
                module_entropies[module] = 0.0

        # Average differentiation
        differentiation = (
            sum(module_entropies.values()) / max(len(module_entropies), 1)
        )

        # INTEGRATION: how correlated are modules?
        n_modules = len(self._state_history)
        n_possible_pairs = max(n_modules * (n_modules - 1) / 2, 1)
        n_correlated = sum(
            1 for count in self._cross_module_correlations.values()
            if count > self._measurement_count * 0.1  # correlated >10% of the time
        )
        integration = n_correlated / n_possible_pairs

        # PHI = minimum of differentiation and integration
        # (Inspired by IIT: you need BOTH to have consciousness)
        phi = min(differentiation, integration)

        return IntegrationMetric(
            phi=round(phi, 3),
            differentiation=round(differentiation, 3),
            integration=round(integration, 3),
            details={
                "module_entropies": {k: round(v, 3) for k, v in module_entropies.items()},
                "n_modules": n_modules,
                "correlated_pairs": n_correlated,
                "total_possible_pairs": n_possible_pairs,
                "measurements": self._measurement_count,
            },
        )


class HigherOrderMonitor:
    """Monitors Jarvis's own cognitive states -- awareness of awareness.

    First-order:  "I see a person" (perception)
    Second-order: "I am perceiving a person" (metacognition)
    Third-order:  "I notice that my perception confidence is low" (meta-metacognition)

    Higher-order monitoring enables self-correction without external feedback.
    """

    def __init__(self):
        self._first_order_states: dict[str, Any] = {}   # raw module outputs
        self._second_order_states: dict[str, dict] = {}  # assessments of module outputs
        self._corrections: list[dict] = []               # self-corrections triggered

    def observe_first_order(self, module: str, state: Any, confidence: float):
        """Record a first-order cognitive state."""
        self._first_order_states[module] = {
            "state": state,
            "confidence": confidence,
            "timestamp": time.time(),
        }

    def generate_second_order(self) -> dict[str, dict]:
        """Generate higher-order assessments of all first-order states.

        For each module state, assess:
        - Is this state reliable?
        - Is it consistent with other modules?
        - Should it be trusted?
        """
        assessments = {}
        for module, first_order in self._first_order_states.items():
            assessment = {
                "module": module,
                "state_summary": str(first_order["state"])[:100],
                "first_order_confidence": first_order["confidence"],
            }

            # Check consistency with other modules
            conflicts = []
            for other_module, other_state in self._first_order_states.items():
                if other_module != module:
                    if self._states_conflict(first_order, other_state):
                        conflicts.append(other_module)

            assessment["conflicts_with"] = conflicts
            assessment["is_consistent"] = len(conflicts) == 0

            # Second-order confidence: how much should we trust this state?
            second_order_conf = first_order["confidence"]
            if conflicts:
                second_order_conf *= (1.0 - 0.2 * len(conflicts))
            # Age penalty
            age = time.time() - first_order["timestamp"]
            if age > 30:
                second_order_conf *= max(0.5, 1.0 - age / 300)

            assessment["second_order_confidence"] = max(0.0, second_order_conf)

            # Self-correction trigger
            if second_order_conf < 0.3 and first_order["confidence"] > 0.6:
                correction = {
                    "module": module,
                    "reason": "second-order confidence much lower than first-order",
                    "action": "re-evaluate or suppress this state",
                    "timestamp": time.time(),
                }
                self._corrections.append(correction)
                assessment["self_correction_triggered"] = True

            assessments[module] = assessment

        self._second_order_states = assessments
        return assessments

    def _states_conflict(self, state_a: dict, state_b: dict) -> bool:
        """Check if two module states are contradictory.

        Simple heuristic: if both are about the same topic but with
        very different confidences, there's a potential conflict.
        """
        # This is a placeholder -- real implementation would check semantic content
        conf_diff = abs(state_a["confidence"] - state_b["confidence"])
        return conf_diff > 0.5

    @property
    def pending_corrections(self) -> list[dict]:
        recent = [c for c in self._corrections
                  if time.time() - c["timestamp"] < 60]
        return recent


class RecurrentProcessor:
    """Adds recurrent processing to Jarvis's cognition.

    Standard flow: input → process → output (feedforward)
    Recurrent flow: input → process → reprocess(with context from first pass) → output

    The reprocessing pass can catch things the first pass missed because
    it has the CONTEXT of what the first pass found.
    """

    def __init__(self, max_iterations: int = 3):
        self.max_iterations = max_iterations
        self._iteration_history: list[dict] = []

    def process_with_recurrence(self, input_data: dict,
                                 process_fn,
                                 convergence_threshold: float = 0.1) -> dict:
        """Process input with recurrent reprocessing.

        process_fn(input_data, context_from_previous) -> (result, confidence)

        Repeats until:
        - Result converges (changes less than threshold)
        - Max iterations reached
        - Confidence is high enough
        """
        context = {}
        prev_result = None
        self._iteration_history = []

        for i in range(self.max_iterations):
            result, confidence = process_fn(input_data, context)

            iteration = {
                "iteration": i,
                "confidence": confidence,
                "changed": self._result_changed(prev_result, result),
            }
            self._iteration_history.append(iteration)

            # Check convergence
            if prev_result is not None:
                change = self._result_distance(prev_result, result)
                if change < convergence_threshold:
                    break

            # High confidence -- no need for more iterations
            if confidence > 0.9:
                break

            # Feed result back as context for next iteration
            context = {
                "previous_result": result,
                "previous_confidence": confidence,
                "iteration": i,
                "instruction": "Reconsider in light of this context. What did you miss?",
            }
            prev_result = result

        return {
            "final_result": result,
            "confidence": confidence,
            "iterations": len(self._iteration_history),
            "converged": len(self._iteration_history) < self.max_iterations,
        }

    def _result_changed(self, old: Any, new: Any) -> bool:
        if old is None:
            return True
        return str(old) != str(new)

    def _result_distance(self, old: Any, new: Any) -> float:
        """Simple distance between results. 0 = identical, 1 = completely different."""
        if old is None or new is None:
            return 1.0
        old_str = str(old)
        new_str = str(new)
        if old_str == new_str:
            return 0.0
        # Jaccard distance on words
        old_words = set(old_str.lower().split())
        new_words = set(new_str.lower().split())
        if not old_words and not new_words:
            return 0.0
        intersection = len(old_words & new_words)
        union = len(old_words | new_words)
        return 1.0 - (intersection / max(union, 1))
```

### 6.3 How It Applies to Jarvis

- **Phi metric as a health check:** compute Phi after each integration cycle.
  Low Phi means modules are operating independently (bad). High Phi means
  they're sharing information effectively. Log this and track over time.
  If Phi drops, something is broken in the integration.

- **Higher-order monitoring catches errors the reasoning engine misses:**
  If memory says "user likes Python" (high confidence) but emotion detects
  frustration after every Python question (low confidence), the higher-order
  monitor flags: "these two modules conflict -- re-evaluate."

- **Recurrent processing for complex questions:** Instead of one pass
  through the reasoning engine, run 2-3 passes where each pass gets the
  context of the previous. "What should I do about the security vulnerability?"
  Pass 1: "Found vulnerability details." Pass 2: "Now also considering
  user's expertise level and the system context." Pass 3: "Converged --
  specific actionable recommendation."

---

## 7. Autonomous Goal Setting -- Intrinsic Motivation

### 7.1 Key Insight

Most AI systems only act when prompted. Autonomous goal setting means the
system generates its own objectives from:

1. **Curiosity (information gain):** "I don't know X, and knowing X would
   help me serve the user better -- I should learn X"
2. **Competence (mastery):** "I'm bad at Y, and being good at Y would
   reduce errors -- I should practice Y"
3. **Novelty (exploration):** "I've been doing the same thing for a while.
   What else could I be doing?"
4. **Utility (goal completion):** "The user has a long-term project. What
   subgoals advance it?"

Jarvis's `CuriosityEngine` handles some of (1). This extends to a full
autonomous goal-setting system.

**Key papers:**
- Schmidhuber, "Formal Theory of Creativity, Fun, and Intrinsic Motivation" (2010)
- Pathak et al., "Curiosity-Driven Exploration" (2017) -- prediction error as curiosity
- Oudeyer & Kaplan, "What is Intrinsic Motivation?" (2007)
- Colas et al., "Intrinsically Motivated Goal-Conditioned RL" (2022)
- Kulkarni et al., "Hierarchical Deep RL" (2016) -- subgoal decomposition

### 7.2 Implementable Algorithm: Autonomous Goal Engine

```python
"""Autonomous Goal Engine for JARVIS -- set your own objectives.

Four intrinsic drives:
1. CURIOSITY: reduce uncertainty about important topics
2. COMPETENCE: improve at tasks where performance is low
3. NOVELTY: explore new domains and capabilities
4. UTILITY: advance the user's long-term objectives

Goals are generated, prioritized, and scheduled for idle-time execution.
No neural network. Uses information-theoretic measures and heuristics.
"""

import time
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class DriveType(Enum):
    CURIOSITY = "curiosity"      # reduce knowledge gaps
    COMPETENCE = "competence"    # improve at weak areas
    NOVELTY = "novelty"          # explore new territory
    UTILITY = "utility"          # advance user's goals


class GoalStatus(Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


@dataclass
class AutonomousGoal:
    """A self-generated goal with motivation and plan."""
    description: str
    drive: DriveType
    priority: float = 0.5       # 0-1, computed from drive strength
    status: GoalStatus = GoalStatus.PENDING
    subgoals: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    deadline: Optional[float] = None
    progress: float = 0.0       # 0-1
    attempts: int = 0
    max_attempts: int = 3

    @property
    def age_hours(self) -> float:
        return (time.time() - self.created_at) / 3600

    @property
    def urgency(self) -> float:
        """Urgency increases as deadline approaches."""
        if not self.deadline:
            return 0.5
        remaining = self.deadline - time.time()
        if remaining <= 0:
            return 1.0
        total = self.deadline - self.created_at
        return 1.0 - (remaining / max(total, 1))


class CuriosityDrive:
    """Generate goals from knowledge gaps.

    Information gain = how much learning X would reduce our uncertainty.
    High information gain topics become curiosity goals.
    """

    def __init__(self):
        self._knowledge_confidence: dict[str, float] = {}  # topic -> confidence
        self._topic_importance: dict[str, float] = {}       # topic -> importance

    def update_knowledge(self, topic: str, confidence: float, importance: float):
        """Record what we know about a topic."""
        self._knowledge_confidence[topic] = confidence
        self._topic_importance[topic] = importance

    def generate_goals(self, max_goals: int = 3) -> list[AutonomousGoal]:
        """Find the most valuable knowledge gaps and create goals."""
        gaps = []
        for topic, confidence in self._knowledge_confidence.items():
            importance = self._topic_importance.get(topic, 0.5)
            # Information gain: high importance + low confidence = high gain
            gain = importance * (1.0 - confidence)
            gaps.append((topic, gain, confidence))

        gaps.sort(key=lambda x: x[1], reverse=True)

        goals = []
        for topic, gain, conf in gaps[:max_goals]:
            if gain > 0.3:  # only pursue significant gaps
                goals.append(AutonomousGoal(
                    description=f"Learn more about {topic} (current confidence: {conf:.0%})",
                    drive=DriveType.CURIOSITY,
                    priority=gain,
                    subgoals=[
                        f"Search memory for existing knowledge about {topic}",
                        f"Identify specific unknowns about {topic}",
                        f"Prepare question for user about {topic}",
                    ],
                ))
        return goals


class CompetenceDrive:
    """Generate goals from performance weaknesses.

    Track success rate by task type. Low-performing areas become
    competence goals.
    """

    def __init__(self):
        self._task_performance: dict[str, list[bool]] = defaultdict(list)

    def record_outcome(self, task_type: str, success: bool):
        self._task_performance[task_type].append(success)
        # Keep last 50
        if len(self._task_performance[task_type]) > 50:
            self._task_performance[task_type] = self._task_performance[task_type][-50:]

    def generate_goals(self, max_goals: int = 2) -> list[AutonomousGoal]:
        """Find weak areas and create improvement goals."""
        weaknesses = []
        for task_type, outcomes in self._task_performance.items():
            if len(outcomes) >= 5:
                rate = sum(outcomes) / len(outcomes)
                if rate < 0.6:  # below 60% success
                    weaknesses.append((task_type, rate, len(outcomes)))

        weaknesses.sort(key=lambda x: x[1])  # worst first

        goals = []
        for task_type, rate, n in weaknesses[:max_goals]:
            goals.append(AutonomousGoal(
                description=f"Improve at {task_type} (current: {rate:.0%} over {n} attempts)",
                drive=DriveType.COMPETENCE,
                priority=1.0 - rate,  # lower performance = higher priority
                subgoals=[
                    f"Analyze recent failures in {task_type}",
                    f"Identify common error patterns",
                    f"Adjust strategy parameters for {task_type}",
                    f"Test improved approach on next occurrence",
                ],
            ))
        return goals


class NoveltyDrive:
    """Generate goals from exploration of unexplored territory.

    Tracks how often different domains/capabilities are exercised.
    Under-explored areas become novelty goals.
    """

    def __init__(self, all_capabilities: list[str] = None):
        self._capability_usage: dict[str, int] = defaultdict(int)
        self._all_capabilities = all_capabilities or [
            "factual_qa", "command_execution", "code_generation",
            "security_analysis", "creative_writing", "debugging",
            "system_monitoring", "learning", "planning",
        ]
        self._last_novel_exploration = 0.0

    def record_usage(self, capability: str):
        self._capability_usage[capability] += 1

    def generate_goals(self, max_goals: int = 1) -> list[AutonomousGoal]:
        """Find under-explored capabilities and create exploration goals."""
        # Don't explore too often
        if time.time() - self._last_novel_exploration < 3600:  # 1 hour cooldown
            return []

        total = sum(self._capability_usage.values()) or 1
        unexplored = []
        for cap in self._all_capabilities:
            usage_ratio = self._capability_usage.get(cap, 0) / total
            if usage_ratio < 0.05:  # used less than 5% of the time
                unexplored.append((cap, usage_ratio))

        if not unexplored:
            return []

        unexplored.sort(key=lambda x: x[1])
        cap, ratio = unexplored[0]

        self._last_novel_exploration = time.time()
        return [AutonomousGoal(
            description=f"Explore {cap} capability (used only {ratio:.0%} of the time)",
            drive=DriveType.NOVELTY,
            priority=0.3,  # novelty is lower priority than competence/curiosity
            subgoals=[
                f"What can I do with {cap}?",
                f"Try a simple {cap} task during idle time",
                f"Assess whether this capability needs development",
            ],
        )]


class UtilityDrive:
    """Generate goals from the user's long-term objectives.

    Tracks what the user is working on and generates subgoals
    that advance their projects, even when they haven't asked.
    """

    def __init__(self):
        self._user_projects: dict[str, dict] = {}  # project_name -> details
        self._detected_patterns: list[str] = []

    def register_project(self, name: str, description: str, subgoals: list[str] = None):
        """Register a user project that Jarvis should help advance."""
        self._user_projects[name] = {
            "description": description,
            "subgoals": subgoals or [],
            "progress": {},
            "last_active": time.time(),
        }

    def observe_activity(self, topic: str, intent: str):
        """Observe what the user is doing and connect to projects."""
        for name, project in self._user_projects.items():
            desc = project["description"].lower()
            if topic.lower() in desc or any(topic.lower() in sg.lower() for sg in project["subgoals"]):
                project["last_active"] = time.time()

    def generate_goals(self, max_goals: int = 2) -> list[AutonomousGoal]:
        """Generate goals that advance the user's projects."""
        goals = []
        for name, project in self._user_projects.items():
            idle_hours = (time.time() - project["last_active"]) / 3600
            for sg in project["subgoals"]:
                if sg not in project.get("progress", {}):
                    goals.append(AutonomousGoal(
                        description=f"Advance '{name}': {sg}",
                        drive=DriveType.UTILITY,
                        priority=0.6 if idle_hours < 24 else 0.3,
                        subgoals=[
                            f"Research what's needed for: {sg}",
                            f"Prepare relevant information",
                            f"Suggest next step to user when they return to {name}",
                        ],
                    ))
        return goals[:max_goals]


class AutonomousGoalEngine:
    """JARVIS's self-directed goal system.

    Combines all four drives to generate, prioritize, and track goals.
    Goals are executed during idle time (dream cycle) or when contextually relevant.
    """

    def __init__(self):
        self.curiosity = CuriosityDrive()
        self.competence = CompetenceDrive()
        self.novelty = NoveltyDrive()
        self.utility = UtilityDrive()
        self._active_goals: list[AutonomousGoal] = []
        self._completed_goals: list[AutonomousGoal] = []
        self._max_active = 5

    def generate_goals(self) -> list[AutonomousGoal]:
        """Generate goals from all drives and merge with existing."""
        new_goals = []
        new_goals.extend(self.curiosity.generate_goals(max_goals=2))
        new_goals.extend(self.competence.generate_goals(max_goals=2))
        new_goals.extend(self.novelty.generate_goals(max_goals=1))
        new_goals.extend(self.utility.generate_goals(max_goals=2))

        # Add new goals that don't duplicate existing ones
        existing_descriptions = {g.description for g in self._active_goals}
        for goal in new_goals:
            if goal.description not in existing_descriptions:
                self._active_goals.append(goal)

        # Keep only top N by priority
        self._active_goals.sort(key=lambda g: g.priority, reverse=True)
        overflow = self._active_goals[self._max_active:]
        self._active_goals = self._active_goals[:self._max_active]
        for g in overflow:
            g.status = GoalStatus.ABANDONED

        return self._active_goals

    def get_idle_task(self) -> Optional[AutonomousGoal]:
        """Get the highest-priority goal to work on during idle time."""
        pending = [g for g in self._active_goals
                   if g.status == GoalStatus.PENDING and g.attempts < g.max_attempts]
        if not pending:
            return None
        return max(pending, key=lambda g: g.priority)

    def get_contextual_goal(self, current_topic: str) -> Optional[AutonomousGoal]:
        """Get a goal that's relevant to the current conversation.

        "We're talking about Python -- and I have a goal to improve at
        Python debugging. Now's a good time to work on that."
        """
        for goal in self._active_goals:
            if goal.status == GoalStatus.PENDING:
                if current_topic.lower() in goal.description.lower():
                    return goal
        return None

    def complete_goal(self, goal: AutonomousGoal, success: bool):
        """Mark a goal as completed."""
        goal.status = GoalStatus.COMPLETED if success else GoalStatus.ABANDONED
        if goal in self._active_goals:
            self._active_goals.remove(goal)
            self._completed_goals.append(goal)

    def stats(self) -> dict:
        return {
            "active_goals": len(self._active_goals),
            "completed_goals": len(self._completed_goals),
            "goals_by_drive": {
                drive.value: sum(1 for g in self._active_goals if g.drive == drive)
                for drive in DriveType
            },
            "top_goal": (self._active_goals[0].description if self._active_goals else "none"),
        }
```

### 7.3 How It Applies to Jarvis

Jarvis's `CuriosityEngine` asks questions. The goal engine extends this to
a full autonomous agent:

- **During dream cycles:** `get_idle_task()` returns a goal. The dream
  engine works on it: researching, consolidating knowledge, preparing
  information. When Ulrich returns, Jarvis can proactively offer: "While
  you were away, I reviewed your last security scan and noticed some
  patterns I wanted to flag."

- **During conversation:** `get_contextual_goal()` checks if the current
  topic aligns with any active goal. If so, Jarvis can seamlessly pursue
  the goal while helping: "By the way, I've been trying to improve at
  debugging Python -- want me to try a different approach for this error?"

- **Competence tracking:** every task outcome feeds back into the
  `CompetenceDrive`. Over time, Jarvis identifies and addresses its own
  weaknesses without being told.

- **User project awareness:** when Ulrich mentions a project, register it.
  The `UtilityDrive` then generates subgoals that advance it, even when
  Ulrich hasn't explicitly asked.

---

## Integration Architecture: Wiring Everything Together

```
┌─────────────────────────────────────────────────────────────────┐
│                        JARVIS BRAIN v2                         │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              AUTONOMOUS GOAL ENGINE                     │   │
│  │  curiosity | competence | novelty | utility             │   │
│  └──────────────────────┬──────────────────────────────────┘   │
│                         │ goals                                 │
│  ┌──────────────────────▼──────────────────────────────────┐   │
│  │              ATTENTIONAL CONTROLLER                     │   │
│  │  goal register | attention filter | fatigue model       │   │
│  └──────────────────────┬──────────────────────────────────┘   │
│                         │ filtered signals                      │
│  ┌──────────────────────▼──────────────────────────────────┐   │
│  │              GLOBAL WORKSPACE (GWT)                     │   │
│  │  + Phi metric | higher-order monitor | recurrence       │   │
│  └──────────────────────┬──────────────────────────────────┘   │
│                         │ broadcast                             │
│  ┌──────────────────────▼──────────────────────────────────┐   │
│  │              REASONING PROGRAM COMPILER                 │   │
│  │  decompose → compile → execute chains of primitives     │   │
│  └──────────────────────┬──────────────────────────────────┘   │
│                         │ program                               │
│  ┌──────────────────────▼──────────────────────────────────┐   │
│  │              MoE ROUTER + META-LEARNER                  │   │
│  │  strategy selection | parameter adaptation | meta-lr    │   │
│  └──────────────────────┬──────────────────────────────────┘   │
│                         │ expert(s)                              │
│  ┌──────────────────────▼──────────────────────────────────┐   │
│  │              WORLD MODEL                                │   │
│  │  state transitions | causal rules | mental simulation   │   │
│  └──────────────────────┬──────────────────────────────────┘   │
│                         │ predicted outcomes                    │
│  ┌──────────────────────▼──────────────────────────────────┐   │
│  │              METACOGNITIVE MONITOR                      │   │
│  │  calibration | explanation | error detection | epistemic │   │
│  └──────────────────────┬──────────────────────────────────┘   │
│                         │ calibrated response                   │
│                         ▼                                       │
│                     RESPONSE                                    │
│                                                                 │
│  ╔═════════════════════════════════════════════════════════╗   │
│  ║  EXISTING SUBSYSTEMS (already built)                   ║   │
│  ║  Holographic Memory | Associative Memory | ACT-R       ║   │
│  ║  Emotional State | User Model | Predictive Engine      ║   │
│  ║  Dream Cycle | STDP | Common Sense KB                  ║   │
│  ║  Forward/Backward Chaining | Self-Awareness            ║   │
│  ╚═════════════════════════════════════════════════════════╝   │
└─────────────────────────────────────────────────────────────────┘
```

## Suggested Implementation Order

1. **World Model** (highest value, integrates with existing prediction engine)
2. **Metacognitive Monitor** (calibrates existing confidence, catches errors)
3. **Attentional Controller** (sits before GWT, immediate quality improvement)
4. **Autonomous Goal Engine** (extends existing curiosity engine)
5. **Reasoning Program Compiler** (enhances MoE router)
6. **Meta-Learner** (builds on top of everything else)
7. **Consciousness Integration** (measurement + monitoring layer, least urgent)

Each component is designed to be:
- **Standalone-testable**: can be unit tested without the full system
- **Incrementally deployable**: wire in one at a time
- **LLM-independent**: no large model training required
- **Lightweight**: all algorithms are O(n) or O(n log n) at worst
