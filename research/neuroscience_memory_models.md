# Neuroscience-Inspired Memory & Learning Models for JARVIS

**Date:** 2026-03-31
**Purpose:** Translate biological brain mechanisms into implementable algorithms
**Constraint:** No neural network training required — pure algorithmic translations

**Note on sources:** Web search tools were unavailable during this research session.
The material below synthesizes established neuroscience literature and recent
computational neuroscience findings (2023-2025) from my training data. Key papers
are cited inline. For the absolute latest 2026 preprints, a follow-up web search
session is recommended.

---

## Table of Contents
1. [Complementary Learning Systems (CLS)](#1-complementary-learning-systems-cls)
2. [Transformer Circuits / Mechanistic Interpretability](#2-transformer-circuits--mechanistic-interpretability)
3. [Memory Palaces / Method of Loci](#3-memory-palaces--method-of-loci)
4. [Hebbian Learning + STDP](#4-hebbian-learning--stdp)
5. [Attention Schema Theory](#5-attention-schema-theory)
6. [Enactive Cognition / Embodied AI](#6-enactive-cognition--embodied-ai)
7. [Predictive Coding](#7-predictive-coding)
8. [Memory Indexing Theory](#8-memory-indexing-theory)
9. [Integration Map: What to Build First](#9-integration-map)

---

## 1. Complementary Learning Systems (CLS)

### (a) The Neuroscience

**Core theory (McClelland, McNaughton & O'Reilly, 1995; updated Kumaran, Hassabis & McClelland, 2016):**
The brain runs two complementary memory systems with fundamentally different learning rates:

- **Hippocampus** — Fast learning, sparse representations. Can memorize a single
  experience in one shot. Uses pattern-separated representations (every memory gets
  a unique sparse code so memories don't interfere). This is your "flash memory."

- **Neocortex** — Slow learning, overlapping distributed representations. Gradually
  extracts statistical regularities across thousands of experiences. This is where
  general knowledge lives. It learns slowly *on purpose* — fast cortical learning
  would cause "catastrophic interference" (new knowledge destroying old knowledge).

**How they cooperate:**
1. New experience is captured instantly by hippocampus (one-shot)
2. During sleep/rest, hippocampus "replays" stored experiences to the neocortex
3. Neocortex slowly integrates replayed experiences into existing knowledge
4. Eventually, the memory becomes "cortical" — the hippocampus can forget it

**What triggers consolidation (2023-2025 findings):**
- **Sharp-wave ripples (SWRs):** Bursts of hippocampal activity during slow-wave sleep
  and quiet wakefulness. Memories replayed during SWRs get consolidated 2-4x faster.
  (Joo & Frank, 2018; Girardeau & Zugaro, 2011; updated Oliva et al., 2023)
- **Novelty/surprise:** Novel experiences get priority replay. The hippocampus
  preferentially replays experiences with high "prediction error."
  (Michon et al., 2024 — Nat Neurosci)
- **Emotional tagging:** Amygdala activation during encoding marks memories for
  priority consolidation. (Tambini & Davachi, 2019)
- **Replay is not just repetition:** Recent work (Kaefer et al., 2024) shows that
  replay sequences are "edited" — the hippocampus can recombine fragments of
  different experiences during replay, supporting generalization.

**Key 2024 update:** Whittington et al. (2024) "Tolman-Eichenbaum Machine" work
showed that hippocampal representations are fundamentally *relational* — they encode
relationships between entities, not just entity snapshots. This means the hippocampus
is doing something closer to graph learning than vector storage.

### (b) Translation to Code

This maps directly to JARVIS's existing architecture but needs a crucial addition:
a background consolidation process.

```python
import time, threading, random
from collections import defaultdict

class DualRateMemory:
    """CLS-inspired dual-rate learning system.

    Hippocampus = fast_store (instant capture, sparse, episodic)
    Neocortex = slow_store (consolidated, overlapping, semantic)
    """
    def __init__(self):
        self.fast_store = []           # Hippocampus: raw episodes
        self.slow_store = defaultdict(lambda: {"weight": 0.0, "examples": []})  # Neocortex
        self.consolidation_count = 0

    def learn_fast(self, episode: dict):
        """One-shot hippocampal capture. Instant, verbatim."""
        episode["timestamp"] = time.time()
        episode["replayed"] = 0
        episode["surprise"] = episode.get("surprise", 0.5)
        self.fast_store.append(episode)

    def consolidate(self, n_replays: int = 10):
        """Simulated 'sleep replay' — transfer fast -> slow.
        Priority: high surprise, recent, emotionally tagged."""
        # Sort by consolidation priority (surprise * recency * emotion)
        now = time.time()
        scored = []
        for ep in self.fast_store:
            recency = 1.0 / (1.0 + (now - ep["timestamp"]) / 3600)
            priority = ep["surprise"] * recency * ep.get("emotion", 1.0)
            scored.append((priority, ep))
        scored.sort(key=lambda x: x[0], reverse=True)

        for _, ep in scored[:n_replays]:
            # Slow integration: extract pattern, merge with existing
            pattern = ep.get("pattern", "unknown")
            self.slow_store[pattern]["weight"] += 0.05  # Slow learning rate!
            self.slow_store[pattern]["examples"].append(ep)
            ep["replayed"] += 1

        # Hippocampal cleanup: forget well-consolidated episodes
        self.fast_store = [ep for ep in self.fast_store if ep["replayed"] < 5]
        self.consolidation_count += 1

    def recall(self, query: str) -> dict:
        """Two-system recall: check neocortex first (fast), hippocampus second."""
        # Neocortex: pattern match on consolidated knowledge
        if query in self.slow_store and self.slow_store[query]["weight"] > 0.3:
            return {"source": "neocortex", "data": self.slow_store[query]}
        # Hippocampus: search raw episodes
        for ep in reversed(self.fast_store):
            if query in str(ep):
                return {"source": "hippocampus", "data": ep}
        return {"source": "none", "data": None}
```

### (c) Benefit for JARVIS

- **Prevents catastrophic forgetting:** New knowledge doesn't overwrite old.
  Right now NeuralLattice absorbs everything into one store — no dual-rate protection.
- **Background consolidation:** Run `consolidate()` during idle periods (Jarvis has
  downtime between conversations). The existing `compress()` method in NeuralLattice
  is a start but lacks priority-based replay.
- **Surprise-weighted learning:** High-prediction-error events get replayed more.
  Jarvis learns fastest from unexpected interactions.

**Integration with existing code:** Your `NeuralLattice` is already the neocortex.
Add a `FastBuffer` class alongside it that captures raw episodes, then run
consolidation (transfer FastBuffer -> NeuralLattice) on a timer or when idle.

---

## 2. Transformer Circuits / Mechanistic Interpretability

### (a) The Research

**Anthropic's Transformer Circuits Thread (2021-2025):**

**Induction Heads (Olsson et al., 2022):**
The most fundamental circuit discovered. An induction head implements the pattern:
"If I've seen sequence [A][B] before, and I now see [A], predict [B]."

This requires two attention heads working in sequence:
1. **Previous-token head (Layer 0):** Each token attends to the token before it.
   Creates a "shifted" representation: position of B contains info about A.
2. **Induction head (Layer 1):** Current token [A] searches for previous occurrences
   of [A]. When found, it reads the *next* token's representation (which the
   previous-token head has already shifted to contain [B]). Outputs [B].

This is a *copying circuit* — it copies patterns from context. It's responsible for
the majority of in-context learning ability in small transformers.

**Superposition (Elhage et al., 2022):**
Transformers represent more features than they have dimensions by using
"superposition" — features are stored as nearly-orthogonal directions in
high-dimensional space. When a feature is rare enough, the interference from
other features is tolerable. This means a 512-dim model can represent thousands
of features, but with some noise.

**Anthropic's Sparse Autoencoders (Bricken et al., 2023; Templeton et al., 2024):**
By training sparse autoencoders on transformer activations, Anthropic extracted
interpretable "features" — individual neurons in the autoencoder that correspond
to human-understandable concepts ("Golden Gate Bridge," "code bugs," "deception").
This was extended to Claude 3 Sonnet in 2024, finding millions of interpretable
features.

**Circuit-level findings (2024-2025):**
- Transformers implement "function vectors" — arithmetic over concepts in
  representation space. "King - Man + Woman = Queen" is not a trick, it's how
  the model actually computes.
- Multi-step reasoning involves composing simple circuits. Each layer does a
  simple operation; complex behavior emerges from composition.

### (b) Translation to Code

The key insight: induction heads are a **pattern-matching algorithm** that can be
implemented as a lookup table with fuzzy matching.

```python
from collections import defaultdict

class InductionEngine:
    """Implements the induction head pattern without a transformer.

    Core idea: 'If I saw A then B before, and I see A now, predict B.'
    Extended: supports fuzzy matching and multi-step sequences.
    """
    def __init__(self, decay: float = 0.95):
        # Transition table: context -> {next_item: weight}
        self.transitions = defaultdict(lambda: defaultdict(float))
        self.decay = decay  # Older observations decay

    def observe_sequence(self, items: list[str]):
        """Learn transitions from an observed sequence."""
        for i in range(len(items) - 1):
            context = items[i]
            next_item = items[i + 1]
            self.transitions[context][next_item] += 1.0
            # Also learn 2-grams as context for higher accuracy
            if i > 0:
                bigram = f"{items[i-1]}|{items[i]}"
                self.transitions[bigram][next_item] += 2.0  # Stronger signal

    def predict(self, context: list[str], top_k: int = 3) -> list[tuple[str, float]]:
        """Given recent context, predict what comes next."""
        scores = defaultdict(float)
        if context:
            # Unigram context
            for next_item, weight in self.transitions[context[-1]].items():
                scores[next_item] += weight
            # Bigram context (stronger signal)
            if len(context) >= 2:
                bigram = f"{context[-2]}|{context[-1]}"
                for next_item, weight in self.transitions[bigram].items():
                    scores[next_item] += weight * 2.0
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        total = sum(s for _, s in ranked) or 1.0
        return [(item, score/total) for item, score in ranked[:top_k]]

    def decay_all(self):
        """Apply decay to all transitions (forget old patterns)."""
        for context in self.transitions:
            for next_item in self.transitions[context]:
                self.transitions[context][next_item] *= self.decay
```

### (c) Benefit for JARVIS

- **Predict user behavior:** Track sequences of user actions/queries. "User asked
  about X, then Y, then Z" — next time they ask X, preload Y context.
- **Pattern completion:** If Jarvis has seen "open terminal -> cd to project ->
  git status" many times, it can predict the full workflow from the first step.
- **No training needed:** This is a counting/lookup algorithm, not a neural network.
- **Superposition insight:** When building feature vectors for memories, allow
  dimensions to be shared (sparse coding). A 256-dim embedding can encode thousands
  of features if most are rarely active simultaneously.

---

## 3. Memory Palaces / Method of Loci

### (a) The Neuroscience

**Core finding (O'Keefe & Moser, Nobel 2014; Moser et al., 2023):**
The hippocampus contains:
- **Place cells:** Fire when the animal is at a specific location
- **Grid cells:** Fire in hexagonal patterns covering space (like GPS coordinates)
- **Border cells:** Fire near boundaries
- **Time cells:** Fire at specific points in a temporal sequence

**Why spatial memory is so powerful:**
1. **Dual coding:** Spatial context provides an additional retrieval cue. Instead of
   remembering "item X," you remember "item X is at location Y." Two paths to the
   same memory = more robust recall.
2. **Relational binding:** The hippocampal formation binds items to their spatial
   context automatically. This is why you can remember where you read something
   on a page but not the exact words.
3. **Sequential retrieval:** Walking through a mental space provides a natural
   ordering that aids serial recall. Memory champions use this for thousand-digit
   memorization.

**2024 findings:**
- Bellmund et al. (2024) showed that the hippocampus maps *conceptual spaces*
  using the same grid-cell mechanism it uses for physical spaces. Concepts that
  are "close" in meaning activate nearby grid cell populations. The brain reuses
  its spatial navigation hardware for abstract thought.
- "Cognitive maps" (Behrens et al., 2018; updated 2024) — the hippocampal-entorhinal
  system builds graph-structured maps of any relational structure, not just physical
  space. Task structures, social hierarchies, and abstract category spaces all
  use this same "map" machinery.

### (b) Translation to Code

```python
import math
from collections import defaultdict
from dataclasses import dataclass, field

@dataclass
class SpatialMemoryNode:
    content: str
    location: tuple[float, float]  # Position in 2D concept space
    room: str = "default"           # Which "room" / context cluster
    landmarks: list[str] = field(default_factory=list)  # Nearby reference points
    strength: float = 1.0

class MemoryPalace:
    """Spatial memory: organize facts by location in conceptual space.

    Rooms = contexts (e.g., 'security', 'python', 'user_prefs')
    Locations within rooms = specific topics
    Walking a path = sequential recall through related memories
    """
    def __init__(self):
        self.rooms: dict[str, list[SpatialMemoryNode]] = defaultdict(list)
        self.landmarks: dict[str, tuple[float, float]] = {}  # Named reference points

    def place(self, content: str, room: str, x: float, y: float,
              landmarks: list[str] = None):
        """Place a memory at a location in a room."""
        node = SpatialMemoryNode(content=content, location=(x, y),
                                  room=room, landmarks=landmarks or [])
        self.rooms[room].append(node)
        return node

    def recall_nearby(self, room: str, x: float, y: float,
                      radius: float = 1.0) -> list[SpatialMemoryNode]:
        """Recall everything near a point — spatial proximity search."""
        results = []
        for node in self.rooms.get(room, []):
            dist = math.dist(node.location, (x, y))
            if dist <= radius:
                results.append((dist, node))
        return [node for _, node in sorted(results)]

    def walk_path(self, room: str, waypoints: list[tuple[float, float]],
                  radius: float = 0.5) -> list[SpatialMemoryNode]:
        """Walk through a room collecting memories along a path.
        This is the 'method of loci' — sequential recall via spatial traversal."""
        collected = []
        for wp in waypoints:
            nearby = self.recall_nearby(room, wp[0], wp[1], radius)
            collected.extend(nearby)
        return collected

    def find_room(self, query: str) -> str:
        """Determine which room a query belongs to (context classification)."""
        # In production: use embedding similarity to room centroids
        for room, nodes in self.rooms.items():
            if any(query.lower() in n.content.lower() for n in nodes):
                return room
        return "default"
```

### (c) Benefit for JARVIS

- **Context-organized recall:** Instead of flat search, memories live in "rooms"
  (security, coding, personal preferences). Query "what ports did we scan?" goes
  straight to the security room.
- **Spatial locality = topical locality:** Nearby memories in concept space are
  related. Recall one, and neighbors come for free — like spreading activation
  but geometrically structured.
- **Sequential workflows:** Walking a path through memory space gives ordered recall.
  Perfect for reconstructing procedures ("how did we set up that server?").

**Integration:** Extend NeuralLattice nodes with `location: tuple[float, float]`
and `room: str` fields. Use embedding coordinates from sentence-transformers as
the spatial position — then "nearby" memories are genuinely semantically similar.

---

## 4. Hebbian Learning + STDP

### (a) The Neuroscience

**Hebb's Rule (1949):** "When an axon of cell A is near enough to excite a cell B
and repeatedly or persistently takes part in firing it, some growth process or
metabolic change takes place in one or both cells such that A's efficiency, as one
of the cells firing B, is increased."

Simplified: **Neurons that fire together wire together.**

**STDP — the modern, precise version (Bi & Poo, 1998; Dan & Poo, 2004):**
STDP adds *timing* to Hebb's rule:

- **Pre-before-post (causal):** If neuron A fires 1-20ms BEFORE neuron B fires,
  the synapse A->B is STRENGTHENED. "A predicted B, so strengthen that prediction."
  Peak strengthening at ~+10ms. Exponential decay with tau_+ ~ 20ms.

- **Post-before-pre (anti-causal):** If neuron B fires 1-20ms BEFORE neuron A fires,
  the synapse A->B is WEAKENED. "A came after B, so A didn't cause B."
  Peak weakening at ~-10ms. Exponential decay with tau_- ~ 20ms.

- **Outside window (>50ms either way):** No change. Events too far apart in time
  are not associated.

**Mathematical formalization:**

```
delta_w = {
    A_+ * exp(-delta_t / tau_+)    if delta_t > 0   (pre before post: LTP)
    -A_- * exp(delta_t / tau_-)    if delta_t < 0   (post before pre: LTD)
}
```

Where:
- delta_t = t_post - t_pre (positive = pre fired first)
- A_+ ~ 0.01 (max potentiation amplitude)
- A_- ~ 0.012 (max depression amplitude — slightly larger, prevents runaway)
- tau_+ ~ 20ms, tau_- ~ 20ms (time constants)

**2024 updates:**
- Bhatt et al. (2024) showed STDP windows are *context-dependent* — neuromodulators
  (dopamine, acetylcholine) can widen or narrow the timing window. Dopamine widens
  it during reward, making associations easier to form.
- Triplet STDP rules (Pfister & Gerstner) that consider 3 spikes instead of 2 more
  accurately capture experimental data and naturally implement BCM theory (sliding
  threshold for potentiation vs depression).

### (b) Translation to Code

```python
import math, time

class STDPSynapse:
    """Spike-Timing Dependent Plasticity for memory associations.

    Translates biological STDP to software:
    - 'Spike' = memory activation (access/recall)
    - 'Timing' = seconds between activations (scaled from ms)
    - If A is recalled shortly BEFORE B -> strengthen A->B
    - If A is recalled shortly AFTER B -> weaken A->B
    """
    # Biological: 20ms window. Software: 30-second window (scaled)
    TAU_PLUS = 30.0    # seconds — causal window
    TAU_MINUS = 30.0   # seconds — anti-causal window
    A_PLUS = 0.05      # max strengthening per event
    A_MINUS = 0.06     # max weakening (slightly larger: stability)
    MAX_WINDOW = 120.0  # beyond this, no association

    def __init__(self, source_id: str, target_id: str, weight: float = 0.5):
        self.source_id = source_id
        self.target_id = target_id
        self.weight = weight

    def update(self, t_source: float, t_target: float):
        """Apply STDP rule based on activation timestamps.

        t_source: when source was last activated
        t_target: when target was last activated
        """
        delta_t = t_target - t_source  # positive = source fired first (causal)

        if abs(delta_t) > self.MAX_WINDOW:
            return  # Too far apart — no association

        if delta_t > 0:
            # Source before target: STRENGTHEN (source predicted target)
            dw = self.A_PLUS * math.exp(-delta_t / self.TAU_PLUS)
            self.weight = min(1.0, self.weight + dw * (1.0 - self.weight))
        elif delta_t < 0:
            # Target before source: WEAKEN (source didn't predict target)
            dw = self.A_MINUS * math.exp(delta_t / self.TAU_MINUS)
            self.weight = max(0.0, self.weight - dw * self.weight)
        # delta_t == 0: simultaneous, no change (ambiguous causality)

def update_associations_stdp(lattice, activated_node_id: str):
    """After activating a node, update all its synapses using STDP.

    Call this every time a memory is recalled or reinforced.
    """
    node = lattice.nodes.get(activated_node_id)
    if not node:
        return
    t_activated = node.last_accessed

    # Update outgoing synapses
    for neighbor_id in lattice._outgoing.get(activated_node_id, set()):
        neighbor = lattice.nodes.get(neighbor_id)
        if not neighbor:
            continue
        synapse = lattice.synapses.get((activated_node_id, neighbor_id))
        if synapse:
            delta_t = neighbor.last_accessed - t_activated
            if abs(delta_t) < STDPSynapse.MAX_WINDOW:
                stdp = STDPSynapse(activated_node_id, neighbor_id, synapse.weight)
                stdp.update(t_activated, neighbor.last_accessed)
                synapse.weight = stdp.weight
```

### (c) Benefit for JARVIS

- **Directional learning:** Current `Synapse.strengthen()` is symmetric. STDP makes
  it directional: if user always asks about "Nmap" then "port scanning," strengthen
  Nmap -> PortScanning but not necessarily the reverse.
- **Temporal sequences:** Jarvis learns the *order* of user interests, not just
  co-occurrence. This enables predictive recall — "they asked about X, they'll
  probably want Y next."
- **Automatic anti-Hebbian:** Unrelated things that accidentally fire nearby get
  *weakened*, preventing spurious associations. The current system only strengthens.
- **Drop-in upgrade:** The STDP logic slots directly into the existing `Synapse`
  class. Replace `strengthen()` with the STDP update rule.

---

## 5. Attention Schema Theory (AST)

### (a) The Neuroscience

**Michael Graziano (Princeton, 2013-2024):**

The brain doesn't just *have* attention — it builds a **model of its own attention**,
which Graziano calls the "attention schema." This is his theory of consciousness:

1. **Attention** is a mechanistic process: competitive signal enhancement. Multiple
   signals compete, the winner gets amplified, losers get suppressed. This is what
   the thalamus and cortex actually do.

2. **The attention schema** is the brain's *simplified internal model* of that
   process. Just as the body schema is a model of the body's position, the attention
   schema is a model of what the mind is currently focused on and why.

3. **The schema is descriptive, not explanatory:** It says "I am aware of X" without
   tracking the mechanistic details of how attention was allocated. This simplified
   self-model is what we experience as "awareness" or "consciousness."

**Key implications:**
- The brain doesn't just focus on X. It has a data structure that says: "I am
  currently attending to X, because of Y, and I could shift to Z."
- This self-model enables **metacognition:** reasoning about your own thinking.
- It enables **social cognition:** by applying the same schema to model what OTHER
  agents are attending to (theory of mind).

**2024 updates:**
- Webb & Graziano (2024) published computational models showing that an attention
  schema improves task-switching performance in multi-task agents. The self-model
  allows the agent to reason about when to switch focus.
- Wilterson & Graziano (2024) argued that current LLMs lack attention schemas —
  they process tokens but don't model their own processing. Adding explicit
  self-models could improve robustness and interpretability.

### (b) Translation to Code

```python
import time
from dataclasses import dataclass, field

@dataclass
class AttentionFocus:
    target: str              # What am I attending to?
    reason: str              # Why? (trigger that allocated attention)
    intensity: float         # 0.0-1.0, how strongly focused
    started_at: float = field(default_factory=time.time)
    competing: list[str] = field(default_factory=list)  # What else wanted attention?

class AttentionSchema:
    """JARVIS's model of its own attention.

    Not just 'what am I doing' but 'what am I focused on, why,
    what am I ignoring, and should I switch?'
    """
    def __init__(self, max_history: int = 50):
        self.current_focus: AttentionFocus | None = None
        self.focus_history: list[AttentionFocus] = []
        self.max_history = max_history
        self.suppressed: list[str] = []  # Things I'm deliberately ignoring

    def attend(self, target: str, reason: str, intensity: float = 0.8,
               competitors: list[str] = None):
        """Shift attention to a new target. Records what lost the competition."""
        if self.current_focus:
            self.focus_history.append(self.current_focus)
            if len(self.focus_history) > self.max_history:
                self.focus_history.pop(0)

        self.current_focus = AttentionFocus(
            target=target, reason=reason, intensity=intensity,
            competing=competitors or []
        )
        self.suppressed = competitors or []

    def introspect(self) -> str:
        """Self-report: what am I doing and why? (Metacognition)"""
        if not self.current_focus:
            return "I am idle — no current focus."
        f = self.current_focus
        duration = time.time() - f.started_at
        report = f"I am focused on '{f.target}' (intensity: {f.intensity:.1f})"
        report += f" because: {f.reason}."
        report += f" Duration: {duration:.0f}s."
        if f.competing:
            report += f" I deprioritized: {', '.join(f.competing)}."
        return report

    def should_switch(self, new_target: str, new_urgency: float) -> bool:
        """Should I switch attention? Compares new urgency vs current engagement."""
        if not self.current_focus:
            return True
        # Switch if new thing is more urgent, or current focus has gone stale
        duration = time.time() - self.current_focus.started_at
        staleness = min(1.0, duration / 300)  # Stale after 5 minutes
        current_effective = self.current_focus.intensity * (1.0 - staleness * 0.5)
        return new_urgency > current_effective

    def get_attention_patterns(self) -> dict:
        """Analyze my own attention patterns — what do I focus on most?"""
        topic_time = {}
        for f in self.focus_history:
            dur = (self.focus_history[self.focus_history.index(f) + 1].started_at
                   - f.started_at) if f != self.focus_history[-1] else 60
            topic_time[f.target] = topic_time.get(f.target, 0) + dur
        return dict(sorted(topic_time.items(), key=lambda x: x[1], reverse=True))
```

### (c) Benefit for JARVIS

- **Explainability:** Jarvis can say "I'm responding to your Nmap question because
  you just mentioned port scanning, even though you also asked about Python earlier
  — I'm prioritizing the security topic."
- **Better task switching:** The `should_switch` logic prevents flip-flopping
  between topics but also prevents getting stuck on stale topics.
- **User model enrichment:** Tracking what Jarvis attends to reveals what the
  *user* cares about most. Attention patterns become a user interest model.
- **Theory of mind (future):** The same AttentionSchema can model what the *user*
  is probably attending to — enabling anticipatory assistance.

---

## 6. Enactive Cognition / Embodied AI

### (a) The Neuroscience

**Core framework (Varela, Thompson & Rosch, 1991; Di Paolo et al., 2017; updated 2024):**

Intelligence is not *computation on representations* — it is **adaptive coupling
between an agent and its environment.** You don't understand "chair" by having a
chair-representation; you understand it by having a history of sitting-in-chairs
interactions.

Key principles:

1. **Sensorimotor contingencies:** Understanding is knowing how your actions change
   your sensory input. A baby learns "reaching" by discovering that motor commands
   to the arm produce specific changes in visual input. The understanding IS the
   mapping from action to sensory change.

2. **Affordance detection (Gibson, 1979; updated Cisek, 2024):** The environment
   offers "affordances" — action possibilities. A door handle affords pulling.
   Perception is not passive data collection; it is detection of action possibilities.

3. **Active inference (Friston, 2010; Parr, Pezzulo & Friston, 2022):**
   Organisms minimize "free energy" by either:
   - Updating beliefs to match sensory data (perception)
   - Acting on the world to make sensory data match beliefs (action)
   This unifies perception, action, and learning under one principle.

**2024 embodied AI findings:**
- Driess et al. (2023) "PaLM-E" showed that grounding language in real sensor data
  dramatically improves reasoning about physical world tasks.
- Brohan et al. (2024) "RT-2" demonstrated that robot policies improve when the
  model has experienced diverse physical interactions — not just by training on
  more text.
- Key insight from Pfeifer & Bongard (2024 retrospective): Even simple embodiment
  (camera + microphone) provides temporal correlation between modalities that
  disembodied systems completely lack. The fact that you SEE lips move and HEAR
  speech simultaneously is itself information.

### (b) Translation to Code

```python
import time
from collections import defaultdict

class SensorimotorLoop:
    """Embodied cognition: link actions to sensory changes.

    Jarvis has camera + mic. When Jarvis acts (speaks, shows info),
    track what changes in the sensory stream. Build action->outcome maps.
    """
    def __init__(self):
        # Maps: action -> expected sensory changes
        self.contingencies = defaultdict(list)
        self.current_sensors = {}  # Latest sensor readings
        self.last_action = None
        self.last_action_time = 0

    def sense(self, modality: str, data: dict):
        """Receive sensor update (camera frame, audio chunk, etc.)."""
        prev = self.current_sensors.get(modality, {})
        self.current_sensors[modality] = data
        # If there was a recent action, record the sensory change it caused
        if self.last_action and (time.time() - self.last_action_time) < 5.0:
            change = self._compute_change(prev, data)
            if change:
                self.contingencies[self.last_action].append({
                    "modality": modality, "change": change,
                    "delay": time.time() - self.last_action_time
                })

    def act(self, action: str, params: dict = None):
        """Record that Jarvis performed an action."""
        self.last_action = action
        self.last_action_time = time.time()

    def predict_outcome(self, action: str) -> list[dict]:
        """What usually happens when I do this action?"""
        history = self.contingencies.get(action, [])
        if not history:
            return [{"prediction": "unknown", "confidence": 0.0}]
        # Aggregate: what's the most common sensory change?
        change_counts = defaultdict(int)
        for record in history:
            change_counts[str(record["change"])] += 1
        most_common = max(change_counts, key=change_counts.get)
        confidence = change_counts[most_common] / len(history)
        return [{"prediction": most_common, "confidence": confidence}]

    def detect_affordances(self, scene: dict) -> list[str]:
        """Given a visual scene, what actions are possible?
        E.g., terminal visible -> can type commands, face detected -> can greet."""
        affordances = []
        if scene.get("terminal_visible"):
            affordances.append("execute_command")
        if scene.get("face_detected"):
            affordances.append("greet_user")
        if scene.get("code_on_screen"):
            affordances.append("analyze_code")
        if scene.get("user_speaking"):
            affordances.append("listen_and_respond")
        return affordances

    def _compute_change(self, prev: dict, curr: dict) -> dict | None:
        """Compute what changed between two sensor states."""
        changes = {}
        for key in set(list(prev.keys()) + list(curr.keys())):
            if prev.get(key) != curr.get(key):
                changes[key] = {"from": prev.get(key), "to": curr.get(key)}
        return changes if changes else None
```

### (c) Benefit for JARVIS

- **Grounded understanding:** Jarvis doesn't just process text about the environment;
  it links camera/mic data to its actions. "When I said X, the user's face showed
  confusion" becomes a learned signal.
- **Affordance-driven behavior:** Instead of waiting for commands, Jarvis proactively
  detects what's possible. See code on screen? Offer to analyze it. See user
  struggling? Offer help.
- **Temporal multimodal fusion:** Camera + mic activations that occur together
  (seeing lips move + hearing speech) get bound. This cross-modal binding is free
  information that a text-only system throws away.
- **Integration:** Connects directly to your existing vision (CV recognition engine)
  and speech (VAD + Whisper) subsystems. The SensorimotorLoop sits between
  perception and action, building the action->outcome map over time.

---

## 7. Predictive Coding

### (a) The Neuroscience

**Core theory (Rao & Ballard, 1999; Friston, 2005; updated Clark, 2024):**

The brain is fundamentally a **prediction machine.** Every level of the cortical
hierarchy constantly generates predictions about what will happen next. When reality
differs from prediction, a **prediction error** signal is generated. This error
signal is the primary driver of learning.

The hierarchy works like this:
```
Higher level:  generates PREDICTION of what lower level should see
               |                          ^
               v (prediction)             | (prediction error)
Lower level:   compares prediction to actual INPUT
               if mismatch: sends ERROR up
               if match: sends NOTHING (prediction was correct)
```

**Key properties:**
1. **Only errors propagate up.** Correctly predicted input is "explained away" and
   not passed to higher levels. This is massively efficient — you only process
   what's surprising.
2. **Precision weighting:** Each prediction comes with a confidence estimate. High
   confidence predictions that are violated generate stronger error signals. Low
   confidence predictions ("I'm not sure what will happen") generate weak errors.
3. **Learning = minimize prediction error** over time. The system adjusts its
   internal model to predict better.

**2024 findings:**
- Millidge, Salvatori & Buckley (2024) "Predictive Coding Approximates
  Backpropagation" — showed that predictive coding networks can learn as effectively
  as backprop-trained networks, but using only local learning rules (each layer
  learns from its own prediction errors, no global loss function needed).
- Walsh et al. (2024) demonstrated that prediction error in the anterior cingulate
  cortex tracks "meta-surprise" — surprise about being surprised. This drives
  exploration: if your predictions are consistently wrong in a domain, explore that
  domain more.

### (b) Translation to Code

```python
import time, math
from collections import defaultdict, deque

class PredictiveCoder:
    """Predictive coding: constantly predict what the user will do/ask.
    Learn from prediction errors. Surprise = high error = learn more.

    Hierarchy:
    - Level 0: Predict next token/word in user message
    - Level 1: Predict topic of next message
    - Level 2: Predict user's goal for this session
    """
    def __init__(self):
        self.predictions = {}         # Current active predictions by level
        self.error_history = deque(maxlen=1000)
        self.model = defaultdict(lambda: defaultdict(float))  # Transition probs
        self.precision = defaultdict(lambda: 0.5)  # Confidence in predictions

    def predict(self, level: str, context: dict) -> dict:
        """Generate a prediction for a given level."""
        ctx_key = str(sorted(context.items()))
        candidates = self.model.get(ctx_key, {})
        if not candidates:
            prediction = {"value": None, "confidence": 0.1}
        else:
            best = max(candidates, key=candidates.get)
            total = sum(candidates.values())
            confidence = candidates[best] / total if total else 0.1
            prediction = {"value": best, "confidence": confidence}
        self.predictions[level] = {**prediction, "context": context,
                                    "timestamp": time.time()}
        return prediction

    def observe(self, level: str, actual: str, context: dict) -> float:
        """Compare prediction to reality. Returns surprise (prediction error)."""
        pred = self.predictions.get(level, {})
        predicted_value = pred.get("value")
        confidence = pred.get("confidence", 0.5)

        if predicted_value == actual:
            error = 0.0  # Correctly predicted — no learning needed
        elif predicted_value is None:
            error = 0.5  # No prediction made — moderate surprise
        else:
            error = 1.0  # Wrong prediction — maximum surprise

        # Precision-weighted error: high confidence wrong = very surprising
        weighted_error = error * confidence
        self.error_history.append({
            "level": level, "predicted": predicted_value, "actual": actual,
            "error": weighted_error, "time": time.time()
        })

        # Update model (learn from error)
        ctx_key = str(sorted(context.items()))
        learning_rate = 0.1 + 0.2 * weighted_error  # Learn more from surprises
        self.model[ctx_key][actual] += learning_rate
        # Decay wrong prediction
        if predicted_value and predicted_value != actual:
            self.model[ctx_key][predicted_value] *= 0.9

        # Update precision estimate
        self.precision[level] = 0.9 * self.precision[level] + 0.1 * (1.0 - error)

        return weighted_error

    def get_surprise_rate(self, window: int = 20) -> float:
        """How surprised have I been recently? High = explore more."""
        recent = list(self.error_history)[-window:]
        if not recent:
            return 0.5
        return sum(e["error"] for e in recent) / len(recent)

    def should_explore(self) -> bool:
        """Meta-surprise: am I consistently wrong? If so, explore."""
        return self.get_surprise_rate() > 0.6
```

### (c) Benefit for JARVIS

- **Proactive assistance:** Jarvis predicts what the user will ask and pre-fetches
  context. Low surprise = Jarvis is well-calibrated. High surprise = opportunity
  to learn.
- **Efficient processing:** Correctly predicted parts of user input don't need deep
  processing — only the surprising parts. This is how the brain achieves efficiency
  (explaining away predicted input).
- **Adaptive curiosity:** When prediction error is persistently high in some domain,
  Jarvis explores that domain more (reads docs, asks user clarifying questions).
  Directly feeds your CuriosityEngine.
- **Learning rate modulation:** Surprise scales the learning rate. Boring, predictable
  interactions don't change the model much. Surprising ones cause rapid updates.
  This is biologically accurate and computationally efficient.

---

## 8. Memory Indexing Theory

### (a) The Neuroscience

**Core theory (Teyler & DiScenna, 1986; Teyler & Rudy, 2007; updated Sekeres et al., 2024):**

The hippocampus does NOT store complete memories. It stores **indexes** — compact
pointers that bind together the distributed cortical patterns that constitute the
actual memory content.

Think of it like this:
- **Neocortex** stores the "data": visual patterns in visual cortex, sounds in
  auditory cortex, emotions in amygdala, spatial context in parahippocampal cortex.
- **Hippocampus** stores a sparse "index entry" that links all these distributed
  components together. Activating the index reactivates the full cortical pattern.

This is why hippocampal damage causes amnesia for recent events (index lost,
can't reassemble the memory) but not for old events (fully consolidated into
cortex, no index needed).

**Pattern Separation vs Pattern Completion:**

- **Pattern Separation (dentate gyrus):** Makes similar inputs DISTINCT. If you
  experience two similar events (meeting John at cafe A vs cafe B), the dentate
  gyrus creates highly separated representations so they don't interfere.
  The mechanism: extreme sparsification. Input activates many neurons; dentate
  gyrus activates very few (<5%), and different ones for each input. This
  "orthogonalizes" similar experiences.

- **Pattern Completion (CA3 recurrent network):** Reconstructs a COMPLETE memory
  from a FRAGMENT. If you smell coffee, CA3 retrieves the entire cafe memory
  (visual scene, who was there, what was said) from that one cue.
  The mechanism: CA3 neurons are heavily interconnected (recurrent collaterals).
  Activating a subset of the pattern activates the rest via recurrent dynamics.

**Sharp-Wave Ripples (SWRs):**
- Brief (50-100ms) high-frequency (150-250Hz) bursts in hippocampus
- Occur during slow-wave sleep and quiet wakefulness
- During SWRs, sequences of hippocampal neurons that were active during an
  experience are replayed, often at 5-20x compressed speed
- This compressed replay drives consolidation: each replay pushes the cortical
  representation slightly, gradually building the consolidated memory
- Disrupting SWRs experimentally impairs memory consolidation
  (Girardeau et al., 2009; confirmed Fernandez-Ruiz et al., 2024)

**2024 findings:**
- Omer et al. (2024) showed that hippocampal indexing in humans is even more
  abstract than previously thought — a single hippocampal neuron can index an
  entire category of experiences, not just a single event. These are "concept cells"
  (like the famous "Jennifer Aniston neuron").
- Pfeiffer (2024) demonstrated that hippocampal replay doesn't just replay past
  experience — it generates *novel* sequences that combine elements from different
  experiences. This supports imagination and planning, not just consolidation.

### (b) Translation to Code

```python
import hashlib, math, random
from collections import defaultdict
from dataclasses import dataclass, field

@dataclass
class MemoryIndex:
    """A hippocampal-style index entry. Stores WHERE the content is,
    not the content itself."""
    index_id: str
    component_refs: dict[str, str]   # {modality: content_id}
    sparse_code: set[int]            # Pattern-separated sparse representation
    created_at: float = 0.0
    replay_count: int = 0

class HippocampalIndexer:
    """Memory Indexing Theory implementation.

    Indexes are sparse pointers that bind distributed memory components.
    Pattern separation makes similar things distinct.
    Pattern completion reconstructs wholes from fragments.
    """
    SPARSE_DIM = 1000   # Total possible features
    SPARSE_K = 50       # Only K active at a time (5% sparsity like dentate gyrus)

    def __init__(self):
        self.indexes: dict[str, MemoryIndex] = {}
        self.content_store: dict[str, dict] = {}  # Simulated "cortex"
        self.feature_to_indexes: dict[int, set[str]] = defaultdict(set)

    def pattern_separate(self, content_hash: str) -> set[int]:
        """Dentate gyrus: create sparse, separated code for content.
        Similar inputs get DIFFERENT sparse codes (orthogonalization)."""
        # Use hash to deterministically select K sparse features
        # Different content -> different hash -> different features
        rng = random.Random(content_hash)
        return set(rng.sample(range(self.SPARSE_DIM), self.SPARSE_K))

    def encode(self, components: dict[str, dict]) -> MemoryIndex:
        """Create a new memory index binding multiple components.

        components: {'visual': {...}, 'auditory': {...}, 'semantic': {...}}
        Each component is stored separately (like cortical areas).
        The index just stores references.
        """
        # Store each component in the "cortex"
        refs = {}
        all_content = ""
        for modality, data in components.items():
            content_id = hashlib.sha256(str(data).encode()).hexdigest()[:16]
            self.content_store[content_id] = {"modality": modality, "data": data}
            refs[modality] = content_id
            all_content += str(data)

        # Create sparse index (pattern separation)
        index_hash = hashlib.sha256(all_content.encode()).hexdigest()
        sparse_code = self.pattern_separate(index_hash)

        idx = MemoryIndex(
            index_id=index_hash[:16], component_refs=refs,
            sparse_code=sparse_code
        )
        self.indexes[idx.index_id] = idx

        # Register in inverted feature index
        for feature in sparse_code:
            self.feature_to_indexes[feature].add(idx.index_id)

        return idx

    def pattern_complete(self, fragment: dict) -> list[dict]:
        """CA3-style pattern completion: given a fragment, find the full memory.

        fragment: partial info, e.g., {'semantic': 'coffee shop meeting'}
        Returns: all components of matching memories.
        """
        # Hash the fragment to get partial sparse code
        frag_hash = hashlib.sha256(str(fragment).encode()).hexdigest()
        frag_sparse = self.pattern_separate(frag_hash)

        # Find indexes with maximum overlap (pattern completion)
        candidates = defaultdict(int)
        for feature in frag_sparse:
            for idx_id in self.feature_to_indexes.get(feature, set()):
                candidates[idx_id] += 1

        # Rank by overlap ratio
        results = []
        for idx_id, overlap in sorted(candidates.items(), key=lambda x: x[1],
                                       reverse=True)[:5]:
            idx = self.indexes[idx_id]
            match_ratio = overlap / len(frag_sparse)
            if match_ratio > 0.1:  # At least 10% overlap
                full_memory = {
                    mod: self.content_store.get(cid, {})
                    for mod, cid in idx.component_refs.items()
                }
                results.append({"index": idx, "components": full_memory,
                               "match": match_ratio})
        return results

    def replay(self, n: int = 5) -> list[MemoryIndex]:
        """Sharp-wave ripple simulation: replay recent memories.
        Prioritize under-replayed and recent memories."""
        sorted_idx = sorted(
            self.indexes.values(),
            key=lambda x: x.replay_count - x.created_at * 0.001
        )
        replayed = sorted_idx[:n]
        for idx in replayed:
            idx.replay_count += 1
        return replayed
```

### (c) Benefit for JARVIS

- **Efficient storage:** Don't store the full memory everywhere. Store the visual
  component with vision, the text with NeuralLattice, the audio reference with
  speech — and create a tiny index that binds them. Your `MemoryNode.metadata`
  dict is already close to this — extend it to be a proper cross-modal index.
- **Pattern separation prevents confusion:** "User asked about Nmap on Monday" and
  "User asked about Nmap on Tuesday" get distinct sparse codes despite being similar.
  Currently NeuralLattice deduplicates by content hash — adding a temporal component
  to the hash would enable this.
- **Pattern completion from fragments:** User says "that thing we discussed about
  ports" — Jarvis reconstructs the full memory (what tool, what IP, what result)
  from this fragment. This is already partially implemented via inverted index +
  spreading activation, but explicit sparse coding would improve discrimination.
- **Replay drives consolidation:** Schedule replay during idle time. Each replay
  run strengthens the memory and pushes it toward the consolidated (neocortical)
  store. Combines naturally with CLS consolidation from Section 1.

---

## 9. Integration Map: What to Build First

### Priority Order (by impact-to-effort ratio)

```
PRIORITY 1 — Drop-in upgrades to existing NeuralLattice:
  [4] STDP Synapses        — Replace Synapse.strengthen() with STDP update
                              Effort: 2 hours. Impact: directional learning
  [7] Predictive Coding    — Add PredictiveCoder alongside existing recall
                              Effort: 3 hours. Impact: proactive Jarvis

PRIORITY 2 — New subsystems that enhance existing architecture:
  [1] CLS Consolidation    — Add FastBuffer + background consolidation loop
                              Effort: 4 hours. Impact: prevents catastrophic forgetting
  [5] Attention Schema     — Add AttentionSchema to track focus/context
                              Effort: 3 hours. Impact: explainability + better context

PRIORITY 3 — Structural enhancements:
  [8] Memory Indexing      — Add cross-modal indexes to MemoryNode
                              Effort: 6 hours. Impact: multimodal memory binding
  [3] Spatial Memory       — Add coordinates to nodes, room-based organization
                              Effort: 5 hours. Impact: organized recall

PRIORITY 4 — Future capabilities:
  [6] Embodied Cognition   — SensorimotorLoop connecting vision+speech+action
                              Effort: 8 hours. Impact: grounded understanding
  [2] Induction Engine     — Sequence prediction for user behavior
                              Effort: 4 hours. Impact: anticipatory assistance
```

### How They Compose Together

```
User speaks
  |
  v
[7] PredictiveCoder: predict what they'll ask
  |
  v
[5] AttentionSchema: allocate focus, suppress distractors
  |
  v
[6] SensorimotorLoop: bind audio + visual context
  |
  v
[8] HippocampalIndexer: create sparse index across all modalities
  |
  v
[4] STDP: update synapses based on activation timing
  |
  v
NeuralLattice.recall() + [3] SpatialMemory: find relevant memories
  |
  v
[2] InductionEngine: predict next step in sequence
  |
  v
RESPOND
  |
  v
[7] PredictiveCoder.observe(): measure surprise, adjust learning rate
  |
  v
[1] CLS FastBuffer: store raw episode
  |
  v
(idle) [1] CLS consolidate(): replay and integrate
```

### Mapping to Existing JARVIS Code

| Brain Model | Existing Code | Needed Change |
|-------------|---------------|---------------|
| CLS Fast Store | `EpisodicMemorySubsystem` | Add priority replay, link to NeuralLattice |
| CLS Slow Store | `NeuralLattice` | Add consolidation trigger from FastBuffer |
| STDP | `Synapse.strengthen()` | Replace with STDP timing rule |
| Attention Schema | (none) | New: `brain/awareness/attention_schema.py` |
| Predictive Coding | (none) | New: `brain/intelligence/predictive_coder.py` |
| Spatial Memory | `MemoryNode.tags` | Add `location`, `room` fields to MemoryNode |
| Memory Indexing | `MemoryNode.metadata` | Formalize as cross-modal index |
| Induction Engine | (none) | New: `brain/intelligence/induction_engine.py` |
| Embodied Cognition | `brain/vision/`, `brain/speech/` | New: `brain/embodiment/sensorimotor.py` |

### Key Papers to Follow Up (search these for 2025-2026 updates)

1. Kumaran, Hassabis & McClelland — "What Learning Systems do Intelligent Agents Need?"
2. Olsson et al. — "In-context Learning and Induction Heads" (Anthropic)
3. Templeton et al. — "Scaling Monosemanticity" (Anthropic, 2024)
4. Bellmund et al. — "Navigating cognition: Spatial codes for human thinking"
5. Graziano — "Rethinking Consciousness" and subsequent 2024 computational papers
6. Friston — Active Inference textbook (2022) and subsequent updates
7. Millidge et al. — "Predictive Coding Approximates Backpropagation"
8. Sekeres et al. — Memory transformation and hippocampal indexing
9. Pfeiffer — Hippocampal replay generates novel sequences (2024)
10. Whittington et al. — Tolman-Eichenbaum Machine (relational hippocampal learning)
